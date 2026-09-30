import json
import logging
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime, timezone
import asyncpg

from shared import DomainPolicy

logger = logging.getLogger(__name__)


class DomainPolicyRepositoryInterface(ABC):
    """Interfaz del repositorio de persistencia para politicas de dominio."""

    @abstractmethod
    async def list_all(self) -> list[DomainPolicy]:
        """Obtiene todas las politicas registradas en la base de datos."""
        pass

    @abstractmethod
    async def get_by_domain(self, domain: str) -> Optional[DomainPolicy]:
        """Obtiene una politica por su nombre de dominio."""
        pass

    @abstractmethod
    async def upsert(self, policy: DomainPolicy) -> DomainPolicy:
        """Inserta o actualiza una politica de dominio."""
        pass

    @abstractmethod
    async def delete(self, domain: str) -> bool:
        """Elimina una politica por su nombre de dominio."""
        pass


class PostgresDomainPolicyRepository(DomainPolicyRepositoryInterface):
    """
    Implementacion asincrona del repositorio de politicas de dominio usando asyncpg.
    """

    def __init__(self, pool: Optional[asyncpg.Pool]):
        self._pool = pool

    def set_pool(self, pool: Optional[asyncpg.Pool]) -> None:
        self._pool = pool

    async def list_all(self) -> list[DomainPolicy]:
        """Devuelve todas las politicas almacenadas en PostgreSQL."""
        if not self._pool:
            return []

        query = """
        SELECT domain, hard_rate_limit, default_rate_limit, default_use_proxy,
               default_respect_robots_txt, default_headers, created_at, updated_at
        FROM domain_policies;
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query)
                policies = []
                for r in rows:
                    headers = json.loads(r["default_headers"]) if isinstance(
                        r["default_headers"], str) else r["default_headers"]
                    policies.append(
                        DomainPolicy(
                            domain=r["domain"],
                            hard_rate_limit=r["hard_rate_limit"],
                            default_rate_limit=r["default_rate_limit"],
                            default_use_proxy=r["default_use_proxy"],
                            default_respect_robots_txt=r["default_respect_robots_txt"],
                            default_headers=headers,
                            created_at=r["created_at"],
                            updated_at=r["updated_at"],
                        )
                    )
                return policies
        except Exception as e:
            logger.error(
                "Error al listar politicas de dominio desde PostgreSQL: %s", e)
            return []

    async def get_by_domain(self, domain: str) -> Optional[DomainPolicy]:
        """Obtiene una politica especifica por dominio."""
        if not self._pool:
            return None

        query = """
        SELECT domain, hard_rate_limit, default_rate_limit, default_use_proxy,
               default_respect_robots_txt, default_headers, created_at, updated_at
        FROM domain_policies
        WHERE domain = $1;
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, domain.strip().lower())
                if not row:
                    return None
                headers = json.loads(row["default_headers"]) if isinstance(
                    row["default_headers"], str) else row["default_headers"]
                return DomainPolicy(
                    domain=row["domain"],
                    hard_rate_limit=row["hard_rate_limit"],
                    default_rate_limit=row["default_rate_limit"],
                    default_use_proxy=row["default_use_proxy"],
                    default_respect_robots_txt=row["default_respect_robots_txt"],
                    default_headers=headers,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
        except Exception as e:
            logger.error(
                "Error al obtener politica de dominio '%s' desde PostgreSQL: %s", domain, e)
            return None

    async def upsert(self, policy: DomainPolicy) -> DomainPolicy:
        """Inserta o actualiza una politica de dominio en PostgreSQL."""
        if not self._pool:
            return policy

        query = """
        INSERT INTO domain_policies (
            domain, hard_rate_limit, default_rate_limit, default_use_proxy,
            default_respect_robots_txt, default_headers, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (domain) DO UPDATE SET
            hard_rate_limit = EXCLUDED.hard_rate_limit,
            default_rate_limit = EXCLUDED.default_rate_limit,
            default_use_proxy = EXCLUDED.default_use_proxy,
            default_respect_robots_txt = EXCLUDED.default_respect_robots_txt,
            default_headers = EXCLUDED.default_headers,
            updated_at = EXCLUDED.updated_at
        RETURNING domain, hard_rate_limit, default_rate_limit, default_use_proxy,
                  default_respect_robots_txt, default_headers, created_at, updated_at;
        """
        now = datetime.now(timezone.utc)
        headers_json = json.dumps(
            policy.default_headers) if policy.default_headers else None

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    query,
                    policy.domain.strip().lower(),
                    policy.hard_rate_limit,
                    policy.default_rate_limit,
                    policy.default_use_proxy,
                    policy.default_respect_robots_txt,
                    headers_json,
                    policy.created_at or now,
                    now,
                )
                headers = json.loads(row["default_headers"]) if isinstance(
                    row["default_headers"], str) else row["default_headers"]
                return DomainPolicy(
                    domain=row["domain"],
                    hard_rate_limit=row["hard_rate_limit"],
                    default_rate_limit=row["default_rate_limit"],
                    default_use_proxy=row["default_use_proxy"],
                    default_respect_robots_txt=row["default_respect_robots_txt"],
                    default_headers=headers,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
        except Exception as e:
            logger.error(
                "Error al persistir politica de dominio '%s' en PostgreSQL: %s", policy.domain, e)
            return policy

    async def delete(self, domain: str) -> bool:
        """Elimina una politica de dominio de PostgreSQL."""
        if not self._pool:
            return False

        query = "DELETE FROM domain_policies WHERE domain = $1;"
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(query, domain.strip().lower())
                return "DELETE 1" in result
        except Exception as e:
            logger.error(
                "Error al eliminar politica de dominio '%s' de PostgreSQL: %s", domain, e)
            return False
