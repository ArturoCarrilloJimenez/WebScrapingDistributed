import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from scraping.parsers.dinamic_parse import DynamicParser
from shared.models import ScrapingTask
from scraping.exceptions import ScrapingError, ErrorCategory
from shared.models.parse_config.dynamic_playwright import Config as PlaywrightConfig

pytestmark = pytest.mark.asyncio


def _create_mock_browser(html_content: str, status_code: int = 200):
    mock_page = AsyncMock()
    mock_response = MagicMock()
    mock_response.status = status_code
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


async def test_dynamic_parser_interactions_and_waits():
    """Valida la ejecución de click_selectors, scroll_to_bottom y wait_for_selector."""
    mock_client = MagicMock()
    mock_client.proxy_provider = None

    parser = DynamicParser(network_client=mock_client)
    mock_browser = _create_mock_browser("<html><body><h1 class='title'>Interactivo</h1></body></html>")
    mock_context = await mock_browser.new_context()
    mock_page = await mock_context.new_page()

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_02",
        url="http://example.com/interactive",
        parser_type="dynamic_playwright",
        parser_config={
            "selectors": {"headline": "h1.title"},
            "click_selectors": ["#btn-cookie", ".load-more"],
            "scroll_to_bottom": True,
            "wait_for_selector": "h1.title",
            "fixed_sleep_s": 0.01,
            "container": "body"
        }
    )

    config = PlaywrightConfig(**(task.parser_config or {}))

    await parser._execute_interactions(mock_page, task, config)
    assert mock_page.click.call_count == 2
    mock_page.evaluate.assert_called_once_with("window.scrollTo(0, document.body.scrollHeight)")

    await parser._wait_for_conditions(mock_page, task, config)
    mock_page.wait_for_selector.assert_called_with("h1.title", timeout=config.timeout_ms)


async def test_dynamic_parser_get_proxy():
    """Valida el formateo del proxy según el provider."""
    mock_client = MagicMock()
    mock_provider = MagicMock()
    mock_provider.get_proxy_url.return_value = "http://user:pass@proxy.example.com:8080"
    mock_client.proxy_provider = mock_provider

    parser = DynamicParser(network_client=mock_client)
    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_03",
        url="http://example.com",
        parser_type="dynamic_playwright",
        parser_config={"selectors": {"headline": "h1"}},
        context={"sticky_session_id": "sess_123"}
    )

    proxy_cfg = parser._get_proxy(task)
    assert proxy_cfg == {
        "server": "http://proxy.example.com:8080",
        "username": "user",
        "password": "pass"
    }


async def test_dynamic_parser_blocked_status():
    """Valida la detección de bloqueos 403 / 429 durante la navegación."""
    mock_client = MagicMock()
    mock_client.proxy_provider = None

    parser = DynamicParser(network_client=mock_client)
    mock_browser = _create_mock_browser("<html></html>", status_code=403)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_04",
        url="http://example.com/blocked",
        parser_type="dynamic_playwright",
        parser_config={"selectors": {"title": "h1"}}
    )

    with patch.object(DynamicParser, "get_browser", new_callable=AsyncMock) as mock_get_browser, \
         patch("scraping.parsers.dinamic_parse.Stealth") as mock_stealth:
        mock_get_browser.return_value = mock_browser
        mock_stealth.return_value.apply_stealth_async = AsyncMock()

        with pytest.raises(ScrapingError) as exc_info:
            await parser.parse(task)
        assert exc_info.value.category == ErrorCategory.BLOCKED
