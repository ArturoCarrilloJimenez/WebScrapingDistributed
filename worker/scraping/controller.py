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
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.running = True
        self._active_tasks = set()
        self.NUM_MAX_TASKS = settings.num_max_tasks

        # Buffer en memoria para acumular las tareas que requieren ACK
        self.ack_queue = asyncio.Queue()
        # Tarea de fondo que procesará los lotes de borrado
        self._ack_flusher_task = None

        # Variable para almacenar la tarea principal del bucle de ejecución, útil para cancelarla desde el manejador de señales
        self._main_task = None

    async def run(self):
        """Punto de entrada principal (El bucle infinito)"""
        # Guarda el puntero de la tarea actual
        self._main_task = asyncio.current_task()

        log.info(
            f"Engine arrancado. Concurrencia máx: {self.semaphore._value}")

        # Iniciamos el flusher en segundo plano
        self._ack_flusher_task = asyncio.create_task(self._ack_flusher())
        try:
            while self.running:
                try:
                    slots_available = self.max_concurrency - len(self._active_tasks)

                    # 1. ¿Tenemos capacidad? Si el semáforo está lleno, esperamos.
                    if slots_available <= 0:
                        await asyncio.sleep(0.5)  # Pequeño respiro para la CPU
                        continue

                    # 2. Fetch de tareas (Long Polling 20s configurado en el adaptador)
                    tasks = await self.consumer.fetch(batch_size=min(slots_available, self.NUM_MAX_TASKS))

                    for task in tasks:
                        # 3. Disparamos la ejecución sin bloquear el bucle principal
                        t = asyncio.create_task(self._process_task_wrapper(task))
                        self._active_tasks.add(t)
                        t.add_done_callback(self._active_tasks.discard)

                except asyncio.CancelledError:
                        # Si cancelamos desde stop(), propagamos el error para salir del while
                        raise
                except Exception as e:
                    log.error(f"Error crítico en el loop del Engine: {str(e)}")
                    await asyncio.sleep(5)  # Evitar bucle de errores infinito
        except asyncio.CancelledError:
            log.warning("Bucle de ejecución interrumpido por señal de apagado del sistema.")

        finally:
            log.warning("=== INICIANDO PROTOCOLO DE APAGADO SEGURO (FINALLY) ===")
            # Si estamos en Windows y 'stop()' no se ejecutó, esto avisa al flusher de inmediato.
            self.running = False

            # PASO 1: Esperar a que las tareas de scraping en vuelo terminen de procesarse.
            if self._active_tasks:
                log.info(f"Esperando la finalización de {len(self._active_tasks)} tareas activas de scraping...")
                
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
                log.info("Todas las tareas de scraping en vuelo han terminado.")

            # PASO 2: Esperar a que el flusher termine de procesar los ACKs restantes.
            if self._ack_flusher_task:
                log.info("Esperando a que el flusher asíncrono vacíe la cola de ACKs en memoria...")
                await self._ack_flusher_task

            # PASO 3: Cerrar conexiones con el broker y limpiar recursos.
            log.info("Cerrando sockets y conexiones con el broker SQS...")
            await self.consumer.close()
                
            log.warning("=== WORKER ENGINE APAGADO LIMPIAMENTE ===")

    async def _process_task_wrapper(self, task: ScrapingTask):
        """Envoltorio para gestionar el semáforo y el ciclo de vida"""
        async with self.semaphore:
            # Antes de procesar, verificamos si el motor sigue corriendo. Esto evita disparar tareas nuevas durante el apagado.
            if not self.running:
                log.warning(f"Evitando disparo de red para tarea {task.task_id} debido a apagado del motor.")
                return


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

        while self.running or not self.ack_queue.empty() or self._active_tasks:
            batch: List[ScrapingTask] = []

            try:
                # 1. Bloqueo controlado: Esperamos hasta 1 segundo a que entre la primera tarea del lote
                current_timeout = 1.0 if self.running else 0.1 # Si ya no estamos corriendo, no queremos esperar tanto por nuevas tareas
                task = await asyncio.wait_for(self.ack_queue.get(), timeout=current_timeout)
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
        if not self.running:
            return
        
        log.info("Petición de parada externa recibida. Cambiando estado de ejecución...")
        self.running = False

        # Cancelamos la tarea principal para romper cualquier await pendiente (como el fetch de 20s)
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()

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
