import logging
from typing import Optional
import asyncpg
from config.settings import Settings

logger = logging.getLogger(__name__)


class DatabaseConnectionManager:
    """
    Gestor del pool de conexiones asincrono para PostgreSQL usando asyncpg.
    Implementa patron Singleton/Lifespan para FastAPI.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> Optional[asyncpg.Pool]:
        """Inicializa el pool de conexiones a PostgreSQL."""
        if self._pool is not None:
            return self._pool

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
                "Pool de conexiones a PostgreSQL inicializado con exito en %s:%d/%s",
                self._settings.postgres_host,
                self._settings.postgres_port,
                self._settings.postgres_db,
            )
            return self._pool
        except Exception as e:
            logger.warning(
                "No se pudo conectar a PostgreSQL (%s). Se operara con respaldo en memoria/semilla.",
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
