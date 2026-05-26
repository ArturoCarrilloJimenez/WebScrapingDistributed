from abc import ABC, abstractmethod
from typing import Any, Dict
from shared.models import ScrapingTask
from shared.logging import Logger


class BaseParser(ABC):
    def __init__(self):
        self.log = Logger(self.__class__.__name__)

    @abstractmethod
    async def parse(self, task: ScrapingTask) -> Dict[str, Any]:
        """
        Lógica de extracción pura.
        Recibe la tarea validada y devuelve los datos limpios.
        """
        pass

    def _prepare_result(self, task: ScrapingTask, data: Any) -> Dict[str, Any]:
        """Estandariza la salida para subir los datos a S3 o el que corresponda."""
        return {
            "job_id": task.job_id,
            "task_id": task.task_id,
            "url": task.url,
            "data": data,
            "parser_type": task.parser_type.value
        }
