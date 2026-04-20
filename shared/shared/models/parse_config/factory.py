import importlib
from typing import Type
from .base import BaseParserConfig
from shared.models.parse_type import ParserType
from shared.logging import Logger

log = Logger("ContractFactory")

class ContractFactory:
    @staticmethod
    def get_config_class(parser_type: ParserType) -> Type[BaseParserConfig]:
        """
        Carga de forma perezosa (Lazy Load) el modelo de configuración 
        específico para un ParserType.
        """
        # Como usamos un Enum, ya sabemos que el valor es seguro
        module_name = parser_type.value 
        
        try:
            # Construimos el path dinámico
            module_path = f".{module_name}" 
            module = importlib.import_module(module_path, package=__package__)
            
            # Buscamos la clase estandarizada 'Config'
            config_class = getattr(module, "Config")
            
            # Verificación de integridad: ¿Hereda de nuestra base?
            if not issubclass(config_class, BaseParserConfig):
                raise TypeError(f"La clase 'Config' en {module_name} no hereda de BaseParserConfig")
                
            return config_class

        except (ImportError, AttributeError) as e:
            log.error(f"Error crítico cargando contrato '{module_name}': {str(e)}")
            raise ValueError(f"Contrato para '{module_name}' no disponible o mal formado.")