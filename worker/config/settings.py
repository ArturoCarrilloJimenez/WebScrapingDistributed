from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    num_max_tasks: int
    default_region_aws: str
    sqs_endpoint_url: str
    sqs_queue_url: str
    aws_access_key_id: str
    aws_secret_access_key: str

    class Config:
        env_file = ".env"


# Creamos la instancia aquí
settings = Settings()
