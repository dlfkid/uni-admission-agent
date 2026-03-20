# Agent Strategy Optimization Design

**Date:** 2026-03-20
**Status:** Draft

## Problem

The agent runtime exposes ~28 tools to the LLM, including team/protocol/worktree/autonomy tools designed for multi-agent collaboration. When given a simple single-page crawl task, the LLM prefers spawning teams and protocols instead of directly calling `browser_automation_skill` + `persist_programs_skill`. This causes:

1. Unnecessary complexity — teams/protocols add overhead with no benefit for single-page tasks
2. Timeouts — the extra coordination rounds consume time and context
3. 409 Conflict — hung tasks block subsequent task submissions in the smoke test

## Design

### 1. Tool Set Trimming by `page_type_hint`

`build_openai_tools()` in `loop.py` accepts a `page_type_hint` parameter that controls which tool categories are included.

| Category | Tools | `detail` page | `index` page | No hint |
|----------|-------|:---:|:---:|:---:|
| Skills (core) | `browser_automation_skill`, `persist_programs_skill`, `review_patch_skill`, `query_db_skill`, `analyze_page_skill`, `select_detail_candidates_skill` | Yes | Yes | Yes |
| Skills (legacy) | `legacy_crawl_batch_skill` | Yes | Yes | Yes |
| Knowledge | `load_skill_knowledge` | Yes | Yes | Yes |
| Team | `team_spawn`, `team_message`, `team_status` | No | Yes | Yes |
| Protocol | `protocol_request`, `protocol_status` | No | No | Yes |
| Worktree | `worktree_create`, `worktree_status` | No | No | Yes |
| Autonomy | `autonomy_*` tools | No | No | Yes |
| Todo | `todo_*` tools | Yes | Yes | Yes |

**Key rules:**

- **`detail` pages** get only skill + knowledge + todo tools (~12 tools instead of ~28). This strongly nudges the LLM toward direct execution.
- **`index` pages** get team tools so they can spawn sub-agents for each detail URL. Teammates inherit the `detail` hint — they cannot spawn further teams (no recursive fan-out).
- **Protocol/worktree/autonomy** are removed entirely for crawl tasks — they are collaboration features irrelevant to single-university crawling.
- **No hint** (default) preserves existing behavior for backward compatibility.

**Implementation:** `build_openai_tools(page_type_hint: str | None = None)` in `src/agent_runtime/loop.py`. The hint flows from the task request context through to tool building.

### 2. Dual Timeout Mechanism

Two independent timeout layers prevent hangs at different levels.

**Layer 1 — LLM single-call timeout (8 minutes):**

- Wraps each `client.chat.completions.create()` call in `asyncio.wait_for(coro, timeout=480)`
- On timeout: log warning, inject a system message telling the agent its last LLM call timed out, continue the loop (the agent can retry with a simpler approach)
- Catches Volcengine API hangs from oversized context

**Layer 2 — Per-page timeout (15 minutes):**

- Wraps the entire `run_agent_loop()` invocation for a single page/task
- On timeout: raises `AgentPageTimeout` (new exception), caller handles cleanup
- For index mode: each step (e.g., each detail URL delegation) gets its own 15-min window. Total index time is unlimited.
- The caller (`pydanticai_runtime.py` or smoke test) wraps the call in `asyncio.wait_for()`

**Constants** (top of `src/agent_runtime/loop.py`):

```python
LLM_CALL_TIMEOUT = 480    # 8 minutes
PAGE_TIMEOUT = 900         # 15 minutes
```

### 3. Smoke Test Cancel Logic

The smoke test enforces per-task timeout and cancels before proceeding to the next test.

**Flow per test case:**

1. Submit task via `POST /agent/task`
2. Poll for completion with `SMOKE_PAGE_TIMEOUT = 900` (15 min)
3. On timeout: `POST /agent/task/{task_id}/cancel` to cancel the hung task
4. Wait briefly (2s) for cancellation to propagate
5. Record result as `timeout` and continue to next test

**Key behaviors:**

- Each test case is independent — timeout on one does not skip the rest
- Results are collected and summarized: pass/fail/timeout per test
- The `e2e_results/` output includes timeout info for debugging

## Files Affected

- `src/agent_runtime/loop.py` — `build_openai_tools()` signature change, timeout wrappers, constants
- `src/agent_runtime/pydanticai_runtime.py` — pass `page_type_hint` from task context, per-page timeout wrapper
- `scripts/e2e_agent_smoke.py` — per-task timeout + cancel logic
- Possibly `src/agent_runtime/skills/registry.py` — if tool categories need metadata

## Non-Goals

- Changing the LLM model or provider
- Modifying the system prompt (tool trimming is sufficient)
- Adding retry logic for failed LLM calls (timeout + continue is enough)
- Rate limiting or throttling
