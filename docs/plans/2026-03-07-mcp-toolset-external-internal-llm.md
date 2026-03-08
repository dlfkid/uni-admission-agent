# MCP Dual Toolset (External/Internal LLM) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide two MCP toolsets: default tools that do not require internal `.env` LLM, plus explicit `_internal_llm` tools that use server-side LLM when available, including post-persist review-and-patch workflow.

**Architecture:** Keep existing MCP server as the single endpoint. Always register base tools and conditionally register `_internal_llm` tools when RouterAgent is available. Reuse taxonomy scoring for auto/interactive candidate flow and add structured review/edit tools (`program_patch`, `program_patch_batch`) so caller LLM can apply user feedback to DB entries.

**Tech Stack:** FastAPI + FastMCP, Pydantic/SQLModel, existing ingestion pipeline + taxonomy service, pytest.

---

### Task 1: Add failing tests for MCP registration split

**Files:**
- Create: `tests/test_mcp_tool_registration_modes.py`
- Modify: `src/api/server.py`

**Step 1: Write failing test for base tools always registered**

```python
def test_base_tools_registered_without_internal_llm(...) -> None:
    # simulate create_router unavailable
    # assert analyze/crawl/crawl_detail_batch/db_query/runtime_status/program_patch/program_patch_batch exist
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py::test_base_tools_registered_without_internal_llm -v`  
Expected: FAIL.

**Step 3: Write failing test for `_internal_llm` tools conditional registration**

```python
def test_internal_llm_tools_registered_only_when_available(...) -> None:
    # simulate router available vs unavailable
```

**Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py::test_internal_llm_tools_registered_only_when_available -v`  
Expected: FAIL.

**Step 5: Commit**

```bash
git add tests/test_mcp_tool_registration_modes.py
git commit -m "test(mcp): add failing dual-toolset registration tests"
```

### Task 2: Implement MCP dual toolset registration

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/agents/factory.py` (if availability helper is needed)

**Step 1: Add internal LLM availability probe**

```python
def _internal_llm_available() -> bool:
    ...
```

**Step 2: Ensure base tools always register**
- `analyze`, `crawl`, `crawl_detail_batch`, `db_query`, `help`
- add `runtime_status`, `program_patch`, `program_patch_batch`

**Step 3: Register `_internal_llm` tools only when available**
- `analyze_internal_llm`, `crawl_internal_llm`, `crawl_detail_batch_internal_llm`

**Step 4: Run Task 1 tests**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/api/server.py src/agents/factory.py
git commit -m "feat(mcp): split base and internal_llm toolsets"
```

### Task 3: Add failing tests for runtime status and provider resolution metadata

**Files:**
- Create: `tests/test_mcp_runtime_status.py`
- Modify: `src/api/server.py`
- Modify: `src/services/browser_provider.py`

**Step 1: Write failing test for `runtime_status` payload**

```python
def test_runtime_status_reports_client_and_internal_llm(...) -> None:
    ...
```

**Step 2: Write failing test for crawl/analyze response metadata**

```python
def test_mcp_tools_return_resolved_browser_provider(...) -> None:
    ...
```

**Step 3: Run failing tests**

Run: `uv run pytest tests/test_mcp_runtime_status.py -v`  
Expected: FAIL.

**Step 4: Commit**

```bash
git add tests/test_mcp_runtime_status.py
git commit -m "test(mcp): add failing runtime status/metadata tests"
```

### Task 4: Implement runtime status and standardized metadata fields

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/services/crawler.py`
- Modify: `src/services/browser_provider.py`

**Step 1: Add `runtime_status` MCP tool**
- include `client_available`, `client_count`, `client_ids`, `internal_llm_available`, `default_browser_provider_resolved`

**Step 2: Return `resolved_browser_provider` and `client_id_used` in analyze/crawl tool responses**

**Step 3: Keep auto/client/server semantics unchanged**
- `strict_client=true` still hard-fails if no client

**Step 4: Run Task 3 tests**

Run: `uv run pytest tests/test_mcp_runtime_status.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/api/server.py src/services/crawler.py src/services/browser_provider.py
git commit -m "feat(mcp): expose runtime_status and provider resolution metadata"
```

### Task 5: Add failing tests for year-gating and taxonomy decision policy

**Files:**
- Create: `tests/test_mcp_interactive_decision_policy.py`
- Modify: `src/api/server.py`
- Modify: `src/services/ingestion_pipeline.py`

**Step 1: Write failing test for missing-year gate**

```python
def test_crawl_requires_year_before_execution(...) -> None:
    # expect requires_user_input/missing_fields
```

**Step 2: Write failing test for taxonomy threshold policy**

