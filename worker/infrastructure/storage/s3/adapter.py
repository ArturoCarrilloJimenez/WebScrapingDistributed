from re import S

from botocore.config import Config
import aioboto3
from shared.logging import Logger

from config.settings import settings
from ..base import BaseStorageRepository

log = Logger("S3 Storage Repository")


class S3StorageRepository(BaseStorageRepository):
    def __init__(self, endpoint_url: str, bucket_name: str, region: str = "us-east-1"):
        self.endpoint_url = endpoint_url
        self.bucket_name = bucket_name
        self.region = region

        self.session = aioboto3.Session()
        self._client = None

    async def _get_client(self):
        if self._client is not None:
            return self._client
        
        config = Config(
            max_pool_connections=250,  # Capacidad masiva para tus tareas concurrentes
            retries={'max_attempts': 5, 'mode': 'standard'}
        )

        log.info(
            "Abriendo canal persistente con el almacenamiento de objetos S3 (Bootstrap Phase)...")
        try:
            client_context = self.session.client(
                "s3",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                config=config
            )
            # Poblamos la referencia entrando explícitamente en el gestor de contexto asíncrono
            self._client = await client_context.__aenter__()
            log.info(
                f"Conectado a S3 con éxito. Bucket asignado: [{self.bucket_name}] | Endpoint: {self.endpoint_url}")

            return self._client
        except Exception as e:
            log.error(
                f"Fallo crítico al conectar con S3 durante el arranque: {e}")
            raise  # Reventamos el proceso en el segundo cero

    async def save(self, key: str, body: str | bytes) -> None:
        """
        Escribe los bytes del fragmento acumulado sin evaluar estados internos ni cerrojos.
        """
        client = await self._get_client()

        try:
            await client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=body
            )
            log.info(
                f"Bloque de datos (.jsonl) persistido con éxito en S3. Ruta: {key}")
        except Exception as e:
            log.error(
                f"Error de red al escribir objeto en S3 bajo la clave [{key}]: {e}")
            raise

    async def close(self) -> None:
        """
        Drenado y apagado controlado del pool de conexiones.
        """
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
                log.info(
                    "Canales del pool de almacenamiento S3 cerrados de forma limpia.")
            except Exception as e:
                log.error(
                    f"Error al cerrar las conexiones físicas de S3: {e}")
