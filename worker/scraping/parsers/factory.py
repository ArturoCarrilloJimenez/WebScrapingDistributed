from typing import Dict, Type
from shared.models import ParserType
from scraping.parsers.static_css_parse import StaticCSSParser
from scraping.parsers import BaseParser

class ParserFactory:
    # Mapeo dinámico entre el Enum del modelo y la clase de estrategia
    _registry: Dict[ParserType, Type[BaseParser]] = {
        ParserType.STATIC_CSS: StaticCSSParser,
        # ParserType.PLAYWRIGHT: PlaywrightParser, <-- Próximamente
    }

    @classmethod
    def get_parser(cls, parser_type: ParserType) -> BaseParser:
        parser_cls = cls._registry.get(parser_type)
        if not parser_cls:
            raise ValueError(f"No existe implementación para: {parser_type}")
        return parser_cls()