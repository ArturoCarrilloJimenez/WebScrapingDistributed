import os
import sys
import pytest
import boto3
import socket
from moto.server import ThreadedMotoServer

# Forzar la inclusión del directorio 'worker' en el PATH de ejecución
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import settings
import dependencies.dependencies as worker_deps
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter

@pytest.fixture(scope="session", autouse=True)
def aws_credentials():
    """Configura credenciales ficticias de AWS para asegurar aislamiento total."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(scope="session")
def moto_sqs_port():
    """
    Reserva dinámicamente un puerto libre asignado por el sistema operativo.
    Soluciona de raíz la ausencia del atributo '.port' en el ThreadedMotoServer.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def moto_sqs_server(moto_sqs_port):
    """
    Levanta un servidor HTTP local en memoria que simula AWS SQS de forma real
    utilizando el puerto libre previamente reservado.
    """
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=moto_sqs_port)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="function")
def sqs_mock(moto_sqs_server, moto_sqs_port):
    """
    Inicializa la cola SQS y el bucket S3 en el servidor Moto local e inyecta los adaptadores apuntando a él.
    """
    # Usamos el puerto que conocemos con total certeza
    endpoint_url = f"http://127.0.0.1:{moto_sqs_port}"
    
    # El cliente síncrono de boto3 crea la cola comunicándose con el servidor local
    client = boto3.client("sqs", region_name="us-east-1", endpoint_url=endpoint_url)
    queue = client.create_queue(QueueName="test-queue")
    queue_url = queue["QueueUrl"]

    # El cliente síncrono de boto3 crea el bucket de S3 en el servidor local
    s3_client = boto3.client("s3", region_name="us-east-1", endpoint_url=endpoint_url)
    s3_client.create_bucket(Bucket="test-bucket")
    
    # Guardamos el estado original de los Singletons para el Teardown
    original_queue_url = settings.sqs_queue_url
    original_adapter_instance = worker_deps._adapter_sqs_instance
    original_aws_key = settings.aws_access_key_id
    original_aws_secret = settings.aws_secret_access_key

    original_s3_endpoint = settings.s3_endpoint_url
    original_s3_bucket = settings.s3_bucket_name
    original_s3_region = settings.s3_region
    original_s3_adapter_instance = worker_deps._adapter_s3_instance

    # Forzamos la configuración del test hacia la cola dinámica de Moto y sus credenciales
    settings.sqs_queue_url = queue_url
    settings.aws_access_key_id = "testing"
    settings.aws_secret_access_key = "testing"

    settings.s3_endpoint_url = endpoint_url
    settings.s3_bucket_name = "test-bucket"
    settings.s3_region = "us-east-1"

    # Instanciamos el adaptador asíncrono apuntando directamente al endpoint HTTP de Moto
    mocked_adapter = SQSAioBotoAdapter(
        endpoint_url=endpoint_url,
        queue_url=queue_url,
        region="us-east-1"
    )
    
    # Inyección de dependencia limpia por reemplazo de referencia global
    worker_deps._adapter_sqs_instance = mocked_adapter

    # Instanciamos el adaptador de S3 apuntando directamente al endpoint HTTP de Moto
    from infrastructure.storage.s3.adapter import S3StorageRepository
    mocked_s3_adapter = S3StorageRepository(
        endpoint_url=endpoint_url,
        bucket_name="test-bucket",
        region="us-east-1"
    )
    worker_deps._adapter_s3_instance = mocked_s3_adapter

    yield client

    # Clean State: Restauración absoluta para evitar contaminación de memoria en la suite
    try:
        client.delete_queue(QueueUrl=queue_url)
    except Exception:
        pass

    try:
        # Drenamos y eliminamos los objetos del bucket S3 en Moto
        objects = s3_client.list_objects_v2(Bucket="test-bucket")
        if "Contents" in objects:
            for obj in objects["Contents"]:
                s3_client.delete_object(Bucket="test-bucket", Key=obj["Key"])
        s3_client.delete_bucket(Bucket="test-bucket")
    except Exception:
        pass

    settings.sqs_queue_url = original_queue_url
    worker_deps._adapter_sqs_instance = original_adapter_instance
    settings.aws_access_key_id = original_aws_key
    settings.aws_secret_access_key = original_aws_secret

    settings.s3_endpoint_url = original_s3_endpoint
    settings.s3_bucket_name = original_s3_bucket
    settings.s3_region = original_s3_region
    worker_deps._adapter_s3_instance = original_s3_adapter_instance


@pytest.fixture(scope="function")
def worker_controller(sqs_mock):
    """Proporciona el controlador configurado contra la infraestructura local."""
    worker_deps._job_buffer_service_instance = None
    controller = worker_deps.get_worker_controller()
    yield controller
    worker_deps._job_buffer_service_instance = None