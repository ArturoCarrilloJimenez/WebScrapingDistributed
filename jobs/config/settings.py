from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # AWS Base / Fallback (Retrocompatible)
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    default_region_aws: str = "us-east-1"

    # S3 Base / Fallback
    s3_endpoint_url: str = "http://localhost:4566"
    s3_bucket_name: str = "scraping-data-lake"
    s3_prefix_raw_data: str = "raw-data"
    s3_prefix_compacted_data: str = "compacted-data"
    s3_region: str = default_region_aws
    raw_data_retention_days: int = 7

    # Origen Crudo (Landing Zone - MinIO Local en VPS / Clúster)
    raw_s3_endpoint_url: str | None = s3_endpoint_url
    raw_s3_bucket_name: str | None = s3_bucket_name
    raw_s3_region: str | None = s3_region
    raw_s3_access_key_id: str | None = aws_access_key_id
    raw_s3_secret_access_key: str | None = aws_secret_access_key

    # Destino Consolidado (Analytical Lake - AWS S3 Cloud o Cloudflare R2)
    target_s3_endpoint_url: str | None = s3_endpoint_url
    target_s3_bucket_name: str | None = s3_bucket_name
    target_s3_region: str | None = s3_region
    target_s3_access_key_id: str | None = aws_access_key_id
    target_s3_secret_access_key: str | None = aws_secret_access_key

    # Estrategia de optimización de disco en VPS
    # Si True: Borra los JSONL de MinIO inmediatamente tras confirmar la subida del Parquet a S3
    delete_raw_after_compaction: bool = True

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
