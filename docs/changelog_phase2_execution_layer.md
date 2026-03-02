# Changelog: Phase 2 Execution-Layer Decoupling

## Date
- 2026-03-03

## Scope
Phase 2 introduces a staged ingestion pipeline that decouples the crawl flow into persisted, resumable execution stages.

## Delivered in this increment

### 1. Job/Task state model (queue-like orchestration)
- Added `ingestion_job` and `ingestion_task` tables.
- Added stage enum contracts:
  - `fetch_raw`
  - `extract_structured`
  - `validate_rules`
  - `persist_versioned`
- Added task lifecycle states:
  - `PENDING`, `RUNNING`, `RETRY_SCHEDULED`, `SUCCEEDED`, `FAILED`, `POISONED`, `SKIPPED`

### 2. Persistent pipeline runner
- Added `src/services/ingestion_pipeline.py`.
- Pipeline now runs stage-by-stage with persisted state updates.
- Per-stage idempotency keys are computed from:
  - stage name,
  - source identity (URL + year + university),
  - stage input fingerprint/content hash.

### 3. Retry and poison-queue handling
- Added bounded retry with exponential backoff.
- Stage enters `POISONED` when retry budget is exceeded.
- Job status transitions to `POISONED` when any stage is poisoned.
- Task progress now surfaces retry scheduling details (`retry in Ns`) for API polling consumers.

### 4. Resume-from-stage support
- Added resume logic at service, API, and CLI layers.
- Resume can start from a specific stage or auto-detect the first unfinished stage.
- Upstream successful stage outputs are reused (no forced rerun unless explicitly reset by resume point).
- Resume now prunes downstream context payload keys from the selected stage to avoid stale-output reuse.

### 5. Crawl entry integration
- `crawl_url` now defaults to the Phase 2 staged ingestion pipeline.
- `continue_depth > 0` now also runs inside the staged pipeline via fetch-stage scout expansion (no legacy fallback path).

### 6. API + CLI exposure
- API:
  - `GET /ingestion/jobs`
  - `GET /ingestion/jobs/{job_uid}`
  - `POST /ingestion/jobs/{job_uid}/resume`
- CLI:
  - `adm-agent ingestion-jobs`
  - `adm-agent ingestion-resume --job <job_uid> [--stage ...]`

### 7. Deterministic stage trace + progress events
- Stage trace now includes monotonic `seq` numbers to keep execution order deterministic.
- Pipeline emits structured stage/job events (`stage_started`, `stage_retry_scheduled`, `stage_succeeded`, etc.).
- API task progress now reflects stage-level transitions during both crawl and resume runs.

## Migrations
- New revision: `20260303_0004`
- File: `migrations/versions/20260303_0004_ingestion_pipeline_jobs.py`

## Verification in this increment
- `uv run python -m pytest --tb=short` passed.
- `uv run pylint src/services/ingestion_pipeline.py src/services/crawler.py src/api/server.py src/cmd/cli.py src/scrapers/page_processor.py --output-format=text --score=y` passed.
