from enum import StrEnum


class RobotsStatus(StrEnum):
    """
    Estados posibles tras la evaluación de cumplimiento normativo de robots.txt.
    """

    BYPASS = "BYPASS"
    ALLOWED = "ALLOWED"
    NOT_FOUND = "NOT_FOUND"
    DISALLOWED = "DISALLOWED"
