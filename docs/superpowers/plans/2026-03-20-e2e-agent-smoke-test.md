# E2E Agent Smoke Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dry-run support to the agent crawl pipeline and build a standalone E2E smoke script that exercises the full agent runtime against real university pages without database writes.

**Architecture:** Thread a `dry_run` flag from `AgentRunRequest` through 9 layers to `ingest_program_records_external()`, where it skips DB upsert but still validates data. A standalone script uses `httpx.ASGITransport` to run the FastAPI app in-process, fires agent requests against a randomly-selected golden sample, and outputs results as both terminal tables and JSON files.

**Tech Stack:** Python 3.12+, FastAPI, httpx (ASGITransport), asyncio, Pydantic v2

**Spec:** `docs/superpowers/specs/2026-03-20-e2e-agent-smoke-test-design.md`

---

## File Map

| File | Action | Responsibility |
|:-----|:-------|:---------------|
| `src/api/schemas.py:184-206` | Modify | Add `dry_run` field to `AgentRunRequest` |
| `src/api/server.py:652-664` | Modify | Pass `dry_run` to `run_agent_crawl()` |
| `src/services/crawler.py:633-676` | Modify | Accept `dry_run` param, put in `AgentRequest.context` |
| `src/services/crawler.py:679-794` | Modify | Add `dry_run` param to `ingest_program_records_external()`, skip upsert |
| `src/agent_runtime/pydanticai_runtime.py:53-65` | Modify | Extract `dry_run` from context, pass modified system_prompt to `agent_loop()` |
| `src/agent_runtime/skills/contracts.py:62-76` | Modify | Add `dry_run` to `PersistProgramsSkillInput` and output fields |
| `src/agent_runtime/skills/impl/common.py:36-42` | Modify | Forward `dry_run` to `ingest_program_records_external()` |
| `scripts/e2e_agent_smoke.py` | Create | E2E smoke test script |
| `.gitignore` | Modify | Add `e2e_results/` |
| `tests/test_dry_run.py` | Create | Unit tests for dry-run propagation |

---

### Task 1: Add `dry_run` to `PersistProgramsSkillInput` and `PersistProgramsSkillOutput`

**Files:**
- Modify: `src/agent_runtime/skills/contracts.py:62-76`
- Test: `tests/test_dry_run.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dry_run.py`:

```python
"""Tests for dry-run propagation through skill contracts and persistence."""

from src.agent_runtime.skills.contracts import (
    PersistProgramsSkillInput,
    PersistProgramsSkillOutput,
)


def test_persist_input_accepts_dry_run_flag():
    payload = PersistProgramsSkillInput(
        univ_slug="ucl", year=2026, programs=[], dry_run=True
    )
    assert payload.dry_run is True


def test_persist_input_defaults_dry_run_false():
    payload = PersistProgramsSkillInput(
        univ_slug="ucl", year=2026, programs=[]
    )
    assert payload.dry_run is False


def test_persist_output_includes_dry_run_and_parsed_programs():
    output = PersistProgramsSkillOutput(
        imported_count=0,
        dry_run=True,
        parsed_programs=[{"name_en": "Test Program"}],
    )
    assert output.dry_run is True
    assert len(output.parsed_programs) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dry_run.py -v`
Expected: FAIL — `dry_run` and `parsed_programs` fields don't exist yet.

- [ ] **Step 3: Add fields to contracts**

In `src/agent_runtime/skills/contracts.py`, modify `PersistProgramsSkillInput` (line 62):

```python
class PersistProgramsSkillInput(BaseModel):
    """Input payload for persistence skill."""

    univ_slug: str = Field(min_length=1)
    year: int = Field(gt=0)
    programs: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = Field(default=False)
```

Modify `PersistProgramsSkillOutput` (line 70):

```python
class PersistProgramsSkillOutput(BaseModel):
    """Output payload for persistence skill."""

    imported_count: int = 0
    updated_count: int = 0
    total_submitted: int = 0
    failed_items: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = False
    parsed_programs: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dry_run.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/skills/contracts.py tests/test_dry_run.py
git commit -m "feat(dry-run): add dry_run field to PersistPrograms skill contracts"
```

---

### Task 2: Implement dry-run logic in `ingest_program_records_external()`

**Files:**
- Modify: `src/services/crawler.py:679-794`
- Modify: `src/agent_runtime/skills/impl/common.py:36-42`
- Test: `tests/test_dry_run.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dry_run.py`:

