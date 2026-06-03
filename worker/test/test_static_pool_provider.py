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
            assert provider._is_sleeping is False
    finally:
        if provider._monitor_task:
            provider._monitor_task.cancel()
            try:
                await provider._monitor_task
            except asyncio.CancelledError:
                pass
