from typing import Any, Dict, List, Optional
from pydantic import BaseModel, HttpUrl, Field, field_validator, ConfigDict, model_validator
from uuid import uuid4
import re

from shared import ParserType, ParserValidatedMixin


class TaskModel(BaseModel, ParserValidatedMixin):
    url: HttpUrl = Field(..., min_length=1,
                         max_length=100000, description="URL a scrapear")
    parser_type: ParserType = Field(..., min_length=1)
    parser_config: Dict[str, Any] = Field(
        default_factory=dict, validate_default=True)
    priority: int = Field(default=1, ge=1, le=10)
    max_depth: int = Field(default=1, ge=0)
    max_retries: int = Field(default=3, ge=0)

    @field_validator("url", mode="after")
    @classmethod
    def convert_url_to_string(cls, v: HttpUrl) -> str:
        return str(v)


class BulkTaskRequest(BaseModel):
    # Configuración de Pydantic V2
    model_config = ConfigDict(
        str_strip_whitespace=True, arbitrary_types_allowed=True)

    job_id: Optional[str] = Field(
        default_factory=lambda: f"job-{uuid4().hex[:8]}",
        description="ID de la misión alfanumérico.",
    )

    tasks: List[TaskModel] = Field(
        ..., min_length=1, max_length=1000, description="Lista de tareas a ejecutar"
    )

    context: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, v: str) -> str:
        if v and not re.match(r"^[a-zA-Z0-9\-_]+$", v):
            raise ValueError("job_id contiene caracteres no permitidos")
        return v
