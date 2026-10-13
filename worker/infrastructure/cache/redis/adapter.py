import redis.asyncio as aioredis
from shared.logging import Logger
from ..base import BaseCacheAdapter

log = Logger("Redis Cache Adapter")


class RedisCacheAdapter(BaseCacheAdapter):
    """
    Adaptador de Redis pasivo (I/O puro) para operaciones clave-valor.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
        socket_timeout: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.password = password or None
        self.db = db
        self.socket_timeout = socket_timeout
        self._client: aioredis.Redis | None = None
        self._connected: bool = True

    async def _get_client(self) -> aioredis.Redis | None:
        if self._client is None and self._connected:
            try:
                self._client = aioredis.Redis(
                    host=self.host,
                    port=self.port,
                    password=self.password,
                    db=self.db,
                    socket_connect_timeout=self.socket_timeout,
                    socket_timeout=self.socket_timeout,
                    decode_responses=True,
                )
                await self._client.ping()
                log.info(f"Conexión establecida con Redis en {self.host}:{self.port}/{self.db}")
            except Exception as e:
                log.warning(f"No se pudo conectar a Redis en {self.host}:{self.port}: {e}")
                self._connected = False
                self._client = None
        return self._client

    async def get(self, key: str) -> str | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception as e:
            log.warning(f"Error en GET de Redis para clave '{key}': {e}")
            return None

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        client = await self._get_client()
        if client is None:
            return
        try:
            if ttl_seconds is not None:
                await client.set(key, value, ex=ttl_seconds)
            else:
                await client.set(key, value)
        except Exception as e:
            log.warning(f"Error en SET de Redis para clave '{key}': {e}")

    async def incr(self, key: str) -> int:
        client = await self._get_client()
        if client is None:
            return 1
        try:
            count = await client.incr(key)
            return int(count)
        except Exception as e:
            log.warning(f"Error en INCR de Redis para clave '{key}': {e}")
            return 1

    async def delete(self, key: str) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            result = await client.delete(key)
            return bool(result > 0)
        except Exception as e:
            log.warning(f"Error en DELETE de Redis para clave '{key}': {e}")
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
                log.info("Conexiones físicas con Redis cerradas limpiamente.")
            except Exception as e:
                log.warning(f"Error al cerrar conexión con Redis: {e}")
            self._client = None
