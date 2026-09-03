"""Motor de extracción universal para elementos BeautifulSoup HTML."""

from typing import Any, Dict, List, Optional, Union
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
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
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

            items: List[Dict[str, Any]] = []
            for node in elements:
                item = self._extract_item_from_node(node, selectors, is_container_child=True)
                items.append(item)
            return items

        # Extracción de Entidad Única (o modo plano sin contenedor)
        return self._extract_item_from_node(soup, selectors, is_container_child=False)

    def _extract_item_from_node(
        self,
        node: Union[BeautifulSoup, Tag],
        selectors: Dict[str, FieldDefinition],
        is_container_child: bool = False
    ) -> Dict[str, Any]:
        """Extrae un único registro (o fila) a partir de un nodo DOM."""
        record: Dict[str, Any] = {}

        for field_name, field_def in selectors.items():
            if isinstance(field_def, str):
                selector = field_def
                attribute = None
                default_val = None
                is_multiple = False if is_container_child else True
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


            matching_elements = node.select(selector)
            if self.honeypot_guard:
                matching_elements = self.honeypot_guard.filter_static_elements(matching_elements)

            if not matching_elements:
                record[field_name] = [] if is_multiple else default_val
                continue

            if is_multiple:
                extracted_list = []
                for el in matching_elements:
                    val = el.get(attribute) if attribute else el.get_text(strip=True)
                    if val is not None:
                        extracted_list.append(str(val).strip())
                record[field_name] = extracted_list
            else:
                el = matching_elements[0]
                val = el.get(attribute) if attribute else el.get_text(strip=True)
                record[field_name] = str(val).strip() if val is not None else default_val

        return record
