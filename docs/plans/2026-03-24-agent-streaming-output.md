# Agent Streaming Output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add low-risk streaming UX for agent mode by introducing lifecycle event streaming for agent tasks and token streaming only for the final user-visible summary, while keeping structured extraction calls non-streaming and preserving current behavior.

**Architecture:** Keep `POST /agent/run` and the existing task/result model. Add a new SSE event feed for agent tasks and extend the agent runtime to emit structured lifecycle events through a shared sink. Add a separate final-summary streaming path that is optional and gracefully falls back to one-shot text when unsupported. Structured LLM extraction paths remain on the current synchronous `generate()` contract.

**Tech Stack:** FastAPI, SSE via `StreamingResponse`, AsyncOpenAI-compatible clients, current `TaskManager`, agent runtime loop, pytest, existing `scripts/e2e_agent_smoke.py`.

---

### Task 1: Add failing tests for agent event stream storage and schema

**Files:**
- Create: `tests/test_agent_event_stream.py`
- Modify: `src/api/task_manager.py`
- Modify: `src/agent_runtime/base.py`

**Step 1: Write the failing test**

```python
def test_task_manager_stores_agent_events_in_order() -> None:
    manager = TaskManager()
    task_id = manager.create_task(params={"mode": "agent"})
    manager.add_event(task_id, {"type": "agent_started", "seq": 1})
    manager.add_event(task_id, {"type": "llm_call_started", "seq": 2})

    task = manager.get_task(task_id)
    assert task is not None
    assert [event["type"] for event in task.events] == [
        "agent_started",
        "llm_call_started",
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_event_stream.py::test_task_manager_stores_agent_events_in_order -v`  
Expected: FAIL because `TaskInfo` does not yet store `events`.

**Step 3: Write minimal implementation**

```python
class TaskInfo:
    __slots__ = (..., "events")

    def __init__(self, task_id: str) -> None:
        ...
        self.events: list[dict[str, Any]] = []

class TaskManager:
    def add_event(self, task_id: str, event: dict[str, Any]) -> None:
        info = self._task_store.get(task_id)
        if info:
            info.events.append(dict(event))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_event_stream.py::test_task_manager_stores_agent_events_in_order -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent_event_stream.py src/api/task_manager.py src/agent_runtime/base.py
git commit -m "feat(agent): add task event storage for streaming progress"
```

### Task 2: Add failing tests for runtime event emission hooks

**Files:**
- Modify: `tests/test_agent_runtime_fallback.py`
- Modify: `src/agent_runtime/pydanticai_runtime.py`
- Modify: `src/agent_runtime/loop.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_pydanticai_runtime_emits_lifecycle_events(monkeypatch):
    emitted = []

    async def fake_loop(**_kwargs):
        return {
            "response": "done",
            "trace": [],
            "iterations": 1,
            "collected_programs": [],
        }

    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.agent_loop", fake_loop)
    runtime = PydanticAIRuntime()
    await runtime.run(
        AgentRequest(
            task="crawl",
            payload={"url": "https://example.com", "univ_slug": "x", "year": 2026},
            context={"event_sink": emitted.append},
        )
    )

    assert [event["type"] for event in emitted][:2] == [
        "agent_started",
        "llm_call_started",
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_runtime_fallback.py::test_pydanticai_runtime_emits_lifecycle_events -v`  
Expected: FAIL because no event sink is used yet.

**Step 3: Write minimal implementation**

```python
event_sink = request.context.get("event_sink")
_emit_event(event_sink, {"type": "agent_started", ...})
```

```python
async def agent_loop(..., event_sink: Callable[[dict[str, Any]], None] | None = None):
    _emit_loop_event(event_sink, "llm_call_started", iteration=iteration)
    ...
    _emit_loop_event(event_sink, "llm_call_finished", iteration=iteration)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_runtime_fallback.py::test_pydanticai_runtime_emits_lifecycle_events -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent_runtime_fallback.py src/agent_runtime/pydanticai_runtime.py src/agent_runtime/loop.py
git commit -m "feat(agent): emit runtime lifecycle events from agent loop"
```

### Task 3: Add failing tests for tool-call event emission

**Files:**
- Modify: `tests/test_agent_runtime_onhold_orchestration.py`
- Modify: `src/agent_runtime/loop.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_agent_loop_emits_tool_call_started_and_finished(monkeypatch):
    emitted = []
    ...
    assert "tool_call_started" in event_types
    assert "tool_call_finished" in event_types
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_runtime_onhold_orchestration.py::test_agent_loop_emits_tool_call_started_and_finished -v`  
Expected: FAIL.

