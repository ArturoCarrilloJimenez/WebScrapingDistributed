import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from scraping.parsers.dinamic_parse import DynamicParser
from shared.models import ScrapingTask
from scraping.exceptions import ScrapingError, ErrorCategory

pytestmark = pytest.mark.asyncio


def _create_mock_browser(html_content: str):
    mock_page = AsyncMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_page.goto.return_value = mock_response
    mock_page.content.return_value = html_content

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_context.set_default_navigation_timeout = MagicMock()
    mock_context.set_default_timeout = MagicMock()

    mock_browser = AsyncMock()
    mock_browser.version = "130.0.0.0"
    mock_browser.new_context.return_value = mock_context
    return mock_browser


async def test_dynamic_parser_success():
    mock_client = MagicMock()
    mock_client.proxy_provider = None

    parser = DynamicParser(network_client=mock_client)
    mock_browser = _create_mock_browser("<html><body><h1 class='title'>Noticia Dinamica</h1></body></html>")

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="http://example.com/test",
        parser_type="dynamic_playwright",
        parser_config={
            "selectors": {"headline": "h1.title"},
            "timeout_ms": 10000,
            "wait_until": "domcontentloaded"
        }
    )

    with patch.object(DynamicParser, "get_browser", new_callable=AsyncMock) as mock_get_browser, \
         patch("scraping.parsers.dinamic_parse.Stealth") as mock_stealth:
        mock_get_browser.return_value = mock_browser
        mock_stealth.return_value.apply_stealth_async = AsyncMock()

        res = await parser.parse(task)
        assert res.data["headline"] == ["Noticia Dinamica"]


async def test_dynamic_parser_invalid_schema():
    mock_client = MagicMock()
    mock_client.proxy_provider = None

    parser = DynamicParser(network_client=mock_client)
    mock_browser = _create_mock_browser("<html><body><h1>No Class Here</h1></body></html>")

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="http://example.com/test-invalid",
        parser_type="dynamic_playwright",
        parser_config={
            "selectors": {"headline": ".missing-class"},
            "timeout_ms": 10000,
            "wait_until": "domcontentloaded"
        }
    )

    with patch.object(DynamicParser, "get_browser", new_callable=AsyncMock) as mock_get_browser, \
         patch("scraping.parsers.dinamic_parse.Stealth") as mock_stealth:
        mock_get_browser.return_value = mock_browser
        mock_stealth.return_value.apply_stealth_async = AsyncMock()

        with pytest.raises(ScrapingError) as exc_info:
            await parser.parse(task)
        assert exc_info.value.category == ErrorCategory.INVALID_SCHEMA