```python
from unittest.mock import patch, MagicMock


def test_ingest_dry_run_skips_db_upsert():
    """When dry_run=True, no DatabaseManager.upsert_program calls are made."""
    with patch("src.services.crawler.DatabaseManager") as mock_db_cls:
        from src.services.crawler import ingest_program_records_external

        result = ingest_program_records_external(
            univ_slug="ucl",
            year=2026,
            programs=[{"name_en": "Test Program", "faculty": "Arts"}],
            dry_run=True,
        )
        mock_db_cls.return_value.upsert_program.assert_not_called()
        assert result["dry_run"] is True
        assert result["imported_count"] == 0
        assert len(result["parsed_programs"]) == 1
        assert result["parsed_programs"][0]["name_en"] == "Test Program"


def test_ingest_dry_run_still_validates():
    """dry_run=True still rejects items without name_en."""
    with patch("src.services.crawler.DatabaseManager"):
        from src.services.crawler import ingest_program_records_external

        result = ingest_program_records_external(
            univ_slug="ucl",
            year=2026,
            programs=[{"faculty": "Arts"}],  # missing name_en
            dry_run=True,
        )
        assert len(result["failed_items"]) == 1
        assert result["failed_items"][0]["error_code"] == "missing_name_en"
        assert len(result["parsed_programs"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dry_run.py::test_ingest_dry_run_skips_db_upsert -v`
Expected: FAIL — `ingest_program_records_external()` does not accept `dry_run` param.

- [ ] **Step 3: Implement dry-run in `ingest_program_records_external()`**

In `src/services/crawler.py`, modify the function signature at line 679:

```python
def ingest_program_records_external(
    *,
    univ_slug: str,
    year: int,
    programs: list[Any],
    dry_run: bool = False,
) -> dict[str, Any]:
```

After the validation loop (line 728-738), add a dry-run branch. Replace the try/except block at lines 740-761 with:

```python
        if dry_run:
            # Dry-run: skip DB, collect validated program data
            parsed_programs.append(payload)
            continue

        try:
            program, created = db.upsert_program(
                payload,
                normalized_univ_slug,
                enable_auto_translation=False,
            )
        except Exception as exc:  # pylint: disable=broad-except
            failed_items.append(
                {
                    "index": idx,
                    "error_code": "upsert_failed",
                    "message": str(exc),
                }
            )
            continue

        if getattr(program, "id", None) is not None:
            persisted_program_ids.append(int(program.id))
        if created:
            imported_count += 1
        else:
            updated_count += 1
```

Add `parsed_programs: list[dict[str, Any]] = []` right after `persisted_program_ids` (line 715).

Skip DB initialization when `dry_run=True` — wrap `db = DatabaseManager()` (line 711) in a conditional:

```python
    db = None if dry_run else DatabaseManager()
```

In the return dict (line 783), add:

```python
        "dry_run": dry_run,
        "parsed_programs": parsed_programs,
```

Also skip `_build_review_items` when `dry_run=True` — wrap lines 763-776:

```python
    if dry_run:
        review_items = []
    else:
        try:
            review_items = _build_review_items(
                univ_slug=normalized_univ_slug,
                year=normalized_year,
                persisted_program_ids=persisted_program_ids,
            )
        except Exception as exc:  # pylint: disable=broad-except
            ...
```

Also update the **early return** for empty `submitted_items` (lines 698-709) to include the new keys:

```python
    if not submitted_items:
        return {
            "imported_count": 0,
            "updated_count": 0,
            "total_submitted": 0,
            "failed_items": [],
            "review_token": uuid.uuid4().hex,
            "review_items": [],
            "univ_slug": normalized_univ_slug,
            "year": normalized_year,
            "summary": "No program records supplied.",
            "dry_run": dry_run,
            "parsed_programs": [],
        }
```

- [ ] **Step 4: Update `persist_programs_skill_handler` to forward dry_run**

In `src/agent_runtime/skills/impl/common.py`, modify `persist_programs_skill_handler` (line 36):

```python
def persist_programs_skill_handler(payload: PersistProgramsSkillInput) -> dict:
    """Persist caller-structured program records using external-ingest path."""
    return ingest_program_records_external(
        univ_slug=payload.univ_slug,
        year=payload.year,
        programs=payload.programs,
        dry_run=payload.dry_run,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dry_run.py -v`
Expected: 5 PASSED

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All existing tests pass (524+).

