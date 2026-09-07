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

    from scraping.parsers.dinamic_parse import DynamicParser
    parser_dyn = factory.get_parser(ParserType.DINAMIC_PLAYWRIGHT)
    assert isinstance(parser_dyn, DynamicParser)
    assert parser_dyn.network_client == mock_client


def test_factory_raises_value_error_for_unknown_parser():
    """Valida que la factoría lance ValueError si se le solicita un tipo de parser no registrado."""
    mock_client = MagicMock()
    factory = ParserFactory(network_client=mock_client)
    
    # Simulamos un tipo de parser desconocido no registrado en el diccionario interno
    with pytest.raises(ValueError) as exc_info:
        factory.get_parser("ai_semantic_parser")
        
    assert "No existe implementación para" in str(exc_info.value)


def test_base_parser_prepare_result():
    from scraping.parsers.base import BaseParser
    from shared.models import ScrapingTask
    from scraping.interfaces.interfaces import ParseResult
    import asyncio

    class DummyParser(BaseParser):
        async def parse(self, task):
            await super().parse(task)

    parser = DummyParser()
    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="https://example.com", parser_type="static_css",
        parser_config={"selectors": {"headline": "h1.title"}}
    )
    
    # Cover _prepare_result
    result = parser._prepare_result(task, {"headline": "Value"})
    assert isinstance(result, ParseResult)
    assert result.task == task
    assert result.data == {"headline": "Value"}

    # Cover abstract parse method pass
    try:
        asyncio.run(parser.parse(task))
    except Exception:
        pass
