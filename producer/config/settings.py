from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    num_max_tasks: int = 10
    producer_port: int = 8000
    producer_host: str = "localhost"
    debug_mode: bool = True
    default_region_aws: str = "us-east-1"
    sqs_endpoint_url: str = "http://localhost:9324"  # URL del endpoint de SQS local
    sqs_queue_url: str = "http://localhost:9324/000000000000/my-queue"  # URL de la cola SQS local
    sqs_queue_url_dynamic: str = "http://localhost:9324/000000000000/my-queue-dynamic"  # URL de la cola SQS local
    aws_access_key_id: str = "test"  # Clave de acceso de AWS (puede ser cualquier valor para pruebas locales)
    aws_secret_access_key: str = "test"  # Clave secreta de acceso de AWS (puede ser cualquier valor para pruebas locales)

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Opcional: ignora variables extra en el .env
    )


# Creamos la instancia aquí
settings = Settings()
