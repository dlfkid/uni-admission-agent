# Agent Strategy Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trim the agent's tool set by `page_type_hint` so detail pages use ~16 tools (no team/protocol/worktree), add dual timeouts (8 min per LLM call, 15 min per page), and cancel hung tasks in the smoke test.

**Architecture:** Three independent changes: (1) `build_openai_tools()` filters tools by hint, (2) `agent_loop()` wraps LLM calls with timeout and the caller wraps the whole loop, (3) the smoke test cancels timed-out tasks via the existing cancel endpoint.

**Tech Stack:** Python 3.11+, asyncio, OpenAI SDK, pytest, httpx

**Spec:** `docs/superpowers/specs/2026-03-20-agent-strategy-optimization-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/agent_runtime/loop.py` | Modify | Tool trimming in `build_openai_tools()`, LLM call timeout in loop body, `AgentPageTimeout` exception, new constants |
| `src/agent_runtime/pydanticai_runtime.py` | Modify | Pass `page_type_hint` to `agent_loop()`, wrap call with `PAGE_TIMEOUT`, re-raise `AgentPageTimeout` |
| `src/agent_runtime/team.py` | Modify | Pass `page_type_hint` through `spawn()` → `_teammate_loop()` → `agent_loop()` so teammates inherit `"detail"` hint |
| `scripts/e2e_agent_smoke.py` | Modify | Update `TIMEOUT` to 900, cancel task on timeout |
| `tests/test_tool_trimming.py` | Create | Unit tests for `build_openai_tools()` with different hints |
| `tests/test_agent_runtime_fallback.py` | Modify | Test that `AgentPageTimeout` is NOT caught by fallback |

---

### Task 1: Tool Trimming in `build_openai_tools()`

**Files:**
- Create: `tests/test_tool_trimming.py`
- Modify: `src/agent_runtime/loop.py:666-690`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_trimming.py`:

```python
import pytest

from src.agent_runtime.loop import build_openai_tools
from src.agent_runtime.skills.registry import build_skill_registry


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


@pytest.fixture()
def registry():
    return build_skill_registry()


def test_no_hint_returns_all_tools(registry):
    """No hint = backward compatible, all tools included."""
    tools = build_openai_tools(registry, page_type_hint=None)
    names = _tool_names(tools)
    # Must include team, protocol, worktree, autonomy
    assert "team_spawn" in names
    assert "protocol_request" in names
    assert "worktree_create" in names
    assert "idle" in names
    assert "task" in names  # subagent tool (include_task defaults True)


def test_detail_hint_excludes_collaboration_tools(registry):
    """Detail pages should NOT have team/subagent/protocol/worktree/autonomy."""
    tools = build_openai_tools(registry, page_type_hint="detail")
    names = _tool_names(tools)
    # Must include core skills
    assert "browser_automation_skill" in names
    assert "persist_programs_skill" in names
    assert "compact" in names
    assert "bg_run" in names
    # Must exclude collaboration tools
    assert "team_spawn" not in names
    assert "team_send" not in names
    assert "team_inbox" not in names
    assert "task" not in names  # subagent
    assert "protocol_request" not in names
    assert "protocol_respond" not in names
    assert "worktree_create" not in names
    assert "idle" not in names
    assert "claim_task" not in names


def test_detail_has_fewer_tools_than_no_hint(registry):
    """Detail mode should have significantly fewer tools than unrestricted."""
    detail = build_openai_tools(registry, page_type_hint="detail")
    full = build_openai_tools(registry, page_type_hint=None)
    # detail drops subagent(1) + team(3) + protocol(3) + worktree(5) + autonomy(2) = 14
    assert len(detail) == len(full) - 14


def test_index_hint_includes_team_but_not_protocol(registry):
    """Index pages get team + subagent, but not protocol/worktree/autonomy."""
    tools = build_openai_tools(registry, page_type_hint="index")
    names = _tool_names(tools)
    # Must include team + subagent
    assert "team_spawn" in names
    assert "team_send" in names
    assert "team_inbox" in names
    assert "task" in names
    # Must exclude protocol/worktree/autonomy
    assert "protocol_request" not in names
    assert "worktree_create" not in names
    assert "idle" not in names


def test_index_has_fewer_tools_than_no_hint(registry):
    """Index mode drops protocol(3) + worktree(5) + autonomy(2) = 10 tools."""
    index = build_openai_tools(registry, page_type_hint="index")
    full = build_openai_tools(registry, page_type_hint=None)
    assert len(index) == len(full) - 10


