import pytest
from shared.models import RobotsStatus
from infrastructure.cache import MemoryCacheAdapter
from scraping.security.robots_service import RobotsCacheService


@pytest.mark.asyncio
async def test_robots_service_bypass():
    service = RobotsCacheService(cache_adapter=MemoryCacheAdapter())
    allowed, status = await service.is_allowed(
        "https://secret.portal.gov/private",
        user_agent="SecBot",
        respect_robots_txt=False,
    )
    assert allowed is True
    assert status == RobotsStatus.BYPASS
    await service.close()


@pytest.mark.asyncio
async def test_robots_service_parsing_logic():
    cache = MemoryCacheAdapter()
    robots_txt_sample = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /private/\n"
        "Allow: /public/\n"
        "\n"
        "User-agent: BadBot\n"
        "Disallow: /\n"
    )
    await cache.set("robots_txt:test-domain.com", robots_txt_sample)

    service = RobotsCacheService(cache_adapter=cache)

    # 1. Allowed path for normal bot
    allowed, status = await service.is_allowed(
        "https://test-domain.com/public/items",
        user_agent="GoodBot",
        respect_robots_txt=True,
    )
    assert allowed is True
    assert status == RobotsStatus.ALLOWED

    # 2. Disallowed path for normal bot
    allowed_admin, status_admin = await service.is_allowed(
        "https://test-domain.com/admin/dashboard",
        user_agent="GoodBot",
        respect_robots_txt=True,
    )
    assert allowed_admin is False
    assert status_admin == RobotsStatus.DISALLOWED

    # 3. Disallowed all for BadBot
    allowed_bad, status_bad = await service.is_allowed(
        "https://test-domain.com/public/items",
        user_agent="BadBot/1.0",
        respect_robots_txt=True,
    )
    assert allowed_bad is False
    assert status_bad == RobotsStatus.DISALLOWED

    await service.close()


@pytest.mark.asyncio
async def test_robots_service_404_not_found():
    cache = MemoryCacheAdapter()
    await cache.set("robots_txt:notfound-domain.com", "__404_NOT_FOUND__")
    service = RobotsCacheService(cache_adapter=cache)

    allowed, status = await service.is_allowed(
        "https://notfound-domain.com/any/path",
        user_agent="GoodBot",
        respect_robots_txt=True,
    )
    assert allowed is True
    assert status == RobotsStatus.NOT_FOUND
    await service.close()
