from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # aws
    aws_access_key_id: str = "test" # Clave de acceso de AWS (puede ser cualquier valor para pruebas locales)
    aws_secret_access_key: str = "test" # Clave secreta de acceso de AWS (puede ser cualquier valor para pruebas locales)
    default_region_aws: str = "us-east-1"

    # s3
    s3_endpoint_url: str = "http://localhost:9000"  # URL del endpoint de S3 local
    s3_bucket_name: str = "my-bucket"  # Nombre del bucket S
    s3_prefix_raw_data: str = "raw-data"  # Prefijo de las claves de S3 para los datos crudos
    s3_prefix_compacted_data: str = "compacted-data"  # Prefijo de las claves de S3 para los datos compactados
    s3_region: str = default_region_aws

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Opcional: ignora variables extra en el .env
    )


# Creamos la instancia aquí
settings = Settings()
