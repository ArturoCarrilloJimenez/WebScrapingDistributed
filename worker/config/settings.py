from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProxyMode(str, Enum):
    STATIC_POOL = "static_pool"
    BACKCONNECT = "backconnect"


class Settings(BaseSettings):
    num_max_tasks: int = 10
    worker_num_max_concurrent_tasks: int = 10
    default_region_aws: str = "us-east-1"
    sqs_endpoint_url: str = "http://localhost:9324"  # URL del endpoint de SQS local
    # URL de la cola SQS local
    sqs_queue_url: str = "http://localhost:9324/000000000000/my-queue"
    # Clave de acceso de AWS (puede ser cualquier valor para pruebas locales)
    aws_access_key_id: str = "test"
    # Clave secreta de acceso de AWS (puede ser cualquier valor para pruebas locales)
    aws_secret_access_key: str = "test"

    proxy_enabled: bool = False  # Controla si se usan proxies o no
    proxy_mode: ProxyMode = ProxyMode.STATIC_POOL  # "static_pool" o "backconnect"
    proxy_static_list: str = ""  # Lista de proxies para el modo static_pool, separada por comas
    proxy_url: str = ""  # URL del proxy para el modo backconnect

    # Nuevas variables de configuración para afinación y resiliencia del proxy/cliente
    proxy_static_check_interval: float = 30.0
    proxy_static_idle_threshold: float = 180.0
    proxy_max_pool_size: int = 150
    proxy_idle_timeout: float = 60.0
    proxy_max_requests_per_session: int = 100
    proxy_min_requests_per_session: int = 10

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Opcional: ignora variables extra en el .env
    )


# Creamos la instancia aquí
settings = Settings()
