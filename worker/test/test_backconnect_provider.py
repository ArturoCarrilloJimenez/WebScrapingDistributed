import pytest
from infrastructure.network.proxy.backconnect_provider import BackconnectProxyProvider

def test_backconnect_provider_without_sticky_session():
    """Valida que si no se requiere sticky session, retorne el proxy intacto."""
    provider = BackconnectProxyProvider("http://user:pass@proxy.webshare.io:80")
    assert provider.get_proxy_url(sticky_session_id=None) == "http://user:pass@proxy.webshare.io:80"

def test_backconnect_provider_injects_sticky_session():
    """Valida que inyecte correctamente el id de sesión en la parte de usuario de las credenciales."""
    provider = BackconnectProxyProvider("http://myuser:mypass@p.webshare.io:80")
    formatted_url = provider.get_proxy_url(sticky_session_id="session_xyz_99")
    assert formatted_url == "http://myuser-session-session_xyz_99:mypass@p.webshare.io:80"

def test_backconnect_provider_without_credentials():
    """Valida que si la URL de proxy no contiene credenciales, retorne la URL intacta."""
    provider = BackconnectProxyProvider("http://198.100.12.1:8080")
    formatted_url = provider.get_proxy_url(sticky_session_id="session_123")
    assert formatted_url == "http://198.100.12.1:8080"

def test_backconnect_provider_empty_or_none():
    """Valida el comportamiento defensivo cuando el proxy es vacío o None."""
    provider = BackconnectProxyProvider("")
    assert provider.get_proxy_url("sess_01") == ""
    
    provider_none = BackconnectProxyProvider(None)
    assert provider_none.get_proxy_url("sess_02") is None
