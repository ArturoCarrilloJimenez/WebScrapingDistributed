from typing import Annotated

from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import TaskProducer
from infrastructure.database import (
    DatabaseConnectionManager,
    PostgresDomainPolicyRepository,
)
from scraping.services.scraping_service import ScrapingOrchestrator
from scraping.services.domain_policy_service import DomainPolicyService
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

_db_manager_instance = DatabaseConnectionManager(settings=settings)
_postgres_repo_instance = PostgresDomainPolicyRepository(pool=None)
_domain_policy_service_instance = DomainPolicyService(
    repository=_postgres_repo_instance
)


# Proveedores de Infraestructura
def get_task_producer() -> TaskProducer:
    return _adapter_sqs_instance

def get_task_producer_dynamic() -> TaskProducer:
    return _adapter_sqs_dinamic_instance

def get_db_connection_manager() -> DatabaseConnectionManager:
    return _db_manager_instance

def get_domain_policy_repository() -> PostgresDomainPolicyRepository:
    return _postgres_repo_instance

def get_domain_policy_service() -> DomainPolicyService:
    return _domain_policy_service_instance


# Proveedor de Lógica de Negocio (Orquestador)
def get_scraping_orchestrator(
    producer_static: Annotated[TaskProducer, Depends(get_task_producer)],
    producer_dynamic: Annotated[TaskProducer, Depends(get_task_producer_dynamic)],
    domain_policy_service: Annotated[DomainPolicyService, Depends(get_domain_policy_service)],
) -> ScrapingOrchestrator:
    return ScrapingOrchestrator(
        adapter_static=producer_static,
        adapter_dynamic=producer_dynamic,
        domain_policy_service=domain_policy_service,
    )
