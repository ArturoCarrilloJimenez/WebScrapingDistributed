from typing import Annotated

from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import TaskProducer
from scraping.services.scraping_service import ScrapingOrchestrator
from fastapi import Depends

_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.default_region_aws,
)

_adapter_sqs_dinamic_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url_dynamic,
    region=settings.default_region_aws,
)


# Proveedor de Infraestructura
def get_task_producer() -> TaskProducer:
    return _adapter_sqs_instance

def get_task_producer_dynamic() -> TaskProducer:
    return _adapter_sqs_dinamic_instance


# Proveedor de Lógica de Negocio (Orquestador)
def get_scraping_orchestrator(
    producer_static: Annotated[TaskProducer, Depends(get_task_producer)],
    producer_dynamic: Annotated[TaskProducer, Depends(get_task_producer_dynamic)],
) -> ScrapingOrchestrator:
    return ScrapingOrchestrator(adapter_static=producer_static, adapter_dynamic=producer_dynamic)
