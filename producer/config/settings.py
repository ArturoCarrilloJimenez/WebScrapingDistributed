from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    num_max_tasks: int
    producer_port: int
    default_region_aws: str
    sqs_queue_url: str
    aws_access_key_id: str
    aws_secret_access_key: str

    class Config:
        env_file = ".env"


# Creamos la instancia aquí
settings = Settings()
