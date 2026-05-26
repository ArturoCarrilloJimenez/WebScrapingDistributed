from pydantic import field_validator, ValidationInfo
from pydantic_core import PydanticCustomError
from .factory import ContractFactory

class ParserValidatedMixin:
    """
    Mixin refinado para validación quirúrgica.
    Se espera que el modelo que lo herede tenga 'parser_type' y 'parser_config'.
    """

    @field_validator("parser_config")
    @classmethod
    def validate_dynamic_config(cls, v: dict, info: ValidationInfo) -> dict:
        # Recuperamos 'parser_type' de los datos ya validados
        p_type = info.data.get("parser_type")
        
        # Pydantic ya suspenderá el modelo de forma nativa por falta de 'parser_type'.
        if not p_type:
            config_class = None
            
        try:
            # Aquí p_type existe con total certeza
            config_class = ContractFactory.get_config_class(p_type)
            config_class(**(v or {}))
        except Exception as e: 
            # Garantizado: config_class está definida porque el try no falló en la asignación
            schema = config_class.model_json_schema()
            
            # Limpieza y extracción del esquema esperado
            expected = {
                field: {
                    "type": props.get("type"),
                    "required": field in schema.get("required", [])
                }
                for field, props in schema.get("properties", {}).items()
            }

            # Lanzamos el error contractual limpio que el adaptador sí interceptará como ValidationError
            raise PydanticCustomError(
                "invalid_parser_config",
                "El contrato para '{parser}' es inválido.",
                {
                    "parser": p_type.value if hasattr(p_type, "value") else str(p_type),
                    "expected_schema": expected,
                    "errors": e.errors() if hasattr(e, 'errors') else str(e)
                }
            )
        
        return v