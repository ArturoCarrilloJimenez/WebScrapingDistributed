import pytest
from unittest.mock import MagicMock
from scraping.parsers.dinamic_parse import DynamicParser
from shared.models import ScrapingTask
from scraping.exceptions import ScrapingError, ErrorCategory

pytestmark = pytest.mark.asyncio

async def test_dynamic_parser_success():
    mock_client = MagicMock()
    mock_client.proxy_provider = None
    
    parser = DynamicParser(network_client=mock_client)
    
    # Obtenemos el singleton del navegador para interceptar la ruta antes de ejecutar parse
    browser = await DynamicParser.get_browser()
    original_new_context = browser.new_context
    
    async def mocked_new_context(*args, **kwargs):
        ctx = await original_new_context(*args, **kwargs)
        # Interceptamos cualquier petición a example.com/test y devolvemos nuestro HTML
        await ctx.route("**/test", lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1 class='title'>Noticia Dinamica</h1></body></html>"
        ))
        return ctx
        
    browser.new_context = mocked_new_context
    
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
    
    try:
        res = await parser.parse(task)
        assert res.data["headline"] == ["Noticia Dinamica"]
    finally:
        # Restauramos el método original y limpiamos
        browser.new_context = original_new_context
        await DynamicParser.close_browser()

async def test_dynamic_parser_invalid_schema():
    mock_client = MagicMock()
    mock_client.proxy_provider = None
    
    parser = DynamicParser(network_client=mock_client)
    
    browser = await DynamicParser.get_browser()
    original_new_context = browser.new_context
    
    async def mocked_new_context(*args, **kwargs):
        ctx = await original_new_context(*args, **kwargs)
        await ctx.route("**/test-invalid", lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1>No Class Here</h1></body></html>"
        ))
        return ctx
        
    browser.new_context = mocked_new_context
    
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
    
    try:
        with pytest.raises(ScrapingError) as exc_info:
            await parser.parse(task)
        assert exc_info.value.category == ErrorCategory.INVALID_SCHEMA
    finally:
        browser.new_context = original_new_context
        await DynamicParser.close_browser()
