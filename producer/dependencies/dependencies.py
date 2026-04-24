from typing import Annotated

from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import TaskProducer
from scraping.services.scraping_service import ScrapingOrchestrator
from fastapi import Depends

_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.default_region_aws
)


# Proveedor de Infraestructura
def get_task_producer() -> TaskProducer:
    return _adapter_sqs_instance


# Proveedor de Lógica de Negocio (Orquestador)
def get_scraping_orchestrator(
    producer: TaskProducer = Annotated[Depends(get_task_producer)],
) -> ScrapingOrchestrator:
    return ScrapingOrchestrator(adapter=producer)
