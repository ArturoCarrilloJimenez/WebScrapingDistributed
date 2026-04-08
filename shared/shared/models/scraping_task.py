from time import timezone
from pydantic import BaseModel, HttpUrl, Field
from typing import Optional, Dict, Any
from uuid import uuid4
from datetime import datetime


class ScrapingTask(BaseModel):
    # Identificadores de Rastreabilidad
    task_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="ID único de la tarea para deduplicación",
    )
    batch_id: str = Field(
        ..., description="ID del late de de la tarea para deduplicación"
    )
    job_id: str = Field(
        ..., description="ID del grupo de scraping (ej: amazon-beauty-2026)"
    )

    # Objetivo
    url: HttpUrl = Field(..., description="URL validada a extraer")

    # Configuración del Motor (Dinámico)
    parser_type: str = Field(
        ...,
        description="Determina el script o lógica a usar (ej: 'static_css', 'playwright_amazon')",
    )
    parser_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Selectores CSS o parámetros para el script dinámico",
    )

    # Control de Flujo y Escalabilidad
    priority: int = Field(default=1, ge=1, le=10, description="Prioridad en SQS (1-10)")
    depth: int = Field(
        default=0, ge=0, description="Nivel de profundidad para modo recursivo"
    )
    max_depth: int = Field(
        default=3, ge=0, description="Límite para evitar bucles infinitos"
    )

    # Resiliencia
    retry_count: int = Field(default=0, description="Contador de reintentos realizados")
    max_retries: int = Field(
        default=3, description="Máximo de intentos antes de ir a la DLQ"
    )

    # Metadata y Auditoría
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    context: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Datos extra que viajan con la tarea (ej: ID de categoría, sesión)",
    )

    class Config:
        # Esto permite que Pydantic convierta tipos complejos (como HttpUrl) a strings fácilmente
        json_encoders = {HttpUrl: lambda v: str(v)}
