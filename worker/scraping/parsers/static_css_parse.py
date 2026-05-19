from typing import Any, Dict

import httpx
from bs4 import BeautifulSoup
from .base import BaseParser
from shared.models import ScrapingTask

class StaticCSSParser(BaseParser):
    async def parse(self, task: ScrapingTask) -> Dict[str, Any]:
        """Parser básico que usa selectores CSS para extraer datos de HTML estático."""

        selectors = task.parser_config.get("selectors", {})
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            self.log.info(f"Descargando HTML de {task.url}")
            response = await client.get(str(task.url))
            response.raise_for_status()

            # Obtenemos el HTML y aplicamos los selectores CSS
            soup = BeautifulSoup(response.text, "html.parser")
            extracted = {}
            
            for field, selector in selectors.items():
                element = soup.select_one(selector)
                extracted[field] = element.get_text(strip=True) if element else None

            return self._prepare_result(task, extracted)