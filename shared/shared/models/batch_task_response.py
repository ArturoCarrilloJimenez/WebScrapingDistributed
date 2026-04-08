from typing import List
from pydantic import HttpUrl
from pydantic import Field
from pydantic import BaseModel


class SummaryBatchResponse(BaseModel):
    total: int = Field(default=0, description="Numero total de tareas")

    processed: int = Field(default=0, description="Numero total de tareas procesadas")

    failed: int = Field(default=0, description="Numero total de tareas fallidas")


class ErrorsBatchResponse(BaseModel):
    task_id: str = Field(
        ...,
        description="ID único de la tarea",
    )

    url: HttpUrl = Field(..., description="URL validada a extraer")

    reason: str = Field(
        ...,
        description="Motivo del error",
    )

    retryable: bool = Field(
        default=True,
        description="Indicador para fallos puntuales que se puedan repetir",
    )


class BatchResponse(BaseModel):
    batch_id: str = Field(
        ..., description="ID del late de de la tarea para deduplicación"
    )

    summary: SummaryBatchResponse = Field(description="Resumen de que ha procesado")

    errors: List[ErrorsBatchResponse] = Field(
        default_factory=list, description="Lista con las tareas fallidas"
    )