- [ ] **Step 7: Commit**

```bash
git add src/services/crawler.py src/agent_runtime/skills/impl/common.py tests/test_dry_run.py
git commit -m "feat(dry-run): skip DB upsert when dry_run=True in ingest pipeline"
```

---

### Task 3: Thread `dry_run` through API → runtime → agent loop

**Files:**
- Modify: `src/api/schemas.py:184-206`
- Modify: `src/api/server.py:652-664`
- Modify: `src/services/crawler.py:633-676`
- Modify: `src/agent_runtime/pydanticai_runtime.py:53-65`
- Test: `tests/test_dry_run.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dry_run.py`:

```python
def test_agent_run_request_accepts_dry_run():
    from src.api.schemas import AgentRunRequest

    req = AgentRunRequest(
        url="https://example.com",
        univ_slug="ucl",
        year=2026,
        dry_run=True,
    )
    assert req.dry_run is True


def test_agent_run_request_dry_run_defaults_false():
    from src.api.schemas import AgentRunRequest

    req = AgentRunRequest(
        url="https://example.com",
        univ_slug="ucl",
        year=2026,
    )
    assert req.dry_run is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dry_run.py::test_agent_run_request_accepts_dry_run -v`
Expected: FAIL — `dry_run` field doesn't exist on `AgentRunRequest`.

- [ ] **Step 3: Add `dry_run` to `AgentRunRequest`**

In `src/api/schemas.py`, add after line 205 (before closing of `AgentRunRequest`):

```python
    dry_run: bool = Field(
        default=False,
        description="Skip DB persistence; return parsed results only",
    )
```

- [ ] **Step 4: Pass `dry_run` in `api_agent_run()` to `run_agent_crawl()`**

In `src/api/server.py`, at line 652, add `dry_run=body.dry_run` to the `run_agent_crawl()` call:

```python
            result = await run_agent_crawl(
                url=body.url,
                univ_slug=body.univ_slug,
                year=body.year,
                page_type_hint=body.page_type_hint,
                runtime_mode=body.runtime,
                autonomous=body.autonomous,
                dry_run=body.dry_run,
                policy_profile=(
                    body.policy_profile.model_dump(exclude_none=True)
                    if body.policy_profile
                    else None
                ),
            )
```

- [ ] **Step 5: Accept `dry_run` in `run_agent_crawl()` and put in context**

In `src/services/crawler.py`, modify `run_agent_crawl()` signature (line 633):

```python
async def run_agent_crawl(
    *,
    url: str,
    univ_slug: str,
    year: int,
    page_type_hint: str = "auto",
    runtime_mode: Optional[str] = None,
    policy_profile: Optional[dict[str, Any]] = None,
    client_id: Optional[str] = None,
    autonomous: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
```

In the `context` dict (line 669), add:

```python
            context={
                "entrypoint": "api",
                "client_id": str(client_id).strip() if client_id else None,
                "autonomous": bool(autonomous),
                "dry_run": bool(dry_run),
            },
```

- [ ] **Step 6: Extract `dry_run` in `PydanticAIRuntime._run_agent()` and inject into system prompt**

In `src/agent_runtime/pydanticai_runtime.py`, modify `_run_agent()` (line 53):

```python
    async def _run_agent(self, request: AgentRequest) -> AgentResponse:
        """Hand the request to the agent loop and let the LLM drive."""
        logger.info(
            "[Agent] Starting LLM-driven loop for task=%s", request.task
        )

        user_message = self._build_user_message(request)
        registry = build_skill_registry()

        # Build system prompt with dry-run instruction if needed
        system_prompt = SYSTEM_PROMPT
        if request.context.get("dry_run"):
            system_prompt += (
                "\n\nIMPORTANT: dry_run mode is active. "
                "When calling persist_programs_skill, set dry_run=true "
                "in the payload. Do NOT attempt database writes."
            )

        result = await agent_loop(
            user_message=user_message,
            registry=registry,
            system_prompt=system_prompt,
        )
```

Add the SYSTEM_PROMPT import at the top of the file:

```python
from src.agent_runtime.loop import agent_loop, SYSTEM_PROMPT
```

- [ ] **Step 7: Write test for system prompt injection**

