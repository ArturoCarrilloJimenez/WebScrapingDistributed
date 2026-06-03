import pytest
from unittest.mock import AsyncMock, MagicMock
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from shared.models import ScrapingTask

pytestmark = pytest.mark.asyncio

async def test_producer_adapter_batch_limit_exceeded():
    """Valida que intentar enviar un lote de más de 10 tareas levante ValueError inmediatamente."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    # Creamos un lote ficticio de 11 tareas (límite es 10)
    tasks = [
        ScrapingTask(
            job_id="j", batch_id="b", task_id=f"t-{i}", url=f"https://x.com/{i}",
            parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
        )
        for i in range(11)
    ]
    
    with pytest.raises(ValueError) as exc_info:
        await adapter.send_batch(tasks)
        
    assert "Lote demasiado grande" in str(exc_info.value)


async def test_producer_adapter_handles_partial_failures():
    """Valida que si SQS devuelve fallos parciales al encolar, se registren correctamente."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    # Mockear el cliente asíncrono
    mock_client = AsyncMock()
    # Una tarea tuvo éxito, la otra falló con SenderFault=True (no reintentable), y otra con SenderFault=False
    mock_client.send_message_batch.return_value = {
        "Successful": [{"Id": "t-01", "MessageId": "msg-01", "MD5OfMessageBody": "md5"}],
        "Failed": [
            {
                "Id": "t-02",
                "Code": "InvalidMessage",
                "Message": "Supera el límite de tamaño de mensaje",
                "SenderFault": True # Error de cliente -> no reintentable
            },
            {
                "Id": "t-03",
                "Code": "InternalError",
                "Message": "Caída del broker SQS temporal",
                "SenderFault": False # Error del servidor -> reintentable
            }
        ]
    }
    adapter._get_client = AsyncMock(return_value=mock_client)
    
    tasks = [
        ScrapingTask(job_id="j", batch_id="b", task_id="t-01", url="https://x.com/1", parser_type="static_css", parser_config={"selectors": {"h": "h1"}}),
        ScrapingTask(job_id="j", batch_id="b", task_id="t-02", url="https://x.com/2", parser_type="static_css", parser_config={"selectors": {"h": "h1"}}),
        ScrapingTask(job_id="j", batch_id="b", task_id="t-03", url="https://x.com/3", parser_type="static_css", parser_config={"selectors": {"h": "h1"}})
    ]
    
    response = await adapter.send_batch(tasks)
    
    assert response.summary.total == 3
    assert response.summary.processed == 1
    assert response.summary.failed == 2
    
    # Comprobar el detalle de errores
    errors = response.errors
    assert len(errors) == 2
    
    # Error 1 (t-02, SenderFault=True)
    err1 = next(e for e in errors if e.task_id == "t-02")
    assert err1.retryable is False
    assert "InvalidMessage" in err1.reason
    
    # Error 2 (t-03, SenderFault=False)
    err2 = next(e for e in errors if e.task_id == "t-03")
    assert err2.retryable is True
    assert "InternalError" in err2.reason


async def test_producer_adapter_close_cleanly():
    """Valida el método de apagado limpio del cliente de red."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    mock_client = AsyncMock()
    adapter._client = mock_client
    
    await adapter.close()
    mock_client.__aexit__.assert_called_once()
