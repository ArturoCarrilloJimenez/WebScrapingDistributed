from typing import List
import asyncio
import time
from curl_cffi.requests import AsyncSession
from shared.logging import Logger
from .proxy_provider import BaseProxyProvider

log = Logger("Proxy Provider")


class StaticPoolProxyProvider(BaseProxyProvider):
    def __init__(self, proxy_urls: List[str], check_interval: float = 30.0, idle_threshold: float = 180.0):
        self.proxy_urls_all = proxy_urls
        self.proxy_urls_active = list(proxy_urls)
        self._index = 0

        self.check_interval = check_interval
        self.idle_threshold = idle_threshold
        self._last_request_time = time.time()
        self._is_sleeping = False
        self._monitor_task = None

    def get_proxy_url(self, sticky_session_id: str = None) -> str | None:
        if not self.proxy_urls_all:
            return None

        # 1. Registrar actividad
        self._last_request_time = time.time()

        # 2. Asegurar que el monitor esté iniciado si hay un loop de eventos de asyncio activo
        try:
            loop = asyncio.get_running_loop()
            if not self._monitor_task or self._monitor_task.done():
                self._monitor_task = loop.create_task(self._health_check_loop())
        except RuntimeError:
            pass

        # 3. Si estaba en standby, despertarlo
        if self._is_sleeping:
            self._is_sleeping = False
            log.info("Petición recibida. Reactivando bucle de salud de proxies...")

        # 4. Si se proporciona un sticky_session_id, se asigna consistentemente al mismo proxy
        if sticky_session_id:
            idx = hash(sticky_session_id) % len(self.proxy_urls_all)
            url = self.proxy_urls_all[idx]
            if url in self.proxy_urls_active:
                return url
            if self.proxy_urls_active:
                return self.proxy_urls_active[0]
            return url

        # 5. Si no hay sesión, rotación pura en cada petición (Round Robin) sobre activos
        pool = self.proxy_urls_active if self.proxy_urls_active else self.proxy_urls_all
        url = pool[self._index % len(pool)]
        self._index = (self._index + 1) % len(pool)
        return url

    async def _ping_proxy(self, proxy_url: str) -> bool:
        """Chequeo de red rápido contra un endpoint ligero (204 No Content)."""
        try:
            async with AsyncSession(proxy=proxy_url, timeout=5.0) as session:
                response = await session.get("http://www.gstatic.com/generate_204", allow_redirects=False)
                if response.status_code == 204 or (200 <= response.status_code < 400):
                    return True
        except Exception:
            pass
        return False

    async def _health_check_loop(self):
        """Bucle periódico de salud que se duerme si no hay actividad en N segundos."""
        log.info("Bucle de monitoreo de proxies iniciado.")
        while True:
            # Si superamos el tiempo límite sin peticiones, suspendemos los pings
            if time.time() - self._last_request_time > self.idle_threshold:
                if not self._is_sleeping:
                    log.warning("Inactividad de peticiones detectada. Suspendiendo pings de proxies para ahorrar ancho de banda.")
                    self._is_sleeping = True
                await asyncio.sleep(5.0)
                continue

            # Realizar pings en paralelo
            tasks = [self._ping_proxy(url) for url in self.proxy_urls_all]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            active = []
            for url, res in zip(self.proxy_urls_all, results):
                if isinstance(res, bool) and res:
                    active.append(url)

            # Actualizamos la lista de activos
            if active:
                self.proxy_urls_active = active
            else:
                log.warning("Todos los proxies fallaron el chequeo de salud. Usando fallback de lista completa.")
                self.proxy_urls_active = list(self.proxy_urls_all)

            await asyncio.sleep(self.check_interval)