from shared.logging import Logger
from fastapi import FastAPI
from contextlib import asynccontextmanager
from shared import BatchResponse, ErrorsBatchResponse, SummaryBatchResponse
import aioboto3
from shared import ScrapingTask
from typing import List
from ..base import TaskProducer
from config.settings import settings

log = Logger("SQS Adapter")


class SQSAioBotoAdapter(TaskProducer):
    def __init__(self, endpoint_url: str, queue_url: str, region: str = "us-east-1"):
        self.endpoint_url = endpoint_url
        self.queue_url = queue_url
        self.region = region
        self.session = aioboto3.Session()
        self._client = None
        self.NUM_MAX_TASKS = settings.num_max_tasks

    async def _get_client(self):
        if self._client is None:
            # Creamos el cliente una sola vez para reutilizar conexiones
            self._client = await self.session.client(
                "sqs",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            ).__aenter__()
        return self._client

    # Gestiona el ciclo de vida
    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        await self._get_client()  # logica al iniciar
        yield
        await self.close()  # logica al cerrar

    async def send_batch(self, tasks: List[ScrapingTask]) -> BatchResponse:
        if len(tasks) > self.NUM_MAX_TASKS:
            raise ValueError(
                f"Lote demasiado grande: {len(tasks)} > {self.NUM_MAX_TASKS}"
            )

        task_map = {t.task_id: t for t in tasks}
        errors_report = []
        try:
            client = await self._get_client()

            # Crear tareas concurrentes
            entries = [
                {"Id": t.task_id, "MessageBody": t.model_dump_json()} for t in tasks
            ]
            response = await client.send_message_batch(
                QueueUrl=self.queue_url, Entries=entries
            )

            for fail in response.get("Failed", []):
                # Recuperamos la tarea original usando el ID que SQS nos devuelve
                original_task = task_map.get(fail["Id"])

                if original_task:
                    errors_report.append(
                        ErrorsBatchResponse(
                            task_id=original_task.task_id,
                            url=original_task.url,
                            reason=f"[{fail.get('Code')}] {fail.get('Message')}",
                            # Si es SenderFault (error del cliente/formato), no es reintentable
                            retryable=not fail.get("SenderFault", False),
                        )
                    )
        except Exception as e:
            log.error(
                "SQS send_batch failed",
                {
                    "error_type": type(e).__name__,
                    "batch_size": len(tasks),
                    "batch_id": tasks[0].batch_id,
                },
            )
            errors_report = [
                ErrorsBatchResponse(
                    task_id=t.task_id,
                    url=t.url,
                    reason=f"[Internal Error: {type(e).__name__}] {str(e)}",
                    retryable=True,
                )
                for t in tasks
            ]

        return BatchResponse(
            batch_id=tasks[0].batch_id,
            summary=SummaryBatchResponse(
                total=len(tasks),
                processed=len(tasks) - len(errors_report),
                failed=len(errors_report),
            ),
            errors=errors_report,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
