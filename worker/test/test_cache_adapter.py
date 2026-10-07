import asyncio
from unittest.mock import AsyncMock
import pytest
from infrastructure.cache import MemoryCacheAdapter, RedisCacheAdapter


@pytest.mark.asyncio
async def test_memory_cache_adapter_crud():
    adapter = MemoryCacheAdapter()

    # Set & Get
    await adapter.set("key1", "val1")
    assert await adapter.get("key1") == "val1"
    assert await adapter.get("nonexistent") is None

    # Delete
    assert await adapter.delete("key1") is True
    assert await adapter.get("key1") is None

    # TTL Expiration in set
    await adapter.set("temp", "exp", ttl_seconds=1)
    assert await adapter.get("temp") == "exp"
    await asyncio.sleep(1.05)
    assert await adapter.get("temp") is None

    # Incr
    assert await adapter.incr("counter") == 1
    assert await adapter.incr("counter") == 2
    assert await adapter.delete("counter") is True

    await adapter.close()


@pytest.mark.asyncio
async def test_redis_cache_adapter_with_mock():
    mock_client = AsyncMock()
    mock_client.get.return_value = "redis_value"
    mock_client.incr.return_value = 1
    mock_client.delete.return_value = 1

    adapter = RedisCacheAdapter(host="localhost", port=6379)
    adapter._client = mock_client

    # Get
    assert await adapter.get("test_key") == "redis_value"
    mock_client.get.assert_awaited_once_with("test_key")

    # Set with TTL
    await adapter.set("test_key", "test_val", ttl_seconds=60)
    mock_client.set.assert_awaited_once_with("test_key", "test_val", ex=60)

    # Incr
    result = await adapter.incr("test_counter")
    assert result == 1
    mock_client.incr.assert_awaited_once_with("test_counter")

    # Delete
    assert await adapter.delete("test_key") is True
    mock_client.delete.assert_awaited_once_with("test_key")

    # Close
    await adapter.close()
    mock_client.aclose.assert_awaited_once()
