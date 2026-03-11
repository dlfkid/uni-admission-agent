# Agent Onhold Batch Review Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable true agent orchestration for single-entry crawl runs where low-confidence cases are placed on hold, summarized in confidence-descending order, and processed only after user selects dynamic indices.

**Architecture:** Keep `/crawl` unchanged and implement this behavior only through agent runtime entrypoints. Extend `PydanticAIRuntime` from skeleton to multi-step orchestration with two phases: (1) auto-process high-confidence items and aggregate low-confidence `onhold_items`; (2) user-confirm selected onhold indices and process only selected items, default-discard others. Persist all review context in `TaskManager` task result so REST/MCP confirm tools can resume without introducing new DB tables.

**Tech Stack:** Python 3.12, FastAPI, MCP, Pydantic v2, existing ingestion pipeline + taxonomy ranking, pytest, pylint.

---

### Task 1: Add typed onhold review models and sorting contracts

**Files:**
- Create: `src/agent_runtime/review_models.py`
- Create: `tests/test_agent_onhold_review_models.py`
- Modify: `src/agent_runtime/__init__.py`

**Step 1: Write failing tests for confidence-desc sorting and dynamic indexing**

```python
def test_build_onhold_items_sorted_by_confidence_desc():
    raw = [
        {"url": "u2", "confidence": 0.51},
        {"url": "u1", "confidence": 0.87},
        {"url": "u3", "confidence": 0.63},
    ]
    items = build_onhold_items(raw)
    assert [item.index for item in items] == [1, 2, 3]
    assert [item.source_url for item in items] == ["u1", "u3", "u2"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_onhold_review_models.py -v`
Expected: FAIL (`build_onhold_items` missing)

**Step 3: Implement onhold models and builder**

```python
class OnholdItem(BaseModel):
    index: int
    item_id: str
    source_url: str
    program_name_candidate: str | None = None
    confidence: float
    hold_reason: str


def build_onhold_items(raw_items: list[dict[str, Any]]) -> list[OnholdItem]:
    ranked = sorted(raw_items, key=lambda row: float(row.get("confidence") or 0.0), reverse=True)
    return [
        OnholdItem(
            index=i + 1,
            item_id=str(row.get("item_id") or f"hold-{i+1}"),
            source_url=str(row.get("url") or ""),
            confidence=float(row.get("confidence") or 0.0),
            hold_reason=str(row.get("hold_reason") or "low_confidence"),
            program_name_candidate=row.get("program_name_candidate"),
        )
        for i, row in enumerate(ranked)
    ]
```

**Step 4: Re-run test to verify it passes**

Run: `uv run pytest tests/test_agent_onhold_review_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent_runtime/review_models.py src/agent_runtime/__init__.py tests/test_agent_onhold_review_models.py
git commit -m "feat(agent-review): add typed onhold models with confidence-desc indexing"
```

### Task 2: Add user selection parser for dynamic index input

**Files:**
- Create: `src/agent_runtime/review_selection.py`
- Create: `tests/test_agent_review_selection_parser.py`

**Step 1: Write failing tests for comma/space/range parsing and invalid indices**

```python
def test_parse_selection_supports_ranges_and_csv():
    parsed = parse_selected_indices("continue 1-3, 6 9")
    assert parsed.selected == [1, 2, 3, 6, 9]


def test_parse_selection_reports_invalid_tokens():
    parsed = parse_selected_indices("2,foo,10-8")
    assert parsed.selected == [2]
    assert parsed.invalid_tokens == ["foo", "10-8"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_review_selection_parser.py -v`
Expected: FAIL (`parse_selected_indices` missing)

**Step 3: Implement parser and result model**

```python
class SelectionParseResult(BaseModel):
    selected: list[int] = Field(default_factory=list)
    invalid_tokens: list[str] = Field(default_factory=list)


def parse_selected_indices(text: str) -> SelectionParseResult:
    # tokenize by comma/space; support "a-b" ranges; dedupe+sort positive ints
```

**Step 4: Re-run tests to verify pass**

