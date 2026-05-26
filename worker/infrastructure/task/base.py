from abc import ABC, abstractmethod
from typing import List

class BaseConsumer[T](ABC):
    @abstractmethod
    async def fetch(self, batch_size: int) -> List[T]:
        """Obtiene una ráfaga de tareas del broker."""
        pass

    @abstractmethod
    async def acknowledge(self, task: T):
        """Confirma que la tarea se procesó con éxito (Delete/Commit)."""
        pass

    @abstractmethod
    async def acknowledge_batch(self, tasks: List[T]):
        """Confirma que las tareas se procesaron con éxito (Delete/Commit)."""
        pass

    @abstractmethod
    async def heartbeat(self, task: T):
        """Mantiene la tarea bloqueada para este worker (Keep-alive)."""
        pass

    @abstractmethod
    async def close(self):
        """Cierra conexiones de forma limpia."""
        pass