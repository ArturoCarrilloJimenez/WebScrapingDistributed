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
    mock_client.get_session = AsyncMock(return_value=mock_session)
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


async def test_parser_container_extraction_success():
    """Prueba la extracción estructurada por contenedor de ítems."""
    html = """
    <html><body>
        <div class="card">
            <h2 class="title">Producto A</h2>
            <span class="price">10€</span>
            <a class="link" href="/p/a">Ver</a>
        </div>
        <div class="card">
            <h2 class="title">Producto B</h2>
            <span class="price">20€</span>
            <a class="link" href="/p/b">Ver</a>
        </div>
    </body></html>
    """
    client = create_mock_client(200, html)
    parser = StaticCSSParser(network_client=client)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_02",
        url="https://example.com/catalog", parser_type="static_css",
        parser_config={
            "container": "div.card",
            "selectors": {
                "nombre": "h2.title",
                "precio": "span.price",
                "link": {"selector": "a.link", "attribute": "href"}
            }
        },
        retry_count=0, max_retries=3
    )

    with patch.object(parser, "_prepare_result", side_effect=lambda t, ext: ext):
        res = await parser.parse(task)
        assert len(res) == 2
        assert res[0] == {"nombre": "Producto A", "precio": "10€", "link": "/p/a"}
        assert res[1] == {"nombre": "Producto B", "precio": "20€", "link": "/p/b"}



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


async def test_parser_unexpected_exception_translation():
    """Valida que cualquier excepción inesperada se convierta en un SERVER_ERROR."""
    client = MagicMock()
    # Forzamos que get_session lance un error inesperado no controlado
    client.get_session.side_effect = AttributeError("Unexpected None object")
    parser = StaticCSSParser(network_client=client)

    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01",
        url="https://example.com", parser_type="static_css",
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=0, max_retries=3
    )

    with pytest.raises(ScrapingError) as exc_info:
        await parser.parse(task)
    assert exc_info.value.category == ErrorCategory.SERVER_ERROR
    assert "Unexpected None object" in exc_info.value.original_error


def _create_multi_url_client(responses_by_url: dict[str, tuple[int, str, dict[str, str] | None]]):
    """Helper para simular múltiples respuestas HTTP según la URL solicitada."""
    async def mock_get(url: str, allow_redirects: bool = True):
        status, text, headers = responses_by_url[url]
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        resp.headers = headers if headers is not None else {"content-type": "text/html; charset=utf-8"}
        resp.raise_for_status.side_effect = None if status < 400 else Exception(f"HTTP {status}")
        return resp

    mock_session = AsyncMock()
    mock_session.get.side_effect = mock_get

    mock_client = MagicMock()
    mock_client.get_session = AsyncMock(return_value=mock_session)
    return mock_client