Run: `uv run pytest tests/test_agent_review_selection_parser.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent_runtime/review_selection.py tests/test_agent_review_selection_parser.py
git commit -m "feat(agent-review): add dynamic index selection parser"
```

### Task 3: Upgrade runtime orchestration to produce onhold review phase

**Files:**
- Create: `tests/test_agent_runtime_onhold_orchestration.py`
- Modify: `src/agent_runtime/pydanticai_runtime.py`
- Modify: `src/agent_runtime/skills/contracts.py`
- Modify: `src/agent_runtime/skills/impl/common.py`
- Modify: `src/agent_runtime/skills/registry.py`
- Modify: `src/agent_runtime/base.py`

**Step 1: Write failing tests for high-confidence auto-run + low-confidence onhold summary**

```python
@pytest.mark.asyncio
async def test_runtime_returns_wait_user_selection_with_onhold_items():
    runtime = PydanticAIRuntime(...)
    result = await runtime.run(AgentRequest(task="crawl", payload={...}))
    assert result.status == "wait_user_selection"
    assert result.output["onhold_items"][0]["confidence"] >= result.output["onhold_items"][1]["confidence"]
```

**Step 2: Run test to verify fail**

Run: `uv run pytest tests/test_agent_runtime_onhold_orchestration.py -v`
Expected: FAIL (runtime still skeleton)

**Step 3: Implement orchestration with confidence split**

```python
# in select_detail_candidates_skill output add taxonomy_score per candidate
# runtime split logic:
# auto_candidates: score >= taxonomy_auto_threshold
# onhold_candidates: keep_threshold <= score < taxonomy_auto_threshold
# discard_candidates: score < keep_threshold

if onhold_items:
    return AgentResponse(status="wait_user_selection", runtime_used="pydanticai", ...)
return AgentResponse(status="done", runtime_used="pydanticai", ...)
```

**Step 4: Re-run tests to verify pass**

Run: `uv run pytest tests/test_agent_runtime_onhold_orchestration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent_runtime/base.py src/agent_runtime/pydanticai_runtime.py src/agent_runtime/skills/contracts.py src/agent_runtime/skills/impl/common.py src/agent_runtime/skills/registry.py tests/test_agent_runtime_onhold_orchestration.py
git commit -m "feat(agent-runtime): add onhold review phase for low-confidence cases"
```

### Task 4: Add review confirmation service (selected apply + default discard)

**Files:**
- Create: `tests/test_agent_review_confirmation_service.py`
- Modify: `src/services/crawler.py`

**Step 1: Write failing tests for selected-only processing and default discard**

```python
@pytest.mark.asyncio
async def test_confirm_onhold_processes_selected_indices_only():
    summary = await run_agent_review_confirmation(..., selected_indices=[3, 6, 18])
    assert summary["selected_count"] == 3
    assert summary["discarded_count"] == summary["total_onhold"] - 3
```

**Step 2: Run test to verify fail**

Run: `uv run pytest tests/test_agent_review_confirmation_service.py -v`
Expected: FAIL (`run_agent_review_confirmation` missing)

**Step 3: Implement confirmation service**

```python
async def run_agent_review_confirmation(*, task_payload: dict, onhold_items: list[dict], selected_indices: list[int]) -> dict:
    # map index -> item
    # selected set
    # process selected urls only (reuse crawl_selected_detail_urls_via_client / ingest path)
    # mark unselected as discarded
    # return summary
```

**Step 4: Re-run tests to verify pass**

Run: `uv run pytest tests/test_agent_review_confirmation_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/crawler.py tests/test_agent_review_confirmation_service.py
git commit -m "feat(agent-review): add selected-index confirmation processing service"
```

### Task 5: Add REST review confirm endpoint

**Files:**
- Create: `tests/test_agent_api_review_confirm.py`
- Modify: `src/api/schemas.py`
- Modify: `src/api/server.py`

**Step 1: Write failing API tests**

```python
def test_agent_review_confirm_rejects_invalid_indices(...):
    ...


def test_agent_review_confirm_applies_selected_and_discards_rest(...):
    ...
```

**Step 2: Run tests to verify fail**

Run: `uv run pytest tests/test_agent_api_review_confirm.py -v`
Expected: FAIL (endpoint missing)

