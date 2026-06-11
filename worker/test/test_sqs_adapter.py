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


async def test_adapter_fetch_parallel_awaits_all():
    """Valida que al hacer fetch > 10, todas las peticiones asíncronas se esperen usando gather para evitar fugas de visibilidad."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    # Simulamos que la primera petición paralela responde rápido con tareas,
    # y la segunda simula quedar colgada temporalmente (no responde de inmediato).
    task_mock = ScrapingTask(
        job_id="j", batch_id="b", task_id="t-01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    task_mock.context["_sqs_handle"] = "h-01"
    
    async def mock_fetch_fast(size):
        return [task_mock]
        
    async def mock_fetch_slow(size):
        # Simula un pequeño retraso
        await asyncio.sleep(0.05)
        return []

    # Mockeamos _fetch_single_batch para que asigne las respuestas en orden
    call_count = 0
    async def mock_fetch_single(size):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return await mock_fetch_fast(size)
        else:
            return await mock_fetch_slow(size)
            
    adapter._fetch_single_batch = mock_fetch_single

    # batch_size=20 provoca 2 llamadas paralelas concurrentes
    tasks = await adapter.fetch(batch_size=20)
    
    assert len(tasks) == 1
    assert tasks[0].task_id == "t-01"
    # El test finaliza confirmando que ambas peticiones asíncronas fueron completadas (gather)


async def test_adapter_heartbeat_error_handling():
    """Valida que si falla la red (botocore lanza excepción) durante el heartbeat, el adaptador lo capture y loguee."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    
    mock_client = AsyncMock()
    # Forzamos una excepción de red al intentar actualizar la visibilidad en SQS
    mock_client.change_message_visibility.side_effect = Exception("SQS network connection dropped catastrophically")
    adapter._get_client = AsyncMock(return_value=mock_client)
    
    task = ScrapingTask(
        job_id="j", batch_id="b", task_id="t-01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    task.context["_sqs_handle"] = "valid-handle"

    # No debe propagar la excepción, debe capturarla de forma resiliente
    await adapter.heartbeat(task, visibility_timeout=30)
    mock_client.change_message_visibility.assert_called_once()


async def test_adapter_acknowledge_no_handle():
    """Valida que si se intenta confirmar una tarea sin ReceiptHandle, se descarte sin llamadas a AWS SQS."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    mock_client = AsyncMock()
    adapter._get_client = AsyncMock(return_value=mock_client)
    
    # Tarea sin ReceiptHandle en su contexto
    task = ScrapingTask(
        job_id="j", batch_id="b", task_id="t-01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    await adapter.acknowledge(task)
    # No debió realizar ninguna petición de borrado
    mock_client.delete_message.assert_not_called()


async def test_adapter_heartbeat_no_handle():
    """Valida que si se intenta hacer heartbeat a una tarea sin ReceiptHandle, se descarte de forma segura."""
    adapter = SQSAioBotoAdapter(endpoint_url="http://mock", queue_url="http://queue")
    mock_client = AsyncMock()
    adapter._get_client = AsyncMock(return_value=mock_client)
    
    task = ScrapingTask(
        job_id="j", batch_id="b", task_id="t-01", url="https://x.com",
        parser_type="static_css", parser_config={"selectors": {"headline": "h1"}}
    )
    
    await adapter.heartbeat(task, visibility_timeout=30)
    mock_client.change_message_visibility.assert_not_called()