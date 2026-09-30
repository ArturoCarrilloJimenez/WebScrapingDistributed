import asyncio
import logging
from typing import Optional
import asyncpg
from config.settings import Settings

logger = logging.getLogger(__name__)


class DatabaseConnectionManager:
    """
    Gestor del pool de conexiones asincrono para PostgreSQL usando asyncpg.
    Implementa patron Singleton/Lifespan para FastAPI con reintentos y tolerancia a fallos.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(
        self, max_retries: int = 3, retry_delay: float = 1.0
    ) -> Optional[asyncpg.Pool]:
        """Inicializa el pool de conexiones a PostgreSQL con reintentos configurables."""
        if self._pool is not None:
            return self._pool

        for attempt in range(1, max_retries + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    host=self._settings.postgres_host,
                    port=self._settings.postgres_port,
                    user=self._settings.postgres_user,
                    password=self._settings.postgres_password,
                    database=self._settings.postgres_db,
                    min_size=2,
                    max_size=10,
                    command_timeout=10,
                )
                logger.info(
                    "Pool de conexiones a PostgreSQL inicializado con exito en %s:%d/%s (intento %d/%d)",
                    self._settings.postgres_host,
                    self._settings.postgres_port,
                    self._settings.postgres_db,
                    attempt,
                    max_retries,
                )
                return self._pool
            except Exception as e:
                if attempt < max_retries:
                    logger.debug(
                        "Intento %d/%d de conexion a PostgreSQL fallo (%s). Reintentando en %.1fs...",
                        attempt,
                        max_retries,
                        e,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.warning(
                        "No se pudo conectar a PostgreSQL tras %d intentos (%s). Se operara con respaldo en memoria/semilla.",
                        max_retries,
                        e,
                    )

        self._pool = None
        return None

    async def close(self) -> None:
        """Cierra el pool de conexiones de forma limpia."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Pool de conexiones a PostgreSQL cerrado.")

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self._pool
