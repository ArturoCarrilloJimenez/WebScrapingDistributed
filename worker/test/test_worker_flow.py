import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch
from config.settings import settings

pytestmark = pytest.mark.asyncio


async def test_worker_lifecycle_and_scraping_flow(sqs_mock, worker_controller):
    """
    Test de integración total: Valida que el Worker extraiga un mensaje de SQS,
    lo procese a través del motor asíncrono, invoque al parser y envíe el ACK (borrado).
    """
    # Override concurrency for testing to avoid concurrent SQS fetch requests blocking Moto
    worker_controller.max_concurrency = 5
    worker_controller.semaphore = asyncio.Semaphore(5)

    # 1. Escenario: Publicamos un mensaje real incluyendo todos los campos obligatorios del modelo
    task_payload = {
        "job_id": "job_worker_test_001",
        # <- ESTO TIENE QUE ESTAR SÍ O SÍ EN EL DISCO
        "batch_id": "batch_worker_test_001",
        "task_id": "task_static_001",
        "url": "https://example.com/noticias",
        "parser_type": "static_css",
        "parser_config": {"selectors": {"titles": "h2.entry-title"}},
        "retry_count": 0,
        "max_retries": 3
    }

    sqs_mock.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps(task_payload)
    )

    # 2. Mockear la llamada de red saliente del Parser (mantenemos foco en la infraestructura)
    mock_parser = AsyncMock()
    mock_parser.parse.return_value = {
        "status": "success",
        "data": {"titles": ["Noticia Extraída 1", "Noticia Extraída 2"]}
    }

    with patch.object(worker_controller.parser_factory, "get_parser", return_value=mock_parser):

        # 3. Ejecución: Lanzamos el motor asíncronamente en segundo plano
        worker_task = asyncio.create_task(worker_controller.run())

        # Esperamos dinámicamente a que se procese el mensaje y se llame al parser (máx 8s)
        for _ in range(80):
            if mock_parser.parse.call_count > 0:
                break
            await asyncio.sleep(0.1)

        # 4. Apagado Controlado: Detenemos el motor emulando la parada del contenedor
        worker_controller.stop()

        # Aseguramos que la tarea principal finalice rompiendo limpiamente
        with pytest.raises(asyncio.CancelledError):
            await worker_task

    # 5. Aseveraciones (Assertions)
    # Comprobamos que el Worker extrajo los datos y llamó al parser mapeando el payload correctamente
    mock_parser.parse.assert_called_once()

    # Comprobamos que el flusher asíncrono funcionó: la cola SQS de Moto debe estar vacía
    sqs_response = sqs_mock.receive_message(
        QueueUrl=settings.sqs_queue_url,
        MaxNumberOfMessages=1
    )
    assert "Messages" not in sqs_response, "El mensaje no fue eliminado de SQS por el Flusher"


async def test_worker_loop_skips_when_concurrency_is_maxed(sqs_mock, worker_controller):
    """
    Valida que si no hay slots disponibles (concurrencia máxima alcanzada), 
    el bucle no realiza fetch a SQS, duerme para aliviar CPU y se detiene limpiamente.
    """
    # 1. Escenario: Creamos una tarea dummy de larga duración para saturar el motor
    dummy_task = asyncio.create_task(asyncio.sleep(10))
    worker_controller._active_tasks.add(dummy_task)
    worker_controller.max_concurrency = 1  # Capacidad: 1 | Activas: 1 -> Slots disponibles = 0

    # 2. Función de interrupción controlada en tiempo de ejecución
    async def break_loop():
        # Damos un margen (0.1s) para que el loop principal evalúe 'slots_available <= 0' y ejecute el sleep(0.5)
        await asyncio.sleep(0.1)
        worker_controller.running = False
        
        # CORRECCIÓN CRÍTICA: Cancelamos la tarea dummy y vaciamos el set.
        # Esto permite que el protocolo 'finally' y el '_ack_flusher' terminen sin quedarse colgados.
        dummy_task.cancel()
        worker_controller._active_tasks.clear()

    asyncio.create_task(break_loop())
    
    # 3. Ejecución: El bucle entrará, detectará saturación, dormirá 0.5s y se apagará por el flag cambiado
    await worker_controller.run()
    
    # Limpieza de la tarea cancelada para evitar advertencias de asyncio en la terminal
    try:
        await dummy_task
    except asyncio.CancelledError:
        pass