```python
def test_auto_ready_when_scores_high_and_count_le_10(...) -> None:
    # retain >=0.75, auto-run >=0.92 and <=10
```

**Step 3: Write failing test for review-required branch**

```python
def test_review_required_when_count_gt_10_or_low_confidence(...) -> None:
    ...
```

**Step 4: Run tests to verify failures**

Run: `uv run pytest tests/test_mcp_interactive_decision_policy.py -v`  
Expected: FAIL.

**Step 5: Commit**

```bash
git add tests/test_mcp_interactive_decision_policy.py
git commit -m "test(mcp): add failing year-gate and taxonomy decision policy tests"
```

### Task 6: Implement decision policy in base tools

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/services/crawler.py`
- Modify: `src/services/ingestion_pipeline.py` (reuse existing scoring helpers)

**Step 1: Implement missing-year guard**
- return structured `requires_user_input`, `missing_fields=["year"]`, `prompt`

**Step 2: Reuse taxonomy scoring for candidate ranking**
- retain threshold `0.75`
- auto-run threshold `0.92`

**Step 3: Add structured decision fields**
- `auto_ready`, `decision_reason`, `requires_user_review`

**Step 4: Run Task 5 tests**

Run: `uv run pytest tests/test_mcp_interactive_decision_policy.py -v`  
Expected: PASS.

**Step 5: Run regression tests**

Run: `uv run pytest tests/test_api_taxonomy_overrides.py tests/test_ingestion_pipeline.py -v`  
Expected: PASS.

**Step 6: Commit**

```bash
git add src/api/server.py src/services/crawler.py src/services/ingestion_pipeline.py
git commit -m "feat(mcp): add year-gating and taxonomy-driven auto/review decision flow"
```

### Task 7: Add failing tests for review-and-patch loop

**Files:**
- Create: `tests/test_mcp_program_patch_tools.py`
- Modify: `src/api/server.py`
- Modify: `src/storage/db_manager.py`
- Modify: `src/api/schemas.py`

**Step 1: Write failing test for `program_patch`**

```python
def test_program_patch_updates_single_record(...) -> None:
    ...
```

**Step 2: Write failing test for `program_patch_batch` with index-range mapping**

```python
def test_program_patch_batch_updates_multiple_records(...) -> None:
    ...
```

**Step 3: Write failing test for partial failures**

```python
def test_program_patch_batch_returns_failed_items_without_abort(...) -> None:
    ...
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_program_patch_tools.py -v`  
Expected: FAIL.

**Step 5: Commit**

```bash
git add tests/test_mcp_program_patch_tools.py
git commit -m "test(mcp): add failing program_patch and batch patch tests"
```

### Task 8: Implement review token + patch MCP tools

**Files:**
- Modify: `src/api/server.py`
- Modify: `src/storage/db_manager.py`
- Modify: `src/api/schemas.py`
- Modify: `src/services/crawler.py`

**Step 1: Add review payload fields in crawl outputs**
- include ordered result list and stable `program_id`
- include `review_token` (request-scoped correlation id)

**Step 2: Implement `program_patch` MCP tool**
- accepts `program_id` + patch body

**Step 3: Implement `program_patch_batch` MCP tool**
- accepts list of `{program_id, patch}` entries
- returns `updated_count`, `failed_items`, `summary`

**Step 4: Ensure no all-or-nothing abort**
- per-item error capture for batch patch

**Step 5: Run Task 7 tests**

Run: `uv run pytest tests/test_mcp_program_patch_tools.py -v`  
Expected: PASS.

**Step 6: Commit**

```bash
git add src/api/server.py src/storage/db_manager.py src/api/schemas.py src/services/crawler.py
git commit -m "feat(mcp): add review_token and program patch tools for user feedback corrections"
```

### Task 9: Documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `change_log.md`

**Step 1: Document dual toolset naming and semantics**
- base tools vs `_internal_llm` tools
- conditional registration rules

**Step 2: Document runtime and decision flow**
- `runtime_status`
- year-gating
- taxonomy thresholds (`0.75`, `0.92`)
- review-and-patch loop

**Step 3: Run targeted tests**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py tests/test_mcp_runtime_status.py tests/test_mcp_interactive_decision_policy.py tests/test_mcp_program_patch_tools.py -v`  
Expected: PASS.

**Step 4: Run full test suite**

Run: `uv run pytest`  
Expected: PASS.

**Step 5: Run lint gate**

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 6: Commit**

```bash
git add README.md PROJECT_CONTEXT.md change_log.md
git commit -m "docs(mcp): explain dual toolsets runtime status and correction workflow"
```

