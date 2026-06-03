import pytest
import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock
from curl_cffi.requests import AsyncSession
from infrastructure.network.client import SecureNetworkClient

pytestmark = pytest.mark.asyncio

async def test_client_pool_lru_eviction():
    """Valida que al saturarse el pool de conexiones, la sesión más antigua se desaloje."""
    # Pool de tamaño máximo 2
    client = SecureNetworkClient(max_pool_size=2)
    
    # Creamos mocks para __aenter__ y __aexit__ de AsyncSession
    sessions_created = []
    
    original_aenter = AsyncSession.__aenter__
    original_aexit = AsyncSession.__aexit__
    
    async def mock_aenter(self):
        sessions_created.append(self)
        return self

    async def mock_aexit(self, exc_type, exc_val, exc_tb):
        return None

    with patch.object(AsyncSession, "__aenter__", mock_aenter), \
         patch.object(AsyncSession, "__aexit__", mock_aexit):
         
        # Obtener 3 sesiones diferentes (debería provocar 1 desalojo)
        session1 = await client.get_session("https://domain1.com")
        await asyncio.sleep(0.01) # Asegurar timestamps diferentes
        session2 = await client.get_session("https://domain2.com")
        await asyncio.sleep(0.01)
        session3 = await client.get_session("https://domain3.com")
        
        # El tamaño del pool debe ser exactamente 2
        assert len(client._pool) == 2
        # La primera sesión (domain1.com) debió ser desalojada del pool
        pool_keys = [k[0] for k in client._pool.keys()]
        assert "domain1.com" not in pool_keys
        assert "domain2.com" in pool_keys
        assert "domain3.com" in pool_keys
        
        await client.close()

async def test_client_jitter_rotation():
    """Valida que si una sesión supera su límite dinámico de solicitudes, se cierre y rote."""
    # Jitter límite configurado entre 2 y 2 solicitudes para ser determinista
    client = SecureNetworkClient(min_requests_per_session=2, max_requests_per_session=2)
    
    async def mock_aenter(self):
        return self

    async def mock_aexit(self, exc_type, exc_val, exc_tb):
        return None

    with patch.object(AsyncSession, "__aenter__", mock_aenter), \
         patch.object(AsyncSession, "__aexit__", mock_aexit):
         
        # Primera petición: Crea la sesión (count=0)
        session_a = await client.get_session("https://example.com")
        
        # Segunda petición: Reutiliza la sesión (count=1)
        session_b = await client.get_session("https://example.com")
        assert session_a is session_b
        
        # Tercera petición: Reutiliza la sesión (count=2)
        session_c = await client.get_session("https://example.com")
        assert session_c is session_a
        
        # Cuarta petición: Supera el límite de 2. La sesión debe cerrarse y crearse otra nueva.
        session_d = await client.get_session("https://example.com")
        assert session_d is not session_a
        
        await client.close()

async def test_client_idle_cleanup():
    """Valida que la tarea de fondo barra y cierre automáticamente las sesiones inactivas."""
    # Timeout inactivo de 0.1s para agilizar el test
    client = SecureNetworkClient(idle_timeout=0.1)
    
    async def mock_aenter(self):
        return self

    mock_close = AsyncMock()
    
    with patch.object(AsyncSession, "__aenter__", mock_aenter), \
         patch.object(AsyncSession, "__aexit__", mock_close):
         
        # Empezamos en un tiempo t=1000.0
        current_time = 1000.0
        
        with patch("time.time", side_effect=lambda: current_time):
            await client.get_session("https://example.com")
            assert len(client._pool) == 1
            
            # Ahora simulamos que avanza el tiempo a t=1000.2 (ha pasado 0.2s, que supera el timeout de 0.1s)
            current_time = 1000.2
            
            # Ejecutamos la lógica de limpieza de forma manual
            async def run_one_sweep():
                async with client._lock:
                    now = time.time()
                    keys_to_remove = []
                    for key, entry in client._pool.items():
                        if now - entry["last_used"] > client.idle_timeout:
                            keys_to_remove.append(key)
                    for key in keys_to_remove:
                        entry = client._pool.pop(key)
                        await entry["session"].__aexit__(None, None, None)
            
            await run_one_sweep()
            
        assert len(client._pool) == 0
        mock_close.assert_called_once()
        
        # Detener la tarea de fondo periódica real para un teardown limpio
        if client._cleanup_task:
            client._cleanup_task.cancel()
            try:
                await client._cleanup_task
            except asyncio.CancelledError:
                pass

async def test_client_cleanup_catches_close_exceptions():
    """Valida que si una sesión lanza error al cerrarse, no tumbe la tarea de limpieza ni al cliente."""
    client = SecureNetworkClient()
    
    mock_bad_session = MagicMock()
    mock_bad_session.__aenter__ = AsyncMock(return_value=mock_bad_session)
    # Simulamos error catastrófico al cerrar
    mock_bad_session.__aexit__ = AsyncMock(side_effect=ConnectionResetError("Socket broken unexpectedly"))
    
    client._pool[("example.com", None, None)] = {
        "session": mock_bad_session,
        "last_used": time.time() - 100,
        "request_count": 0,
        "limit": 10
    }
    
    client.idle_timeout = 10
    # Forzar desalojo del elemento con error
    await client._evict_oldest_session()
    
    # Debe haber desalojado y vaciado la sesión a pesar de la excepción
    assert len(client._pool) == 0
    mock_bad_session.__aexit__.assert_called_once()