Append to `tests/test_dry_run.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_run_agent_injects_dry_run_into_system_prompt():
    """When context has dry_run=True, agent_loop receives a modified system_prompt."""
    from unittest.mock import AsyncMock, patch
    from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
    from src.agent_runtime.base import AgentRequest

    mock_loop = AsyncMock(return_value={
        "response": "done", "trace": [], "iterations": 1
    })

    with patch("src.agent_runtime.pydanticai_runtime.agent_loop", mock_loop):
        runtime = PydanticAIRuntime()
        request = AgentRequest(
            task="crawl",
            payload={"url": "https://example.com", "univ_slug": "test", "year": 2026},
            context={"dry_run": True},
        )
        await runtime._run_agent(request)

    # Verify agent_loop was called with a system_prompt containing dry_run instruction
    call_kwargs = mock_loop.call_args
    system_prompt = call_kwargs.kwargs.get("system_prompt", "")
    assert "dry_run" in system_prompt
    assert "persist_programs_skill" in system_prompt


@pytest.mark.asyncio
async def test_run_agent_no_dry_run_uses_default_prompt():
    """When dry_run is not set, system_prompt is the default."""
    from unittest.mock import AsyncMock, patch
    from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
    from src.agent_runtime.base import AgentRequest
    from src.agent_runtime.loop import SYSTEM_PROMPT

    mock_loop = AsyncMock(return_value={
        "response": "done", "trace": [], "iterations": 1
    })

    with patch("src.agent_runtime.pydanticai_runtime.agent_loop", mock_loop):
        runtime = PydanticAIRuntime()
        request = AgentRequest(
            task="crawl",
            payload={"url": "https://example.com", "univ_slug": "test", "year": 2026},
            context={},
        )
        await runtime._run_agent(request)

    call_kwargs = mock_loop.call_args
    system_prompt = call_kwargs.kwargs.get("system_prompt", "")
    assert system_prompt == SYSTEM_PROMPT
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_dry_run.py -v`
Expected: 9 PASSED

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All existing tests pass.

- [ ] **Step 10: Commit**

```bash
git add src/api/schemas.py src/api/server.py src/services/crawler.py src/agent_runtime/pydanticai_runtime.py tests/test_dry_run.py
git commit -m "feat(dry-run): thread dry_run from API through runtime to agent loop"
```

---

### Task 4: Add `e2e_results/` to `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add entry**

Append to `.gitignore`:

```
# E2E smoke test results
e2e_results/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add e2e_results/ to .gitignore"
```

---

### Task 5: Create the E2E smoke test script

**Files:**
- Create: `scripts/e2e_agent_smoke.py`

- [ ] **Step 1: Create the script**

Create `scripts/e2e_agent_smoke.py` with the following structure:

