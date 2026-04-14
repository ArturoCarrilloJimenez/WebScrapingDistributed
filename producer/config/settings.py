from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    num_max_tasks: int
    producer_port: int
    default_region_aws: str
    sqs_queue_url: str
    aws_access_key_id: str
    aws_secret_access_key: str

    # Security settings
    api_key: str = ""  # Required for authenticated access; leave empty to disable auth
    enable_docs: bool = False  # Disable Swagger/Redoc in production by default
    debug: bool = False  # Disable debug mode by default
    cors_allowed_origins: List[str] = ["http://localhost:3000"]  # Restrictive CORS origins

    class Config:
        env_file = ".env"


# Creamos la instancia aquí
settings = Settings()
