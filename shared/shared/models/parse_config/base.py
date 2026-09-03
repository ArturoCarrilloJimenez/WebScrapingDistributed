from typing import Optional, Union, Any
from pydantic import BaseModel, ConfigDict, Field


class FieldSpec(BaseModel):
    """Especificación detallada para la extracción de un campo individual."""
    selector: str = Field(..., description="Selector CSS o XPath del campo")
    attribute: Optional[str] = Field(
        default=None,
        description="Atributo HTML a extraer (ej: 'href', 'src', 'data-id'). Si es None, extrae texto."
    )
    default: Optional[Any] = Field(
        default=None,
        description="Valor por defecto si el elemento no existe en el DOM"
    )
    multiple: bool = Field(
        default=False,
        description="Si es True, extrae una lista con todos los coincidentes dentro del elemento"
    )

    model_config = ConfigDict(frozen=True)


FieldDefinition = Union[str, FieldSpec]


class BaseParserConfig(BaseModel):
    """
    Clase base para todas las configuraciones de parsers.
    Permite asegurar una interfaz común.
    """
    model_config = ConfigDict(
        extra='forbid',  # No permitimos basura extra en la config
        frozen=True     # Inmutable para mayor seguridad en procesos async
    )
