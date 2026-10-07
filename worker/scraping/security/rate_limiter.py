import asyncio
import time
from urllib.parse import urlparse
from shared.logging import Logger
from infrastructure.cache import BaseCacheAdapter, MemoryCacheAdapter

log = Logger("Domain Rate Limiter")


class DomainRateLimiter:
    """
    Controlador de tasa de salida distribuido por dominio.
    Utiliza el algoritmo Token Bucket basado en ventanas de 1 segundo sobre un adaptador de caché
    (Redis para entornos distribuidos o Memoria para local/fallback).
    """

    def __init__(self, cache_adapter: BaseCacheAdapter | None = None):
        self._cache = cache_adapter or MemoryCacheAdapter()

    def extract_domain(self, url_or_domain: str) -> str:
        clean = str(url_or_domain).strip().lower()
        if clean.startswith(("http://", "https://")):
            host = urlparse(clean).netloc or clean
        else:
            host = clean.split("/")[0]
        if ":" in host:
            host = host.split(":")[0]
        return host

    async def acquire(self, url_or_domain: str, rate_limit_per_second: float | None) -> None:
        """
        Adquiere permiso para emitir una petición hacia el dominio objetivo.
        Si la tasa excede el límite por segundo, duerme asíncronamente hasta el siguiente segundo.
        """
        if rate_limit_per_second is None or rate_limit_per_second <= 0:
            return

        domain = self.extract_domain(url_or_domain)
        max_allowed = max(1, int(rate_limit_per_second))

        while True:
            now = time.time()
            current_sec = int(now)
            key = f"ratelimit:{domain}:{current_sec}"

            count = await self._cache.incr(key)
            if count <= max_allowed:
                return

            # Si se supera el límite, esperar hasta el inicio del siguiente segundo
            time_to_wait = (current_sec + 1.0) - now + 0.05
            if time_to_wait > 0:
                log.info(
                    f"Rate limit de {rate_limit_per_second} req/s alcanzado para '{domain}' (Conteo: {count}/{max_allowed}). Esperando {time_to_wait:.3f}s..."
                )
                await asyncio.sleep(time_to_wait)

    async def close(self) -> None:
        """Cierra el adaptador de caché."""
        await self._cache.close()
