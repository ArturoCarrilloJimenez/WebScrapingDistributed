from pydantic import BaseModel, ConfigDict

class BaseParserConfig(BaseModel):
    """
    Clase base para todas las configuraciones de parsers.
    Permite asegurar una interfaz común.
    """
    model_config = ConfigDict(
        extra='forbid', # No permitimos basura extra en la config
        frozen=True     # Inmutable para mayor seguridad en procesos async
    )