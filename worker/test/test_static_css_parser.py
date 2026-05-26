import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scraping.parsers.static_css_parse import StaticCSSParser
from scraping.exceptions import ScrapingError, ErrorCategory
from shared.models import ScrapingTask

pytestmark = pytest.mark.asyncio


def create_mock_client(status_code: int, html_text: str = ""):
    """Helper para simular la sesión de red asíncrona del client."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = html_text
    mock_response.raise_for_status.side_effect = None if status_code < 400 else Exception()

    mock_session = AsyncMock()
    mock_session.get.return_value = mock_response

    mock_client = MagicMock()
    mock_client.create_session.return_value.__aenter__.return_value = mock_session
    return mock_client


async def test_parser_extraction_success():
    """Prueba el camino feliz: extracción correcta con BeautifulSoup."""
    html = "<html><body><h1 class='title'>Noticia de Impacto</h1></body></html>"
    client = create_mock_client(200, html)
    parser = StaticCSSParser(network_client=client)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="https://example.com", parser_type="static_css",
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=0, max_retries=3
    )

    with patch.object(parser, "_prepare_result", side_effect=lambda t, ext: ext):
        res = await parser.parse(task)
        assert res["headline"] == ["Noticia de Impacto"]


@pytest.mark.parametrize("status, expected_category", [
    (404, ErrorCategory.NOT_FOUND),
    (403, ErrorCategory.BLOCKED),
    (429, ErrorCategory.BLOCKED),
    (500, ErrorCategory.SERVER_ERROR),
])
async def test_parser_http_errors(status, expected_category):
    """Prueba la conversión correcta de códigos HTTP corruptos a ScrapingErrors."""
    client = create_mock_client(status)
    parser = StaticCSSParser(network_client=client)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="https://example.com", parser_type="static_css",
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=0, max_retries=3
    )

    with pytest.raises(ScrapingError) as exc_info:
        await parser.parse(task)
    assert exc_info.value.category == expected_category


async def test_parser_invalid_schema_exception():
    """Prueba que si los selectores no extraen nada, lanza INVALID_SCHEMA (DOM cambiado)."""
    html = "<html><body><div class='vacio'></div></body></html>"
    client = create_mock_client(200, html)
    parser = StaticCSSParser(network_client=client)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="https://example.com", parser_type="static_css",
        parser_config={"selectors": {"headline": "h1.missing-class"}},
        retry_count=0, max_retries=3
    )

    with pytest.raises(ScrapingError) as exc_info:
        await parser.parse(task)
    assert exc_info.value.category == ErrorCategory.INVALID_SCHEMA