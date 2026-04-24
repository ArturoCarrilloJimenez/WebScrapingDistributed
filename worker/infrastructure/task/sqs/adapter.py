from typing import List

from shared.logging import Logger
import aioboto3
from shared.models import ScrapingTask
from ..base import BaseConsumer
from config.settings import settings

log = Logger("SQS Adapter")


class SQSAioBotoAdapter(BaseConsumer):
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

    async def fetch(self, batch_size: int = 10) -> List[ScrapingTask]:
        client = await self._get_client()

        response = await client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=batch_size,
            WaitTimeSeconds=20,
            AttributeNames=["All"]
        )

        tasks = []
        for msg in response.get("Messages", []):
            # Inyectamos el ReceiptHandle en la metadata para poder hacer Ack luego
            task = ScrapingTask.model_validate_json(msg["Body"])
            task.context["_sqs_handle"] = msg["ReceiptHandle"]
            tasks.append(task)
        return tasks

    async def acknowledge(self, task: ScrapingTask):
        handle = task.context.get("_sqs_handle")
        client = await self._get_client()

        await client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle)

    async def heartbeat(self, task: ScrapingTask):
        handle = task.context.get("_sqs_handle")
        client = await self._get_client()
        await client.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=handle,
                VisibilityTimeout=60
            )

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
