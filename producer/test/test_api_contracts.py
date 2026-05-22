import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_scraping_job_success(async_client: AsyncClient, sqs_mock):
    """Test valid payload returns 202 Accepted."""
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
    
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    
    if response.status_code != 202:
        print(response.json())
    assert response.status_code == 202
    data = response.json()
    assert data["job_id"] == "job_dist_test_001"

@pytest.mark.asyncio
async def test_create_scraping_job_invalid_job_id(async_client: AsyncClient):
    """Test job_id validation."""
    payload = {
        "job_id": "invalid job id with spaces!",
        "tasks": [
            {
                "url": "https://example.com",
                "parser_type": "static_css"
            }
        ]
    }
    
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    
    assert response.status_code == 422
    data = response.json()
    assert any("job_id contiene caracteres no permitidos" in err["msg"] for err in data["detail"])

@pytest.mark.asyncio
async def test_create_scraping_job_missing_urls(async_client: AsyncClient):
    """Test urls validation (min_length=1)."""
    payload = {
        "job_id": "test-job-123",
        "tasks": [],
        "context": {}
    }
    
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    
    assert response.status_code == 422
    data = response.json()
    assert any("tasks" in err["loc"] for err in data["detail"])

@pytest.mark.asyncio
async def test_create_scraping_job_invalid_priority(async_client: AsyncClient):
    """Test priority bounds."""
    payload = {
        "job_id": "test-job-123",
        "tasks": [
            {
                "url": "https://example.com",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"title": "h1"}},
                "priority": 15
            }
        ],
        "context": {
            "environment": "distributed_load_test",
            "cluster_nodes": 4
        }
    }
    
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    
    assert response.status_code == 422
    data = response.json()
    assert any("priority" in err["loc"] for err in data["detail"])
