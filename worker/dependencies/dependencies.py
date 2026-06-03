from config.settings import ProxyMode, settings
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from infrastructure.task.base import BaseConsumer
from infrastructure.network.client import SecureNetworkClient
from scraping.parsers.factory import ParserFactory
from scraping.controller import WorkerController
from infrastructure.network.proxy import StaticPoolProxyProvider, BackconnectProxyProvider


# Instancias Únicas (Singletons de Infraestructura)
_adapter_sqs_instance = SQSAioBotoAdapter(
    endpoint_url=settings.sqs_endpoint_url,
    queue_url=settings.sqs_queue_url,
    region=settings.default_region_aws
)


def get_task_consumer() -> BaseConsumer:
    return _adapter_sqs_instance


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
