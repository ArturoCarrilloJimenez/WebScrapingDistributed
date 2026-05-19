import asyncio
from typing import List
from shared.logging import Logger
from infrastructure.task.base import BaseConsumer
from shared.models import ScrapingTask
from scraping.parsers import ParserFactory
from config.settings import settings

log = Logger("Worker Engine")


class WorkerController:
    def __init__(self, consumer: BaseConsumer, max_concurrency: int = 5):
        self.consumer = consumer
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.running = True
        self._active_tasks = set()
        self.NUM_MAX_TASKS = settings.num_max_tasks

        # Buffer en memoria para acumular las tareas que requieren ACK
        self.ack_queue = asyncio.Queue()
        # Tarea de fondo que procesará los lotes de borrado
        self._ack_flusher_task = None

    async def run(self):
        """Punto de entrada principal (El bucle infinito)"""
        log.info(
            f"Engine arrancado. Concurrencia máx: {self.semaphore._value}")

        # Iniciamos el flusher en segundo plano
        self._ack_flusher_task = asyncio.create_task(self._ack_flusher())

        while self.running:
            try:
                # 1. ¿Tenemos capacidad? Si el semáforo está lleno, esperamos.
                if self.semaphore.locked():
                    await asyncio.sleep(0.5)  # Pequeño respiro para la CPU
                    continue

                # 2. Fetch de tareas (Long Polling 20s configurado en el adaptador)
                tasks = await self.consumer.fetch()

                for task in tasks:
                    # 3. Disparamos la ejecución sin bloquear el bucle principal
                    t = asyncio.create_task(self._process_task_wrapper(task))
                    self._active_tasks.add(t)
                    t.add_done_callback(self._active_tasks.discard)

            except Exception as e:
                log.error(f"Error crítico en el loop del Engine: {str(e)}")
                await asyncio.sleep(5)  # Evitar bucle de errores infinito

    async def _process_task_wrapper(self, task: ScrapingTask):
        """Envoltorio para gestionar el semáforo y el ciclo de vida"""
        async with self.semaphore:
            log.info(f"Procesando tarea: {task.task_id} | URL: {task.url}")
            try:
                parser = ParserFactory.get_parser(task.parser_type)

                result = await parser.parse(task)

                log.info(f"✅ Datos extraídos de {task.url}: {result['data']}")

                # Si todo sale bien, lo enviamos al buffer de ACK (borrar de SQS)
                await self.ack_queue.put(task)
                log.info(f"Tarea completada y borrada: {task.task_id}")

            except Exception as e:
                log.error(f"Fallo en tarea {task.task_id}: {str(e)}")
                # Aquí decidiríamos si hacer heartbeat o dejar que SQS lo reintente

    async def _ack_flusher(self):
        """
        Consumidor en segundo plano encargado de agrupar ACKs en lotes de hasta 10
        y enviarlos a SQS de un solo golpe de red.
        """
        log.info("Flusher de ACKs asíncrono inicializado.")
        LINGER_TIME_SECS = 0.1

        while self.running or not self.ack_queue.empty():
            batch: List[ScrapingTask] = []

            try:
                # Esperamos de forma bloqueante el primer elemento para no quemar CPU en vacío
                task = await asyncio.wait_for(self.ack_queue.get(), timeout=1.0)
                batch.append(task)

                deadline = asyncio.get_running_loop().time() + LINGER_TIME_SECS

                # Intentamos extraer el resto del lote de forma no bloqueante hasta llegar al límite de SQS (10)
                while len(batch) < self.NUM_MAX_TASKS and not self.ack_queue.empty():
                    time_left = deadline - asyncio.get_running_loop().time()
                    if time_left <= 0:
                        break

                    try:
                        # Esperamos de forma bloqueante el tiempo restante de la ventana
                        task = await asyncio.wait_for(self.ack_queue.get(), timeout=max(time_left, 0.001))
                        batch.append(task)
                    except asyncio.TimeoutError:
                        break  # No entraron más tareas en el lapso configurado

                    task = self.ack_queue.get_nowait()
                    batch.append(task)

            except asyncio.TimeoutError:
                # Si expira el segundo de espera y tenemos elementos parciales, se procesan
                pass

            if batch:
                try:
                   # 3. Ejecución del ACK en SQS
                    await self.consumer.acknowledge_batch(batch)
                    log.info(
                        f"Batch ACK completado con éxito para {len(batch)} tareas.")

                    # Confirmamos el procesamiento a la cola de asyncio SOLO tras confirmar en SQS
                    for _ in range(len(batch)):
                        self.ack_queue.task_done()
                except Exception as e:
                    log.error(
                        f"Fallo catastrófico al procesar el lote de ACKs: {str(e)}")

    async def stop(self):
        """Detiene el controlador asegurando que no se queden ACKs colgados en memoria"""
        log.info("Deteniendo el Worker Engine...")
        self.running = False

        # Esperamos a que terminen las tareas de scraping que están en ejecución
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

        # Esperamos a que el flusher termine de vaciar la cola de ACKs persistentes en memoria
        if self._ack_flusher_task:
            await self._ack_flusher_task

        log.info("Worker Engine detenido limpiamente.")
