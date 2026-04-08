from shared import BatchResponse, ErrorsBatchResponse, SummaryBatchResponse
import aioboto3
import os
from shared import ScrapingTask
from typing import List
from ..base import TaskProducer


class SQSAioBotoAdapter(TaskProducer):
    def __init__(self, queue_url: str, region: str = "us-east-1"):
        self.queue_url = queue_url
        self.region = region
        self.session = aioboto3.Session()
        self._client = None
        self.NUM_MAX_TASKS = int(os.environ.get("NUM_MAX_TASKS") or 10)

    async def _get_client(self):
        if self._client is None:
            # Creamos el cliente una sola vez para reutilizar conexiones
            self._client = await self.session.client(
                "sqs", region_name=self.region
            ).__aenter__()
        return self._client

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
                {"Id": t.job_id, "MessageBody": t.model_dump_json()} for t in tasks
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
        except:  # noqa: E722
            errors_report = [
                ErrorsBatchResponse(
                    task_id=original_task.task_id,
                    url=original_task.url,
                    reason=f"[{fail.get('Code')}] {fail.get('Message')}",
                    # Si es SenderFault (error del cliente/formato), no es reintentable
                    retryable=not fail.get("SenderFault", False),
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
