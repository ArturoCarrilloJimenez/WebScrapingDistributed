from typing import Any, List

from pydantic import BaseModel
from shared.models.scraping_task import ScrapingTask


class ParseResult(BaseModel):
    task: ScrapingTask
    data: Any


class Buffer(BaseModel):
    first_inserted_at: float
    current_bytes_size: int
    records: List[str]
    tasks: List[ScrapingTask]
