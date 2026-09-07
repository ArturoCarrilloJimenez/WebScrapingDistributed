import pytest
from bs4 import BeautifulSoup
from unittest.mock import MagicMock
from scraping.parsers.extractor import UniversalDOMExtractor
from shared.models.parse_config.base import FieldSpec


def test_extract_single_entity_string_selectors():
    """Valida la extracción de una entidad única con selectores en formato cadena."""
    extractor = UniversalDOMExtractor()
    html = """
    <html>
        <body>
            <h1 class="title">  Título de prueba  </h1>
            <p class="author">Juan Pérez</p>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "title": "h1.title",
        "author": "p.author",
        "missing": ".no-exist"
    }

    result = extractor.extract_from_soup(soup, selectors)

    assert result["title"] == ["Título de prueba"]
    assert result["author"] == ["Juan Pérez"]
    assert result["missing"] == []


def test_extract_collection_with_container():
    """Valida la extracción de colecciones usando un selector de contenedor."""
    extractor = UniversalDOMExtractor()
    html = """
    <div class="product-list">
        <div class="product">
            <span class="name">Producto A</span>
            <a href="/item-a">Link A</a>
        </div>
        <div class="product">
            <span class="name">Producto B</span>
            <a href="/item-b">Link B</a>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "name": "span.name",
        "link": FieldSpec(selector="a", attribute="href")
    }

    result = extractor.extract_from_soup(soup, selectors, container=".product")

    assert len(result) == 2
    assert result[0] == {"name": "Producto A", "link": "/item-a"}
    assert result[1] == {"name": "Producto B", "link": "/item-b"}


def test_extract_dict_definition_and_defaults():
    """Valida la extracción usando diccionarios como definiciones de campo y valores por defecto."""
    extractor = UniversalDOMExtractor()
    html = """
    <div id="card">
        <span class="price">$100</span>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "price": {"selector": ".price"},
        "stock": {"selector": ".stock", "default": "0 items"},
        "tags": {"selector": ".tag", "multiple": True}
    }

    result = extractor.extract_from_soup(soup, selectors)

    assert result["price"] == "$100"
    assert result["stock"] == "0 items"
    assert result["tags"] == []


def test_extract_with_honeypot_guard():
    """Valida la integración con HoneypotGuard para descartar elementos ocultos."""
    mock_guard = MagicMock()
    mock_guard.filter_static_elements.side_effect = lambda els: [e for e in els if "hidden" not in e.get("class", [])]

    extractor = UniversalDOMExtractor(honeypot_guard=mock_guard)
    html = """
    <div>
        <p class="text">Visible</p>
        <p class="text hidden">Oculto</p>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = {"text": "p.text"}

    result = extractor.extract_from_soup(soup, selectors)

    assert result["text"] == ["Visible"]
    assert mock_guard.filter_static_elements.called
