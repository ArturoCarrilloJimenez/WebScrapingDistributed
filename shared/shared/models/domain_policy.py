from datetime import datetime, timezone
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, ConfigDict, field_validator



class DomainPolicyBase(BaseModel):
    """Modelo base para la configuracion de politicas de un dominio."""
    domain: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Nombre de dominio o FQDN (ej: sec.gov, data.sec.gov, default)",
    )
    hard_rate_limit: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1000.0,
        description="Limite estricto e infranqueable de peticiones por segundo (null para sin limite)",
    )
    default_rate_limit: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1000.0,
        description="Tasa de peticiones por segundo por defecto para tareas (null para sin limite)",
    )
    default_use_proxy: bool = Field(
        default=True,
        description="Indica si por defecto se deben enrutar las peticiones por proxy comercial",
    )
    default_respect_robots_txt: bool = Field(
        default=True,
        description="Indica si por defecto se debe consultar y respetar el robots.txt",
    )
    default_headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Cabeceras HTTP por defecto para este dominio (ej: User-Agent oficial)",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Descripcion o notas del dominio (ej: APIs publicas, proteccion WAF)",
    )

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, v: str) -> str:
        """Normaliza el dominio a minusculas y elimina espacios/puertos si vinieran."""
        clean = v.strip().lower()
        if ":" in clean:
            clean = clean.split(":")[0]
        return clean

    @field_validator("default_rate_limit")
    @classmethod
    def validate_rate_limits(cls, v: Optional[float], info) -> Optional[float]:
        """Garantiza que default_rate_limit no exceda hard_rate_limit si este ultimo esta presente."""
        if v is None:
            return None
        hard_limit = info.data.get("hard_rate_limit")
        if hard_limit is not None and v > hard_limit:
            return hard_limit
        return v



class DomainPolicyCreate(DomainPolicyBase):
    """Esquema para la creacion o registro de una nueva politica de dominio."""
    pass


class DomainPolicyUpdate(BaseModel):
    """Esquema para la actualizacion parcial de una politica de dominio existente."""
    hard_rate_limit: Optional[float] = Field(
        default=None, gt=0.0, le=100.0, description="Nuevo limite estricto"
    )
    default_rate_limit: Optional[float] = Field(
        default=None, gt=0.0, le=100.0, description="Nueva tasa por defecto"
    )
    default_use_proxy: Optional[bool] = Field(
        default=None, description="Actualizar uso de proxy"
    )
    default_respect_robots_txt: Optional[bool] = Field(
        default=None, description="Actualizar respeto a robots.txt"
    )
    default_headers: Optional[Dict[str, str]] = Field(
        default=None, description="Actualizar cabeceras por defecto"
    )
    description: Optional[str] = Field(
        default=None, max_length=500, description="Nueva descripcion"
    )



class DomainPolicy(DomainPolicyBase):
    """Modelo completo de la politica de dominio con metadatos de auditoria."""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Fecha y hora UTC de creacion",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Fecha y hora UTC de ultima modificacion",
    )

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class DomainPolicyListResponse(BaseModel):
    """Respuesta paginada o listado de politicas de dominio registradas."""
    total: int = Field(..., description="Total de dominios configurados")
    policies: List[DomainPolicy] = Field(
        ..., description="Lista de politicas de dominio"
    )
