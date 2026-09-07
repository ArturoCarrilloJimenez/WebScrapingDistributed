import re
from typing import List, Optional
from bs4 import Tag
from playwright.async_api import Page, Locator
from shared.logging import Logger


class HoneypotGuard:
    """Capa activa de seguridad Anti-Bot para filtrado y descarte preventivo de Honeypots.

    Soporta análisis estático de nodos HTML (BeautifulSoup) y evaluación
    dinámica optimizada en lote dentro del motor Chromium (Playwright).
    """

    def __init__(self):
        """Inicializa los patrones de inspección heurística de Honeypots."""
        self.log = Logger(self.__class__.__name__)

        # Expresiones regulares para reglas CSS de ocultación en línea
        self._style_honeypot_patterns = [
            re.compile(r"display\s*:\s*none", re.IGNORECASE),
            re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE),
            re.compile(r"opacity\s*:\s*0(\.0+)?", re.IGNORECASE),
            re.compile(r"font-size\s*:\s*0(px|em|rem)?", re.IGNORECASE),
            re.compile(r"left\s*:\s*-\d{2,}", re.IGNORECASE),
            re.compile(r"top\s*:\s*-\d{2,}", re.IGNORECASE),
            re.compile(r"width\s*:\s*0(px)?", re.IGNORECASE),
            re.compile(r"height\s*:\s*0(px)?", re.IGNORECASE),
        ]

        # Clases CSS asociadas comúnmente a elementos invisibles o trampas
        self._suspicious_classes = {
            "hidden", "d-none", "sr-only", "invisible",
            "visually-hidden", "offscreen", "off-screen", "honeypot", "trap-link"
        }

    # ------------------------------------------------------------------
    # 🔍 ANÁLISIS ESTÁTICO (BeautifulSoup bs4.Tag)
    # ------------------------------------------------------------------

    def _has_hidden_style(self, node: Tag) -> bool:
        """Verifica si el atributo 'style' en línea oculta el nodo mediante reglas CSS."""
        style = node.get("style", "")
        if not style:
            return False

        for pattern in self._style_honeypot_patterns:
            if pattern.search(style):
                self.log.debug(f"Honeypot detectado por style='{style}' en <{node.name}>")
                return True

        return False

    def _has_suspicious_class(self, node: Tag) -> bool:
        """Verifica si las clases CSS del nodo contienen clases de ocultación o trampas."""
        classes = node.get("class", [])
        classes_set = set(classes) if isinstance(classes, list) else set(classes.split())
        if self._suspicious_classes.intersection(classes_set):
            self.log.debug(f"Honeypot detectado por clase sospechosa en <{node.name}>")
            return True

        return False

    def _has_accessibility_hidden_attrs(self, node: Tag) -> bool:
        """Verifica si el nodo tiene atributos ARIA o de tabulación que indiquen ocultación."""
        return node.get("aria-hidden") == "true" or node.get("tabindex") == "-1"

    def _is_inside_noscript(self, node: Tag) -> bool:
        """Verifica si el nodo está dentro de un contenedor <noscript>."""
        return node.find_parent("noscript") is not None

    def _is_invalid_anchor_link(self, node: Tag) -> bool:
        """Para etiquetas <a>, desestima enlaces con hrefs vacíos, '#' o scripts 'javascript:'."""
        if node.name != "a":
            return False
        href = node.get("href", "").strip()
        return not href or href == "#" or href.startswith("javascript:")

    def is_static_node_honeypot(self, node: Tag) -> bool:
        """Evalúa si un nodo HTML estático es un Honeypot o elemento oculto.

        Args:
            node: Objeto Tag de BeautifulSoup a analizar.

        Returns:
            True si el nodo es sospechoso/trampa, False si es seguro.
        """
        if not node or not hasattr(node, "get"):
            return False

        return (
            self._has_hidden_style(node)
            or self._has_suspicious_class(node)
            or self._has_accessibility_hidden_attrs(node)
            or self._is_inside_noscript(node)
            or self._is_invalid_anchor_link(node)
        )

    def filter_static_elements(self, elements: List[Tag]) -> List[Tag]:
        """Filtra una lista de nodos BeautifulSoup descartando activamente los Honeypots.

        Args:
            elements: Lista de nodos Tag seleccionados.

        Returns:
            Lista de nodos seguros.
        """
        return [el for el in elements if not self.is_static_node_honeypot(el)]

    # ------------------------------------------------------------------
    # 🎭 ANÁLISIS DINÁMICO EN LOTE (Playwright Page & Locator)
    # ------------------------------------------------------------------

    async def filter_playwright_locators_in_batch(self, page: Page, selector: str) -> List[Locator]:
        """Evalúa en LOTE todos los elementos que coinciden con un selector en Chromium.

        Ejecuta 1 sola llamada JavaScript enviada al motor V8 de Chromium para analizar 
        la visibilidad, dimensiones físicas y CSS calculado en ~15 ms.

        Args:
            page: Instancia activa de la página de Playwright.
            selector: Selector CSS a buscar en el DOM.

        Returns:
            Lista de Locators de Playwright correspondientes a elementos seguros.
        """
        try:
            # 1 Sola llamada enviada a Chromium para evaluar todos los elementos a la vez
            valid_indices: List[int] = await page.evaluate("""(sel) => {
                const elements = Array.from(document.querySelectorAll(sel));
                return elements
                    .map((el, index) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        
                        const isHidden = (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            parseFloat(style.opacity) === 0 ||
                            style.pointerEvents === 'none' ||
                            rect.width <= 1 || rect.height <= 1 ||
                            rect.x < 0 || rect.y < 0
                        );
                        
                        return isHidden ? null : index;
                    })
                    .filter(idx => idx !== null);
            }""", selector)

            all_locators = page.locator(selector)
            count = await all_locators.count()
            return [all_locators.nth(i) for i in valid_indices if i < count]
        except Exception as e:
            self.log.warning(f"Fallo en evaluación de Honeypots en lote para '{selector}': {e}")
            # Fallback seguro: devolver locators nativos de Playwright
            locs = await page.locator(selector).all()
            return locs
