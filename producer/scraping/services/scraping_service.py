from shared import SummaryBatchResponse
from typing import List
from typing import Any
from scraping.models.bulk_task import BulkTaskRequest
from shared import ScrapingTask, BatchResponse
from infrastructure.task.base import TaskProducer

import asyncio
from uuid import uuid4


class ScrapingOrchestrator:
    def __init__(self, adapter: TaskProducer):
        self.adapter = adapter
        # Solo 20 lotes (200 URLs) volando simultáneamente para no saturar la red
        self._semaphore = asyncio.Semaphore(20)

    async def create_tasks_from_request(
        self, request: BulkTaskRequest
    ) -> BatchResponse:
        unique_urls = list(set(request.urls))

        return await self._process_in_parallel(unique_urls, request, request.job_id)

    def _map_to_task(
        self, batch_id: str, url: Any, request: BulkTaskRequest
    ) -> ScrapingTask:
        return ScrapingTask(
            job_id=request.job_id,
            batch_id=batch_id,
            url=url,
            parser_type=request.parser_type,
            parser_config=request.parser_config,
            priority=request.priority,
            max_depth=request.max_depth,
            max_retries=request.max_retries,
            context=request.context,
        )

    async def _process_in_parallel(
        self, urls: List[Any], request: List[BulkTaskRequest], job_id: str
    ) -> BatchResponse:
        # Dividimos el millón de URLs en trozos de 10 (lo que acepta SQS)
        url_chunks = [urls[i : i + 10] for i in range(0, len(urls), 10)]

        str(uuid4())
        send_tasks = []
        for chunk in url_chunks:
            batch_id = str(uuid4())
            # Envolvemos cada envío en el semáforo para no saturar la red
            tasks_in_batch = [
                self._map_to_task(batch_id, url, request) for url in chunk
            ]

            send_tasks.append(self.adapter.send_batch(tasks_in_batch))

        # Ejecutamos todo y esperamos los reportes de cada lote
        batch_reports = await asyncio.gather(*send_tasks)

        return self._merge_reports(batch_reports, job_id, len(urls))

    def _merge_reports(
        self, reports: List[BatchResponse], job_id: str, total: int
    ) -> BatchResponse:
        final_processed = sum(r.summary.processed for r in reports)
        final_failed = sum(r.summary.failed for r in reports)
        all_errors = [error for r in reports for error in r.errors]

        return BatchResponse(
            batch_id=job_id,
            summary=SummaryBatchResponse(
                total=total, processed=final_processed, failed=final_failed
            ),
            errors=all_errors,
        )