async def test_parser_follow_links_with_source_url_sec_style():
    """Valida flujo completo SEC 8-K + source_url (-index.htm) + limpieza iXBRL + rate limiter."""
    doc_url = "https://www.sec.gov/Archives/edgar/data/895421/000089542123000123/ms-20231208.htm"
    index_url = "https://www.sec.gov/Archives/edgar/data/895421/000089542123000123/0000895421-23-000123-index.htm"
    ex99_url = "https://www.sec.gov/Archives/edgar/data/895421/000089542123000123/ex99-1.htm"

    index_html = """
    <html><body>
      <table class="tableFile">
        <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
        <tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/895421/000089542123000123/ms-20231208.htm">ms.htm</a></td><td>8-K</td></tr>
        <tr><td>2</td><td>EXHIBIT 99.1</td><td><a href="/ix?doc=/Archives/edgar/data/895421/000089542123000123/ex99-1.htm">ex99-1.htm</a></td><td>EX-99.1</td></tr>
        <tr><td>3</td><td>LOGO</td><td><a href="/Archives/edgar/data/895421/000089542123000123/logo.jpg">logo.jpg</a></td><td>GRAPHIC</td></tr>
      </table>
    </body></html>
    """
    client = _create_multi_url_client({
        doc_url: (200, "<html><body><p>Cover sheet Item 2.02</p></body></html>", None),
        index_url: (200, index_html, None),
        ex99_url: (200, "<html><body><h1>Earnings Q4</h1><p>Net income rose 15%</p></body></html>", None),
    })
    rate_limiter = MagicMock()
    rate_limiter.acquire = AsyncMock()
    robots_service = MagicMock()
    robots_service.is_allowed = AsyncMock(return_value=(True, "allowed"))

    parser = StaticCSSParser(
        network_client=client,
        rate_limiter=rate_limiter,
        robots_service=robots_service,
    )

    task = ScrapingTask(
        job_id="sec-8k-test",
        batch_id="b-1",
        task_id="t-sec-1",
        url=doc_url,
        parser_type="static_css",
        rate_limit_per_second=8.0,
        parser_config={
            "selectors": {"body_text": "body"},
            "follow_links": [
                {
                    "name": "exhibits",
                    "source_url": index_url,
                    "row_selector": "table.tableFile tr",
                    "link_selector": "td:nth-of-type(3) a",
                    "filter_selector": "td:nth-of-type(4)",
                    "filter_regex": "(?i)^EX-99",
                    "url_strip_regex": r"^/ix\?doc=",
                    "max_links": 5,
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    result = await parser.parse(task)
    assert result.data["body_text"] == ["Cover sheet Item 2.02"]
    assert len(result.data["exhibits"]) == 1
    assert result.data["exhibits"][0]["url"] == ex99_url
    assert result.data["exhibits"][0]["label"] == "EX-99.1"
    assert result.data["exhibits"][0]["data"]["body_text"] == ["Earnings Q4Net income rose 15%"]
    # Subconsultas (source_url + ex99_url) deben adquirir token de rate limit y validar robots
    assert rate_limiter.acquire.await_count == 2
    assert robots_service.is_allowed.await_count == 2


async def test_parser_follow_links_direct_from_main_html_and_isolates_permanent_suberrors():
    """Valida follow_links sin source_url y aislamiento de sub-enlaces 404 o no-HTML."""
    main_url = "https://example.com/filing"
    ok_url = "https://example.com/exhibit-1.htm"
    pdf_url = "https://example.com/exhibit-2.pdf"
    missing_url = "https://example.com/exhibit-3.htm"

    main_html = """
    <html><body>
      <p>Main filing text</p>
      <a class="sub" href="/exhibit-1.htm">EX-99.1</a>
      <a class="sub" href="/exhibit-2.pdf">EX-99.2</a>
      <a class="sub" href="/exhibit-3.htm">EX-99.3</a>
      <a class="sub" href="/other.htm">IGNORE</a>
    </body></html>
    """
    client = _create_multi_url_client({
        main_url: (200, main_html, None),
        ok_url: (200, "<html><body><p>Exhibit 1 OK</p></body></html>", None),
        pdf_url: (200, "%PDF-1.4 binary", {"content-type": "application/pdf"}),
        missing_url: (404, "Not Found", None),
    })

    parser = StaticCSSParser(network_client=client)
    task = ScrapingTask(
        job_id="j_sub",
        batch_id="b_sub",
        task_id="t_sub",
        url=main_url,
        parser_type="static_css",
        parser_config={
            "selectors": {"body_text": "p"},
            "follow_links": [
                {
                    "name": "exhibits",
                    "link_selector": "a.sub",
                    "filter_regex": "^EX-99",
                    "max_links": 5,
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    result = await parser.parse(task)
    assert result.data["body_text"] == ["Main filing text"]
    assert len(result.data["exhibits"]) == 3
    assert result.data["exhibits"][0]["data"]["body_text"] == ["Exhibit 1 OK"]
    assert "Contenido no HTML" in result.data["exhibits"][1]["error"]
    assert "Página no existe" in result.data["exhibits"][2]["error"]


async def test_parser_follow_links_raises_on_transient_429_to_trigger_sqs_retry():
    """Valida que un 429 en un sub-enlace aborte la tarea para reintento con backoff en SQS."""
    main_url = "https://www.sec.gov/doc.htm"
    sub_url = "https://www.sec.gov/ex99-1.htm"

    client = _create_multi_url_client({
        main_url: (200, "<html><body><p>Main</p><a class='ex' href='/ex99-1.htm'>EX-99.1</a></body></html>", None),
        sub_url: (429, "Rate limit exceeded", None),
    })
    parser = StaticCSSParser(network_client=client)
    task = ScrapingTask(
        job_id="j_429",
        batch_id="b_429",
        task_id="t_429",
        url=main_url,
        parser_type="static_css",
        parser_config={
            "selectors": {"body_text": "p"},
            "follow_links": [
                {
                    "name": "exhibits",
                    "link_selector": "a.ex",
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    with pytest.raises(ScrapingError) as exc_info:
        await parser.parse(task)
    assert exc_info.value.category == ErrorCategory.BLOCKED


async def test_parser_follow_links_source_url_404_preserves_main_extraction():
    """Si source_url devuelve 404, conserva la extracción principal devolviendo lista vacía."""
    main_url = "https://www.sec.gov/doc.htm"
    missing_index = "https://www.sec.gov/missing-index.htm"

    client = _create_multi_url_client({
        main_url: (200, "<html><body><p>Main 8-K body</p></body></html>", None),
        missing_index: (404, "Not Found", None),
    })
    parser = StaticCSSParser(network_client=client)
    task = ScrapingTask(
        job_id="j_idx404",
        batch_id="b_idx404",
        task_id="t_idx404",
        url=main_url,
        parser_type="static_css",
        parser_config={
            "selectors": {"body_text": "p"},
            "follow_links": [
                {
                    "name": "exhibits",
                    "source_url": missing_index,
                    "link_selector": "a",
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    result = await parser.parse(task)
    assert result.data["body_text"] == ["Main 8-K body"]
    assert result.data["exhibits"] == []


async def test_parser_follow_links_fallback_source_url_metadata_and_exclude_ixbrl():
    """Valida fallback -index.html ante 404, metadata_selectors, exclude_selectors (ix:header) y regex EX-(?!101)."""
    doc_url = "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/jpm-20241011.htm"
    index_htm = "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/0000019617-24-000555-index.htm"
    index_html = "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/0000019617-24-000555-index.html"
    ex99_1_url = "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/ex99-1.htm"
    ex10_1_url = "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/ex10-1.htm"

    index_table_html = """
    <html><body>
      <table class="tableFile">
        <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
        <tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/19617/000001961724000555/jpm-20241011.htm">jpm.htm</a></td><td>8-K</td><td>15000</td></tr>
        <tr><td>2</td><td>EARNINGS RELEASE - 3Q24</td><td><a href="/ix?doc=/Archives/edgar/data/19617/000001961724000555/ex99-1.htm">ex99-1.htm</a></td><td>EX-99.1</td><td>245612</td></tr>
        <tr><td>3</td><td>MATERIAL CONTRACT</td><td><a href="/Archives/edgar/data/19617/000001961724000555/ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>84200</td></tr>
        <tr><td>4</td><td>XBRL SCHEMA</td><td><a href="/Archives/edgar/data/19617/000001961724000555/jpm.xsd">jpm.xsd</a></td><td>EX-101.SCH</td><td>12000</td></tr>
        <tr><td>5</td><td>LOGO</td><td><a href="/Archives/edgar/data/19617/000001961724000555/logo.jpg">logo.jpg</a></td><td>GRAPHIC</td><td>4000</td></tr>
      </table>
    </body></html>
    """
    doc_html = (
        "<html><body>"
        "<ix:header><ix:hidden>SECRET_XBRL_META</ix:hidden></ix:header>"
        "<style>.cls{color:red}</style>"
        "<script>var x=1;</script>"
        "<p>Cover sheet Item 2.02</p>"
        "</body></html>"
    )
    ex99_html = (
        "<html><body>"
        "<ix:header>NOISE_HEADER</ix:header>"
        "<h1>3Q24 Net Income</h1>"
        "</body></html>"
    )

    recorded_headers: list[dict | None] = []

    async def mock_get(url: str, allow_redirects: bool = True):
        responses = {
            doc_url: (200, doc_html, None),
            index_htm: (404, "Not Found", None),
            index_html: (200, index_table_html, None),
            ex99_1_url: (200, ex99_html, None),
            ex10_1_url: (200, "<html><body><p>Contract terms</p></body></html>", None),
        }
        status, text, headers = responses[url]
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        resp.headers = headers or {"content-type": "text/html"}
        resp.raise_for_status.side_effect = None if status < 400 else Exception(f"HTTP {status}")
        return resp

    mock_session = AsyncMock()
    mock_session.get.side_effect = mock_get

    async def mock_get_session(**kwargs):
        recorded_headers.append(kwargs.get("custom_headers"))
        return mock_session

    mock_client = MagicMock()
    mock_client.get_session = AsyncMock(side_effect=mock_get_session)

    parser = StaticCSSParser(network_client=mock_client)
    sec_headers = {"User-Agent": "TFM_Data_Science_EBIS discovery_scraper@ebis.edu"}

    task = ScrapingTask(
        job_id="sec-8k-full",
        batch_id="b-sec",
        task_id="t-sec-full",
        url=doc_url,
        parser_type="static_css",
        headers=sec_headers,
        parser_config={
            "selectors": {"body_text": "body"},
            "exclude_selectors": ["ix:header", "script", "style"],
            "follow_links": [
                {
                    "name": "exhibits",
                    "source_url": index_htm,
                    "fallback_source_urls": [index_html],
                    "row_selector": "table.tableFile tr",
                    "link_selector": "td:nth-of-type(3) a",
                    "filter_selector": "td:nth-of-type(4)",
                    "filter_regex": r"(?i)^EX-(?!101\b)",
                    "url_strip_regex": r"^/ix\?doc=",
                    "max_links": 50,
                    "metadata_selectors": {
                        "description": "td:nth-of-type(2)",
                        "size": "td:nth-of-type(5)",
                    },
                    "exclude_selectors": ["ix:header", "script", "style"],
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    result = await parser.parse(task)
    assert result.data["body_text"] == ["Cover sheet Item 2.02"]
    assert "SECRET_XBRL_META" not in result.data["body_text"][0]
    assert len(result.data["exhibits"]) == 2
    assert result.data["exhibits"][0]["label"] == "EX-99.1"
    assert result.data["exhibits"][0]["metadata"] == {
        "description": "EARNINGS RELEASE - 3Q24",
        "size": "245612",
    }
    assert result.data["exhibits"][0]["data"]["body_text"] == ["3Q24 Net Income"]
    assert result.data["exhibits"][1]["label"] == "EX-10.1"
    assert result.data["exhibits"][1]["metadata"] == {
        "description": "MATERIAL CONTRACT",
        "size": "84200",
    }
    assert all(h == sec_headers for h in recorded_headers)


async def test_parser_follow_links_all_index_urls_404_preserves_main():
    """Si source_url y fallback_source_urls devuelven 404, conserva el 8-K principal con exhibits == []."""
    doc_url = "https://www.sec.gov/Archives/edgar/data/19617/000001961710000268/doc.htm"
    index_htm = "https://www.sec.gov/Archives/edgar/data/19617/000001961710000268/0000019617-10-000268-index.htm"
    index_html = "https://www.sec.gov/Archives/edgar/data/19617/000001961710000268/0000019617-10-000268-index.html"

    client = _create_multi_url_client({
        doc_url: (200, "<html><body><p>Legacy 2010 8-K</p></body></html>", None),
        index_htm: (404, "Not Found", None),
        index_html: (404, "Not Found", None),
    })
    parser = StaticCSSParser(network_client=client)
    task = ScrapingTask(
        job_id="j_all_404",
        batch_id="b_all_404",
        task_id="t_all_404",
        url=doc_url,
        parser_type="static_css",
        parser_config={
            "selectors": {"body_text": "body"},
            "follow_links": [
                {
                    "name": "exhibits",
                    "source_url": index_htm,
                    "fallback_source_urls": [index_html],
                    "row_selector": "table.tableFile tr",
                    "link_selector": "td:nth-of-type(3) a",
                    "selectors": {"body_text": "body"},
                }
            ],
        },
    )

    result = await parser.parse(task)
    assert result.data["body_text"] == ["Legacy 2010 8-K"]
    assert result.data["exhibits"] == []