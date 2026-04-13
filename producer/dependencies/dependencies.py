from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import TaskProducer
from scraping.services.scraping_service import ScrapingOrchestrator
from fastapi import Depends


# 1. Proveedor de Infraestructura
def get_task_producer() -> TaskProducer:
    return SQSAioBotoAdapter(
        queue_url=settings.sqs_queue_url, region=settings.default_region_aws
    )


# 2. Proveedor de Lógica de Negocio (Orquestador)
def get_scraping_orchestrator(
    producer: TaskProducer = Depends(get_task_producer),
) -> ScrapingOrchestrator:
    return ScrapingOrchestrator(adapter=producer)
