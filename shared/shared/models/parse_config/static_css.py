from typing import Dict
from pydantic import Field
from .base import BaseParserConfig

class Config(BaseParserConfig):
    """
    Configuración para el motor 'static_css'.
    Espera un diccionario donde la clave es el nombre del campo 
    y el valor es el selector CSS.
    """
    selectors: Dict[str, str] = Field(
        ..., 
        min_length=1,
        description="Mapeo de nombre de campo a selector CSS. Ej: {'precio': '.price-tag'}"
    )
    
    wait_for_selector: str | None = Field(
        None, 
        description="Opcional: Esperar a que este selector aparezca antes de extraer."
    )