**Step 3: Implement request/response schema + endpoint**

```python
class AgentReviewConfirmRequest(BaseModel):
    task_id: str
    selection_text: str | None = None
    selected_indices: list[int] | None = None


@app.post("/agent/review/confirm")
async def api_agent_review_confirm(body: AgentReviewConfirmRequest) -> AgentRunResponse:
    # parse selection text when provided
    # validate against onhold index range
    # call run_agent_review_confirmation
    # update task result summary
```

**Step 4: Re-run tests to verify pass**

Run: `uv run pytest tests/test_agent_api_review_confirm.py tests/test_agent_api_entrypoints.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/api/schemas.py src/api/server.py tests/test_agent_api_review_confirm.py tests/test_agent_api_entrypoints.py
git commit -m "feat(agent-api): add batch review confirm endpoint for onhold items"
```

### Task 6: Add MCP `agent_review_confirm` tool and registration gate

**Files:**
- Modify: `tests/test_mcp_tool_registration_modes.py`
- Create: `tests/test_mcp_agent_review_confirm.py`
- Modify: `src/api/server.py`

**Step 1: Write failing MCP tests for conditional registration and tool behavior**

```python
def test_agent_review_confirm_registered_only_when_agent_enabled(...):
    ...


@pytest.mark.asyncio
async def test_mcp_agent_review_confirm_applies_selected_indices(...):
    ...
```

**Step 2: Run tests to verify fail**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py tests/test_mcp_agent_review_confirm.py -v`
Expected: FAIL

**Step 3: Implement MCP tool**

```python
@mcp.tool(name="agent_review_confirm")
async def mcp_agent_review_confirm(task_id: str, selection_text: str = "") -> dict:
    parsed = parse_selected_indices(selection_text)
    return await run_agent_review_confirmation(...)
```

**Step 4: Re-run tests to verify pass**

Run: `uv run pytest tests/test_mcp_tool_registration_modes.py tests/test_mcp_agent_review_confirm.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/api/server.py tests/test_mcp_tool_registration_modes.py tests/test_mcp_agent_review_confirm.py
git commit -m "feat(agent-mcp): add onhold batch review confirm tool"
```

### Task 7: Update docs and user guidance for onhold batch review flow

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `change_log.md`

**Step 1: Add docs for user flow**

```markdown
- Agent runs high-confidence items automatically.
- Low-confidence items are listed as onhold (confidence-desc, dynamic index).
- User confirms with index selection (e.g., "continue 3,6,18").
- Unselected onhold items are discarded by default.
```

**Step 2: Verify docs mention REST + MCP confirm entrypoints**

Run: `rg -n "agent/review/confirm|agent_review_confirm|onhold" README.md PROJECT_CONTEXT.md change_log.md`
Expected: hits in all three files

**Step 3: Commit docs**

```bash
git add README.md PROJECT_CONTEXT.md change_log.md
git commit -m "docs(agent-review): document onhold batch confirmation workflow"
```

### Task 8: Final verification gates

**Files:**
- Modify (if needed): failing tests/lint findings from this feature

**Step 1: Run new/changed test suites**

Run:
`uv run pytest tests/test_agent_onhold_review_models.py tests/test_agent_review_selection_parser.py tests/test_agent_runtime_onhold_orchestration.py tests/test_agent_review_confirmation_service.py tests/test_agent_api_review_confirm.py tests/test_mcp_agent_review_confirm.py tests/test_agent_api_entrypoints.py tests/test_mcp_tool_registration_modes.py -v`
Expected: PASS

**Step 2: Run full suite and lint**

Run:
- `uv run pytest`
- `uv run pylint $(git ls-files '*.py')`
Expected: PASS

**Step 3: Run critical regressions**

Run:
`uv run pytest tests/test_crawler_service_phase2.py tests/test_api_crawl_browser_provider.py tests/test_mcp_runtime_status.py tests/test_taxonomy_name_resolution.py -v`
Expected: PASS

**Step 4: Final commit for any last fixes**

```bash
git add -A
git commit -m "test(agent-review): finalize verification fixes" || true
```
