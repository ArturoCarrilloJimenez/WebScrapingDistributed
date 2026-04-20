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
        
        if not p_type:
            return v

        try:
            config_class = ContractFactory.get_config_class(p_type)
            config_class(**(v or {}))
        except Exception as e: # Capturamos ValidationError o ImportError del Factory
            schema = config_class.model_json_schema()
            
            # Limpieza y extracción del esquema esperado
            expected = {
                field: {
                    "type": props.get("type"),
                    "required": field in schema.get("required", [])
                }
                for field, props in schema.get("properties", {}).items()
            }

            # LANZAMOS EL ERROR. Pydantic automáticamente le pondrá el loc: ["parser_config"]
            raise PydanticCustomError(
                "invalid_parser_config",
                "El contrato para '{parser}' es inválido.",
                {
                    "parser": p_type.value,
                    "expected_schema": expected,
                    "errors": e.errors() if hasattr(e, 'errors') else str(e)
                }
            )
        return v