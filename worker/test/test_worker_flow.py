import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch
from config.settings import settings
from scraping.interfaces.interfaces import ParseResult

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
    mock_parser.parse.side_effect = lambda task: ParseResult(
        task=task,
        data={"titles": ["Noticia Extraída 1", "Noticia Extraída 2"]}
    )

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

async def test_graceful_shutdown_with_s3_upload(sqs_mock, worker_controller):
    """
    Test de cerrado seguro: Verifica que si el motor se apaga con datos en RAM (buffer de storage),
    estos se envíen a S3 (save) y luego a la cola de ACKs para confirmar a SQS, sin dejar memoria huérfana.
    """
    # Override concurrency for testing to avoid concurrent SQS fetch requests blocking Moto
    worker_controller.max_concurrency = 5
    worker_controller.semaphore = asyncio.Semaphore(5)

    # 1. Configuramos el mock de S3 para interceptar la subida
    mock_repository = AsyncMock()
    worker_controller.buffer_service.repository = mock_repository

    # 2. Publicamos un mensaje en SQS
    task_payload = {
        "job_id": "job_shutdown_test_001",
        "batch_id": "batch_shutdown_test_001",
        "task_id": "task_shutdown_001",
        "url": "https://example.com/noticias",
        "parser_type": "static_css",
        "parser_config": {"selectors": {"titles": "h2"}},
        "retry_count": 0,
        "max_retries": 3
    }
    sqs_mock.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps(task_payload)
    )

    # 3. Mockear el Parser para que retorne un resultado
    mock_parser = AsyncMock()
    mock_parser.parse.side_effect = lambda task: ParseResult(
        task=task,
        data={"titles": ["Noticia 1"]}
    )

    with patch.object(worker_controller.parser_factory, "get_parser", return_value=mock_parser):
        # Lanzamos el motor asíncronamente
        worker_task = asyncio.create_task(worker_controller.run())

        # Esperamos a que el parser sea llamado (se procese la tarea)
        for _ in range(80):
            if mock_parser.parse.call_count > 0:
                break
            await asyncio.sleep(0.1)
        
        # Verificamos que el parser se llamó
        assert mock_parser.parse.call_count == 1

        # En este punto, el resultado de la tarea está en el buffer de memoria del JobBufferService,
        # pero NO ha sido subido a S3 ni enviado a la ack_queue porque el buffer tiene un timeout de 60s
        # y no se ha llenado (max_bytes).
        # Verificamos que no se ha llamado a save en S3 aún
        mock_repository.save.assert_not_called()
        
        # Y la cola de ACKs del flusher está vacía
        assert worker_controller.ack_queue.qsize() == 0

        # 4. Iniciamos el cerrado seguro (Graceful Shutdown) llamando a stop()
        worker_controller.stop()

        # Esperamos que el motor termine rompiendo limpiamente
        with pytest.raises(asyncio.CancelledError):
            await worker_task

    # 5. Aseveraciones post-apagado:
    # A) Se debió haber llamado a la subida de S3 (save) al drenar la memoria en el close() del buffer service
    mock_repository.save.assert_called_once()
    
    # B) Los datos debieron haber sido enviados a SQS como ACK y borrados de la cola
    # (El flusher procesó la ack_queue después del close y vació SQS)
    sqs_response = sqs_mock.receive_message(
        QueueUrl=settings.sqs_queue_url,
        MaxNumberOfMessages=1
    )
    assert "Messages" not in sqs_response, "El mensaje no fue borrado de SQS"
    
    # C) La cola de ACKs en memoria debe estar completamente vacía (sin leaks de memoria)
    assert worker_controller.ack_queue.qsize() == 0