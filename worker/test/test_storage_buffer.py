import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from scraping.services.storage_buffer import JobBufferService
from scraping.interfaces.interfaces import ParseResult
from shared.models.scraping_task import ScrapingTask
from infrastructure.storage.base import BaseStorageRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_repository():
    repo = AsyncMock(spec=BaseStorageRepository)
    repo.save = AsyncMock()
    repo.close = AsyncMock()
    return repo


@pytest.fixture
def ack_queue():
    return asyncio.Queue()


async def test_job_buffer_service_init(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    assert service.repository == mock_repository
    assert service.ack_queue == ack_queue
    assert service._ticker_task is None
    assert len(service._buffers) == 0


async def test_add_record_lazy_initialization(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result = ParseResult(task=task, data={"titles": ["Title 1"]})

    await service.add_record(result)

    assert "job_1" in service._buffers
    buffer = service._buffers["job_1"]
    assert len(buffer.records) == 1
    assert len(buffer.tasks) == 1
    assert buffer.tasks[0] == task
    assert service._ticker_task is not None
    
    # Cleanup ticker loop
    await service.close()


async def test_add_record_serialization_error(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result = ParseResult(task=task, data={"titles": ["Title 1"]})

    # Mock ParseResult.model_dump_json class method to avoid __delattr__ on Pydantic instances
    with patch("scraping.interfaces.interfaces.ParseResult.model_dump_json", side_effect=ValueError("Dump error")):
        await service.add_record(result)

    # Since it failed serialisation, no buffer should be created/initialized
    assert "job_1" not in service._buffers
    await service.close()


async def test_add_record_size_exceeded_flushes(mock_repository, ack_queue):
    # Set max_bytes to a tiny limit (e.g., 50 bytes)
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue, max_bytes=50)
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result = ParseResult(task=task, data={"titles": ["Title 1"]})

    # We add a record that exceeds 50 bytes. This should trigger flush immediately.
    await service.add_record(result)

    # Wait for the background task of the flush to finish
    await asyncio.sleep(0.05)

    # Verify S3 upload was called
    mock_repository.save.assert_called_once()
    # Verify SQS ack_queue has the task
    assert ack_queue.qsize() == 1
    assert await ack_queue.get() == task
    assert "job_1" not in service._buffers
    
    await service.close()


async def test_add_record_time_expired_flushes(mock_repository, ack_queue):
    # Set max_seconds to a tiny limit (e.g., 0.01 seconds)
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue, max_seconds=0.01)
    
    task1 = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result1 = ParseResult(task=task1, data={"titles": ["Title 1"]})
    
    task2 = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_2", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result2 = ParseResult(task=task2, data={"titles": ["Title 2"]})

    # Add first record
    await service.add_record(result1)
    
    # Wait until max_seconds is exceeded
    await asyncio.sleep(0.02)
    
    # Adding another record should trigger flush of the buffer containing both records
    await service.add_record(result2)

    await asyncio.sleep(0.05)

    # The buffer should have been flushed (saving both tasks)
    mock_repository.save.assert_called_once()
    assert ack_queue.qsize() == 2
    assert await ack_queue.get() == task1
    assert await ack_queue.get() == task2
    assert "job_1" not in service._buffers
    
    await service.close()


async def test_flush_buffer_to_storage_empty(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    from scraping.interfaces.interfaces import Buffer
    empty_buffer = Buffer(
        first_inserted_at=time.time(),
        current_bytes_size=0,
        records=[],
        tasks=[]
    )
    
    await service._flush_buffer_to_storage("job_1", empty_buffer)
    
    mock_repository.save.assert_not_called()
    assert ack_queue.qsize() == 0
    await service.close()


async def test_flush_buffer_to_storage_repository_fails(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    from scraping.interfaces.interfaces import Buffer
    buffer = Buffer(
        first_inserted_at=time.time(),
        current_bytes_size=100,
        records=["{}"],
        tasks=[task]
    )
    
    # Mock save to raise an exception
    mock_repository.save.side_effect = RuntimeError("S3 Upload Failed")
    
    # Call flush. It should log error and not push to ack_queue
    await service._flush_buffer_to_storage("job_1", buffer)
    
    mock_repository.save.assert_called_once()
    assert ack_queue.qsize() == 0  # Tasks not confirmed!
    await service.close()


async def test_ticker_loop_flushes_inactive(mock_repository, ack_queue):
    # Set a tiny max_seconds limit
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue, max_seconds=0.01)
    
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    result = ParseResult(task=task, data={"titles": ["Title 1"]})

    real_sleep = asyncio.sleep

    # Define an async function side effect for sleep
    async def mock_sleep_fn(delay):
        if delay == 5.0:
            await real_sleep(0.01)
        else:
            await real_sleep(delay)

    # Mock asyncio.sleep inside storage_buffer to run the ticker loop quickly
    with patch("scraping.services.storage_buffer.asyncio.sleep", side_effect=mock_sleep_fn):
        # Adding record starts the ticker loop
        await service.add_record(result)
        
        # Wait for the ticker loop to execute and flush
        await asyncio.sleep(0.05)

    # Verify S3 upload was automatically called by the ticker
    mock_repository.save.assert_called_once()
    assert ack_queue.qsize() == 1
    assert await ack_queue.get() == task
    assert "job_1" not in service._buffers
    
    await service.close()


async def test_close_awaits_background_tasks(mock_repository, ack_queue):
    service = JobBufferService(repository=mock_repository, ack_queue=ack_queue)
    
    # Create a background task that takes some time to simulate network I/O
    async def slow_upload():
        await asyncio.sleep(0.05)
        
    task = asyncio.create_task(slow_upload())
    service._background_tasks.add(task)
    task.add_done_callback(service._background_tasks.discard)
    
    # Now call close, which should await the background tasks
    await service.close()
    
    assert task.done()
    assert len(service._background_tasks) == 0
