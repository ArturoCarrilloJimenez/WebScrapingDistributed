import os
import sys
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from moto import mock_aws
import boto3

# Add producer directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import application
from main import app
from config.settings import settings

from dependencies.dependencies import get_task_producer
from infrastructure.task.sqs.adapter import SQSAioBotoAdapter

# Fix pytest-asyncio strict warning
pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="session", autouse=True)
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["SQS_QUEUE_URL"] = (
        "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"
    )


@pytest.fixture(scope="function")
def sqs_mock():
    """Start moto mock for SQS and create a test queue."""
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        queue = client.create_queue(QueueName="test-queue")
        # Update queue URL
        settings.sqs_queue_url = queue["QueueUrl"]

        # Override FastAPI dependency to not use LocalStack endpoint
        adapter = SQSAioBotoAdapter(
            endpoint_url=None, queue_url=queue["QueueUrl"], region="us-east-1"
        )
        app.dependency_overrides[get_task_producer] = lambda: adapter

        yield client

        app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def async_client():
    """Async HTTP client for FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
