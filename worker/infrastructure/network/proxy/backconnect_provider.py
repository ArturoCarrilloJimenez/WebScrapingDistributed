from .proxy_provider import BaseProxyProvider


class BackconnectProxyProvider(BaseProxyProvider):
    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url

    def get_proxy_url(self, sticky_session_id: str = None) -> str:
        if not self.proxy_url or not sticky_session_id:
            return self.proxy_url

        # Inyección de Sticky Session modificando el usuario del proxy para mantener la sesión persistente
        if "@" in self.proxy_url:
            auth, netloc = self.proxy_url.split("@", 1)
            scheme, credentials = auth.split("//", 1)
            if ":" in credentials:
                user, password = credentials.split(":", 1)
                return f"{scheme}//{user}-session-{sticky_session_id}:{password}@{netloc}"
        return self.proxy_url