**Step 3: Write minimal implementation**

```python
_emit_loop_event(event_sink, "tool_call_started", tool=fn_name, iteration=iteration)
...
_emit_loop_event(event_sink, "tool_call_finished", tool=fn_name, iteration=iteration)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_runtime_onhold_orchestration.py::test_agent_loop_emits_tool_call_started_and_finished -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent_runtime_onhold_orchestration.py src/agent_runtime/loop.py
git commit -m "feat(agent): emit tool lifecycle events for streaming progress"
```

### Task 4: Add failing tests for SSE endpoint over task events

**Files:**
- Create: `tests/test_agent_task_sse_disconnect.py`
- Modify: `src/api/server.py`

**Step 1: Write the failing test for endpoint existence**

```python
def test_agent_task_events_endpoint_streams_sse(client):
    task_id = ...
    response = client.get(f"/tasks/{task_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
```

**Step 2: Write the failing test for disconnect safety**

```python
def test_sse_disconnect_does_not_fail_task(monkeypatch):
    # simulate a task that continues after client stops consuming the generator
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_task_sse_disconnect.py -v`  
Expected: FAIL because endpoint does not exist.

**Step 4: Write minimal implementation**

```python
@app.get("/tasks/{task_id}/events")
async def api_task_events(task_id: str):
    async def event_stream():
        ...
        yield f"data: {json.dumps(event)}\\n\\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_task_sse_disconnect.py -v`  
Expected: PASS.

**Step 6: Commit**

```bash
git add tests/test_agent_task_sse_disconnect.py src/api/server.py
git commit -m "feat(api): add SSE task event stream for agent progress"
```

### Task 5: Add failing tests for final summary streaming contract

**Files:**
- Create: `tests/test_agent_summary_stream_fallback.py`
- Create: `src/agent_runtime/summary_stream.py`
- Modify: `src/agent_runtime/pydanticai_runtime.py`

**Step 1: Write the failing test for supported streaming**

```python
@pytest.mark.asyncio
async def test_summary_stream_emits_deltas_when_supported():
    chunks = ["Hello", " world"]
    emitted = []
    result = await generate_summary_with_stream(..., event_sink=emitted.append)
    assert any(event["type"] == "summary_delta" for event in emitted)
    assert result == "Hello world"
```

**Step 2: Write the failing test for fallback**

```python
@pytest.mark.asyncio
async def test_summary_stream_falls_back_to_one_shot_when_unsupported():
    emitted = []
    result = await generate_summary_with_stream(..., event_sink=emitted.append)
    assert result
    assert emitted[0]["type"] == "summary_started"
    assert emitted[-1]["type"] == "summary_finished"
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_summary_stream_fallback.py -v`  
Expected: FAIL because summary streaming helper does not exist.

**Step 4: Write minimal implementation**

```python
async def generate_summary_with_stream(...):
    try:
        async for chunk in provider.stream_text(...):
            ...
    except Exception:
        text = await provider.generate_text(...)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_summary_stream_fallback.py -v`  
Expected: PASS.

**Step 6: Commit**

```bash
git add tests/test_agent_summary_stream_fallback.py src/agent_runtime/summary_stream.py src/agent_runtime/pydanticai_runtime.py
git commit -m "feat(agent): add streamed final summary with graceful fallback"
```

### Task 6: Add failing tests for provider-side text streaming adapter

**Files:**
- Create: `tests/test_agent_text_stream_provider.py`
- Modify: `src/agent_runtime/model_provider.py`
- Modify: `src/agents/factory.py`

**Step 1: Write the failing test**

```python
def test_model_provider_adapter_exposes_stream_text_when_supported():
    adapter = ModelProviderAdapter(...)
    client = adapter.resolve(mode="internal")
    assert hasattr(client, "stream_text")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_text_stream_provider.py -v`  
Expected: FAIL.

**Step 3: Write minimal implementation**

```python
class ResolvedModelClient:
    ...
    stream_text: Callable[..., Any] | None = None
    generate_text: Callable[..., Any] | None = None
```

```python
return ResolvedModelClient(
    mode="internal",
    client=factory(),
    stream_text=_stream_text_internal,
    generate_text=_generate_text_internal,
)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_text_stream_provider.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent_text_stream_provider.py src/agent_runtime/model_provider.py src/agents/factory.py
git commit -m "feat(agent-llm): add text streaming adapter for final summary generation"
```

