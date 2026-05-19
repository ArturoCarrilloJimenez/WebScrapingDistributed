from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import BaseConsumer
from scraping.controller import WorkerController

_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.default_region_aws
)


# Proveedor de Infraestructura
def get_task_consumer() -> BaseConsumer:
    return _adapter_sqs_instance


def get_worker_controller() -> WorkerController:
    # Resolvemos la cadena de dependencias manualmente
    consumer = get_task_consumer()
    return WorkerController(
        consumer=consumer,
        max_concurrency=settings.num_max_tasks
    )
