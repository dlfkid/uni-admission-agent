# Agent Strategy Optimization Design

**Date:** 2026-03-20
**Status:** Draft

## Problem

The agent runtime exposes ~30 tools to the LLM, including team/protocol/worktree/autonomy tools designed for multi-agent collaboration. When given a simple single-page crawl task, the LLM prefers spawning teams and protocols instead of directly calling `browser_automation_skill` + `persist_programs_skill`. This causes:

1. Unnecessary complexity — teams/protocols add overhead with no benefit for single-page tasks
2. Timeouts — the extra coordination rounds consume time and context
3. 409 Conflict — hung tasks block subsequent task submissions in the smoke test

## Design

### 1. Tool Set Trimming by `page_type_hint`

`build_openai_tools()` in `loop.py` accepts a `page_type_hint` parameter that controls which tool categories are included.

**Complete tool inventory and trimming matrix:**

| Category | Actual tool names | Count | `detail` | `index` | No hint |
|----------|-------------------|:-----:|:---:|:---:|:---:|
| Skills (core) | `browser_automation_skill`, `persist_programs_skill`, `review_patch_skill`, `query_db_skill`, `analyze_page_skill`, `select_detail_candidates_skill` | 6 | Yes | Yes | Yes |
| Skills (legacy) | `legacy_crawl_batch_skill` | 1 | Yes | Yes | Yes |
| Knowledge | `load_skill` | 1 | Yes | Yes | Yes |
| Todo | `todo` | 1 | Yes | Yes | Yes |
| Compact | `compact` | 1 | Yes | Yes | Yes |
| Task graph | `task_create`, `task_update`, `task_list`, `task_get` | 4 | Yes | Yes | Yes |
| Background | `bg_run`, `bg_check` | 2 | Yes | Yes | Yes |
| Subagent | `task` | 1 | No | Yes | Yes |
| Team | `team_spawn`, `team_send`, `team_inbox` | 3 | No | Yes | Yes |
| Protocol | `protocol_request`, `protocol_respond`, `protocol_status` | 3 | No | No | Yes |
| Worktree | `worktree_create`, `worktree_run`, `worktree_list`, `worktree_keep`, `worktree_remove` | 5 | No | No | Yes |
| Autonomy | `idle`, `claim_task` | 2 | No | No | Yes |

**Totals per mode:**

- `detail`: 16 tools (skills + knowledge + todo + compact + task graph + background)
- `index`: 20 tools (detail + subagent + team)
- No hint: 30 tools (all — backward compatible)

**Key rules:**

- **`detail` pages** get no team/subagent/protocol/worktree/autonomy tools. This strongly nudges the LLM toward direct execution.
- **`index` pages** add subagent + team tools so they can spawn sub-agents for each detail URL. Teammates inherit the `detail` hint — they cannot spawn further teams (no recursive fan-out).
- **Protocol/worktree/autonomy** are removed entirely for crawl tasks — they are collaboration features irrelevant to single-university crawling.
- **No hint** (default) preserves existing behavior for backward compatibility.
- The existing `include_task` parameter (used by subagent loops) composes with `page_type_hint`: a subagent with `detail` hint has neither `task` tool nor team tools.

**Implementation — plumbing path:**

1. `build_openai_tools(registry, *, include_task=True, page_type_hint=None)` — new parameter in `src/agent_runtime/loop.py:666`. Hard-code the category-to-tool mapping directly in this function (no registry schema changes needed).
2. `agent_loop(*, ..., page_type_hint=None)` — new parameter in `src/agent_runtime/loop.py:764`, passed through to `build_openai_tools()`.
3. `PydanticAIRuntime._run_agent()` in `src/agent_runtime/pydanticai_runtime.py` — extract `page_type_hint` from `request.payload` (where the crawler places it at `src/services/crawler.py:661`) and pass it to `agent_loop()`.

### 2. Dual Timeout Mechanism

Two independent timeout layers prevent hangs at different levels.

**Layer 1 — LLM single-call timeout (8 minutes):**

