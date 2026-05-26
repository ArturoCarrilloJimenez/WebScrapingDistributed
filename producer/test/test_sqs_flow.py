import pytest
import json
import asyncio
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_sqs_message_enqueued(async_client: AsyncClient, sqs_mock):
    """Test that a valid request results in messages being sent to SQS."""
    # Get queue URL from the mock (moto)
    response_queue = sqs_mock.get_queue_url(QueueName="test-queue")
    queue_url = response_queue["QueueUrl"]

    payload = {
        "job_id": "job_dist_test_001",
        "tasks": [
            {
                "url": "https://example.com/",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"news_titles": "span.titleline > a"}},
                "priority": 1,
                "max_depth": 1,
                "max_retries": 3
            },
            {
                "url": "https://example.com/welcome",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"welcome_msg": "#mp-welcome"}},
                "priority": 2,
                "max_depth": 1,
                "max_retries": 3
            }
        ],
        "context": {
            "environment": "distributed_load_test",
            "cluster_nodes": 4
        }
    }

    # Send request
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202

    # The tasks are processed in the background (FastAPI BackgroundTasks).
    # In tests with AsyncClient, background tasks might need a tiny sleep to execute
    # depending on the test client implementation, but ASGITransport usually runs them inline or we wait.
    await asyncio.sleep(1.0)

    # Read messages from SQS
    response = sqs_mock.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10
    )

    messages = response.get("Messages", [])
    if len(messages) != 2:
        print(f"Messages found: {len(messages)}, raw response: {response}")
    # We sent 2 URLs, so there should be 2 messages in the queue
    assert len(messages) == 2

    # Verify the content of the first message
    body_0 = json.loads(messages[0]["Body"])
    assert body_0["job_id"] == "job_dist_test_001"
    assert "url" in body_0
    assert body_0["url"] in ["https://example.com/", "https://example.com/welcome"]
    assert body_0["parser_type"] == "static_css"
