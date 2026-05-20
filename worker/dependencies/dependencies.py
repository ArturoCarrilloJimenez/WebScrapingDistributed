from config.settings import settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import BaseConsumer
from infrastructure.network.client import SecureNetworkClient
from scraping.parsers.factory import ParserFactory
from scraping.controller import WorkerController

# Instancias Únicas (Singletons de Infraestructura)
_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.default_region_aws
)

_network_client_instance = SecureNetworkClient()


def get_task_consumer() -> BaseConsumer:
    return _adapter_sqs_instance


def get_secure_network_client() -> SecureNetworkClient:
    return _network_client_instance


def get_parser_factory() -> ParserFactory:
    # La factoría se alimenta del cliente centralizado de red
    network_client = get_secure_network_client()
    return ParserFactory(network_client=network_client)


def get_worker_controller(max_concurrency: int = settings.worker_num_max_concurrent_tasks) -> WorkerController:
    # Resolvemos limpiamente el grafo de dependencias del sistema
    consumer = get_task_consumer()
    parser_factory = get_parser_factory()
    
    return WorkerController(
        consumer=consumer,
        parser_factory=parser_factory,
        max_concurrency=max_concurrency
    )