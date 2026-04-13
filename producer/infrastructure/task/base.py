from shared import BatchResponse, ScrapingTask
from abc import ABC, abstractmethod
from typing import List


class TaskProducer(ABC):
    @abstractmethod
    async def send_batch(self, tasks: List[ScrapingTask]) -> BatchResponse:
        """Envía una lista de tareas de forma asíncrona."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cierra conexiones si es necesario."""
        pass
