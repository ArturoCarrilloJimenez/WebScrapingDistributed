# Workspace Agent Guidelines — WebScrapingDistributed

This document configures the operational guidelines, developer profile, architectural standards, and the synchronization protocol with the project's **Obsidian Vault** (including the project memory and the core Knowledge Graph).

---

## 🔮 1. Obsidian Vault & Knowledge Graph Synchronization (Mandatory on Startup)

To preserve context, avoid common pitfalls, and align with the developer's knowledge network, the agent **MUST** execute the following protocol at the start of every session/conversation.

### 🏁 Startup Verification Steps
1. **Connect to the Obsidian Vault** using the `obsidian` MCP server.
2. **Read the Projects Index:** Read the Map of Content (MOC) at `AI_Brain/02_Projects/projects_index.md`.
3. **Analyze Project Context:** For this workspace (`WebScrapingDistributed`), read:
   * `AI_Brain/02_Projects/WebScrapingDistributed/overview.md` — High-level objectives and roadmap.
   * `AI_Brain/02_Projects/WebScrapingDistributed/backlog.md` — Current active backlog and Kanban status.
   * `AI_Brain/03_Telemetry_Logs/engineering_diary.md` — Recent development sessions, actions, and milestones.
4. **Inspect the Knowledge Graph (Knowledge Base):** Check notes in `AI_Brain/04_Knowledge_Base/` to align with the core system concepts and troubleshooting logs:
   * `AI_Brain/04_Knowledge_Base/troubleshooting.md` — Read to understand common DNS, network resolution, Docker, and Floci setup errors.
   * `AI_Brain/04_Knowledge_Base/well_architected/` — Review the structural pillars of AWS Well-Architected Framework applied to this scraper (especially `reliability.md` and standard concepts under `concepts/` such as SQS resilience or S3 storage optimization).
5. **Acknowledge Current State:** In the first response to the user, briefly acknowledge the latest session state and any key constraints retrieved from both the project memory and the Knowledge Graph to confirm synchronization.

### 🔄 Telemetry & Logging Updates
Upon completing tasks, implementing new features, or resolving bugs, the agent **MUST** update the Obsidian Vault:
* **Log the Session:** Append details of the current development actions to `AI_Brain/03_Telemetry_Logs/engineering_diary.md` (or the specific session file in `session_logs/`).
* **Update the Backlog:** Move completed tasks to `✅ Done` and update the status of in-progress tasks in `AI_Brain/02_Projects/WebScrapingDistributed/backlog.md`.
* **Update Configuration/Architecture/Knowledge:** If new environment variables are added, connections change, or new architectural constraints are resolved, update `environment_variables.md`, `connection_map.md`, or create a corresponding concept/troubleshooting note in `AI_Brain/04_Knowledge_Base/`.

---

## 📂 2. Raw Documentation Processing & Mapping Protocol

Whenever new documentation, whitepapers, or manuals are added to `AI_Brain/05_Raw_Sources/`, the agent **MUST** process and map them into the core Knowledge Graph:

1. **Analysis & Synthesis:** Read and extract key technical insights, requirements, and recommendations from the raw source file.
2. **Knowledge Base Mapping:** 
   * Create structured concept notes under `AI_Brain/04_Knowledge_Base/`.
   * For large frameworks (similar to the *AWS Well-Architected Framework*), create a Master Index / MOC (Map of Content), a Review Process, and dedicated pillar/conceptual pages.
3. **Traceability Links:** Connect the synthesized notes back to the raw source reference, and link them to the relevant project documentation under `AI_Brain/02_Projects/` so the insights are actively applied to development.

---

## 🛡️ 3. Git & Version Control Guardrails

To preserve repository integrity and maintain code review quality, the agent **MUST** adhere to the following rules:

