import pytest
from bs4 import BeautifulSoup
from scraping.security.honeypot_guard import HoneypotGuard


def test_honeypot_guard_static_style_hidden():
    """Prueba que HoneypotGuard detecta elementos ocultos mediante estilos en línea."""
    guard = HoneypotGuard()
    html = """
    <div>
        <a id="safe" href="https://example.com/safe">Enlace Seguro</a>
        <a id="trap1" href="https://example.com/trap1" style="display: none;">Trap 1</a>
        <a id="trap2" href="https://example.com/trap2" style="visibility: hidden;">Trap 2</a>
        <a id="trap3" href="https://example.com/trap3" style="opacity: 0;">Trap 3</a>
        <a id="trap4" href="https://example.com/trap4" style="position: absolute; left: -9999px;">Trap 4</a>
        <a id="trap5" href="https://example.com/trap5" style="width: 0px; height: 0px;">Trap 5</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select("a")

    safe_elements = guard.filter_static_elements(elements)

    assert len(safe_elements) == 1
    assert safe_elements[0]["id"] == "safe"


def test_honeypot_guard_static_classes_and_noscript():
    """Prueba que HoneypotGuard detecta elementos ocultos por clases CSS y dentro de noscript."""
    guard = HoneypotGuard()
    html = """
    <div>
        <a id="valid" href="https://example.com/valid">Válido</a>
        <a id="hidden_class" href="https://example.com/trap" class="d-none hidden">Clase oculta</a>
        <a id="sr_only" href="https://example.com/trap" class="sr-only">Solo lector</a>
        <noscript>
            <a id="noscript_trap" href="https://example.com/trap">Trap NoScript</a>
        </noscript>
        <a id="aria_hidden" href="https://example.com/trap" aria-hidden="true">Aria hidden</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select("a")

    safe_elements = guard.filter_static_elements(elements)

    assert len(safe_elements) == 1
    assert safe_elements[0]["id"] == "valid"


def test_honeypot_guard_static_empty_hrefs():
    """Prueba que HoneypotGuard descarta enlaces con hrefs vacíos o javascript:."""
    guard = HoneypotGuard()
    html = """
    <div>
        <a id="valid" href="https://example.com/item">Válido</a>
        <a id="empty" href="">Vacío</a>
        <a id="hash" href="#">Hash</a>
        <a id="js" href="javascript:void(0)">JS</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select("a")

    safe_elements = guard.filter_static_elements(elements)

    assert len(safe_elements) == 1
    assert safe_elements[0]["id"] == "valid"
