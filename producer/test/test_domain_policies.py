import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from httpx import AsyncClient
from scraping.services.domain_policy_service import DomainPolicyService
from infrastructure.database.domain_repository import (
    DomainPolicyRepositoryInterface,
    PostgresDomainPolicyRepository,
)
from shared.models.domain_policy import (
    DomainPolicy,
    DomainPolicyCreate,
    DomainPolicyUpdate,
)
from scraping.models import BulkTaskRequest, TaskModel


@pytest.mark.asyncio
async def test_domain_policy_service_hierarchical_resolution():
    service = DomainPolicyService()
    
    # 1. Exact match
    policy_exact = service.get_policy("https://data.sec.gov/api/xbrl/companyfacts")
    assert policy_exact.domain == "data.sec.gov"
    assert policy_exact.hard_rate_limit == 10.0
    assert policy_exact.default_use_proxy is False

    # 2. Subdomain fallback: test.sec.gov -> sec.gov
    policy_sub = service.get_policy("https://test.sec.gov/filings")
    assert policy_sub.domain == "sec.gov"
    assert policy_sub.default_use_proxy is False
    assert policy_sub.hard_rate_limit == 10.0

    # 3. Default wildcard fallback
    policy_random = service.get_policy("https://unknown-shop-123.com/products")
    assert policy_random.domain == "default"
    assert policy_random.hard_rate_limit is None
    assert policy_random.default_use_proxy is True


@pytest.mark.asyncio
async def test_domain_policy_service_crud():
    service = DomainPolicyService()
    
    # Upsert new domain
    new_policy = await service.upsert_policy(
        "custom-portal.org",
        DomainPolicyCreate(
            domain="custom-portal.org",
            hard_rate_limit=5.0,
            default_rate_limit=4.0,
            default_use_proxy=False,
            default_respect_robots_txt=True,
            description="Custom Portal",
        ),
    )
    assert new_policy.domain == "custom-portal.org"
    assert service.get_policy("https://custom-portal.org/test").domain == "custom-portal.org"

    # Update existing domain
    updated = await service.upsert_policy(
        "custom-portal.org",
        DomainPolicyUpdate(default_rate_limit=2.0),
    )
    assert updated.default_rate_limit == 2.0

    # Delete custom domain
    assert await service.delete_policy("custom-portal.org") is True
    assert service.get_policy_by_name("custom-portal.org") is None

    # Deleting 'default' must raise ValueError
    with pytest.raises(ValueError):
        await service.delete_policy("default")


@pytest.mark.asyncio
async def test_domain_policy_service_postgres_sync():
    # Mock Repository
    mock_repo = MagicMock(spec=DomainPolicyRepositoryInterface)
    mock_repo.list_all = AsyncMock(
        return_value=[
            DomainPolicy(
                domain="elconfidencial.com",
                hard_rate_limit=5.0,
                default_rate_limit=3.0,
                default_use_proxy=True,
                default_respect_robots_txt=True,
            )
        ]
    )
    mock_repo.upsert = AsyncMock(side_effect=lambda p: p)
    mock_repo.delete = AsyncMock(return_value=True)

    service = DomainPolicyService(repository=mock_repo)
    await service.sync_from_db()

    # Verify that elconfidencial.com was loaded into RAM L1 cache
    policy = service.get_policy("https://www.elconfidencial.com/espana")
    assert policy.domain == "elconfidencial.com"
    assert policy.hard_rate_limit == 5.0

    # Upsert with repo
    created = await service.upsert_policy(
        "boe.es",
        DomainPolicyCreate(
            domain="boe.es",
            hard_rate_limit=2.0,
            default_rate_limit=1.0,
            default_use_proxy=False,
            default_respect_robots_txt=True,
        ),
    )
    assert created.domain == "boe.es"
    mock_repo.upsert.assert_awaited_once()

    # Delete with repo
    deleted = await service.delete_policy("boe.es")
    assert deleted is True
    mock_repo.delete.assert_awaited_once_with("boe.es")


@pytest.mark.asyncio
async def test_domain_endpoints_api(async_client: AsyncClient):
    # 1. GET /v1/domains
    resp = await async_client.get("/v1/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    domains = [p["domain"] for p in data["policies"]]
    assert "sec.gov" in domains
    assert "default" in domains

    # 2. GET /v1/domains/sec.gov
    resp_sec = await async_client.get("/v1/domains/sec.gov")
    assert resp_sec.status_code == 200
    sec_data = resp_sec.json()
    assert sec_data["domain"] == "sec.gov"
    assert sec_data["default_use_proxy"] is False
    assert sec_data["hard_rate_limit"] == 10.0

    # 3. GET /v1/domains/non-existent
    resp_404 = await async_client.get("/v1/domains/non-existent.xyz")
    assert resp_404.status_code == 404

    # 4. PUT /v1/domains/temp-test.com
    put_resp = await async_client.put(
        "/v1/domains/temp-test.com",
        json={
            "domain": "temp-test.com",
            "hard_rate_limit": 15.0,
            "default_rate_limit": 10.0,
            "default_use_proxy": False,
            "default_respect_robots_txt": True,
            "description": "Temp Domain",
        },
    )
    assert put_resp.status_code == 201
    assert put_resp.json()["domain"] == "temp-test.com"

    # 5. DELETE /v1/domains/temp-test.com
    del_resp = await async_client.delete("/v1/domains/temp-test.com")
    assert del_resp.status_code == 200

    # 6. DELETE /v1/domains/default -> 400 Bad Request
    del_default = await async_client.delete("/v1/domains/default")
    assert del_default.status_code == 400


@pytest.mark.asyncio
async def test_task_enrichment_with_domain_governance(sqs_mock):
    from dependencies.dependencies import get_task_producer, get_task_producer_dynamic, get_domain_policy_service
    from scraping.services.scraping_service import ScrapingOrchestrator

    orchestrator = ScrapingOrchestrator(
        adapter_static=get_task_producer(),
        adapter_dynamic=get_task_producer_dynamic(),
        domain_policy_service=get_domain_policy_service(),
    )

    request = BulkTaskRequest(
        job_id="job-sec-test",
        tasks=[
            TaskModel(
                url="https://data.sec.gov/submissions/CIK0000320193.json",
                parser_type="static_css",
                parser_config={"selectors": {"title": "h1"}},
                headers={"User-Agent": "TestCompany research@test.com"},
            ),
            TaskModel(
                url="https://generic-store.com/item/123",
                parser_type="static_css",
                parser_config={"selectors": {"title": "h1"}},
                respect_robots_txt=False,
            ),
        ],
    )

    # Validate mapping logic
    sec_task = orchestrator._map_to_task("batch-1", request.tasks[0], request)
    assert sec_task.use_proxy is False  # SEC has default_use_proxy = False
    # User-Agent from task overrides default, while default headers (Accept-Encoding) are preserved
    assert sec_task.headers["User-Agent"] == "TestCompany research@test.com"
    assert sec_task.headers["Accept-Encoding"] == "gzip, deflate"
    assert sec_task.respect_robots_txt is True  # Default for SEC is True
    assert sec_task.rate_limit_per_second == 8.0

    generic_task = orchestrator._map_to_task("batch-2", request.tasks[1], request)
    assert generic_task.use_proxy is True  # Default policy has default_use_proxy = True
    assert generic_task.headers is None  # Neither task nor default domain policy specifies headers
    assert generic_task.respect_robots_txt is False  # Explicit override from task
    assert generic_task.rate_limit_per_second is None  # Unlimited
