import asyncio

from config.settings import ProxyMode, settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import BaseConsumer
from infrastructure.network.client import SecureNetworkClient
from scraping.parsers.factory import ParserFactory
from scraping.controller import WorkerController
from infrastructure.network.proxy import StaticPoolProxyProvider, BackconnectProxyProvider
from infrastructure.storage.base import BaseStorageRepository
from infrastructure.storage.s3.adapter import S3StorageRepository
from scraping.services.storage_buffer import JobBufferService


# Instancias Únicas (Singletons de Infraestructura)
_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.sqs_region
)

_adapter_s3_instance = S3StorageRepository(
    endpoint_url=settings.s3_endpoint_url,
    bucket_name=settings.s3_bucket_name,
    prefix_raw_data=settings.s3_prefix_raw_data,
    region=settings.s3_region
)

_job_buffer_service_instance = None

def get_task_consumer() -> BaseConsumer:
    return _adapter_sqs_instance


def get_storage_repository() -> BaseStorageRepository:
    return _adapter_s3_instance


def get_secure_network_client() -> SecureNetworkClient:
    if not settings.proxy_enabled:
        return SecureNetworkClient(
            proxy_provider=None,
            max_pool_size=settings.proxy_max_pool_size,
            idle_timeout=settings.proxy_idle_timeout,
            max_requests_per_session=settings.proxy_max_requests_per_session,
            min_requests_per_session=settings.proxy_min_requests_per_session
        )

    # Configuramos el proveedor de proxies según el modo seleccionado en la configuración
    if settings.proxy_mode == ProxyMode.STATIC_POOL:
        raw_list = settings.proxy_static_list or ""
        proxy_urls = [url.strip()
                      for url in raw_list.split(",") if url.strip()]
        provider = StaticPoolProxyProvider(
            proxy_urls=proxy_urls,
            check_interval=settings.proxy_static_check_interval,
            idle_threshold=settings.proxy_static_idle_threshold
        )
    else:
        provider = BackconnectProxyProvider(settings.proxy_url or None)

    return SecureNetworkClient(
        proxy_provider=provider,
        max_pool_size=settings.proxy_max_pool_size,
        idle_timeout=settings.proxy_idle_timeout,
        max_requests_per_session=settings.proxy_max_requests_per_session,
        min_requests_per_session=settings.proxy_min_requests_per_session
    )


from scraping.security.honeypot_guard import HoneypotGuard
from scraping.parsers.extractor import UniversalDOMExtractor

_honeypot_guard_instance = HoneypotGuard()
_dom_extractor_instance = UniversalDOMExtractor(honeypot_guard=_honeypot_guard_instance)


def get_honeypot_guard() -> HoneypotGuard:
    return _honeypot_guard_instance


def get_dom_extractor() -> UniversalDOMExtractor:
    return _dom_extractor_instance


def get_parser_factory() -> ParserFactory:
    # La factoría se alimenta del cliente centralizado de red y del extractor universal (que integra HoneypotGuard)
    network_client = get_secure_network_client()
    extractor = get_dom_extractor()
    return ParserFactory(
        network_client=network_client,
        extractor=extractor
    )





def get_worker_controller(max_concurrency: int = settings.worker_num_max_concurrent_tasks) -> WorkerController:
    # Resolvemos limpiamente el grafo de dependencias del sistema
    consumer = get_task_consumer()
    parser_factory = get_parser_factory()

    controller =  WorkerController(
        consumer=consumer,
        parser_factory=parser_factory,
        max_concurrency=max_concurrency
    )

    controller.buffer_service = get_job_buffer_service(controller.ack_queue)

    return controller


def get_job_buffer_service(ack_queue: asyncio.Queue) -> JobBufferService:
    global _job_buffer_service_instance
    if _job_buffer_service_instance is None:
        _job_buffer_service_instance = JobBufferService(
            repository=get_storage_repository(),
            ack_queue=ack_queue,
            max_bytes=3 * 1024 * 1024,
            max_seconds=60.0
        )
    return _job_buffer_service_instance
