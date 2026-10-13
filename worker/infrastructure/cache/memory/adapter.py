import asyncio
import time
from ..base import BaseCacheAdapter


class MemoryCacheAdapter(BaseCacheAdapter):
    """
    Adaptador pasivo en memoria RAM para entornos locales, tests y contingencia.
    """

    def __init__(self):
        self._store: dict[str, tuple[str, float | None]] = {}
        self._counters: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        async with self._lock:
            if key not in self._store:
                return None
            value, expires_at = self._store[key]
            if expires_at is not None and time.time() > expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        async with self._lock:
            expires_at = (time.time() + ttl_seconds) if ttl_seconds is not None else None
            self._store[key] = (value, expires_at)

    async def incr(self, key: str) -> int:
        async with self._lock:
            current = self._counters.get(key, 0) + 1
            self._counters[key] = current
            return current

    async def delete(self, key: str) -> bool:
        async with self._lock:
            deleted_store = self._store.pop(key, None) is not None
            deleted_counter = self._counters.pop(key, None) is not None
            return deleted_store or deleted_counter

    async def close(self) -> None:
        async with self._lock:
            self._store.clear()
            self._counters.clear()
