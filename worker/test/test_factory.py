import pytest
from unittest.mock import MagicMock
from scraping.parsers.factory import ParserFactory
from shared.models import ParserType
from scraping.parsers.static_css_parse import StaticCSSParser

def test_factory_resolves_valid_parser():
    """Valida que la factoría resuelva correctamente una estrategia registrada."""
    mock_client = MagicMock()
    factory = ParserFactory(network_client=mock_client)
    
    parser = factory.get_parser(ParserType.STATIC_CSS)
    assert isinstance(parser, StaticCSSParser)
    assert parser.network_client == mock_client


def test_factory_raises_value_error_for_unknown_parser():
    """Valida que la factoría lance ValueError si se le solicita un tipo de parser no registrado."""
    mock_client = MagicMock()
    factory = ParserFactory(network_client=mock_client)
    
    # Simulamos un tipo de parser desconocido no registrado en el diccionario interno
    with pytest.raises(ValueError) as exc_info:
        factory.get_parser("ai_semantic_parser")
        
    assert "No existe implementación para" in str(exc_info.value)
