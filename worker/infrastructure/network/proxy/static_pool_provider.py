from typing import List

from .proxy_provider import BaseProxyProvider


class StaticPoolProxyProvider(BaseProxyProvider):
    def __init__(self, proxy_urls: List[str]):
        self.proxy_urls = proxy_urls
        self._index = 0

    def get_proxy_url(self, sticky_session_id: str = None) -> str | None:
        if not self.proxy_urls:
            return None

        # Si se proporciona un sticky_session_id, se asigna consistentemente al mismo proxy
        if sticky_session_id:
            idx = hash(sticky_session_id) % len(self.proxy_urls)
            return self.proxy_urls[idx]

        # Si no hay sesión, rotación pura en cada petición (Round Robin)
        url = self.proxy_urls[self._index]
        self._index = (self._index + 1) % len(self.proxy_urls)
        return url