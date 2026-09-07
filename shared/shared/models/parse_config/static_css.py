from typing import Dict, Optional
from pydantic import Field
from .base import BaseParserConfig, FieldDefinition


class Config(BaseParserConfig):
    """
    Configuración universal para el motor 'static_css'.
    Soporta extracción de entidad única y colecciones de ítems.
    """
    container: Optional[str] = Field(
        default=None,
        description="Selector CSS del contenedor padre para colecciones repetitivas (ej: '.product-card', 'tr.item')"
    )
    selectors: Dict[str, FieldDefinition] = Field(
        ...,
        min_length=1,
        description="Mapeo de nombre de campo a selector CSS o FieldSpec. Ej: {'precio': '.price-tag', 'link': {'selector': 'a', 'attribute': 'href'}}"
    )
