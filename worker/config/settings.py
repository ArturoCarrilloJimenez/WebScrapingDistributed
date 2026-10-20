from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProxyMode(str, Enum):
    STATIC_POOL = "static_pool"
    BACKCONNECT = "backconnect"


class Settings(BaseSettings):
    num_max_tasks: int = 10
    worker_num_max_concurrent_tasks: int = 10
    
    # aws
    aws_access_key_id: str = "test" # Clave de acceso de AWS (puede ser cualquier valor para pruebas locales)
    aws_secret_access_key: str = "test" # Clave secreta de acceso de AWS (puede ser cualquier valor para pruebas locales)
    default_region_aws: str = "us-east-1"

    # sqs
    sqs_endpoint_url: str = "http://localhost:9324"  # URL del endpoint de SQS local
    sqs_queue_url: str = "http://localhost:9324/000000000000/my-queue" # URL de la cola SQS local
    sqs_region: str = default_region_aws

    # Landing Zone (MinIO Local / S3 Crudo)
    raw_s3_endpoint_url: str | None = None
    raw_s3_bucket_name: str | None = None
    raw_s3_region: str | None = None
    raw_s3_access_key_id: str | None = None
    raw_s3_secret_access_key: str | None = None

    # Fallback legacy / genérico
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket_name: str = "scraping-data-lake"
    s3_prefix_raw_data: str = "raw-data"
    s3_region: str = default_region_aws

    @property
    def effective_raw_endpoint_url(self) -> str:
        return self.raw_s3_endpoint_url or self.s3_endpoint_url

    @property
    def effective_raw_bucket_name(self) -> str:
        return self.raw_s3_bucket_name or self.s3_bucket_name

    @property
    def effective_raw_region(self) -> str:
        return self.raw_s3_region or self.s3_region

    @property
    def effective_raw_access_key(self) -> str:
        return self.raw_s3_access_key_id or self.aws_access_key_id

    @property
    def effective_raw_secret_key(self) -> str:
        return self.raw_s3_secret_access_key or self.aws_secret_access_key


    # Proxy y cliente de red
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

    # Configuración de renderizado dinámico Playwright & Gestión de recursos
    playwright_max_tasks_per_browser: int = 25  # Recicla Chromium cada N tareas para liberar V8 Heap / RAM
    playwright_v8_max_old_space_size_mb: int = 512  # Límite máximo de Heap JS por pestaña Chromium en MB

    # Redis (Rate Limiting distribuido y Caché robots.txt)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0


    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Opcional: ignora variables extra en el .env
    )


# Creamos la instancia aquí
settings = Settings()
