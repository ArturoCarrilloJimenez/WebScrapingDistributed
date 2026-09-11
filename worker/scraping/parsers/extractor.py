"""Motor de extracción universal para elementos BeautifulSoup HTML."""

from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag
from shared.models.parse_config.base import FieldDefinition

from scraping.security.honeypot_guard import HoneypotGuard


class UniversalDOMExtractor:
    """Extrae datos estructurados (entidades únicas o colecciones) a partir de DOMs parsed con BeautifulSoup."""

    def __init__(self, honeypot_guard: Optional[HoneypotGuard] = None):
        """Inicializa el extractor inyectando la capa de seguridad HoneypotGuard."""
        self.honeypot_guard = honeypot_guard

    def extract_from_soup(
        self,
        soup: BeautifulSoup,
        selectors: Dict[str, FieldDefinition],
        container: Optional[str] = None
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        """Extrae datos estructurados desde una instancia de BeautifulSoup.

        Args:
            soup: Árbol DOM cargado con BeautifulSoup.
            selectors: Diccionario de campos a selectores CSS o FieldSpec.
            container: Selector CSS opcional del contenedor para colecciones.

        Returns:
            Un diccionario (si single) o una lista de diccionarios (si collection).
        """
        is_collection = container is not None

        if is_collection and container:
            elements = soup.select(container)
            if self.honeypot_guard:
                elements = self.honeypot_guard.filter_static_elements(elements)

            return [
                self._extract_item_from_node(node, selectors, is_container_child=True)
                for node in elements
            ]

        # Extracción de Entidad Única (o modo plano sin contenedor)
        return self._extract_item_from_node(soup, selectors, is_container_child=False)

    def _parse_field_definition(
        self,
        field_def: FieldDefinition,
        is_container_child: bool
    ) -> Tuple[str, Optional[str], Any, bool]:
        """Normaliza la definición del campo (str, dict u objeto FieldSpec) a valores estándar."""
        if isinstance(field_def, str):
            selector = field_def
            attribute = None
            default_val = None
            is_multiple = not is_container_child
        elif isinstance(field_def, dict):
            selector = field_def.get("selector", "")
            attribute = field_def.get("attribute")
            default_val = field_def.get("default")
            is_multiple = field_def.get("multiple", False)
        else:
            selector = getattr(field_def, "selector", "")
            attribute = getattr(field_def, "attribute", None)
            default_val = getattr(field_def, "default", None)
            is_multiple = getattr(field_def, "multiple", False)

        return selector, attribute, default_val, is_multiple

    def _select_and_filter_elements(
        self,
        node: BeautifulSoup | Tag,
        selector: str
    ) -> List[Tag]:
        """Selecciona elementos coincidentes en el nodo y filtra trampas honeypot si aplica."""
        if not selector or selector == "self":
            return [node] if isinstance(node, Tag) else []
        elements = node.select(selector)
        if self.honeypot_guard:
            elements = self.honeypot_guard.filter_static_elements(elements)
        return elements

    def _extract_element_value(
        self,
        element: Tag,
        attribute: Optional[str]
    ) -> Optional[str]:
        """Obtiene el texto o atributo de un elemento individual."""
        val = element.get(attribute) if attribute else element.get_text(strip=True)
        return str(val).strip() if val is not None else None

    def _extract_field_value(
        self,
        matching_elements: List[Tag],
        attribute: Optional[str],
        default_val: Any,
        is_multiple: bool
    ) -> Any:
        """Procesa la lista de elementos para devolver un valor escalar o una lista."""
        if not matching_elements:
            return [] if is_multiple else default_val

        if is_multiple:
            extracted_list = []
            for el in matching_elements:
                val = self._extract_element_value(el, attribute)
                if val is not None:
                    extracted_list.append(val)
            return extracted_list

        el = matching_elements[0]
        val = self._extract_element_value(el, attribute)
        return val if val is not None else default_val

    def _extract_field(
        self,
        node: BeautifulSoup | Tag,
        field_def: FieldDefinition,
        is_container_child: bool
    ) -> Any:
        """Extrae el valor de un único campo configurado."""
        selector, attribute, default_val, is_multiple = self._parse_field_definition(
            field_def, is_container_child
        )
        matching_elements = self._select_and_filter_elements(node, selector)
        return self._extract_field_value(matching_elements, attribute, default_val, is_multiple)

    def _extract_item_from_node(
        self,
        node: BeautifulSoup | Tag,
        selectors: Dict[str, FieldDefinition],
        is_container_child: bool = False
    ) -> Dict[str, Any]:
        """Extrae un único registro (o fila) a partir de un nodo DOM."""
        return {
            field_name: self._extract_field(node, field_def, is_container_child)
            for field_name, field_def in selectors.items()
        }
