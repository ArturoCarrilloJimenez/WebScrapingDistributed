from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    num_max_tasks: int
    default_region_aws: str
    sqs_endpoint_url: str
    sqs_queue_url: str
    aws_access_key_id: str
    aws_secret_access_key: str

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Opcional: ignora variables extra en el .env
    )


# Creamos la instancia aquí
settings = Settings()
