from abc import ABC, abstractmethod

class BaseStorageRepository(ABC):
    @abstractmethod
    async def save(self, key: str, body: str | bytes) -> None:
        """
        Persiste un payload de forma asíncrona e idempotente en una coordenada (Key) específica.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Libera los recursos del pool y cierra los sockets de conexión de forma limpia.
        """
        pass