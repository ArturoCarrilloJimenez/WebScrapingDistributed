import logging
import sys
import json
import datetime
from typing import Any, Dict, Optional
from uuid import UUID
from decimal import Decimal


class StyleFormatter(logging.Formatter):
    # (Tus colores se mantienen igual...)
    LEVEL_COLORS = {
        logging.DEBUG: "\x1b[38;5;244m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }
    RESET = "\x1b[0m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        level_name = f"{color}{record.levelname:<8s}{self.RESET}"
        now = datetime.datetime.now()
        logger_name = f"{self.MAGENTA}{record.name}{self.RESET}"

        return f" {level_name} | {now} | {logger_name} | {record.getMessage()}"


class Logger:
    def __init__(self, name: str, level: int = logging.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False

        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(StyleFormatter())
            self.logger.addHandler(handler)

    @staticmethod
    def _universal_serializer(obj: Any) -> Any:
        """Manejador de seguridad para Pydantic, fechas y objetos complejos."""
        if hasattr(obj, "model_dump"):  # Pydantic v2
            return obj.model_dump()
        if hasattr(obj, "dict"):  # Pydantic v1
            return obj.dict()
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, (UUID, Decimal)):
            return str(obj)
        try:
            return repr(obj)  # Último recurso: representación en string
        except Exception:
            return "<Unserializable Object>"

    def _build_msg(self, msg: str, context: Optional[Dict[str, Any]]) -> str:
        """Serialización segura con fallback ante fallos."""
        if not context:
            return msg

        try:
            # Usamos el serializador universal como 'default'
            ctx_json = json.dumps(
                context, default=self._universal_serializer, ensure_ascii=False
            )
        except Exception as e:
            # Si incluso el serializador falla (ej. recursión infinita),
            # enviamos un mensaje de emergencia para no tirar el proceso.
            ctx_json = f'{{"log_error": "Serialization failed", "raw": "{str(context)[:100]}"}}'

        return f"{msg} | \x1b[36m{ctx_json}\x1b[0\n"

    # Métodos de log (info, error, etc.) se mantienen igual llamando a self._build_msg
    def info(self, msg: str, context: Optional[Dict[str, Any]] = None):
        self.logger.info(self._build_msg(msg, context))

    def error(self, msg: str, context: Optional[Dict[str, Any]] = None):
        self.logger.error(self._build_msg(msg, context))

    def warning(self, msg: str, context: Optional[Dict[str, Any]] = None):
        self.logger.warning(self._build_msg(msg, context))

    def debug(self, msg: str, context: Optional[Dict[str, Any]] = None):
        self.logger.debug(self._build_msg(msg, context))
