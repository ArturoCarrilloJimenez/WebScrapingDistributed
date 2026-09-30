import logging
from typing import Optional
from urllib.parse import urlparse
from datetime import datetime, timezone

from shared import (
    DomainPolicy,
    DomainPolicyCreate,
    DomainPolicyUpdate,
    load_seed_domain_policies,
)
from infrastructure.database.domain_repository import DomainPolicyRepositoryInterface

logger = logging.getLogger(__name__)


class DomainPolicyService:
    """
    Servicio centralizado de gobernanza de politicas de dominio.
    Mantiene una cache en memoria RAM (L1) para resolucion ultrarrapida (0.001 ms)
    y conexion asincrona para persistencia en base de datos PostgreSQL.
    """

    def __init__(
        self,
        initial_policies: Optional[list[DomainPolicy]] = None,
        repository: Optional[DomainPolicyRepositoryInterface] = None,
    ):
        self._policies: dict[str, DomainPolicy] = {}
        self._repository = repository
        self._load_initial_policies(initial_policies)

    def set_repository(self, repository: Optional[DomainPolicyRepositoryInterface]) -> None:
        """Asigna o actualiza el repositorio de persistencia."""
        self._repository = repository

    def _load_initial_policies(
        self, custom_policies: Optional[list[DomainPolicy]] = None
    ) -> None:
        """Carga politicas iniciales desde la libreria shared o lista personalizada."""
        policies_to_load = custom_policies if custom_policies is not None else load_seed_domain_policies()
        for policy in policies_to_load:
            self._policies[policy.domain.lower()] = policy
        logger.info(
            "DomainPolicyService inicializado con %d politicas de dominio en memoria",
            len(self._policies),
        )

    async def sync_from_db(self) -> None:
        """
        Sincroniza la cache L1 en RAM con los registros almacenados en PostgreSQL.
        Sobreescribe o anade las politicas persistidas sobre las semillas JSON.
        """
        if not self._repository:
            return

        try:
            db_policies = await self._repository.list_all()
            for policy in db_policies:
                self._policies[policy.domain.lower()] = policy
            logger.info(
                "Cache L1 de DomainPolicyService sincronizada con PostgreSQL (%d politicas en total)",
                len(self._policies),
            )
        except Exception as e:
            logger.warning(
                "Error durante la sincronizacion inicial con PostgreSQL: %s. Manteniendo politicas en memoria.",
                e,
            )

    def extract_domain_from_url(self, url_or_host: str) -> str:
        """Normaliza una URL o string para extraer unicamente el host en minusculas."""
        clean = str(url_or_host).strip().lower()
        if clean.startswith(("http://", "https://")):
            parsed = urlparse(clean)
            host = parsed.netloc or parsed.path
        else:
            host = clean.split("/")[0]
        # Remover puerto si existiera
        if ":" in host:
            host = host.split(":")[0]
        return host

    def get_policy(self, url_or_domain: str) -> DomainPolicy:
        """
        Resuelve la politica aplicable en tiempo real (0.001 ms) desde la cache L1 en RAM
        mediante coincidencia jerarquica:
        1. Host exacto (ej: api.data.sec.gov)
        2. Subdominios padres (ej: data.sec.gov -> sec.gov)
        3. Politica comodin 'default'
        """
        host = self.extract_domain_from_url(url_or_domain)
        parts = host.split(".")

        # Probar desde el FQDN exacto hacia dominios padres
        for i in range(len(parts) - 1):
            subdomain_candidate = ".".join(parts[i:])
            if subdomain_candidate in self._policies:
                return self._policies[subdomain_candidate]

        # Si no coincide ninguno, retornar la politica 'default'
        if "default" in self._policies:
            return self._policies["default"]

        return DomainPolicy(
            domain="default",
            hard_rate_limit=None,
            default_rate_limit=None,
            default_use_proxy=True,
            default_respect_robots_txt=True,
        )

    def list_policies(self) -> list[DomainPolicy]:
        """Devuelve todas las politicas de dominio registradas en la cache L1."""
        return list(self._policies.values())

    def get_policy_by_name(self, domain: str) -> Optional[DomainPolicy]:
        """Obtiene la politica exacta de un dominio si esta registrada."""
        clean_domain = domain.strip().lower()
        return self._policies.get(clean_domain)

    async def upsert_policy(
        self, domain: str, policy_data: DomainPolicyCreate | DomainPolicyUpdate
    ) -> DomainPolicy:
        """Crea o actualiza una politica de dominio en caliente (RAM + PostgreSQL)."""
        clean_domain = domain.strip().lower()
        existing = self._policies.get(clean_domain)

        now = datetime.now(timezone.utc)
        if existing:
            update_dict = policy_data.model_dump(exclude_unset=True)
            for k, v in update_dict.items():
                if v is not None:
                    setattr(existing, k, v)
            existing.updated_at = now
            target_policy = existing
        else:
            if isinstance(policy_data, DomainPolicyCreate):
                data = policy_data.model_dump()
                data["domain"] = clean_domain
            else:
                data = policy_data.model_dump(exclude_unset=True)
                data["domain"] = clean_domain
            target_policy = DomainPolicy(**data)

        # Actualizar RAM inmediatamente (L1)
        self._policies[clean_domain] = target_policy

        # Persistir en PostgreSQL de forma asincrona
        if self._repository:
            try:
                saved = await self._repository.upsert(target_policy)
                self._policies[clean_domain] = saved
                return saved
            except Exception as e:
                logger.error(
                    "Error al persistir politica '%s' en PostgreSQL: %s. Operando en memoria.",
                    clean_domain,
                    e,
                )

        return target_policy

    async def delete_policy(self, domain: str) -> bool:
        """Elimina una politica de dominio de la cache L1 y de PostgreSQL."""
        clean_domain = domain.strip().lower()
        if clean_domain == "default":
            raise ValueError(
                "No se puede eliminar la politica por defecto 'default'"
            )

        found = clean_domain in self._policies
        if found:
            del self._policies[clean_domain]

        if self._repository:
            try:
                await self._repository.delete(clean_domain)
            except Exception as e:
                logger.error(
                    "Error al eliminar politica '%s' de PostgreSQL: %s",
                    clean_domain,
                    e,
                )

        return found
