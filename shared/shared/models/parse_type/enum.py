from enum import Enum


class ParserType(str, Enum):
    """
    Tipos de parsers soportados por el sistema.
    """
    STATIC_CSS = "static_css"
    DINAMIC_PLAYWRIGHT = "dynamic_playwright"
