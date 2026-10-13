from abc import ABC, abstractmethod


class BaseCacheAdapter(ABC):
    """
    Interfaz abstracta y minimalista para almacenamiento clave-valor (I/O puro).
    """

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Obtiene el valor crudo asociado a la clave o None si no existe."""
        pass

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        """Guarda un valor con un tiempo de vida (TTL) opcional en segundos."""
        pass

    @abstractmethod
    async def incr(self, key: str) -> int:
        """Incrementa atómicamente el valor numérico de la clave y devuelve el nuevo total."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Elimina una clave del almacenamiento."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cierra conexiones o recursos del cliente."""
        pass
