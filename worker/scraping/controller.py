import asyncio
from typing import List
from shared.logging import Logger
from infrastructure.task.base import BaseConsumer
from shared.models import ScrapingTask
from scraping.parsers import ParserFactory
from config.settings import settings
from scraping.exceptions import ErrorCategory, ScrapingError

log = Logger("Worker Engine")


class WorkerController:
    def __init__(self, consumer: BaseConsumer, parser_factory: ParserFactory, max_concurrency: int = 5):
        self.consumer = consumer
        self.parser_factory = parser_factory
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
                parser = self.parser_factory.get_parser(task.parser_type)

                result = await parser.parse(task)

                log.info(
                    f"✅ Datos extraídos de {task.task_id} | URL: {task.url}: {result['data']}")

                # Si todo sale bien, lo enviamos al buffer de ACK (borrar de SQS)
                await self.ack_queue.put(task)
                log.info(
                    f"Tarea completada y enviada para ACK: {task.task_id}")
            except ScrapingError as se:
                await self._handle_scraping_error(task, se)

            except Exception as e:
                log.error(f"Fallo en tarea {task.task_id}: {str(e)}")

    async def _ack_flusher(self):
        """
        Consumidor en segundo plano encargado de agrupar ACKs en lotes de hasta 10
        y enviarlos a SQS de un solo golpe de red sin fugas de excepciones.
        """
        log.info("Flusher de ACKs asíncrono inicializado.")
        LINGER_TIME_SECS = 0.2  # Tiempo máximo a esperar para acumular un lote completo

        while self.running or not self.ack_queue.empty():
            batch: List[ScrapingTask] = []

            try:
                # 1. Bloqueo controlado: Esperamos hasta 1 segundo a que entre la primera tarea del lote
                task = await asyncio.wait_for(self.ack_queue.get(), timeout=1.0)
                batch.append(task)
            except asyncio.TimeoutError:
                # Si no hay tareas en 1 segundo, volvemos a evaluar el estado del loop
                continue

            # Calculamos un deadline para no esperar indefinidamente por tareas adicionales y garantizar cierta fluidez en el ACK de SQS
            deadline = asyncio.get_running_loop().time() + LINGER_TIME_SECS

            # 2.  Extraemos el resto hasta llenar el lote de SQS (máx 10)
            while len(batch) < self.NUM_MAX_TASKS:
                # Si llegamos al deadline, procesamos lo que tengamos sin esperar más
                time_left = deadline - asyncio.get_running_loop().time()
                if time_left <= 0:
                    break

                try:
                    task = await asyncio.wait_for(self.ack_queue.get(), timeout=max(time_left, 0.001))
                    batch.append(task)

                except asyncio.TimeoutError:
                    # Si la cola se vacía, salimos del empaquetado de forma segura y procedemos al ACK
                    break

            # 3. Despacho atómico del lote
            if batch:
                try:
                    await self.consumer.acknowledge_batch(batch)
                    log.info(
                        f"Batch ACK completado con éxito para {len(batch)} tareas.")

                    # Confirmamos el procesamiento a la cola de asyncio SOLO tras confirmar en SQS
                    for _ in range(len(batch)):
                        self.ack_queue.task_done()
                except Exception as e:
                    log.error(
                        f"Fallo catastrófico al procesar el lote de ACKs en red: {str(e)}")

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

    async def _handle_scraping_error(self, task: ScrapingTask, error: ScrapingError):
        category = error.category

        # --- ERRORES FATALES (Eliminación inmediata) ---
        if category in [ErrorCategory.NOT_FOUND, ErrorCategory.INVALID_SCHEMA]:
            log.error(
                f"Error Fatal [{category.value}] en tarea {task.task_id}: {str(error)}. Forzando ACK (Muerte del mensaje).")
            # Mandamos al flusher para borrarlo de SQS. Esto evita que los 404 huelguen en el clúster.
            await self.ack_queue.put(task)
            return

        # --- ERRORES RECUPERABLES (Gestión de reintentos) ---
        if task.retry_count >= task.max_retries:
            log.error(
                f"Tarea {task.task_id} superó el máximo de reintentos ({task.retry_count}/{task.max_retries}) por [{category.value}]. Enviando a DLQ.")
            await self.ack_queue.put(task)
            return

        # Incrementar contador de intentos internos
        task.retry_count = task.retry_count + 1

        # --- ESTRATEGIA DE REINTENTO SEGÚN LA CATEGORÍA ---
        if category == ErrorCategory.TIMEOUT:
            # Un timeout requiere un backoff corto
            delay = 5 * task.retry_count
            log.warning(
                f"Timeout en tarea {task.task_id}. Reintento {task.context['_current_retries']} en {delay}s.")

        elif category == ErrorCategory.BLOCKED:
            # Un bloqueo antibot requiere enfriar la IP / rotar proxy. Backoff agresivo + Jitter
            delay = min(30 * (2 ** task.retry_count), 300)
            log.warning(
                f"🛡️ Bloqueo detectado en tarea {task.task_id}. Enfriando infraestructura por {delay}s.")

        elif category == ErrorCategory.SERVER_ERROR:
            # Tenías duda con el 500: SÍ se reintenta. Los errores 5xx (500, 502, 503) suelen ser
            # micro-caídas del servidor remoto o despliegues en caliente del objetivo.
            delay = 15 * task.retry_count
            log.warning(
                f"⚙️ Error de servidor remoto en tarea {task.task_id}. Reintento en {delay}s.")

        await self.consumer.heartbeat(task, visibility_timeout=delay)
