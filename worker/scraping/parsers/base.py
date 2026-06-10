from abc import ABC, abstractmethod
from typing import Any
from shared.models import ScrapingTask
from shared.logging import Logger

from scraping.interfaces.interfaces import ParseResult


class BaseParser(ABC):
    def __init__(self):
        self.log = Logger(self.__class__.__name__)

    @abstractmethod
    async def parse(self, task: ScrapingTask) -> ParseResult:
        """
        Lógica de extracción pura.
        Recibe la tarea validada y devuelve los datos limpios.
        """
        pass

    def _prepare_result(self, task: ScrapingTask, data: Any) -> ParseResult:
        """Estandariza la salida para subir los datos a S3 o el que corresponda."""
        return ParseResult(
            task=task,
            data=data
        )
