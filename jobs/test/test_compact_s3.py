import pytest
import asyncio
import datetime
import json
import uuid
from unittest.mock import patch
from interfaces.compact_s3 import ListOfJobs, S3BatchFile
import compact_s3

pytestmark = pytest.mark.asyncio

# Helper to get client from the context manager
async def get_aioboto_client():
    client_ctx = compact_s3._get_client()
    # We enter the client_ctx context manager and yield the client
    return client_ctx

# 1. Test get_list_of_jobs
async def test_get_list_of_jobs(s3_mock):
    # Upload some dummy objects to create virtual folders
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/part1.jsonl", Body=b"")
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job2/part1.jsonl", Body=b"")
    # This shouldn't match because of prefix raw-data/
    s3_mock.put_object(Bucket="test-bucket", Key="other-prefix/job_id=job3/part1.jsonl", Body=b"")

    client_ctx = await get_aioboto_client()
    async with client_ctx as client:
        prefixes = await compact_s3.get_list_of_jobs(client)
    
    assert "raw-data/job_id=job1/" in prefixes
    assert "raw-data/job_id=job2/" in prefixes
    assert len(prefixes) == 2

# 2. Test get_list_of_batches
async def test_get_list_of_batches(s3_mock):
    # Upload objects under a job prefix
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/part1.jsonl", Body=b"A"*5000)
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/part2.jsonl", Body=b"A"*7000)
    
    semaphore = asyncio.Semaphore(1)
    client_ctx = await get_aioboto_client()
    async with client_ctx as client:
        job_info = await compact_s3.get_list_of_batches(client, "raw-data/job_id=job1/", semaphore)
        
    assert isinstance(job_info, ListOfJobs)
    assert job_info.prefix == "raw-data/job_id=job1/"
    assert job_info.total_bytes == 12000
    assert job_info.total_tasks == 2
    assert len(job_info.batches) == 2
    # Sort by key to ensure deterministic assertion
    sorted_batches = sorted(job_info.batches, key=lambda b: b.key)
    assert sorted_batches[0].key == "raw-data/job_id=job1/part1.jsonl"
    assert sorted_batches[0].size == 5000
    assert sorted_batches[1].key == "raw-data/job_id=job1/part2.jsonl"
    assert sorted_batches[1].size == 7000

# 3. Test clear_job
async def test_clear_job(s3_mock):
    # Upload files to delete
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/p1.jsonl", Body=b"A")
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/p2.jsonl", Body=b"B")
    
    batches = [
        S3BatchFile(key="raw-data/job_id=job1/p1.jsonl", size=1, last_modified=datetime.datetime.now()),
        S3BatchFile(key="raw-data/job_id=job1/p2.jsonl", size=1, last_modified=datetime.datetime.now())
    ]
    job = ListOfJobs(
        prefix="raw-data/job_id=job1/", 
        batches=batches, 
        total_bytes=2, 
        total_tasks=2, 
        last_modified=datetime.datetime.now(datetime.timezone.utc),
        inactive_time=datetime.timedelta(minutes=10)
    )
    
    client_ctx = await get_aioboto_client()
    async with client_ctx as client:
        await compact_s3.clear_job(client, job)
        
    # Verify they are deleted
    resp = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="raw-data/job_id=job1/")
    assert "Contents" not in resp

# 4. Test process_job (ignored)
async def test_process_job_ignored(s3_mock):
    now = datetime.datetime.now(datetime.timezone.utc)
    job = ListOfJobs(
        prefix="raw-data/job_id=job1/",
        batches=[],
        total_bytes=100,
        total_tasks=0,
        last_modified=now - datetime.timedelta(minutes=5),
        inactive_time=datetime.timedelta(minutes=5)
    )
    
    semaphore = asyncio.Semaphore(1)
    client_ctx = await get_aioboto_client()
    async with client_ctx as client:
        result = await compact_s3.process_job(client, job, semaphore)
        
    assert result is None

# 5. Test process_job (compaction execution)
async def test_process_job_execute(s3_mock):
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Upload raw jsonl file with a valid scraping task
    task_data = {
        "task": {
            "job_id": "job1",
            "batch_id": "batch1",
            "task_id": "task_id_123",
            "url": "http://127.0.0.1/t1",
            "parser_type": "static_css",
            "parser_config": {
                "selectors": {
                    "content": "#content"
                }
            },
            "priority": 1,
            "max_depth": 0,
            "max_retries": 1,
            "created_at": "2026-06-25T12:00:00.000000Z"
        },
        "data": {"content": ["Line 1 extracted"]}
    }
    raw_line = json.dumps(task_data).encode("utf-8") + b"\n"
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/p1.jsonl", Body=raw_line)
    
    batches = [
        S3BatchFile(key="raw-data/job_id=job1/p1.jsonl", size=len(raw_line), last_modified=now)
    ]
    job = ListOfJobs(
        prefix="raw-data/job_id=job1/",
        batches=batches,
        total_bytes=len(raw_line),
        total_tasks=1,
        last_modified=now - datetime.timedelta(hours=1),
        inactive_time=datetime.timedelta(hours=1)
    )
    
    # Bypass the size threshold by patching MIN_INACTIVITY_SECONDS to 0 (so 1 hour inactive meets threshold!)
    with patch("compact_s3.MIN_INACTIVITY_SECONDS", 0):
        semaphore = asyncio.Semaphore(1)
        client_ctx = await get_aioboto_client()
        async with client_ctx as client:
            result = await compact_s3.process_job(client, job, semaphore)
            
    assert result == job
    
    # Check that the compacted parquet file was written to S3
    resp = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="compacted-data/job_id=job1/")
    assert "Contents" in resp
    assert len(resp["Contents"]) == 1
    assert resp["Contents"][0]["Key"].endswith(".parquet")

