import asyncio
from typing import List
from shared.logging import Logger
from infrastructure.task.base import BaseConsumer
from shared.models import ScrapingTask
from scraping.parsers import ParserFactory
from config.settings import settings
from scraping.exceptions import ErrorCategory, ScrapingError
from scraping.services.storage_buffer import JobBufferService

log = Logger("Worker Engine")


class WorkerController:
    def __init__(self, consumer: BaseConsumer, parser_factory: ParserFactory, max_concurrency: int = 5, buffer_service: JobBufferService = None):
        self.consumer = consumer
        self.parser_factory = parser_factory
        self.buffer_service = buffer_service
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.running = True
        self._active_tasks = set()
        self.NUM_MAX_TASKS = settings.num_max_tasks

        # Buffer en memoria para acumular las tareas que requieren ACK
        self.ack_queue = asyncio.Queue()
        # Tarea de fondo que procesará los lotes de borrado
        self._ack_flusher_task = None
        self._flusher_should_exit = False

        # Variable para almacenar la tarea principal del bucle de ejecución, útil para cancelarla desde el manejador de señales
        self._main_task = None

        # Variables de control para Polling Dinámico y Backoff Corto
        self.current_backoff = 0.0
        self.max_backoff = 10.0  # Techo de 10 segundos para no demorar el inicio de lotes de URLs
        self.backoff_step = 2.0

    async def _run_cycle(self) -> None:
        """Maneja la lógica de un único ciclo de ejecución: capacidad, backoff y despacho."""
        slots_available = self.max_concurrency - len(self._active_tasks)

        # 1. Control de capacidad
        if slots_available <= 0:
            await asyncio.wait(self._active_tasks, return_when=asyncio.FIRST_COMPLETED)
            return

        # 2. Configuración de lote dinámico y micro-backoff
        batch_size = 10 if self.current_backoff > 0 else slots_available
        if self.current_backoff > 0:
            await asyncio.sleep(self.current_backoff)

        # 3. Consumo y ajuste dinámico de polling
        tasks = await self.consumer.fetch(batch_size=batch_size)
        if not tasks:
            self.current_backoff = min(self.current_backoff + self.backoff_step, self.max_backoff)
        else:
            self.current_backoff = 0.0

        # 4. Despacho concurrente de tareas
        for task in tasks:
            t = asyncio.create_task(self._process_task_wrapper(task))
            self._active_tasks.add(t)
            t.add_done_callback(self._active_tasks.discard)

    async def _execute_shutdown(self) -> None:
        """Protocolo seguro y ordenado para liberar recursos y esperar tareas en vuelo."""
        log.warning("=== INICIANDO PROTOCOLO DE APAGADO SEGURO (FINALLY) ===")
        self.running = False

        # PASO 1: Esperar tareas de scraping activas
        if self._active_tasks:
            log.info(f"Esperando la finalización de {len(self._active_tasks)} tareas activas de scraping...")
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            log.info("Todas las tareas de scraping en vuelo han terminado.")

        # PASO 2: Cerrar conexiones y limpiar buffers (drenar RAM a S3 y encolar ACKs)
        if self.buffer_service:
            await self.buffer_service.close()

        # PASO 3: Indicar al flusher que puede terminar una vez se vacíe la cola
        self._flusher_should_exit = True

        # PASO 4: Vaciar flusher asíncrono de ACKs
        if self._ack_flusher_task:
            log.info("Esperando a que el flusher asíncrono vacíe la cola de ACKs en memoria...")
            await self._ack_flusher_task

        # PASO 5: Cerrar el consumidor (liberar recursos de red/SQS)
        if hasattr(self.consumer, "close"):
            await self.consumer.close()

        # PASO 6: Cerrar navegadores de Playwright si existen
        try:
            from scraping.parsers.dinamic_parse import DynamicParser
            await DynamicParser.close_browser()
            log.info("Navegadores Playwright cerrados limpiamente.")
        except Exception as e:
            log.error(f"Error cerrando navegadores Playwright en el shutdown: {e}")

    async def run(self) -> None:
        """Punto de entrada principal (El bucle infinito). Flujo plano y legible."""
        self._main_task = asyncio.current_task()
        log.info(f"Engine arrancado. Concurrencia máx: {self.semaphore._value}")
        
        self._ack_flusher_task = asyncio.create_task(self._ack_flusher())
        try:
            while self.running:
                try:
                    await self._run_cycle()
                except asyncio.CancelledError:
                    raise  # Propagamos para romper el bucle while limpiamente
                except Exception as e:
                    log.error(f"Error crítico en el loop del Engine: {str(e)}")
                    await asyncio.sleep(5)  # Evitar bucle de errores infinito
        except asyncio.CancelledError:
            log.warning("Bucle de ejecución interrumpido por señal de apagado del sistema.")
            raise
        finally:
            await self._execute_shutdown()

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
                    f"Datos extraídos de {task.task_id} | URL: {task.url} | Parser: {task.parser_type.value}")

                # Si todo sale bien, lo enviamos al buffer de ACK (borrar de SQS)
                await self.buffer_service.add_record(result)
                log.info(
                    f"Tarea completada y enviada para ACK: {task.task_id}")
            except ScrapingError as se:
                await self._handle_scraping_error(task, se)

            except Exception as e:
                log.error(f"Fallo en tarea {task.task_id}: {str(e)}")

    async def _ack_flusher(self):
        """
        Orquestador: Mantiene el flujo de ACK mientras el worker esté vivo.
        """
        log.info("Flusher de ACKs asíncrono inicializado.")
        
        while not self._flusher_should_exit or not self.ack_queue.empty():
            batch = await self._gather_batch()
            if batch:
                await self._process_batch(batch)

    async def _gather_batch(self) -> List[ScrapingTask]:
        """
        Responsabilidad única: Recolectar hasta NUM_MAX_TASKS con un timeout.
        """
        batch = []
        try:
            # Espera inicial
            timeout = 1.0 if self.running else 0.1
            batch.append(await asyncio.wait_for(self.ack_queue.get(), timeout=timeout))
        except asyncio.TimeoutError:
            return batch

        # Llenado del lote
        deadline = asyncio.get_running_loop().time() + 0.2
        while len(batch) < self.NUM_MAX_TASKS:
            time_left = max(deadline - asyncio.get_running_loop().time(), 0.001)
            try:
                task = await asyncio.wait_for(self.ack_queue.get(), timeout=time_left)
                batch.append(task)
            except asyncio.TimeoutError:
                break
        return batch

    async def _process_batch(self, batch: List[ScrapingTask]):
        """
        Responsabilidad única: Envío a SQS y limpieza de estados.
        """
        try:
            await self.consumer.acknowledge_batch(batch)
            log.info(f"Batch ACK completado para {len(batch)} tareas.")
            for _ in batch:
                self.ack_queue.task_done()
        except Exception as e:
            log.error(f"Fallo catastrófico en ACK: {str(e)}")

    def stop(self):
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
                f"Tarea {task.task_id} superó el máximo de reintentos ({task.retry_count}/{task.max_retries}) por [{category.value}]. Eliminando de la cola principal (descartada).")
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
                f"Bloqueo detectado en tarea {task.task_id}. Enfriando infraestructura por {delay}s.")

        elif category == ErrorCategory.SERVER_ERROR:
            # Tenías duda con el 500: SÍ se reintenta. Los errores 5xx (500, 502, 503) suelen ser
            # micro-caídas del servidor remoto o despliegues en caliente del objetivo.
            delay = 15 * task.retry_count
            log.warning(
                f"Error de servidor remoto en tarea {task.task_id}. Reintento en {delay}s.")

        await self.consumer.heartbeat(task, visibility_timeout=delay)
