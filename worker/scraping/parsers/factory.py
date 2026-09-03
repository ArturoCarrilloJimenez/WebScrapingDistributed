from typing import Dict, Type, Optional
from shared.models import ParserType
from scraping.parsers.static_css_parse import StaticCSSParser
from scraping.parsers.base import BaseParser
from infrastructure.network.client import SecureNetworkClient
from scraping.parsers.dinamic_parse import DynamicParser
from scraping.security.honeypot_guard import HoneypotGuard


class ParserFactory:
    def __init__(self, network_client: SecureNetworkClient, honeypot_guard: Optional[HoneypotGuard] = None):
        """La factoría recibe por constructor la infraestructura de red y de seguridad requerida."""
        self._network_client = network_client
        self._honeypot_guard = honeypot_guard

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

        # Inyección explícita del cliente de red y de la capa de seguridad en los parsers
        return parser_cls(network_client=self._network_client, honeypot_guard=self._honeypot_guard)