# 6. Test process_job lookahead (elastic chunking absorbed vs split)
async def test_process_job_lookahead_elastic_and_split(s3_mock):
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Setup two task lines
    t1 = {"task": {"job_id": "job1", "batch_id": "b1", "task_id": "t1", "url": "http://x/1", "parser_type": "static_css", "parser_config": {"selectors": {"c": ".c"}}, "priority": 1, "max_depth": 0, "max_retries": 1, "created_at": "2026-06-25T12:00:00Z"}, "data": {"c": ["t1"]}}
    t2 = {"task": {"job_id": "job1", "batch_id": "b1", "task_id": "t2", "url": "http://x/2", "parser_type": "static_css", "parser_config": {"selectors": {"c": ".c"}}, "priority": 1, "max_depth": 0, "max_retries": 1, "created_at": "2026-06-25T12:00:00Z"}, "data": {"c": ["t2"]}}
    
    line1 = json.dumps(t1).encode("utf-8") + b"\n"
    line2 = json.dumps(t2).encode("utf-8") + b"\n"
    
    # Create two batches in S3
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/p1.jsonl", Body=line1)
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job1/p2.jsonl", Body=line2)
    
    batches = [
        S3BatchFile(key="raw-data/job_id=job1/p1.jsonl", size=len(line1), last_modified=now),
        S3BatchFile(key="raw-data/job_id=job1/p2.jsonl", size=len(line2), last_modified=now)
    ]
    job = ListOfJobs(
        prefix="raw-data/job_id=job1/",
        batches=batches,
        total_bytes=len(line1) + len(line2),
        total_tasks=2,
        last_modified=now,
        inactive_time=datetime.timedelta(hours=1)
    )
    
    # CASE A: Remaining size fits within elastic tolerance (TOLERANCIA_COLA=1000)
    # MAX_RECORDS_PER_FILE is set to 1 so p1.jsonl (1 record) hits the limit, but because p2.jsonl is small (size fits), it absorbs it in the same file!
    with patch("compact_s3.MIN_INACTIVITY_SECONDS", 0), \
         patch("compact_s3.MAX_RECORDS_PER_FILE", 1), \
         patch("compact_s3.TOLERANCIA_COLA", 1000):
        
        semaphore = asyncio.Semaphore(1)
        client_ctx = await get_aioboto_client()
        async with client_ctx as client:
            await compact_s3.process_job(client, job, semaphore)
            
    # Should create exactly 1 Parquet file (absorbed)
    resp = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="compacted-data/job_id=job1/")
    assert len(resp.get("Contents", [])) == 1
    
    # Clean up compacted output for Case B
    s3_mock.delete_objects(Bucket="test-bucket", Delete={'Objects': [{'Key': resp["Contents"][0]["Key"]}]})

    # CASE B: Remaining size exceeds tolerance (TOLERANCIA_COLA=1)
    # Remaining size of p2.jsonl is larger than 1, so it splits into 2 Parquet files!
    with patch("compact_s3.MIN_INACTIVITY_SECONDS", 0), \
         patch("compact_s3.MAX_RECORDS_PER_FILE", 1), \
         patch("compact_s3.TOLERANCIA_COLA", 1):
        
        semaphore = asyncio.Semaphore(1)
        client_ctx = await get_aioboto_client()
        async with client_ctx as client:
            await compact_s3.process_job(client, job, semaphore)
            
    # Should create exactly 2 Parquet files (split)
    resp = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="compacted-data/job_id=job1/")
    assert len(resp.get("Contents", [])) == 2

# 7. Test main flow (full integration)
async def test_main_flow(s3_mock):
    # Upload raw jsonl file with a valid scraping task
    task_data = {
        "task": {
            "job_id": "job-integrated",
            "batch_id": "batch1",
            "task_id": "task_id_1",
            "url": "http://127.0.0.1/t1",
            "parser_type": "static_css",
            "parser_config": {"selectors": {"c": ".c"}},
            "priority": 1,
            "max_depth": 0,
            "max_retries": 1,
            "created_at": "2026-06-25T12:00:00Z"
        },
        "data": {"c": ["Text"]}
    }
    raw_line = json.dumps(task_data).encode("utf-8") + b"\n"
    s3_mock.put_object(Bucket="test-bucket", Key="raw-data/job_id=job-integrated/p1.jsonl", Body=raw_line)
    
    # Run main flow bypassing thresholds (so it compacts our uploaded job!)
    with patch("compact_s3.MIN_INACTIVITY_SECONDS", 0), \
         patch("compact_s3.MIN_BYTES_FOR_COMPACTION", 0):
        await compact_s3.main()
        
    # 1. Compacted parquet file should be written to test-bucket
    resp = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="compacted-data/job_id=job-integrated/")
    assert "Contents" in resp
    assert len(resp["Contents"]) == 1
    assert resp["Contents"][0]["Key"].endswith(".parquet")
    
    # 2. Raw landing zone file should be successfully purged
    resp_raw = s3_mock.list_objects_v2(Bucket="test-bucket", Prefix="raw-data/job_id=job-integrated/")
    assert "Contents" not in resp_raw


async def test_sync_serialize_to_parquet():
    import pyarrow as pa
    schema = pa.schema([pa.field("a", pa.int64())])
    data = {"a": [1, 2, 3]}
    result = compact_s3._sync_serialize_to_parquet(schema, data)
    assert isinstance(result, bytes)
    assert result.startswith(b"PAR1")

