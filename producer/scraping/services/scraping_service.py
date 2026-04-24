from shared.logging import Logger
from typing import List, Any

from shared import SummaryBatchResponse, ScrapingTask, BatchResponse
from scraping.models import BulkTaskRequest
from infrastructure.task.base import TaskProducer

import asyncio
from uuid import uuid4

log = Logger("Scraping Tasks Orchestrator")


class ScrapingOrchestrator:
    def __init__(self, adapter: TaskProducer):
        self.adapter = adapter
        # Solo 20 lotes (200 URLs) volando simultáneamente para no saturar la red
        self._semaphore = asyncio.Semaphore(20)

    async def create_tasks_from_request(
        self, request: BulkTaskRequest
    ) -> BatchResponse:
        unique_urls = list(set(request.urls))

        return await self._process_in_parallel(unique_urls, request)

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
        self, urls: List[Any], request: List[BulkTaskRequest]
    ) -> BatchResponse:
        log.info("Iniciamos el procesaminto de tareas", request.job_id)

        queue = asyncio.Queue(maxsize=100)

        # Mapea las tareas
        async def mapper():
            chunks = [urls[i : i + 10] for i in range(0, len(urls), 10)]
            for chunk in chunks:
                batch_id = str(uuid4())
                tasks = [self._map_to_task(batch_id, url, request) for url in chunk]
                await queue.put(tasks)  # Mete el lote mapeado en la cola
                log.info(f"Mapeamos el lote con id {batch_id}")
            await queue.put(None)  # Señal de fin

        # Envia las tareas
        async def sender():
            results = []
            while True:
                batch = await queue.get()
                if batch is None:
                    break

                # Aquí usamos el semáforo para controlar la concurrencia de RED
                async with self._semaphore:
                    res = await self.adapter.send_batch(batch)
                    results.append(res)
                    log.info(f"Enviamos el lote {batch[0].batch_id}")
                queue.task_done()
            return results

        results = await asyncio.gather(
            mapper(), sender()
        )  # Realizamos el procesaminto en parelelo

        log.info("Obtenemos la respuesta", results)

        return self._merge_reports(results[1], request.job_id, len(urls))

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
