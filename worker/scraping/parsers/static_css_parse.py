from typing import Any, Dict
from bs4 import BeautifulSoup
from scraping.exceptions import ErrorCategory, ScrapingError
from .base import BaseParser
from shared.models import ScrapingTask
from infrastructure.network.client import SecureNetworkClient


class StaticCSSParser(BaseParser):
    def __init__(self, network_client: SecureNetworkClient):
        super().__init__()
        self.network_client = network_client

    async def parse(self, task: ScrapingTask) -> Dict[str, Any]:
        """Parser asíncrono universal para HTML estático libre de bloqueos TLS 403."""
        selectors = task.parser_config.get("selectors", {})

        # Obtenemos o reutilizamos la sesión persistente del Pool
        session = await self.network_client.get_session(str(task.url), task.context.get("sticky_session_id"))
        self.log.info(
            f"Infiltración TLS Keep-Alive activa. Descargando: {task.url} | Tarea ID: {task.task_id}")

        try:
            response = await session.get(str(task.url), allow_redirects=True)

            if response.status_code == 404:
                raise ScrapingError(
                    ErrorCategory.NOT_FOUND, "Página no existe", task.task_id)
            if response.status_code in [403, 429]:
                raise ScrapingError(
                    ErrorCategory.BLOCKED, "IP bloqueada o Rate Limit", task.task_id)
            if response.status_code >= 500:
                raise ScrapingError(
                    ErrorCategory.SERVER_ERROR, f"Fallo del servidor destino: {response.status_code}", task.task_id)

            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            extracted = {}
            is_empty = []  # Bandera para detectar si no se extrajo nada

            for field, selector in selectors.items():
                elements = soup.select(selector)
                extracted[field] = [el.get_text(
                    strip=True) for el in elements]
                if elements:
                    is_empty.append(False)
                else:
                    is_empty.append(True)

            # Si todos los selectores resultaron en listas vacías, consideramos que el esquema es inválido (posible cambio de DOM)
            if not selectors or all(is_empty):
                raise ScrapingError(
                    ErrorCategory.INVALID_SCHEMA, "Selectores no extrajeron datos (posible cambio de DOM)", task.task_id)

        except ScrapingError:
            raise
        except Exception as e:
            self.log.error(
                f"Error al procesar la tarea {task.task_id}: {str(e)}")
            raise ScrapingError(
                ErrorCategory.SERVER_ERROR, "Error inesperado durante el scraping", task.task_id, original_error=str(e))

        return self._prepare_result(task, extracted)
