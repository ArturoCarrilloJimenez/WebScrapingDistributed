import re
from typing import Dict, List, Optional
from pydantic import Field, HttpUrl, field_validator, model_validator
from .base import BaseParserConfig, FieldDefinition


class FollowLinkRule(BaseParserConfig):
    """Regla declarativa para descubrir y extraer sub-documentos desde HTML en la misma tarea."""

    name: str = Field(
        ...,
        min_length=1,
        description="Nombre de la clave en el JSON resultante donde se guardarán los sub-elementos (ej: 'exhibits')"
    )
    source_url: Optional[HttpUrl] = Field(
        default=None,
        description="URL opcional donde buscar los enlaces (ej: index_url). Si es None, busca en la URL principal"
    )
    row_selector: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Selector CSS opcional de cada fila o bloque contenedor (ej: 'table.tableFile tr')"
    )
    link_selector: str = Field(
        ...,
        min_length=1,
        description="Selector CSS del elemento que contiene el enlace destino (ej: 'td:nth-of-type(3) a')"
    )
    link_attribute: str = Field(
        default="href",
        min_length=1,
        description="Atributo HTML del que extraer la URL (por defecto 'href')"
    )
    filter_selector: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Selector CSS opcional cuyo texto debe coincidir con 'filter_regex' (ej: 'td:nth-of-type(4)')"
    )
    filter_regex: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Expresión regular que debe cumplir el texto del filtro (ej: '(?i)^EX-99')"
    )
    url_strip_regex: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Expresión regular opcional a eliminar de la URL extraída antes de resolverla (ej: '^/ix\\?doc=')",
    )
    fallback_source_urls: Optional[List[HttpUrl]] = Field(
        default=None,
        description="Lista ordenada de URLs índice alternativas si 'source_url' devuelve 404 (ej: '-index.html')",
    )
    metadata_selectors: Optional[Dict[str, str]] = Field(
        default=None,
        description="Mapa opcional {clave: selector_css} evaluado dentro de cada fila ('row_selector') para capturar metadatos",
    )
    exclude_selectors: Optional[List[str]] = Field(
        default=None,
        description="Selectores CSS o etiquetas a eliminar del sub-documento antes de extraer (ej: ['ix:header', 'script', 'style'])",
    )
    max_links: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Límite máximo de sub-enlaces a descargar por regla",
    )
    container: Optional[str] = Field(
        default=None,
        description="Selector CSS opcional del contenedor padre dentro de cada sub-documento",
    )
    selectors: Dict[str, FieldDefinition] = Field(
        ...,
        min_length=1,
        description="Selectores CSS a extraer de cada sub-documento descargado",
    )

    @field_validator("filter_regex", "url_strip_regex")
    @classmethod
    def validate_regex(cls, v: Optional[str]) -> Optional[str]:
        """Valida que las expresiones regulares declaradas compilen correctamente."""
        if v is not None:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"Expresión regular inválida '{v}': {exc}") from exc
        return v

    @field_validator("exclude_selectors")
    @classmethod
    def validate_exclude_selectors(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Valida que los selectores de exclusión no estén vacíos."""
        if v is not None and any(not s or not s.strip() for s in v):
            raise ValueError("'exclude_selectors' no puede contener cadenas vacías.")
        return v

    @field_validator("metadata_selectors")
    @classmethod
    def validate_metadata_selectors(
        cls, v: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """Valida que las claves y selectores de metadata_selectors no estén vacíos."""
        if v is not None:
            for key, sel in v.items():
                if not key or not key.strip() or not sel or not sel.strip():
                    raise ValueError(
                        "'metadata_selectors' no puede contener claves ni selectores vacíos."
                    )
        return v

    @model_validator(mode="after")
    def validate_fallback_source_urls(self) -> "FollowLinkRule":
        """Valida que fallback_source_urls solo se use cuando existe source_url."""
        if self.fallback_source_urls and self.source_url is None:
            raise ValueError(
                "'fallback_source_urls' requiere definir 'source_url'."
            )
        return self


class Config(BaseParserConfig):
    """
    Configuración universal para el motor 'static_css'.
    Soporta extracción de entidad única, colecciones de ítems y subconsultas declarativas (follow_links).
    """
    container: Optional[str] = Field(
        default=None,
        description="Selector CSS del contenedor padre para colecciones repetitivas (ej: '.product-card', 'tr.item')",
    )
    selectors: Dict[str, FieldDefinition] = Field(
        ...,
        min_length=1,
        description="Mapeo de nombre de campo a selector CSS o FieldSpec. Ej: {'precio': '.price-tag', 'link': {'selector': 'a', 'attribute': 'href'}}",
    )
    exclude_selectors: Optional[List[str]] = Field(
        default=None,
        description="Selectores CSS o etiquetas a eliminar del DOM principal antes de extraer (ej: ['ix:header', 'script', 'style'])",
    )
    follow_links: Optional[List[FollowLinkRule]] = Field(
        default=None,
        description="Reglas opcionales para descubrir y extraer sub-enlaces dentro de la misma tarea",
    )

    @field_validator("exclude_selectors")
    @classmethod
    def validate_root_exclude_selectors(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Valida que los selectores de exclusión en raíz no estén vacíos."""
        if v is not None and any(not s or not s.strip() for s in v):
            raise ValueError("'exclude_selectors' no puede contener cadenas vacías.")
        return v

    @model_validator(mode="after")
    def validate_follow_links_layout(self) -> "Config":
        """Valida compatibilidad de nombres y estructura cuando se configura follow_links."""
        if not self.follow_links:
            return self

        if self.container is not None:
            raise ValueError(
                "'follow_links' no puede combinarse con 'container' en el nivel raíz."
            )

        seen_names: set[str] = set()
        for rule in self.follow_links:
            if rule.name in self.selectors:
                raise ValueError(
                    f"El nombre de regla '{rule.name}' en 'follow_links' colisiona con una clave de 'selectors'."
                )
            if rule.name in seen_names:
                raise ValueError(
                    f"Nombre duplicado '{rule.name}' en 'follow_links'."
                )
            seen_names.add(rule.name)

        return self