### Task 7: Add failing tests for `/agent/run` task integration with event sink

**Files:**
- Modify: `tests/test_agent_api_entrypoints.py`
- Modify: `src/api/server.py`
- Modify: `src/services/crawler.py`

**Step 1: Write the failing test**

```python
def test_agent_run_task_records_events(monkeypatch, client):
    ...
    status = client.get(f"/tasks/{task_id}").json()
    assert status["progress_meta"]["event"] in {"agent_task_started", "agent_task_succeeded"}
    assert status["events"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_api_entrypoints.py::test_agent_run_task_records_events -v`  
Expected: FAIL because task events are not wired through `/agent/run`.

**Step 3: Write minimal implementation**

```python
def _event_sink(event: dict[str, Any]) -> None:
    task_manager.add_event(task_id, event)
```

```python
result = await run_agent_crawl(..., event_sink=_event_sink)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_api_entrypoints.py::test_agent_run_task_records_events -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent_api_entrypoints.py src/api/server.py src/services/crawler.py
git commit -m "feat(agent-api): wire runtime events into task lifecycle"
```

### Task 8: Extend the existing E2E smoke test with streaming acceptance gates

**Files:**
- Modify: `scripts/e2e_agent_smoke.py`
- Create: `tests/test_e2e_agent_smoke_streaming.py`

**Step 1: Write the failing smoke-focused test**

```python
def test_e2e_agent_smoke_checks_streaming_acceptance(monkeypatch):
    # validate the smoke script now asserts:
    # - lifecycle events observed
    # - summary_delta or graceful fallback summary observed
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e2e_agent_smoke_streaming.py -v`  
Expected: FAIL because the smoke script does not yet enforce streaming conditions.

**Step 3: Write minimal implementation**

```python
assert any(event["type"] == "llm_call_started" for event in events)
assert any(event["type"] == "tool_call_finished" for event in events)
assert any(event["type"] == "agent_done" for event in events)
assert summary_delta_seen or summary_fallback_seen
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_e2e_agent_smoke_streaming.py -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/e2e_agent_smoke.py tests/test_e2e_agent_smoke_streaming.py
git commit -m "test(smoke): require streaming progress and summary behavior"
```

### Task 9: Run targeted regression suites for untouched structured paths

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_CONTEXT.md`

**Step 1: Add docs note that structured LLM calls remain non-streaming**

```markdown
- Agent lifecycle progress is streamed through SSE events.
- Final summary may stream token deltas.
- Cleaner extraction, page-type classification, and name resolution remain non-streaming for stability.
```

**Step 2: Run targeted regressions**

Run: `uv run pytest tests/test_cleaner_agent.py tests/test_dry_run.py tests/test_agent_runtime_fallback.py tests/test_agent_api_entrypoints.py tests/test_agent_event_stream.py tests/test_agent_summary_stream_fallback.py tests/test_agent_task_sse_disconnect.py -v`  
Expected: PASS.

**Step 3: Run existing smoke test with streaming checks**

Run: `uv run python scripts/e2e_agent_smoke.py`  
Expected: PASS, including streaming acceptance checks.

**Step 4: Commit docs updates**

```bash
git add README.md PROJECT_CONTEXT.md
git commit -m "docs(agent): document event streaming and summary streaming boundaries"
```

### Task 10: Final verification gate

**Files:**
- Modify: `change_log.md`

**Step 1: Record the feature in changelog**

```markdown
- Added agent SSE lifecycle events and streamed final summary output.
- Added smoke-test acceptance gate for streaming behavior.
```

**Step 2: Run full verification command set**

Run: `uv run pytest tests/test_agent_event_stream.py tests/test_agent_summary_stream_fallback.py tests/test_agent_task_sse_disconnect.py tests/test_agent_api_entrypoints.py tests/test_agent_runtime_fallback.py tests/test_e2e_agent_smoke_streaming.py tests/test_cleaner_agent.py tests/test_dry_run.py -v`  
Expected: PASS.

**Step 3: Run lint gate**

Run: `uv run pylint $(git ls-files '*.py')`  
Expected: exit code 0.

**Step 4: Re-run smoke gate**

Run: `uv run python scripts/e2e_agent_smoke.py`  
Expected: PASS.

**Step 5: Commit**

```bash
git add change_log.md
git commit -m "chore(agent): finalize streaming output verification gate"
```