```python
#!/usr/bin/env python3
"""E2E Agent Smoke Test.

Exercises the full agent runtime (pydanticai mode) against a randomly
selected golden-sample university. Runs detail + index page crawls in
dry-run mode and outputs results for human quality judgment.

Usage:
    uv run python scripts/e2e_agent_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CASES_DIR = PROJECT_ROOT / "golden_samples" / "cases"
E2E_RESULTS_DIR = PROJECT_ROOT / "e2e_results"
POLL_INTERVAL = 3  # seconds
TIMEOUT = 8 * 60  # 8 minutes per test


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def check_env() -> None:
    """Verify .env exists and load it."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("ERROR: .env file not found at project root.")
        print(f"Expected: {env_path}")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(env_path)
    os.environ["AGENT_ENABLED"] = "true"
    print(f"Loaded .env from {env_path}")


def pick_golden_case() -> dict[str, Any]:
    """Randomly select a golden sample case and return its metadata."""
    cases = sorted(GOLDEN_CASES_DIR.iterdir())
    case_dirs = [d for d in cases if (d / "metadata.json").exists()]
    if not case_dirs:
        print("ERROR: No golden sample cases found.")
        sys.exit(1)

    chosen = random.choice(case_dirs)
    metadata = json.loads((chosen / "metadata.json").read_text())
    print(f"Selected case: {metadata['case_id']} ({metadata['name']})")
    return metadata


# ---------------------------------------------------------------------------
# Table rendering (stdlib only)
# ---------------------------------------------------------------------------

def truncate(text: str, width: int) -> str:
    """Truncate text to width, adding '...' if needed."""
    text = str(text or "")
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def render_programs_table(programs: list[dict[str, Any]]) -> str:
    """Render a simple ASCII table of programs."""
    if not programs:
        return "  (no programs extracted)"

    cols = [
        ("#", 4),
        ("Program Name", 30),
        ("Faculty", 18),
        ("Tuition", 12),
        ("Study Mode", 20),
    ]

    header_widths = [w for _, w in cols]
    sep = "+" + "+".join("-" * (w + 2) for w in header_widths) + "+"
    header = "|" + "|".join(
        f" {name:<{w}} " for name, w in cols
    ) + "|"

    lines = [sep, header, sep]
    for idx, prog in enumerate(programs, 1):
        study_opts = prog.get("study_options") or []
        study_mode = ""
        if study_opts and isinstance(study_opts, list):
            first = study_opts[0] if study_opts else {}
            if isinstance(first, dict):
                mode = first.get("study_mode") or ""
                dur = first.get("duration") or ""
                study_mode = f"{mode} / {dur}".strip(" /")

        row = "|" + "|".join([
            f" {truncate(str(idx), 4):<4} ",
            f" {truncate(prog.get('name_en') or prog.get('name', ''), 30):<30} ",
            f" {truncate(prog.get('faculty') or '', 18):<18} ",
            f" {truncate(prog.get('tuition_fee') or prog.get('tuition') or '', 12):<12} ",
            f" {truncate(study_mode, 20):<20} ",
        ]) + "|"
        lines.append(row)

    lines.append(sep)

    # Extra details per program
    for idx, prog in enumerate(programs, 1):
        deadlines = prog.get("deadlines") or []
        source = prog.get("source_url") or ""
        lines.append(
            f"  [{idx}] Deadlines: {len(deadlines)} item(s)"
            + (f"  |  Source: {truncate(source, 60)}" if source else "")
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def extract_programs(task_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract program list from agent task result."""
    output = task_result.get("output") or {}

    # Check parsed_programs from dry-run output
    parsed = output.get("parsed_programs") or []
    if parsed:
        return parsed

    # Fallback: check agent_response for structured data
    response_text = output.get("agent_response") or ""
    # Programs might be embedded in the response
    return parsed  # Return empty if nothing found


async def run_single_test(
    client: Any,
    *,
    label: str,
    url: str,
    univ_slug: str,
    year: int,
    page_type_hint: str,
    policy_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one agent test and poll until completion."""
    payload: dict[str, Any] = {
        "url": url,
        "univ_slug": univ_slug,
        "year": year,
        "page_type_hint": page_type_hint,
        "autonomous": True,
        "dry_run": True,
        "runtime": "pydanticai",
    }
    if policy_profile:
        payload["policy_profile"] = policy_profile

    print(f"\n{'═' * 50}")
    print(f"  [{label}] {univ_slug} — {page_type_hint}")
    print(f"  URL: {truncate(url, 70)}")
    print(f"{'═' * 50}")

    # Submit
    resp = await client.post("/agent/run", json=payload)
    if resp.status_code != 200:
        print(f"  ERROR: POST /agent/run returned {resp.status_code}")
        print(f"  Body: {resp.text}")
        return {"status": "ERROR", "error": resp.text, "programs": []}

    task_id = resp.json()["task_id"]
    print(f"  Task ID: {task_id}")

    # Poll
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT:
            print(f"  TIMEOUT after {elapsed:.0f}s")
            return {"status": "TIMEOUT", "duration_sec": elapsed, "programs": []}

        await asyncio.sleep(POLL_INTERVAL)

        status_resp = await client.get(f"/tasks/{task_id}")
        if status_resp.status_code != 200:
            continue

        data = status_resp.json()
        state = data.get("state", "")
        progress = data.get("progress") or ""

        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        print(f"  [{mins:02d}:{secs:02d}] {state} — {truncate(progress, 50)}", end="\r")

        if state == "DONE":
            duration = time.monotonic() - start
            print(f"\n  Status: DONE | Duration: {duration:.1f}s")
            result = data.get("result") or {}
            programs = extract_programs(result)
            print(f"  Programs extracted: {len(programs)}")
            print(render_programs_table(programs))
            return {
                "status": "DONE",
                "duration_sec": round(duration, 1),
                "url": url,
                "programs": programs,
                "raw_result": result,
            }

        if state == "FAILED":
            duration = time.monotonic() - start
            error = data.get("error") or "unknown"
            print(f"\n  FAILED after {duration:.1f}s: {error}")
            return {
                "status": "FAILED",
                "duration_sec": round(duration, 1),
                "error": error,
                "programs": [],
            }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    check_env()
    metadata = pick_golden_case()

    case_id = metadata["case_id"]
    univ_name = metadata["name"]
    # Derive slug from case_id (e.g. "ucl_undergrad_anthropology" -> "ucl")
    # or fall back to lowercased name
    univ_slug = case_id.split("_")[0] if "_" in case_id else univ_name.lower().replace(" ", "-")
    index_url = metadata["index_url"]
    detail_url = metadata["detail_url"]
    year = datetime.now(tz=timezone.utc).year

    # Import app after env is loaded
    from httpx import ASGITransport, AsyncClient
    from src.api.server import app

    transport = ASGITransport(app=app)  # type: ignore[arg-type]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        total_start = time.monotonic()

        # Test 1: Detail page
        detail_result = await run_single_test(
            client,
            label="Detail",
            url=detail_url,
            univ_slug=univ_slug,
            year=year,
            page_type_hint="detail",
        )

        # Test 2: Index page (max 3 candidates)
        index_result = await run_single_test(
            client,
            label="Index",
            url=index_url,
            univ_slug=univ_slug,
            year=year,
            page_type_hint="index",
            policy_profile={"auto_run_max_candidates": 3},
        )

        total_duration = time.monotonic() - total_start

    # Save JSON results
    E2E_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    result_path = E2E_RESULTS_DIR / f"{timestamp}_{univ_slug}.json"
    archive = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "case_id": case_id,
        "univ_name": univ_name,
        "year": year,
        "detail_test": {
            k: v for k, v in detail_result.items() if k != "raw_result"
        },
        "index_test": {
            k: v for k, v in index_result.items() if k != "raw_result"
        },
    }
    # Add auto_run_max_candidates to index test
    archive["index_test"]["auto_run_max_candidates"] = 3

    result_path.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False, default=str)
    )

    # Final summary
    detail_status = detail_result.get("status", "?")
    detail_progs = len(detail_result.get("programs") or [])
    detail_dur = detail_result.get("duration_sec", 0)

    index_status = index_result.get("status", "?")
    index_progs = len(index_result.get("programs") or [])
    index_dur = index_result.get("duration_sec", 0)

    print(f"\n{'═' * 50}")
    print(f"  University: {univ_name} ({case_id})")
    print(f"  Detail test:  {detail_status} — {detail_progs} program(s), {detail_dur:.1f}s")
    print(f"  Index test:   {index_status} — {index_progs} program(s), {index_dur:.1f}s")
    print(f"  Total time:   {total_duration:.1f}s")
    print(f"  Results saved: {result_path.relative_to(PROJECT_ROOT)}")
    print(f"{'═' * 50}")

    # Exit code: 0 if both tests succeeded, 1 otherwise
    if detail_status != "DONE" or index_status != "DONE":
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify script runs (basic syntax check)**

Run: `uv run python -c "import ast; ast.parse(open('scripts/e2e_agent_smoke.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_agent_smoke.py
git commit -m "feat(e2e): add agent smoke test script with dry-run and table output"
```

---

### Task 6: Run full test suite and pylint

**Files:** None (validation only)

- [ ] **Step 1: Run all existing tests**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass (524+).

- [ ] **Step 2: Run pylint**

Run: `uv run pylint $(git ls-files '*.py')`
Expected: 10.00/10 (or no new issues introduced).

- [ ] **Step 3: Fix any issues found and commit**

If pylint or tests fail, fix and commit with:
```bash
git commit -m "fix: address test/lint issues from dry-run implementation"
```

---

### Task 7: Manual E2E verification

**Files:** None (manual execution)

- [ ] **Step 1: Run the smoke test**

Run: `uv run python scripts/e2e_agent_smoke.py`

This will:
1. Load `.env` and set `AGENT_ENABLED=true`
2. Pick a random golden sample case
3. Start the FastAPI app in-process
4. Run detail + index page tests with dry-run
5. Print results table and save JSON

Expected: Both tests complete with status `DONE`, program data visible in table output, JSON saved to `e2e_results/`.

- [ ] **Step 2: Inspect output quality**

Review the terminal output:
- Does the detail test extract at least 1 program?
- Does the index test extract up to 3 programs (limited by `auto_run_max_candidates`)?
- Are program names, faculty, and other fields populated?

- [ ] **Step 3: Final commit if any adjustments needed**

```bash
git commit -m "fix(e2e): adjustments from manual verification"
```
