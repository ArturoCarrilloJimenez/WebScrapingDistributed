from shared.logging import Logger

from curl_cffi.requests import AsyncSession
from urllib.parse import urlparse
from .proxy import BaseProxyProvider

log = Logger(__name__)


class SecureNetworkClient:
    def __init__(self, proxy_provider: BaseProxyProvider = None):
        # El proveedor puede ser None si trabajamos sin proxies en desarrollo
        self.proxy_provider = proxy_provider

    def _generate_contextual_headers(self, url: str) -> dict:
        parsed_url = urlparse(url)
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": f"{parsed_url.scheme}://{parsed_url.netloc}/",
            "Cache-Control": "max-age=0",
        }

    def create_session(self, target_url: str, sticky_session_id: str = None) -> AsyncSession:
        headers = self._generate_contextual_headers(target_url)

        # Solicitamos la IP resolutiva al proveedor inyectado
        proxy_string = None
        if self.proxy_provider:
            proxy_string = self.proxy_provider.get_proxy_url(sticky_session_id)
            log.info(f"Proxy asignado para {target_url}: {proxy_string.split('@')[-1] if proxy_string else 'No Proxy'}")

        return AsyncSession(
            impersonate="chrome",
            timeout=15.0,
            headers=headers,
            proxy=proxy_string
        )
