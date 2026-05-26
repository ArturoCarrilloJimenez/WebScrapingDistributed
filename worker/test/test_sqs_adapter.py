import pytest
from unittest.mock import AsyncMock, MagicMock
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter
from shared.models import ScrapingTask

pytestmark = pytest.mark.asyncio


async def test_adapter_fetch_handles_poison_pill_and_deletes_it():
    """Valida que un JSON corrupto en SQS ejecute el bloque ValidationError y lo borre."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    # Simulamos que SQS devuelve un mensaje con un Body totalmente roto (rompe Pydantic)
    mock_client = AsyncMock()
    mock_client.receive_message.return_value = {
        "Messages": [{
            "Body": '{"job_id": "missing_fields_corrupt_json"}',
            "ReceiptHandle": "poison-handle",
            "MessageId": "msg-01"
        }]
    }
    adapter._get_client = AsyncMock(return_value=mock_client)

    tasks = await adapter.fetch(batch_size=1)

    assert len(tasks) == 0  # El mensaje corrupto se descartó limpiamente
    # Comprobamos que el adaptador ejecutó la llamada defensiva de borrado inmediato en AWS
    mock_client.delete_message.assert_called_once_with(
        QueueUrl="http://queue",
        ReceiptHandle="poison-handle"
    )


async def test_adapter_acknowledge_batch_logs_partial_failures():
    """Valida el comportamiento cuando SQS responde con errores parciales en el borrado."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    mock_client = AsyncMock()
    # Simulamos la respuesta estructural de AWS cuando un mensaje del lote no pudo eliminarse
    mock_client.delete_message_batch.return_value = {
        "Failed": [{"Id": "task_failed_01", "Code": "ReceiptHandleIsInvalid", "SenderFault": True}]
    }
    adapter._get_client = AsyncMock(return_value=mock_client)

    task = ScrapingTask(
        job_id="j", batch_id="b", task_id="task_failed_01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    task.context["_sqs_handle"] = "valid-handle"

    # No debe lanzar excepción, debe procesar la respuesta, leer el nodo 'Failed' y loguearlo
    await adapter.acknowledge_batch([task])
    mock_client.delete_message_batch.assert_called_once()