def test_include_task_false_overrides_index_hint(registry):
    """Subagent loops with index hint still exclude the task tool."""
    tools = build_openai_tools(
        registry, include_task=False, page_type_hint="index"
    )
    names = _tool_names(tools)
    assert "task" not in names
    # But team tools are still there for index
    assert "team_spawn" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_trimming.py -v`
Expected: FAIL — `build_openai_tools()` does not accept `page_type_hint` parameter yet.

- [ ] **Step 3: Implement tool trimming in `build_openai_tools()`**

Modify `src/agent_runtime/loop.py:666-690`. Replace the current `build_openai_tools` function:

```python
def build_openai_tools(
    registry: SkillRegistry,
    *,
    include_task: bool = True,
    page_type_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI tool definitions from all registered skills + built-ins.

    Args:
        include_task: When *True* (default, parent agent) the ``task`` tool is
            included so the LLM can spawn subagents.  Set to *False* for
            subagent loops to prevent recursive spawning.
        page_type_hint: Controls which tool categories are included.
            ``"detail"`` — core skills + knowledge + todo + compact + task graph
            + background only (16 tools).  ``"index"`` — adds subagent + team
            (20 tools).  ``None`` — all tools (backward compatible).
    """
    # Always-included tools
    tools: list[dict[str, Any]] = [
        _TODO_TOOL, _LOAD_SKILL_TOOL, _COMPACT_TOOL,
        *_TASK_GRAPH_TOOLS,
        _BG_RUN_TOOL, _BG_CHECK_TOOL,
    ]

    # Collaboration tools gated by page_type_hint
    if page_type_hint != "detail":
        # index and None both get team tools
        tools.extend(_TEAM_TOOLS)
        if include_task:
            tools.append(_TASK_TOOL)

    if page_type_hint is None:
        # Only unrestricted mode gets protocol/worktree/autonomy
        tools.extend(_PROTOCOL_TOOLS)
        tools.extend(_AUTONOMY_TOOLS)
        tools.extend(_WORKTREE_TOOLS)

    # Skill tools (always included)
    for name in registry:
        skill = registry._skills[name]  # noqa: SLF001
        tools.append(_skill_to_openai_tool(skill))

    return tools
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tool_trimming.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_tool_trimming.py src/agent_runtime/loop.py
git commit -m "feat(agent): add page_type_hint tool trimming to build_openai_tools"
```

---

### Task 2: Plumb `page_type_hint` through `agent_loop()`, `PydanticAIRuntime`, and teammates

**Files:**
- Modify: `src/agent_runtime/loop.py:764-788` and `1237-1251`
- Modify: `src/agent_runtime/pydanticai_runtime.py:53-79`
- Modify: `src/agent_runtime/team.py:119-149` and `170-211`

- [ ] **Step 1: Add `page_type_hint` parameter to `agent_loop()`**

Modify `src/agent_runtime/loop.py:764-788`. Add the parameter and pass it to `build_openai_tools`:

```python
async def agent_loop(
    *,
    user_message: str,
    registry: SkillRegistry,
    system_prompt: str = SYSTEM_PROMPT,
    max_iterations: int = MAX_ITERATIONS,
    _is_subagent: bool = False,
    _teammate_name: str | None = None,
    _message_bus: MessageBus | None = None,
    page_type_hint: str | None = None,
) -> dict[str, Any]:
```

And change line 788 from:
```python
    tools = build_openai_tools(registry, include_task=not _is_subagent)
```
to:
```python
    tools = build_openai_tools(
        registry,
        include_task=not _is_subagent,
        page_type_hint=page_type_hint,
    )
```

- [ ] **Step 2: Extract `page_type_hint` in `PydanticAIRuntime._run_agent()`**

Modify `src/agent_runtime/pydanticai_runtime.py:53-79`. Add hint extraction and pass it to `agent_loop`:

After line 60 (`registry = build_skill_registry()`), add:
```python
        hint = (request.payload or {}).get("page_type_hint")
```

Change the `agent_loop` call (line 75-79) to:
```python
        result = await agent_loop(
            user_message=user_message,
            registry=registry,
            system_prompt=system_prompt,
            page_type_hint=hint,
        )
```

- [ ] **Step 3: Propagate `page_type_hint` to `_handle_team_spawn` and teammate loop**

Teammates spawned from an `index` page must inherit `page_type_hint="detail"` so they cannot spawn further teams (no recursive fan-out).

Modify `src/agent_runtime/loop.py:1237-1251`. Add `page_type_hint` parameter to `_handle_team_spawn`:

```python
def _handle_team_spawn(
    fn_args_raw: str,
    team: TeammateManager,
    registry: SkillRegistry,
    page_type_hint: str | None = None,
) -> str:
    """Spawn a new teammate (s09)."""
    try:
        fn_args = json.loads(fn_args_raw)
        return team.spawn(
            name=fn_args.get("name", ""),
            role=fn_args.get("role", ""),
            prompt=fn_args.get("prompt", ""),
            registry=registry,
            page_type_hint="detail" if page_type_hint in ("index", "detail") else page_type_hint,
        )
    except Exception as exc:
        logger.warning("[AgentLoop] team_spawn failed: %s", exc)
        return json.dumps({"error": str(exc)})
```

Update the call site at line ~906 to pass the hint:
```python
                result_str = _handle_team_spawn(fn_args_raw, team, registry, page_type_hint)
```

Note: `page_type_hint` is already available as a local variable in `agent_loop()` from Step 1.

- [ ] **Step 4: Add `page_type_hint` to `TeammateManager.spawn()` and `_teammate_loop()`**

Modify `src/agent_runtime/team.py:119-149`. Add parameter to `spawn()`:

```python
    def spawn(
        self,
        name: str,
        role: str,
        prompt: str,
        registry: Any,
        page_type_hint: str | None = None,
    ) -> str:
```

And pass it to `_teammate_loop` at line 146:
```python
        async_task = asyncio.ensure_future(
            self._teammate_loop(name, role, prompt, registry, page_type_hint)
        )
```

Modify `src/agent_runtime/team.py:170-211`. Add parameter to `_teammate_loop()`:

```python
    async def _teammate_loop(
        self,
        name: str,
        role: str,
        prompt: str,
        registry: Any,
        page_type_hint: str | None = None,
    ) -> None:
```

And pass it to `agent_loop` at line 203:
```python
                result = await agent_loop(
                    user_message=current_prompt,
                    registry=registry,
                    system_prompt=system_prompt,
                    max_iterations=TEAMMATE_MAX_ITERATIONS,
                    _is_subagent=True,
                    _teammate_name=name,
                    _message_bus=self.bus,
                    page_type_hint=page_type_hint,
                )
```

- [ ] **Step 5: Run existing tests to verify nothing breaks**

Run: `uv run pytest tests/test_agent_runtime_fallback.py tests/test_agent_skill_registry.py tests/test_tool_trimming.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_runtime/loop.py src/agent_runtime/pydanticai_runtime.py src/agent_runtime/team.py
git commit -m "feat(agent): plumb page_type_hint through agent_loop, PydanticAIRuntime, and teammates"
```

---

### Task 3: LLM Call Timeout (Layer 1)

**Files:**
- Modify: `src/agent_runtime/loop.py` (constants at top, loop body at ~832)

- [ ] **Step 1: Add timeout constants**

At the top of `src/agent_runtime/loop.py`, near the existing `MAX_ITERATIONS = 25` line, add:

```python
LLM_CALL_TIMEOUT = 480    # 8 minutes — single LLM API call
PAGE_TIMEOUT = 900         # 15 minutes — entire agent_loop for one page
```

- [ ] **Step 2: Define `AgentPageTimeout` exception**

Below the constants, add:

```python
class AgentPageTimeout(Exception):
    """Raised when agent_loop exceeds PAGE_TIMEOUT."""
```

- [ ] **Step 3: Wrap LLM call with timeout**

Modify the LLM call block at line ~832. Replace:

```python
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools or None,
            max_tokens=32768,
        )
```

with:

```python
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools or None,
                    max_tokens=32768,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            consecutive_timeouts += 1
            logger.warning(
                "[AgentLoop] LLM call timed out after %ds at iteration %d "
                "(%d consecutive)",
                LLM_CALL_TIMEOUT, iteration, consecutive_timeouts,
            )
            if consecutive_timeouts >= 2:
                logger.error(
                    "[AgentLoop] %d consecutive LLM timeouts — aborting loop",
                    consecutive_timeouts,
                )
                return {
                    "response": "",
                    "trace": trace,
                    "iterations": iteration,
                    "todos": todo.items,
                    "error": "consecutive LLM timeouts",
                }
            messages.append({
                "role": "system",
                "content": (
                    "Your last LLM call timed out after 8 minutes. "
                    "The context may be too large. Try a simpler approach "
                    "or call compact to reduce context."
                ),
            })
            continue
```

Also add `consecutive_timeouts = 0` before the loop (after `iterations_since_todo = 0` at line 806), and add `consecutive_timeouts = 0` as reset after a successful LLM response (right after `choice = response.choices[0]` at line 839):

```python
        choice = response.choices[0]
        consecutive_timeouts = 0  # reset on successful call
```

- [ ] **Step 4: Update the import in `pydanticai_runtime.py`**

Modify `src/agent_runtime/pydanticai_runtime.py` line 10. Change:
```python
from src.agent_runtime.loop import agent_loop, SYSTEM_PROMPT
```
to:
```python
from src.agent_runtime.loop import agent_loop, AgentPageTimeout, PAGE_TIMEOUT, SYSTEM_PROMPT
```

- [ ] **Step 5: Run existing tests**

Run: `uv run pytest tests/test_tool_trimming.py tests/test_agent_runtime_fallback.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_runtime/loop.py src/agent_runtime/pydanticai_runtime.py
git commit -m "feat(agent): add LLM call timeout (8 min) with consecutive-timeout abort"
```

---

### Task 4: Page-Level Timeout (Layer 2) and `AgentPageTimeout` Handling

**Files:**
- Modify: `src/agent_runtime/pydanticai_runtime.py:42-47` and `53-79`
- Modify: `tests/test_agent_runtime_fallback.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_runtime_fallback.py`:

```python
from src.agent_runtime.loop import AgentPageTimeout


@pytest.mark.asyncio
async def test_page_timeout_is_not_caught_by_fallback(monkeypatch):
    """AgentPageTimeout must propagate — it should NOT trigger LegacyRuntime fallback."""
    runtime = PydanticAIRuntime()

    async def timeout_agent(_request):
        raise AgentPageTimeout("agent_loop exceeded 900s")

    monkeypatch.setattr(runtime, "_run_agent", timeout_agent)

    with pytest.raises(AgentPageTimeout):
        await runtime.run(AgentRequest(task="crawl", payload={"url": "https://x"}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_runtime_fallback.py::test_page_timeout_is_not_caught_by_fallback -v`
Expected: FAIL — currently the broad `except Exception` catches `AgentPageTimeout` and falls back.

- [ ] **Step 3: Implement the fix**

Modify `src/agent_runtime/pydanticai_runtime.py`. Change the `run()` method (lines 42-47):

```python
    async def run(self, request: AgentRequest) -> AgentResponse:
        try:
            return await self._run_agent(request)
        except AgentPageTimeout:
            raise  # Do NOT fall back — timeouts are not runtime failures
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("pydanticai runtime failed, falling back: %s", exc)
            return await self.fallback_runtime.run(request)
```

- [ ] **Step 4: Wrap `agent_loop()` call with `PAGE_TIMEOUT`**

In `_run_agent()`, change the `agent_loop` call to:

```python
        try:
            result = await asyncio.wait_for(
                agent_loop(
                    user_message=user_message,
                    registry=registry,
                    system_prompt=system_prompt,
                    page_type_hint=hint,
                ),
                timeout=PAGE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AgentPageTimeout(
                f"agent_loop exceeded {PAGE_TIMEOUT}s for task={request.task}"
            ) from None
```

Also add `import asyncio` at the top of the file.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_runtime_fallback.py -v`
Expected: All 3 tests PASS (existing 2 + new 1).

- [ ] **Step 6: Commit**

```bash
git add src/agent_runtime/pydanticai_runtime.py tests/test_agent_runtime_fallback.py
git commit -m "feat(agent): add PAGE_TIMEOUT wrapper and AgentPageTimeout re-raise"
```

---

### Task 5: Smoke Test Cancel-on-Timeout

**Files:**
- Modify: `scripts/e2e_agent_smoke.py:35` and `197-199`

- [ ] **Step 1: Update `TIMEOUT` constant**

Change line 35 from:
```python
TIMEOUT = 8 * 60  # 8 minutes per test
```
to:
```python
TIMEOUT = 900  # 15 minutes per test (matches PAGE_TIMEOUT)
```

- [ ] **Step 2: Add cancel-on-timeout logic**

Replace lines 197-199 (the timeout branch in `run_single_test`):

```python
        if elapsed > TIMEOUT:
            print(f"  TIMEOUT after {elapsed:.0f}s")
            return {"status": "TIMEOUT", "duration_sec": elapsed, "programs": []}
```

with:

```python
        if elapsed > TIMEOUT:
            print(f"  TIMEOUT after {elapsed:.0f}s — cancelling task")
            try:
                await client.post(f"/tasks/{task_id}/cancel")
                await asyncio.sleep(2)
            except Exception:
                pass  # Best-effort cancel
            return {"status": "TIMEOUT", "duration_sec": elapsed, "programs": []}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_agent_smoke.py
git commit -m "feat(smoke): update timeout to 15 min and cancel hung tasks"
```

---

### Task 6: Final Integration Test

- [ ] **Step 1: Run all unit tests**

Run: `uv run pytest tests/test_tool_trimming.py tests/test_agent_runtime_fallback.py tests/test_agent_skill_registry.py -v`
Expected: All tests PASS.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest --timeout=60 -x -q`
Expected: No regressions.

- [ ] **Step 3: Commit any fixes if needed**

Only if tests revealed issues in the previous steps.
