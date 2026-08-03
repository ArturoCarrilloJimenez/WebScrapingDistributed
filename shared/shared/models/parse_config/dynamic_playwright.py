from typing import Dict, List, Optional
from pydantic import Field, PositiveInt
from .base import BaseParserConfig

class Config(BaseParserConfig):
    """
    Configuración para el motor 'dynamic_playwright'.
    Espera un diccionario donde la clave es el nombre del campo
    y el valor es el selector CSS.
    """
    
    # Selectores CSS/XPath
    selectors: Dict[str, str] = Field(
        ...,
        min_length=1,
        description="Mapeo de campo a selector CSS/XPath. Ej: {'precio': '.price-tag'}"
    )
    
    # Parámetros de Espera y Carga
    timeout_ms: PositiveInt = Field(
        default=30000,
        description="Límite de tiempo en milisegundos para la carga"
    )
    wait_until: str = Field(
        default="domcontentloaded",
        pattern="^(domcontentloaded|networkidle|load|commit)$",
        description="Estado del ciclo de vida de la página a esperar"
    )
    wait_for_selector: Optional[str] = Field(
        default=None,
        description="Selector específico que debe aparecer en el DOM antes de extraer"
    )
    fixed_sleep_s: Optional[float] = Field(
        default=0.0,
        ge=0.0,
        description="Retardo de tiempo fijo opcional (sleep) en segundos tras la carga"
    )
    
    # Interacciones Básicas
    scroll_to_bottom: bool = Field(
        default=False,
        description="Si es True, realiza scroll al final para activar cargas perezosas"
    )
    click_selectors: Optional[List[str]] = Field(
        default=None,
        description="Lista de selectores CSS sobre los que hacer click antes de extraer"
    )