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


@pytest.mark.asyncio
async def test_create_scraping_job_accepts_null_and_task_context(async_client: AsyncClient, sqs_mock):
    """Test that null contexts at global and task levels are handled gracefully without 422 errors."""
    payload = {
        "job_id": "job_null_ctx_001",
        "tasks": [
            {
                "url": "https://example.com/task1",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"title": "h1"}},
                "context": None
            },
            {
                "url": "https://example.com/task2",
                "parser_type": "static_css",
                "parser_config": {"selectors": {"title": "h2"}},
                "context": {"cik": "0000320193", "year": 2023}
            }
        ],
        "context": None
    }
    
    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_create_scraping_job_accepts_follow_links_config(async_client: AsyncClient, sqs_mock):
    """Test that static_css accepts follow_links declarative sub-queries."""
    payload = {
        "job_id": "sec-8k-follow-links",
        "tasks": [
            {
                "url": "https://www.sec.gov/Archives/edgar/data/895421/000089542123000123/ms-20231208.htm",
                "parser_type": "static_css",
                "parser_config": {
                    "selectors": {"body_text": "body"},
                    "follow_links": [
                        {
                            "name": "exhibits",
                            "source_url": "https://www.sec.gov/Archives/edgar/data/895421/000089542123000123/0000895421-23-000123-index.htm",
                            "row_selector": "table.tableFile tr",
                            "link_selector": "td:nth-of-type(3) a",
                            "filter_selector": "td:nth-of-type(4)",
                            "filter_regex": "(?i)^EX-99",
                            "url_strip_regex": r"^/ix\?doc=",
                            "max_links": 5,
                            "selectors": {"body_text": "body"},
                        }
                    ],
                },
            }
        ],
    }

    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202
    assert response.json()["job_id"] == "sec-8k-follow-links"


@pytest.mark.asyncio
async def test_create_scraping_job_rejects_invalid_follow_links_config(async_client: AsyncClient):
    """Test validation errors on invalid regex or key collision in follow_links."""
    payload = {
        "job_id": "sec-invalid-follow",
        "tasks": [
            {
                "url": "https://example.com/doc",
                "parser_type": "static_css",
                "parser_config": {
                    "selectors": {"exhibits": "body"},
                    "follow_links": [
                        {
                            "name": "exhibits",
                            "link_selector": "a",
                            "filter_regex": "[unclosed",
                            "selectors": {"body_text": "body"},
                        }
                    ],
                },
            }
        ],
    }

    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_scraping_job_accepts_fallback_metadata_and_exclude_selectors(
    async_client: AsyncClient, sqs_mock
):
    """Test static_css accepts fallback_source_urls, metadata_selectors, and exclude_selectors."""
    payload = {
        "job_id": "sec-8k-full-exhibits",
        "tasks": [
            {
                "url": "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/jpm-20241011.htm",
                "parser_type": "static_css",
                "parser_config": {
                    "selectors": {"body_text": "body"},
                    "exclude_selectors": ["ix:header", "script", "style"],
                    "follow_links": [
                        {
                            "name": "exhibits",
                            "source_url": "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/0000019617-24-000555-index.htm",
                            "fallback_source_urls": [
                                "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/0000019617-24-000555-index.html"
                            ],
                            "row_selector": "table.tableFile tr",
                            "link_selector": "td:nth-of-type(3) a",
                            "filter_selector": "td:nth-of-type(4)",
                            "filter_regex": r"(?i)^EX-(?!101\b)",
                            "url_strip_regex": r"^/ix\?doc=",
                            "max_links": 50,
                            "metadata_selectors": {
                                "description": "td:nth-of-type(2)",
                                "size": "td:nth-of-type(5)",
                            },
                            "exclude_selectors": ["ix:header", "script", "style"],
                            "selectors": {"body_text": "body"},
                        }
                    ],
                },
            }
        ],
    }

    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_create_scraping_job_rejects_fallback_without_source_url(
    async_client: AsyncClient,
):
    """Test validation rejects fallback_source_urls when source_url is omitted."""
    payload = {
        "job_id": "sec-invalid-fallback",
        "tasks": [
            {
                "url": "https://example.com/doc",
                "parser_type": "static_css",
                "parser_config": {
                    "selectors": {"body_text": "body"},
                    "follow_links": [
                        {
                            "name": "exhibits",
                            "fallback_source_urls": ["https://example.com/index.html"],
                            "link_selector": "a",
                            "selectors": {"body_text": "body"},
                        }
                    ],
                },
            }
        ],
    }

    response = await async_client.post("/v1/scraping/tasks", json=payload)
    assert response.status_code == 422

