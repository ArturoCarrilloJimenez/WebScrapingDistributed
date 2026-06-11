import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from infrastructure.network.proxy.static_pool_provider import StaticPoolProxyProvider

pytestmark = pytest.mark.asyncio


async def test_static_pool_provider_rotation():
    proxies = ["http://proxy1", "http://proxy2", "http://proxy3"]
    provider = StaticPoolProxyProvider(proxies, check_interval=10, idle_threshold=10)
    try:
        # Verify pure Round Robin rotation
        assert provider.get_proxy_url() == "http://proxy1"
        assert provider.get_proxy_url() == "http://proxy2"
        assert provider.get_proxy_url() == "http://proxy3"
        assert provider.get_proxy_url() == "http://proxy1"
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass


async def test_static_pool_provider_sticky_session():
    proxies = ["http://proxy1", "http://proxy2", "http://proxy3"]
    provider = StaticPoolProxyProvider(proxies)
    try:
        # Sticky session should consistently return the same proxy
        url1 = provider.get_proxy_url(sticky_session_id="session_a")
        url2 = provider.get_proxy_url(sticky_session_id="session_a")
        assert url1 == url2
        provider.get_proxy_url(sticky_session_id="session_b")
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass


async def test_static_pool_provider_health_check_filtering():
    proxies = ["http://proxy1", "http://proxy2"]
    provider = StaticPoolProxyProvider(proxies, check_interval=0.05, idle_threshold=10)
    
    async def mock_ping(url):
        return url == "http://proxy1"
        
    try:
        with patch.object(provider, "_ping_proxy", side_effect=mock_ping):
            provider.get_proxy_url()
            # Wait for loop to run
            await asyncio.sleep(0.15)
            assert provider.get_proxy_url() == "http://proxy1"
            assert provider.get_proxy_url() == "http://proxy1"
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass


async def test_static_pool_provider_standby_on_idle():
    proxies = ["http://proxy1", "http://proxy2"]
    # Set a very low idle threshold of 0.05s
    provider = StaticPoolProxyProvider(proxies, check_interval=0.05, idle_threshold=0.05)
    
    async def mock_ping(url):
        return True
        
    try:
        with patch.object(provider, "_ping_proxy", side_effect=mock_ping):
            # Initial request
            provider.get_proxy_url()
            
            # Wait for idle_threshold to pass and monitor loop to detect it
            start = time.time()
            while time.time() - start < 1.0:
                if provider._is_sleeping:
                    break
                await asyncio.sleep(0.01)
                
            assert provider._is_sleeping is True
            
            # Now request again, which should wake it up
            provider.get_proxy_url()
            await asyncio.sleep(0.02)
            assert provider._is_sleeping is False
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass


async def test_static_pool_provider_empty_urls():
    provider = StaticPoolProxyProvider([])
    assert provider.get_proxy_url() is None


async def test_static_pool_provider_no_running_loop():
    provider = StaticPoolProxyProvider(["http://proxy1"])
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No loop")):
        assert provider.get_proxy_url() == "http://proxy1"
        assert provider._monitor_task is None


async def test_static_pool_provider_sticky_session_inactive_fallbacks():
    proxies = ["http://proxy1", "http://proxy2", "http://proxy3"]
    provider = StaticPoolProxyProvider(proxies)
    
    # Force self.proxy_urls_active to only contain proxy2
    provider.proxy_urls_active = ["http://proxy2"]
    
    # We want to pick a sticky_session_id that hashes to something other than proxy2 (index 1)
    found_different_idx = False
    for i in range(100):
        session_id = f"session_{i}"
        idx = hash(session_id) % len(proxies)
        if idx != 1:  # Not proxy2
            assert provider.get_proxy_url(sticky_session_id=session_id) == "http://proxy2"
            found_different_idx = True
            break
    assert found_different_idx, "Failed to find a session ID that hashes to index != 1"
    
    # Now force self.proxy_urls_active to be empty. It should fallback to the hashed proxy from proxy_urls_all
    provider.proxy_urls_active = []
    found_fallback = False
    for i in range(100):
        session_id = f"session_{i}"
        idx = hash(session_id) % len(proxies)
        if idx == 0:  # proxy1
            assert provider.get_proxy_url(sticky_session_id=session_id) == "http://proxy1"
            found_fallback = True
            break
    assert found_fallback


async def test_static_pool_provider_ping_proxy():
    provider = StaticPoolProxyProvider(["http://proxy1"])
    
    # 1. Success case (204 status)
    with patch("infrastructure.network.proxy.static_pool_provider.AsyncSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_session.get.return_value = mock_response
        
        result = await provider._ping_proxy("http://proxy1")
        assert result is True
        
    # 2. Failure case (exception)
    with patch("infrastructure.network.proxy.static_pool_provider.AsyncSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_session.get.side_effect = Exception("Connection error")
        
        result = await provider._ping_proxy("http://proxy1")
        assert result is False


async def test_static_pool_provider_health_check_all_failed():
    proxies = ["http://proxy1", "http://proxy2"]
    provider = StaticPoolProxyProvider(proxies, check_interval=0.05, idle_threshold=10)
    
    async def mock_ping(url):
        return False  # All fail!
        
    try:
        with patch.object(provider, "_ping_proxy", side_effect=mock_ping):
            provider.get_proxy_url()
            await asyncio.sleep(0.15)
            # Should fallback to full list
            assert provider.proxy_urls_active == proxies
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass


async def test_base_proxy_provider():
    from infrastructure.network.proxy.proxy_provider import BaseProxyProvider
    
    class DummyProxyProvider(BaseProxyProvider):
        def get_proxy_url(self, sticky_session_id: str = None) -> str | None:
            return super().get_proxy_url(sticky_session_id)
            
    dummy = DummyProxyProvider()
    assert dummy.get_proxy_url() is None