1. **No Direct Git Operations:** The agent is **PROHIBITED** from running `git commit`, `git push`, or committing changes directly to remote repositories. The developer will review all changes locally and perform Git commits/pushes.
2. **Never Target main Directly:** If explicitly asked to prepare or draft files or scripts for Git (e.g., preparing a PR or a release branch), **NEVER** write instructions or target the `main` branch directly. All work must go through specific feature or integration branches first.

---

## 🎨 4. Developer Profile & Coding Preferences

All code generated must adhere to the following developer guidelines:

* **Target Stack:** Python 3.13+, FastAPI, Amazon SQS, Amazon S3 (local emulation via LocalStack/Floci), Docker, Docker Compose.
* **Package Manager:** Strict preference for `uv` by Astral (avoid `pip` or `poetry`).
* **Linter & Formatter:** Use `Ruff` or `Black` guidelines.
* **Design Pattern:** Clean Architecture (strict separation of interfaces, use cases, entities, and infrastructure).
* **Static Typing:** Mandatory Python type hints on all functions and variables.
* **Documentation:** Google-style docstrings on all main classes and functions.
* **Logging & Observability:**
  * Use structured JSON logs in production.
  * Inject traceability IDs (e.g., `task_id`, `job_id`, `request_id`) in all log lines.

---

## 🏗️ 5. Architectural Guidelines

Implement system logic following these technical guidelines:

### A. API Design (FastAPI)
* **Pydantic V2:** Enforce strict validation contracts for inputs and outputs.
* **Dependency Injection:** Inject external service clients (SQS, S3, proxies) using FastAPI's `Depends` to facilitate unit testing.
* **Asynchronous Responses (Fire & Forget):** For long-running batch ingestion processes, respond immediately with HTTP `202 Accepted` after task serialization and queue enqueueing.

### B. Asynchronous Workers & Ingestion
* **asyncio Concurrency:** Use non-blocking, asynchronous I/O.
* **Concurrency Limits:** Bind execution concurrency using async semaphores (`asyncio.Semaphore`) to avoid socket starvation.
* **Graceful Shutdown:** Capture `SIGINT`/`SIGTERM`. Upon shutdown:
  1. Stop polling messages immediately.
  2. Await in-flight tasks to finish.
  3. Manually flush any in-memory buffers to storage before exiting.
* **Buffering & Batching:**
  * Buffer scraped records in memory and flush to S3 in batches (concatenated JSONL files) based on size (e.g., 3MB) or time (e.g., 60 seconds).
  * Enqueue and delete SQS messages in batches of up to 10.

### C. Resilience & Error Handling
* **Error Classification:** Distinguish between *Recoverable* exceptions (e.g., network timeouts, proxy errors) and *Fatal* exceptions (e.g., invalid schemas, 404s).
* **Dynamic Backoff:** Adjust SQS visibility timeouts dynamically based on exception types (e.g., exponential backoff on HTTP 429 rate-limiting).
* **Dead Letter Queue (DLQ):** Ensure failed tasks fallback to a DLQ after maximum retries.

---

## 🧪 6. Testing & Data Lake Optimization Guidelines

### A. Testing Philosophy & Mocking Strategy
* **Pytest Isolation:** Never run tests against live AWS resources or credentials. Use `pytest-asyncio` for asynchronous execution.
* **In-Memory SQS/S3 Mocking:**
  * For API Producer tests (`producer/test/`), use the `@pytest.fixture` with `@mock_aws` from the `moto` library.
  * For Worker tests (`worker/test/`) and Scheduled Jobs (`jobs/test/`), use a `ThreadedMotoServer` on a dynamically assigned port to persist connections and prevent TCP/aioboto3 connection leaks across async loops.
* **State Cleanup:** Always restore settings and clean up queues/buckets post-test to avoid cross-test contamination.

### B. Small Files Problem Mitigation
* **Data Compaction:** The Worker writes data in fragmented JSONL format to S3. To prevent performance degradation, the scheduled ETL compactor (`jobs/compact_s3.py`) consolidates these fragments into unified **Parquet** files using **ZSTD** compression. Maintain this compression standard for analytical querying.
