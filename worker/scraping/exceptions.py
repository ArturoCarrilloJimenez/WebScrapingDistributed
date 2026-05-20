from enum import Enum

class ErrorCategory(Enum):
    # Errores fatales (No reintentar, ir directo a DLQ o descarte)
    NOT_FOUND = "NOT_FOUND"                 # HTTP 404, recurso inexistente
    INVALID_SCHEMA = "INVALID_SCHEMA"       # DOM cambiado, selectores rotos, listas vacías inválidas

    # Errores recuperables (Requieren Backoff e intervención a nivel de Worker/Proxy)
    BLOCKED = "BLOCKED"                     # HTTP 403, 429, Captchas detectados
    TIMEOUT = "TIMEOUT"                     # El servidor tardó en responder, caídas de DNS locales
    SERVER_ERROR = "SERVER_ERROR"           # HTTP 500, 502, 503, 504 (Fallos temporales del destino)


class ScrapingError(Exception):
    """Única excepción que el WorkerController está autorizado a capturar."""
    def __init__(self, category: ErrorCategory, message: str, task_id: str, original_error: str = None):
        super().__init__(message)
        self.category = category
        self.task_id = task_id
        self.original_error = original_error or message