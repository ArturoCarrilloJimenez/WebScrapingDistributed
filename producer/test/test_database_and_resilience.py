import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient

from config.settings import Settings
from infrastructure.database.connection import DatabaseConnectionManager
from infrastructure.database.domain_repository import PostgresDomainPolicyRepository
from shared.models.domain_policy import DomainPolicy, DomainPolicyCreate
from scraping.services.domain_policy_service import DomainPolicyService
import main


@pytest.mark.asyncio
async def test_connection_manager_success():
    settings = Settings()
    manager = DatabaseConnectionManager(settings)

    mock_pool = MagicMock()
    with patch("asyncpg.create_pool", AsyncMock(return_value=mock_pool)):
        pool = await manager.connect(max_retries=1)
        assert pool == mock_pool
        assert manager.pool == mock_pool

        # Segunda llamada debe retornar el pool existente
        pool_again = await manager.connect(max_retries=1)
        assert pool_again == mock_pool

        # Cerrar el pool
        mock_pool.close = AsyncMock()
        await manager.close()
        mock_pool.close.assert_awaited_once()
        assert manager.pool is None

        # Cerrar cuando ya es None no debe lanzar error
        await manager.close()


@pytest.mark.asyncio
async def test_connection_manager_retries_and_failure():
    settings = Settings()
    manager = DatabaseConnectionManager(settings)

    with patch("asyncpg.create_pool", AsyncMock(side_effect=Exception("Connection refused"))):
        pool = await manager.connect(max_retries=2, retry_delay=0.01)
        assert pool is None
        assert manager.pool is None


@pytest.mark.asyncio
async def test_postgres_domain_repository_no_pool():
    repo = PostgresDomainPolicyRepository(pool=None)
    assert await repo.list_all() == []
    assert await repo.get_by_domain("example.com") is None
    
    dummy_policy = DomainPolicy(domain="test.com")
    assert await repo.upsert(dummy_policy) == dummy_policy
    assert await repo.delete("test.com") is False

    # Probar set_pool
    mock_pool = MagicMock()
    repo.set_pool(mock_pool)
    assert repo._pool == mock_pool


@pytest.mark.asyncio
async def test_postgres_domain_repository_list_all():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_acquire_ctx.__aexit__.return_value = None
    mock_pool.acquire.return_value = mock_acquire_ctx

    now = datetime.now(timezone.utc)
    mock_conn.fetch.return_value = [
        {
            "domain": "sec.gov",
            "hard_rate_limit": 10.0,
            "default_rate_limit": 8.0,
            "default_use_proxy": False,
            "default_respect_robots_txt": True,
            "default_headers": json.dumps({"User-Agent": "Bot/1.0"}),
            "created_at": now,
            "updated_at": now,
        },
        {
            "domain": "data.gov",
            "hard_rate_limit": None,
            "default_rate_limit": None,
            "default_use_proxy": True,
            "default_respect_robots_txt": True,
            "default_headers": {"User-Agent": "DictHeader/1.0"},
            "created_at": now,
            "updated_at": now,
        },
    ]

    repo = PostgresDomainPolicyRepository(pool=mock_pool)
    policies = await repo.list_all()
    assert len(policies) == 2
    assert policies[0].domain == "sec.gov"
    assert policies[0].default_headers == {"User-Agent": "Bot/1.0"}
    assert policies[1].domain == "data.gov"

    # Test excepcion en list_all
    mock_conn.fetch.side_effect = Exception("DB Query Error")
    assert await repo.list_all() == []


@pytest.mark.asyncio
async def test_postgres_domain_repository_get_by_domain():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_acquire_ctx.__aexit__.return_value = None
    mock_pool.acquire.return_value = mock_acquire_ctx

    now = datetime.now(timezone.utc)
    repo = PostgresDomainPolicyRepository(pool=mock_pool)

    # 1. Encontrado con headers string
    mock_conn.fetchrow.return_value = {
        "domain": "sec.gov",
        "hard_rate_limit": 10.0,
        "default_rate_limit": 8.0,
        "default_use_proxy": False,
        "default_respect_robots_txt": True,
        "default_headers": json.dumps({"User-Agent": "Bot/1.0"}),
        "created_at": now,
        "updated_at": now,
    }
    res = await repo.get_by_domain("SEC.GOV")
    assert res is not None
    assert res.domain == "sec.gov"

    # 2. No encontrado
    mock_conn.fetchrow.return_value = None
    assert await repo.get_by_domain("notfound.com") is None

    # 3. Excepcion
    mock_conn.fetchrow.side_effect = Exception("DB Error")
    assert await repo.get_by_domain("error.com") is None


