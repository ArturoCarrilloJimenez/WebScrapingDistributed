from typing import List
from botocore.config import Config
import asyncio

from shared.logging import Logger
import aioboto3
from shared.models import ScrapingTask
from ..base import BaseConsumer
from config.settings import settings
from pydantic import ValidationError


log = Logger("SQS Adapter")


class SQSAioBotoAdapter(BaseConsumer):
    def __init__(self, endpoint_url: str, queue_url: str, region: str = "us-east-1"):
        self.endpoint_url = endpoint_url
        self.queue_url = queue_url
        self.region = region
        self.session = aioboto3.Session()
        self._client = None
        self.NUM_MAX_TASKS = min(settings.num_max_tasks, 10)

    async def _get_client(self):
        if self._client is None:
            config = Config(
                max_pool_connections=250,
                retries={'max_attempts': 5, 'mode': 'standard'}
            )
            # Creamos el cliente una sola vez para reutilizar conexiones
            self._client = await self.session.client(
                "sqs",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                config=config
            ).__aenter__()
        return self._client

    async def fetch(self, batch_size: int = min(settings.num_max_tasks, 10)) -> List[ScrapingTask]:
        # Si batch_size <= 10, lo hacemos de forma directa en una sola llamada
        if batch_size <= 10:
            return await self._fetch_single_batch(batch_size)

        # Si batch_size > 10, dividimos en lotes concurrentes de máximo 10
        fetch_sizes = []
        remaining = batch_size
        while remaining > 0:
            size = min(remaining, 10)
            fetch_sizes.append(size)
            remaining -= size

        # Limitamos a un máximo de 10 peticiones concurrentes para evitar saturar sockets
        fetch_sizes = fetch_sizes[:10]

        fetch_tasks = [asyncio.create_task(self._fetch_single_batch(size)) for size in fetch_sizes]
        pending = set(fetch_tasks)
        tasks = []

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            found_any = False
            for fut in done:
                try:
                    res = fut.result()
                    if res:
                        tasks.extend(res)
                        found_any = True
                except Exception as e:
                    log.error(f"Error en llamada paralela de fetch en SQS Adapter: {str(e)}")

            # Si ya conseguimos tareas en alguna de las peticiones paralelas,
            # cancelamos las restantes inmediatamente para no retrasar el procesamiento.
            if found_any:
                for p in pending:
                    p.cancel()
                break

        return tasks

    async def _fetch_single_batch(self, batch_size: int) -> List[ScrapingTask]:
        client = await self._get_client()

        response = await client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=batch_size,
            WaitTimeSeconds=20,  # Long Polling activo para reducir costes y llamadas vacías
            AttributeNames=["All"]
        )

        tasks = []
        for msg in response.get("Messages", []):
            try:
                # Inyectamos el ReceiptHandle en la metadata para poder hacer Ack luego
                task = ScrapingTask.model_validate_json(msg["Body"])
                task.context["_sqs_handle"] = msg["ReceiptHandle"]

                # Obtenemos el número de intentos desde los atributos del mensaje para gestionar reintentos
                # Debemos de hacerlo de esta forma porque SQS no reenvía el mismo mensaje con un contador de reintentos actualizado, sino que es el consumidor quien debe inferirlo a partir de los atributos del mensaje.
                attributes = msg.get("Attributes", {})
                approximate_receive_count = int(attributes.get("ApproximateReceiveCount", 1))

                task.retry_count = approximate_receive_count - 1

                tasks.append(task)
            except ValidationError as ve:
                # Lógica de defensa contra Poison Pills
                log.error(
                    "Poison Pill detectada. Error de validación en el payload del mensaje.",
                    {"exception": str(ve), "message_id": msg.get("MessageId")}
                )
                # Eliminamos el mensaje corrupto de inmediato para que no vuelva a ciclar en la cola
                await client.delete_message(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=msg["ReceiptHandle"]
                )
            except Exception as e:
                log.error(
                    "Fallo inesperado al deserializar mensaje en el adaptador.",
                    {"exception": type(e).__name__, "error": str(e)}
                )
        return tasks

    async def acknowledge(self, task: ScrapingTask):
        handle = task.context.get("_sqs_handle")

        if not handle:
            log.error(
                "No se encontró el ReceiptHandle para la tarea. No se puede hacer ACK.",
                {"task_id": task.task_id}
            )
            return
        client = await self._get_client()

        await client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle)

    async def acknowledge_batch(self, tasks: List[ScrapingTask]) -> None:
        if not tasks:
            return

        client = await self._get_client()
        entries = []
        
        for task in tasks:
            handle = task.context.get("_sqs_handle")
            if handle:
                entries.append({
                    "Id": task.task_id,
                    "ReceiptHandle": handle
                })

        if entries:
            for i in range(0, len(entries), 10):
                chunk = entries[i:i+10]
                response = await client.delete_message_batch(
                    QueueUrl=self.queue_url,
                    Entries=chunk
                )
                
                # Validar si SQS rechazó el borrado de algún mensaje de forma interna
                if response.get("Failed"):
                    log.error(
                        "Errores parciales detectados en SQS delete_message_batch. Elementos no eliminados.",
                        {"failed_entries": response["Failed"]}
                    )

    async def heartbeat(self, task: ScrapingTask, visibility_timeout: int = 60):
        handle = task.context.get("_sqs_handle")
        if not handle:
            log.error(
                "No se encontró el ReceiptHandle para la tarea. No se puede hacer heartbeat.",
                {"task_id": task.task_id}
            )
            return
        try:
            client = await self._get_client()
            await client.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=handle,
                VisibilityTimeout=visibility_timeout
            )
        except Exception as e:
            log.error(
                "Fallo al enviar heartbeat a SQS (cambiar visibilidad del mensaje).",
                {"task_id": task.task_id, "exception": type(e).__name__, "error": str(e)}
            )

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
