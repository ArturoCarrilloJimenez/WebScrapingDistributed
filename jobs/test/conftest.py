import os
import sys
import pytest
import boto3
import socket
from moto.server import ThreadedMotoServer

# Forzar la inclusión del directorio 'jobs' en el PATH de ejecución
jobs_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if jobs_root not in sys.path:
    sys.path.insert(0, jobs_root)

# También añadimos la raíz del proyecto para importar 'config' y 'shared'
project_root = os.path.abspath(os.path.join(jobs_root, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import settings

@pytest.fixture(scope="session", autouse=True)
def aws_credentials():
    """Configura credenciales ficticias de AWS para asegurar aislamiento total."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["DEFAULT_REGION_AWS"] = "us-east-1"

@pytest.fixture(scope="session")
def moto_s3_port():
    """Reserva dinámicamente un puerto libre asignado por el sistema operativo."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

@pytest.fixture(scope="session")
def moto_s3_server(moto_s3_port):
    """Levanta un servidor HTTP local que simula AWS S3."""
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=moto_s3_port)
    server.start()
    yield server
    server.stop()

@pytest.fixture(scope="function")
def s3_mock(moto_s3_server, moto_s3_port):
    """Inicializa el bucket S3 en Moto e inyecta las configuraciones de test."""
    endpoint_url = f"http://127.0.0.1:{moto_s3_port}"
    
    # Crear cliente síncrono para inicializar recursos en el servidor moto local
    s3_client = boto3.client("s3", region_name="us-east-1", endpoint_url=endpoint_url)
    s3_client.create_bucket(Bucket="test-bucket")
    
    # Backup de configuraciones
    orig_endpoint = settings.s3_endpoint_url
    orig_bucket = settings.s3_bucket_name
    orig_aws_key = settings.aws_access_key_id
    orig_aws_secret = settings.aws_secret_access_key
    orig_region = settings.s3_region

    # Sobrescribir settings globales
    settings.s3_endpoint_url = endpoint_url
    settings.s3_bucket_name = "test-bucket"
    settings.aws_access_key_id = "testing"
    settings.aws_secret_access_key = "testing"
    settings.s3_region = "us-east-1"
    
    yield s3_client
    
    # Limpiar bucket
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        keys_to_delete = []
        for page in paginator.paginate(Bucket="test-bucket"):
            for item in page.get('Contents', []):
                keys_to_delete.append({'Key': item['Key']})
        if keys_to_delete:
            s3_client.delete_objects(Bucket="test-bucket", Delete={'Objects': keys_to_delete})
        s3_client.delete_bucket(Bucket="test-bucket")
    except Exception:
        pass
        
    # Restaurar settings
    settings.s3_endpoint_url = orig_endpoint
    settings.s3_bucket_name = orig_bucket
    settings.aws_access_key_id = orig_aws_key
    settings.aws_secret_access_key = orig_aws_secret
    settings.s3_region = orig_region