@pytest.mark.asyncio
async def test_postgres_domain_repository_upsert_and_delete():
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__.return_value = mock_conn
    mock_acquire_ctx.__aexit__.return_value = None
    mock_pool.acquire.return_value = mock_acquire_ctx

    now = datetime.now(timezone.utc)
    repo = PostgresDomainPolicyRepository(pool=mock_pool)

    # 1. Upsert exitoso
    mock_conn.fetchrow.return_value = {
        "domain": "custom.com",
        "hard_rate_limit": 5.0,
        "default_rate_limit": 3.0,
        "default_use_proxy": True,
        "default_respect_robots_txt": True,
        "default_headers": json.dumps({"X-Test": "1"}),
        "created_at": now,
        "updated_at": now,
    }
    policy_in = DomainPolicy(
        domain="custom.com",
        hard_rate_limit=5.0,
        default_headers={"X-Test": "1"}
    )
    res_upsert = await repo.upsert(policy_in)
    assert res_upsert.domain == "custom.com"

    # 2. Upsert con error
    mock_conn.fetchrow.side_effect = Exception("DB Upsert Error")
    assert await repo.upsert(policy_in) == policy_in

    # 3. Delete exitoso
    mock_conn.execute.return_value = "DELETE 1"
    assert await repo.delete("custom.com") is True

    # 4. Delete 0 filas
    mock_conn.execute.return_value = "DELETE 0"
    assert await repo.delete("notfound.com") is False

    # 5. Delete excepcion
    mock_conn.execute.side_effect = Exception("DB Delete Error")
    assert await repo.delete("custom.com") is False


@pytest.mark.asyncio
async def test_domain_policy_service_edge_cases():
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(side_effect=Exception("DB Read Error"))
    mock_repo.upsert = AsyncMock(side_effect=Exception("DB Upsert Error"))
    mock_repo.delete = AsyncMock(side_effect=Exception("DB Delete Error"))

    service = DomainPolicyService(repository=mock_repo)

    # 1. sync_from_db maneja excepcion sin propagar
    await service.sync_from_db()

    # 2. delete de dominio inexistente retorna False
    assert await service.delete_policy("nonexistent-domain-xyz.com") is False

    # 3. upsert maneja fallo del repositorio y actualiza RAM
    created = await service.upsert_policy(
        "fallback.com",
        DomainPolicyCreate(domain="fallback.com", default_rate_limit=2.0)
    )
    assert created.domain == "fallback.com"
    assert service.get_policy("https://fallback.com").domain == "fallback.com"

    # 4. get_policy con URLs malformadas o sin netloc cae en default
    assert service.get_policy("").domain == "default"
    assert service.get_policy("not-a-valid-url").domain == "default"


@pytest.mark.asyncio
async def test_domain_controller_delete_errors(async_client: AsyncClient):
    # 1. Borrar 'default' debe retornar 400 Bad Request
    resp = await async_client.delete("/v1/domains/default")
    assert resp.status_code == 400
    assert "default" in resp.json()["detail"]

    # 2. Borrar dominio que no existe debe retornar 404 Not Found
    resp_404 = await async_client.delete("/v1/domains/non-existent-domain-404.com")
    assert resp_404.status_code == 404


@pytest.mark.asyncio
async def test_background_sync_and_lifespan():
    # 1. Test unitario de _background_db_health_and_sync
    mock_db_manager = MagicMock()
    mock_db_manager.pool = None
    mock_pool = MagicMock()
    mock_db_manager.connect = AsyncMock(return_value=mock_pool)

    mock_repo = MagicMock()
    mock_service = MagicMock()
    mock_service.sync_from_db = AsyncMock()

    with patch("main.get_db_connection_manager", return_value=mock_db_manager), \
         patch("main.get_domain_policy_repository", return_value=mock_repo), \
         patch("main.get_domain_policy_service", return_value=mock_service):

        # Ejecutamos 1 iteracion de la corrutina de fondo
        task = asyncio.create_task(main._background_db_health_and_sync(interval_seconds=0.01))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        mock_db_manager.connect.assert_awaited()
        mock_repo.set_pool.assert_called_with(mock_pool)
        mock_service.sync_from_db.assert_awaited()

    # 2. Test lifespan context manager
    mock_producer = MagicMock()
    mock_producer.close = AsyncMock()
    mock_db_mgr = MagicMock()
    mock_db_mgr.connect = AsyncMock(return_value=MagicMock())
    mock_db_mgr.close = AsyncMock()

    with patch("main.get_task_producer", return_value=mock_producer), \
         patch("main.get_db_connection_manager", return_value=mock_db_mgr), \
         patch("main.get_domain_policy_repository", return_value=mock_repo), \
         patch("main.get_domain_policy_service", return_value=mock_service), \
         patch("main._background_db_health_and_sync", AsyncMock()):

        async with main.lifespan(main.app):
            pass

        mock_producer.close.assert_awaited_once()
        mock_db_mgr.close.assert_awaited_once()
