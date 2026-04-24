from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import BaseConsumer

_adapter_sqs_instance = SQSAioBotoAdapter(
    queue_url=settings.sqs_queue_url, region=settings.default_region_aws
)


# Proveedor de Infraestructura
def get_task_consumer() -> BaseConsumer:
    return _adapter_sqs_instance