- Wraps each `client.chat.completions.create()` call in `asyncio.wait_for(coro, timeout=480)`
- On timeout: log warning, discard the timed-out response (do not add to history), inject a system message: `"Your last LLM call timed out after 8 minutes. The context may be too large. Try a simpler approach or call compact to reduce context."`, then continue the loop.
- If 2 consecutive LLM calls timeout, break the loop and return a partial result with `"error": "consecutive LLM timeouts"`.
- Catches Volcengine API hangs from oversized context.

**Layer 2 — Per-page timeout (15 minutes):**

- Applied in `_run_agent()` wrapping the `agent_loop()` call:

```python
try:
    result = await asyncio.wait_for(
        agent_loop(..., page_type_hint=hint),
        timeout=PAGE_TIMEOUT,
    )
except asyncio.TimeoutError:
    raise AgentPageTimeout(f"agent_loop exceeded {PAGE_TIMEOUT}s") from None
```

- `AgentPageTimeout` is defined in `src/agent_runtime/loop.py` as a subclass of `Exception`.
- The caller `PydanticAIRuntime.run()` must be modified: the current broad `except Exception` at line 45 catches all exceptions and falls back to `LegacyRuntime`. This must be changed to re-raise `AgentPageTimeout` before the fallback catch:

```python
async def run(self, request: AgentRequest) -> AgentResponse:
    try:
        return await self._run_agent(request)
    except AgentPageTimeout:
        raise  # Do NOT fall back — timeouts are not runtime failures
    except Exception as exc:
        logger.warning("pydanticai runtime failed, falling back: %s", exc)
        return await self.fallback_runtime.run(request)
```

- For index mode: each step (each detail URL delegation via subagent/team) has its own agent loop with its own 15-min window. Total index time is unlimited.

**Constants** (top of `src/agent_runtime/loop.py`):

```python
LLM_CALL_TIMEOUT = 480    # 8 minutes
PAGE_TIMEOUT = 900         # 15 minutes
```

### 3. Smoke Test Cancel Logic

The smoke test enforces per-task timeout and cancels before proceeding to the next test. **This is new behavior** — the current `run_single_test()` simply returns `{"status": "TIMEOUT"}` without cancelling the server-side task.

**Cancel endpoint** (already exists): `POST /tasks/{task_id}/cancel` in `src/api/server.py:1057`. Calls `task_manager.cancel_task(task_id)` which cancels the asyncio.Task and sets state to `FAILED` with error `"Cancelled by user"`.

**Changes to `run_single_test()` in `scripts/e2e_agent_smoke.py`:**

1. Update `TIMEOUT` constant from `8 * 60` (480s) to `900` (15 min)
2. In the timeout branch (line 197-199), add cancel call before returning:

```python
if elapsed > TIMEOUT:
    print(f"  TIMEOUT after {elapsed:.0f}s — cancelling task")
    await client.post(f"/tasks/{task_id}/cancel")
    await asyncio.sleep(2)  # Wait for cancellation to propagate
    return {"status": "TIMEOUT", "duration_sec": elapsed, "programs": []}
```

**Key behaviors:**

- Each test case is independent — timeout on one does not skip the rest
- Results are collected and summarized: pass/fail/timeout per test
- The `e2e_results/` output includes timeout info for debugging

## Files Affected

- `src/agent_runtime/loop.py` — `build_openai_tools()` signature + filtering logic, `agent_loop()` signature + LLM timeout wrapper, new `AgentPageTimeout` exception, new constants
- `src/agent_runtime/pydanticai_runtime.py` — extract `page_type_hint` from `request.payload`, wrap `agent_loop()` in `asyncio.wait_for`, re-raise `AgentPageTimeout` before fallback catch
- `scripts/e2e_agent_smoke.py` — update `TIMEOUT` to 900, add cancel-on-timeout logic in `run_single_test()`

## Non-Goals

- Changing the LLM model or provider
- Modifying the system prompt (tool trimming is sufficient)
- Adding retry logic for failed LLM calls (timeout + continue is enough)
- Rate limiting or throttling
- Modifying `SkillRegistry` schema (tool categories are hard-coded in `build_openai_tools`)
- Feature flags / environment variable toggles (the `page_type_hint=None` default preserves backward compatibility)
