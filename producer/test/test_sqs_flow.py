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


@pytest.mark.asyncio
async def test_sqs_message_routing_static_and_dynamic(async_client: AsyncClient, sqs_mock):
    """Test that static and dynamic tasks are routed to their respective SQS queues."""
    # Get queue URLs from mock SQS
    static_queue = sqs_mock.get_queue_url(QueueName="test-queue")["QueueUrl"]
    dynamic_queue = sqs_mock.get_queue_url(QueueName="test-queue-dynamic")["QueueUrl"]

    payload = {
        "job_id": "job_routing_test_101",
        "tasks": [
            {
                "url": "https://example.com/static-page",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"title": "h1"}},
                "priority": 1
            },
            {
                "url": "https://example.com/dynamic-page",
                "parser_type": "dynamic_playwright",
                "parser_config": {"selectors": {"price": ".price"}, "timeout_ms": 10000},
                "priority": 2
            }
        ],
        "context": {"env": "test"}
    }

    # Send request to FastAPI Producer
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202

    # Give BackgroundTasks a moment to process the queues
    await asyncio.sleep(1.0)

    # 1. Verify static queue contains only the static task
    static_response = sqs_mock.receive_message(QueueUrl=static_queue, MaxNumberOfMessages=10)
    static_messages = static_response.get("Messages", [])
    assert len(static_messages) == 1
    static_body = json.loads(static_messages[0]["Body"])
    assert static_body["url"] == "https://example.com/static-page"
    assert static_body["parser_type"] == "static_css"

    # 2. Verify dynamic queue contains only the dynamic task
    dynamic_response = sqs_mock.receive_message(QueueUrl=dynamic_queue, MaxNumberOfMessages=10)
    dynamic_messages = dynamic_response.get("Messages", [])
    assert len(dynamic_messages) == 1
    dynamic_body = json.loads(dynamic_messages[0]["Body"])
    assert dynamic_body["url"] == "https://example.com/dynamic-page"
    assert dynamic_body["parser_type"] == "dynamic_playwright"


@pytest.mark.asyncio
async def test_sqs_task_context_overrides_global_context(async_client: AsyncClient, sqs_mock):
    """Test that task-specific context takes precedence over global context and both are enqueued to SQS."""
    static_queue = sqs_mock.get_queue_url(QueueName="test-queue")["QueueUrl"]

    payload = {
        "job_id": "job_ctx_merge_001",
        "tasks": [
            {
                "url": "https://example.com/sec-10k",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"title": "h1"}},
                "context": {
                    "cik": "0000320193",
                    "year": 2023,
                    "specific_flag": True
                }
            }
        ],
        "context": {
            "source": "sec_edgar",
            "year": 2024,
            "global_flag": "global_value"
        }
    }

    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202

    await asyncio.sleep(1.0)

    static_response = sqs_mock.receive_message(QueueUrl=static_queue, MaxNumberOfMessages=10)
    messages = static_response.get("Messages", [])
    assert len(messages) >= 1

    matched_message = None
    for msg in messages:
        body = json.loads(msg["Body"])
        if body.get("job_id") == "job_ctx_merge_001":
            matched_message = body
            break

    assert matched_message is not None
    ctx = matched_message["context"]

    # 1. Inherited from global context
    assert ctx["source"] == "sec_edgar"
    assert ctx["global_flag"] == "global_value"

    # 2. Task-specific keys
    assert ctx["cik"] == "0000320193"
    assert ctx["specific_flag"] is True

    # 3. Collision: task context MUST override global context
    assert ctx["year"] == 2023


