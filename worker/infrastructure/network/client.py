import secrets

from curl_cffi.requests import AsyncSession
from urllib.parse import urlparse
import asyncio
import time
from shared.logging import Logger
from .proxy import BaseProxyProvider

log = Logger("Network Client Pool")


class SecureNetworkClient:
    def __init__(self, proxy_provider: BaseProxyProvider = None, max_pool_size: int = 150, idle_timeout: float = 60.0, max_requests_per_session: int = 100, min_requests_per_session: int = 10):
        self.proxy_provider = proxy_provider
        self.max_pool_size = max_pool_size
        self.idle_timeout = idle_timeout
        self.max_requests_per_session = max_requests_per_session
        self.min_requests_per_session = min_requests_per_session

        self._pool = {}
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    def _generate_contextual_headers(self, url: str) -> dict:
        parsed_url = urlparse(url)
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": f"{parsed_url.scheme}://{parsed_url.netloc}/",
            "Cache-Control": "max-age=0",
        }

    async def get_session(self, target_url: str, sticky_session_id: str = None) -> AsyncSession:
        parsed_url = urlparse(target_url)
        domain = parsed_url.netloc

        # Obtener Proxy correspondiente
        proxy_string = None
        if self.proxy_provider:
            proxy_string = self.proxy_provider.get_proxy_url(sticky_session_id)

        key = (domain, proxy_string, sticky_session_id)

        async with self._lock:
            now = time.time()
            # 1. Reutilización
            if key in self._pool:
                entry = self._pool[key]
                entry["last_used"] = now
                entry["request_count"] += 1

                # Si superamos el límite dinámico aleatorio, cerramos la sesión para forzar rotación de IP
                if entry["request_count"] > entry["limit"]:
                    log.info(
                        f"Sesión para {domain} superó su límite dinámico de {entry['limit']} peticiones. "
                        f"Cerrando socket para romper patrón de IP y forzar rotación en proxy."
                    )
                    try:
                        await entry["session"].__aexit__(None, None, None)
                    except Exception as e:
                        log.error(f"Error al cerrar sesión para rotación: {e}")
                    del self._pool[key]
                else:
                    log.info(f"Reutilizando sesión existente para dominio: {domain} | Proxy: {proxy_string} | Sticky ID: {sticky_session_id} | Request Count: {entry['request_count']}/{entry['limit']}")
                    return entry["session"]

            # 2. Desalojo si el pool está lleno (LRU básico)
            if len(self._pool) >= self.max_pool_size:
                await self._evict_oldest_session()

            # 3. Creación de una sesión nueva de larga duración
            headers = self._generate_contextual_headers(target_url)
            session = AsyncSession(
                impersonate="chrome",
                timeout=15.0,
                headers=headers,
                proxy=proxy_string
            )
            # Inicializar la sesión explícitamente para abrir recursos de curl en C
            await session.__aenter__()

            rango_peticiones = (self.max_requests_per_session - self.min_requests_per_session) + 1
            limite_aleatorio = self.min_requests_per_session + secrets.randbelow(rango_peticiones)

            self._pool[key] = {
                "session": session,
                "last_used": now,
                "request_count": 0,
                "limit": limite_aleatorio
            }

            log.info(
                f"Sesión creada para dominio: {domain} | Proxy: {proxy_string} | Sticky ID: {sticky_session_id}")

            # Lanzar tarea de fondo para limpieza de sesiones inactivas
            if not self._cleanup_task or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_sessions())

            return session

    async def _evict_oldest_session(self):
        if not self._pool:
            return
        oldest_key = min(self._pool, key=lambda k: self._pool[k]["last_used"])
        oldest_entry = self._pool.pop(oldest_key)
        try:
            await oldest_entry["session"].__aexit__(None, None, None)
            log.info(
                f"Sesión desalojada por límite de tamaño de Pool: {oldest_key[0]}")
        except Exception as e:
            log.error(f"Error al cerrar sesión desalojada: {e}")

    async def _cleanup_idle_sessions(self):
        """Tarea periódica que elimina sesiones inactivas para evitar fugas"""
        while True:
            await asyncio.sleep(15.0)
            async with self._lock:
                if not self._pool:
                    break
                now = time.time()
                keys_to_remove = []
                for key, entry in self._pool.items():
                    if now - entry["last_used"] > self.idle_timeout or entry["request_count"] >= entry["limit"]:
                        keys_to_remove.append(key)

                for key in keys_to_remove:
                    entry = self._pool.pop(key)
                    try:
                        await entry["session"].__aexit__(None, None, None)
                        log.info(
                            f"Sesión inactiva cerrada automáticamente o por límite de solicitudes: {key[0]} | Proxy: {key[1]} | Sticky ID: {key[2]} | Requests: {entry['request_count']}")
                    except Exception as e:
                        log.error(f"Error al cerrar sesión inactiva: {e}")

    async def close(self):
        """Método invocado en el apagado limpio del worker"""
        async with self._lock:
            for key, entry in self._pool.items():
                try:
                    await entry["session"].__aexit__(None, None, None)
                except Exception:
                    pass
            self._pool.clear()
            log.warning(
                "Todas las sesiones persistentes del Pool han sido cerradas limpiamente.")
