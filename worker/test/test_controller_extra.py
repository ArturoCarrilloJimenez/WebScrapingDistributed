import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from scraping.controller import WorkerController
from scraping.exceptions import ScrapingError, ErrorCategory
from shared.models.scraping_task import ScrapingTask

pytestmark = pytest.mark.asyncio


@pytest.fixture
def controller():
    consumer = AsyncMock()
    parser_factory = MagicMock()
    buffer_service = AsyncMock()
    return WorkerController(
        consumer=consumer,
        parser_factory=parser_factory,
        buffer_service=buffer_service,
        max_concurrency=2
    )


async def test_run_cycle_backoff_sleep_and_increase(controller):
    # Mock consumer to return no tasks
    controller.consumer.fetch.return_value = []
    
    # First cycle: should increase backoff to 2.0
    await controller._run_cycle()
    assert controller.current_backoff == 2.0
    
    # Mock sleep so we don't actually wait
    with patch("scraping.controller.asyncio.sleep") as mock_sleep:
        # Second cycle: should sleep and increase backoff to 4.0
        await controller._run_cycle()
        mock_sleep.assert_called_once_with(2.0)
        assert controller.current_backoff == 4.0


async def test_execute_shutdown_with_active_tasks(controller):
    # Create a dummy active task
    async def dummy_task_fn():
        await asyncio.sleep(0.01)
        
    task = asyncio.create_task(dummy_task_fn())
    controller._active_tasks.add(task)
    task.add_done_callback(controller._active_tasks.discard)
    
    # Run shutdown
    await controller._execute_shutdown()
    
    # Active tasks should be empty/done
    assert len(controller._active_tasks) == 0
    assert task.done()


async def test_run_loop_exception_handling(controller):
    # Mock _run_cycle to raise an exception
    controller._run_cycle = AsyncMock(side_effect=Exception("cycle error"))
    
    real_sleep = asyncio.sleep
    async def mock_sleep_fn(delay):
        await real_sleep(0.001)
        
    # Patch sleep to avoid waiting 5 seconds
    with patch("scraping.controller.asyncio.sleep", side_effect=mock_sleep_fn) as mock_sleep:
        # Run loop in a background task
        run_task = asyncio.create_task(controller.run())
        
        # Give it a moment to run one iteration and raise exception
        await real_sleep(0.02)
        
        # Stop the engine
        controller.stop()
        
        try:
            await run_task
        except asyncio.CancelledError:
            pass
            
        mock_sleep.assert_any_call(5)


async def test_process_task_wrapper_stops_if_not_running(controller):
    controller.running = False
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    # This should return immediately and not call get_parser
    await controller._process_task_wrapper(task)
    controller.parser_factory.get_parser.assert_not_called()


async def test_process_task_wrapper_errors(controller):
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    # Scenario A: Parser raises ScrapingError
    mock_parser = AsyncMock()
    mock_parser.parse.side_effect = ScrapingError(ErrorCategory.TIMEOUT, "Timeout", "task_1")
    controller.parser_factory.get_parser.return_value = mock_parser
    
    with patch.object(controller, "_handle_scraping_error", new_callable=AsyncMock) as mock_handle:
        await controller._process_task_wrapper(task)
        mock_handle.assert_called_once()
        
    # Scenario B: Parser raises generic Exception
    mock_parser.parse.side_effect = ValueError("generic value error")
    # This should log the error and complete without crashing
    await controller._process_task_wrapper(task)


async def test_gather_batch_multiple_items(controller):
    task1 = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    task2 = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_2", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    await controller.ack_queue.put(task1)
    await controller.ack_queue.put(task2)
    
    batch = await controller._gather_batch()
    # Should gather both items (covering line 171)
    assert len(batch) == 2
    assert batch[0] == task1
    assert batch[1] == task2


async def test_process_batch_catastrophic_failure(controller):
    task = ScrapingTask(
        job_id="job_1", batch_id="batch_1", task_id="task_1", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    controller.consumer.acknowledge_batch.side_effect = RuntimeError("Catastrophic error")
    # This should log error and not raise exception
    await controller._process_batch([task])
    controller.consumer.acknowledge_batch.assert_called_once()


async def test_stop_when_already_stopped(controller):
    controller.running = False
    # This should return immediately
    controller.stop()
    assert controller._main_task is None
