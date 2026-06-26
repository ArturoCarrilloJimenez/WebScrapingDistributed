import datetime
from typing import Any, List

from pydantic import BaseModel


from shared.models.scraping_task import ScrapingTask


class S3BatchFile(BaseModel):
    key: str
    size: int
    last_modified: datetime.datetime


class ListOfJobs(BaseModel):
    prefix: str
    total_tasks: int
    total_bytes: int
    last_modified: datetime.datetime
    inactive_time: datetime.timedelta
    batches: List[S3BatchFile]


class ParseResult(BaseModel):
    task: ScrapingTask
    data: Any


class ClearResult(BaseModel):
    task_id: str
    url: str
    date: datetime.datetime
    data: Any