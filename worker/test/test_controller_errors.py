import pytest
from unittest.mock import AsyncMock, MagicMock
from scraping.controller import WorkerController
from scraping.exceptions import ScrapingError, ErrorCategory
from shared.models import ScrapingTask

pytestmark = pytest.mark.asyncio


@pytest.fixture
def controller():
    return WorkerController(consumer=AsyncMock(), parser_factory=MagicMock())


async def test_handle_error_fatal_sends_to_ack_queue(controller):
    """Errores fatales (NOT_FOUND) deben ir directo a ACK para morir."""
    task = ScrapingTask(
        job_id="j_01",
        batch_id="b_01",
        task_id="t_01",
        url="https://x.com",
        parser_type="static_css",
        # <- CONTRATO VÁLIDO
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=0,
        max_retries=3
    )
    error = ScrapingError(ErrorCategory.NOT_FOUND, "Not Found", "t_01")

    await controller._handle_scraping_error(task, error)

    assert controller.ack_queue.qsize() == 1  # Forzó el ACK inmediato


async def test_handle_error_exhausted_retries_sends_to_dlq(controller):
    """Si supera el máximo de reintentos, se envía a ACK (para morir/DLQ)."""
    task = ScrapingTask(
        job_id="j_01",
        batch_id="b_01",
        task_id="t_01",
        url="https://x.com",
        parser_type="static_css",
        # <- CONTRATO VÁLIDO
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=3,
        max_retries=3
    )
    error = ScrapingError(ErrorCategory.TIMEOUT, "Timeout", "t_01")

    await controller._handle_scraping_error(task, error)

    assert controller.ack_queue.qsize() == 1


async def test_handle_error_backoff_strategies(controller):
    """Valida los diferentes cálculos de backoff y llamadas a heartbeat."""
    task = ScrapingTask(
        job_id="j_01",
        batch_id="b_01",
        task_id="t_01",
        url="https://x.com",
        parser_type="static_css",
        # <- CONTRATO VÁLIDO
        parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=1,
        max_retries=5
    )
    task.context["_current_retries"] = 1

    # Escenario A: TIMEOUT -> delay = 5 * retry_count (5 * 2 = 10s tras incremento)
    error_timeout = ScrapingError(ErrorCategory.TIMEOUT, "Timeout", "t_01")
    await controller._handle_scraping_error(task, error_timeout)
    controller.consumer.heartbeat.assert_called_with(
        task, visibility_timeout=10)

    # Escenario B: BLOCKED (Antibot) -> delay agresivo min(30 * 2^retry_count, 300)
    # retry_count sube a 3 -> 30 * 8 = 240s
    error_blocked = ScrapingError(ErrorCategory.BLOCKED, "Blocked", "t_01")
    await controller._handle_scraping_error(task, error_blocked)
    controller.consumer.heartbeat.assert_called_with(
        task, visibility_timeout=240)


async def test_handle_error_server_500_strategy(controller):
    """Valida que los errores 5xx apliquen un backoff proporcional de 15s."""
    task = ScrapingTask(
        job_id="j_01", batch_id="b_01", task_id="t_01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1.title"}},
        retry_count=1, max_retries=5
    )
    error_500 = ScrapingError(ErrorCategory.SERVER_ERROR, "Internal Server Error", "t_01")
    
    await controller._handle_scraping_error(task, error_500)
    
    # 15 * retry_count (tras incremento interno sube a 2 -> 15 * 2 = 30s)
    controller.consumer.heartbeat.assert_called_with(task, visibility_timeout=30)