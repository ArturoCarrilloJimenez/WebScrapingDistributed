from abc import ABC, abstractmethod

class BaseProxyProvider(ABC):
    @abstractmethod
    def get_proxy_url(self, sticky_session_id: str = None) -> str | None:
        """Devuelve la cadena de conexión completa del proxy a utilizar."""
        pass