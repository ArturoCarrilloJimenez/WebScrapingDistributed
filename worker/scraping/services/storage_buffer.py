import time
import asyncio
import uuid
from typing import Dict
from shared.logging import Logger

from infrastructure.storage.base import BaseStorageRepository
from scraping.interfaces.interfaces import Buffer, ParseResult

log = Logger("Storage Buffer Service")


class JobBufferService:
    def __init__(
        self,
        repository: BaseStorageRepository,
        ack_queue: asyncio.Queue,
        max_bytes: int = 3 * 1024 * 1024,  # 3 MB por defecto
        max_seconds: float = 60.0,         # 60 segundos por defecto
        worker_id: str = f"worker-{uuid.uuid4().hex[:6]}"
    ):
        self.repository = repository
        self.ack_queue = ack_queue
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.worker_id = worker_id

        self._lock = asyncio.Lock()
        self._buffers: Dict[str, Buffer] = {}

        # Tarea de fondo para limpiar buffers inactivos (evita que se queden huérfanos)
        self._ticker_task = None

    async def add_record(self, result: ParseResult) -> None:
        """
        Acumula los resultados en RAM.
        Serializa a JSON de forma inmediata para calcular el tamaño real en bytes
        y amortizar el coste de CPU antes del volcado masivo.
        """
        if self._ticker_task is None:
            self._ticker_task = asyncio.create_task(self._start_ticker_loop())

        job_id = result.task.job_id

        try:
            # Serialización inmediata (estrategia Big Tech para evitar picos de latencia en I/O)
            json_line = result.model_dump_json(by_alias=True)
            # +1 cuenta el salto de línea '\n' fundamental para el formato JSON Lines
            record_bytes = len(json_line.encode("utf-8")) + 1
        except Exception as e:
            log.error(
                f"Error crítico de serialización para la tarea [{result.task.task_id}] del Job [{job_id}]: {e}"
            )
            return

        async with self._lock:
            now = time.time()

            # Inicialización perezosa (Lazy initialization) del buffer del Job
            if job_id not in self._buffers:
                self._buffers[job_id] = Buffer(
                    first_inserted_at=now,
                    current_bytes_size=0,
                    records=[],
                    tasks=[]
                )

            buffer = self._buffers[job_id]
            
            # Guardamos los tipos correctos mapeados con tu modelo Pydantic
            buffer.records.append(json_line)
            buffer.tasks.append(result.task)
            buffer.current_bytes_size += record_bytes

            # Evaluación determinista de límites
            time_expired = now - buffer.first_inserted_at >= self.max_seconds
            size_exceeded = buffer.current_bytes_size >= self.max_bytes

            if size_exceeded or time_expired:
                reason = "TAMAÑO MÁXIMO" if size_exceeded else "TIEMPO LÍMITE EXCEDIDO"
                log.info(f"Disparando Flush para Job [{job_id}] debido a: {reason}.")

                # Extraemos y removemos el buffer de la RAM atómicamente antes de iniciar I/O
                old_buffer = self._buffers.pop(job_id)
                
                # Desacoplamos la subida a S3 en una tarea background no bloqueante
                asyncio.create_task(self._flush_buffer_to_storage(job_id, old_buffer))

    async def _flush_buffer_to_storage(self, job_id: str, buffer: Buffer) -> None:
        """
        Consolida las líneas JSON y realiza la subida al Data Lake (S3).
        Garantiza consistencia At-Least-Once: Solo confirma a SQS si el almacenamiento responde OK.
        """
        if not buffer.records:
            return

        # 1. Construcción eficiente O(N) del stream JSON Lines
        content_string = "\n".join(buffer.records) + "\n"

        # 2. Particionamiento estilo Hive explícito usando metadatos limpios
        timestamp = int(time.time())
        key = f"raw-data/job_id={job_id}/part-{self.worker_id}-{timestamp}.jsonl"

        try:
            # 3. Operación de Red/Escritura contra el repositorio inyectado
            await self.repository.save(key=key, body=content_string)

            # 4. Confirmación segura en lote: enviamos los objetos ScrapingTask reales a la cola
            for task in buffer.tasks:
                await self.ack_queue.put(task)

            log.info(
                f"Vaciado exitoso para Job [{job_id}]. {len(buffer.records)} tareas transferidas a ack_queue."
            )
        except Exception as e:
            # Si el almacenamiento falla, las tareas NO se confirman. 
            # SQS las liberará automáticamente por Visibility Timeout para que otro worker las procese.
            log.error(
                f"Fallo crítico de infraestructura al vaciar Job [{job_id}] en S3. "
                f"Los mensajes se mantendrán en SQS para reintento automático. Motivo: {e}"
            )

    async def _start_ticker_loop(self):
        """Bucle supervisor asíncrono para asegurar que los Jobs de bajo flujo no queden atascados en RAM"""
        while True:
            await asyncio.sleep(5.0)  # Evaluación periódica cada 5 segundos
            async with self._lock:
                now = time.time()
                jobs_to_flush = []

                # Identificamos qué buffers han expirado cronológicamente
                for job_id, buffer in self._buffers.items():
                    if buffer.records and (now - buffer.first_inserted_at >= self.max_seconds):
                        jobs_to_flush.append(job_id)

                # Desacoplamos y vaciamos controladamente sin alterar la iteración del diccionario
                for job_id in jobs_to_flush:
                    log.info(f"Ticker detectó inactividad por tiempo en Job [{job_id}]. Forzando Flush.")
                    old_buffer = self._buffers.pop(job_id)
                    asyncio.create_task(self._flush_buffer_to_storage(job_id, old_buffer))

    async def close(self):
        """Graceful Shutdown: Mecanismo de drenado rápido para evitar pérdida de datos al apagar el contenedor"""
        log.warning("Señal de apagado interceptada. Iniciando drenado síncrono de la RAM hacia S3...")
        if self._ticker_task:
            self._ticker_task.cancel()
            try:
                await self._ticker_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            flush_tasks = []
            # Capturamos absolutamente todo lo que quede pendiente en memoria
            for job_id, old_buffer in self._buffers.items():
                if old_buffer.records:
                    flush_tasks.append(self._flush_buffer_to_storage(job_id, old_buffer))

            if flush_tasks:
                # Bloqueamos el cierre del proceso hasta que todas las subidas terminen o fallen de forma segura
                await asyncio.gather(*flush_tasks, return_exceptions=True)
            
            self._buffers.clear()
            
            await self.repository.close()

            log.warning("Proceso de cierre completado. Estado de memoria volátil: Limpio.")