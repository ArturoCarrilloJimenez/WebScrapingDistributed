import time
import pytest
from infrastructure.cache import MemoryCacheAdapter
from scraping.security.rate_limiter import DomainRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_unlimited():
    limiter = DomainRateLimiter(cache_adapter=MemoryCacheAdapter())
    start = time.time()
    # Sin límite debe retornar inmediatamente
    await limiter.acquire("https://shop.com/item/1", rate_limit_per_second=None)
    await limiter.acquire("https://shop.com/item/2", rate_limit_per_second=None)
    duration = time.time() - start
    assert duration < 0.1
    await limiter.close()


@pytest.mark.asyncio
async def test_rate_limiter_local_fallback():
    limiter = DomainRateLimiter(cache_adapter=MemoryCacheAdapter())
    domain = "https://sec.gov/edgar"

    # Enviar 2 peticiones con límite 2 req/s
    await limiter.acquire(domain, rate_limit_per_second=2.0)
    await limiter.acquire(domain, rate_limit_per_second=2.0)

    # La 3ª petición en el mismo segundo debe pausarse hasta el siguiente segundo
    start = time.time()
    await limiter.acquire(domain, rate_limit_per_second=2.0)
    duration = time.time() - start
    assert duration >= 0.05  # Hubo un delay para respetar el segundo
    await limiter.close()


@pytest.mark.asyncio
async def test_rate_limiter_domain_normalization():
    limiter = DomainRateLimiter(cache_adapter=MemoryCacheAdapter())
    assert limiter.extract_domain("https://data.sec.gov:443/filings") == "data.sec.gov"
    assert limiter.extract_domain("http://sec.gov/test") == "sec.gov"
    assert limiter.extract_domain("sec.gov/api") == "sec.gov"
    await limiter.close()
