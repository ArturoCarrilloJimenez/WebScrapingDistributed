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
    raw_s3_endpoint_url: str | None = None
    raw_s3_bucket_name: str | None = None
    raw_s3_region: str | None = None
    raw_aws_access_key_id: str | None = None
    raw_aws_secret_access_key: str | None = None

    # Destino Consolidado (Analytical Lake - AWS S3 Cloud o MinIO)
    target_s3_endpoint_url: str | None = None
    target_s3_bucket_name: str | None = None
    target_s3_region: str | None = None
    target_aws_access_key_id: str | None = None
    target_aws_secret_access_key: str | None = None

    # Estrategia de optimización de disco en VPS
    # Si True: Borra los JSONL de MinIO inmediatamente tras confirmar la subida del Parquet a S3
    delete_raw_after_compaction: bool = True

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
        return self.raw_aws_access_key_id or self.aws_access_key_id

    @property
    def effective_raw_secret_key(self) -> str:
        return self.raw_aws_secret_access_key or self.aws_secret_access_key

    @property
    def effective_target_endpoint_url(self) -> str:
        return self.target_s3_endpoint_url or self.s3_endpoint_url

    @property
    def effective_target_bucket_name(self) -> str:
        return self.target_s3_bucket_name or self.s3_bucket_name

    @property
    def effective_target_region(self) -> str:
        return self.target_s3_region or self.s3_region

    @property
    def effective_target_access_key(self) -> str:
        return self.target_aws_access_key_id or self.aws_access_key_id

    @property
    def effective_target_secret_key(self) -> str:
        return self.target_aws_secret_access_key or self.aws_secret_access_key

    # Se usa model_config con SettingsConfigDict
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
