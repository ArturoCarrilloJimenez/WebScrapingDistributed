from typing import Dict, Type
from shared.models import ParserType
from scraping.parsers.static_css_parse import StaticCSSParser
from scraping.parsers.base import BaseParser
from infrastructure.network.client import SecureNetworkClient
from scraping.parsers.dinamic_parse import DynamicParser


class ParserFactory:
    def __init__(self, network_client: SecureNetworkClient):
        """La factoría ahora recibe por constructor la infraestructura de red requerida."""
        self._network_client = network_client

        # Mapeo dinámico entre el Enum del modelo y la clase de estrategia
        self._registry: Dict[ParserType, Type[BaseParser]] = {
            ParserType.STATIC_CSS: StaticCSSParser,
            ParserType.DINAMIC_PLAYWRIGHT: DynamicParser,
        }

    def get_parser(self, parser_type: ParserType) -> BaseParser:
        """Resuelve el parser inyectándole sus dependencias operativas."""
        parser_cls = self._registry.get(parser_type)
        if not parser_cls:
            raise ValueError(f"No existe implementación para: {parser_type}")

        # Inyección explícita del cliente de red blindado en el ciclo de vida del parser
        return parser_cls(network_client=self._network_client)
