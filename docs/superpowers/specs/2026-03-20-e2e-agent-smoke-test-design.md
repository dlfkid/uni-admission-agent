# E2E Agent Smoke Test — Design Spec

**Date**: 2026-03-20
**Branch**: feature/agent-compatible
**Status**: Approved (rev 2 — post-review fixes)

---

## 1. Goal

Create an end-to-end smoke test that exercises the full agent runtime (s01–s12)
against real university pages, verifying the crawl pipeline produces valid
structured data without persisting to the database. Results are displayed in the
terminal for human quality judgment and saved as JSON for cross-run comparison.

## 2. Decisions

| Decision | Choice | Rationale |
|:---------|:-------|:----------|
| dry-run implementation | `AgentRunRequest.dry_run` flag propagated through to skill layer | Production code gains dry-run capability; test runs the real agent chain |
| max detail candidates | Reuse existing `auto_run_max_candidates` in `PolicyProfile` (default 10) | Field already exists with normalization; test passes `auto_run_max_candidates: 3` |
| execution method | Standalone script (`scripts/e2e_agent_smoke.py`) | Not a regression test; human judges quality |
| output format | Terminal table + JSON file | Immediate inspection + archival diff |
| timeout | 8 minutes per test | Real crawl + LLM latency budget |

## 3. Production Code Changes

### 3.1 `AgentRunRequest.dry_run` (schemas.py)

Add field to `AgentRunRequest`:
```python
dry_run: bool = Field(default=False, description="Skip DB persistence; return parsed results only")
```

### 3.2 Full `dry_run` propagation path

Every layer needs explicit changes:

```
1. AgentRunRequest.dry_run              [src/api/schemas.py — add field]
2. api_agent_run() reads body.dry_run   [src/api/server.py — pass to run_agent_crawl]
3. run_agent_crawl(dry_run=...)         [src/services/crawler.py — add param, put in context]
4. AgentRequest.context["dry_run"]      [src/services/crawler.py — set in context dict]
5. PydanticAIRuntime._run_agent()        [src/agent_runtime/pydanticai_runtime.py — extract dry_run from request.context]
6. agent_loop(system_prompt=modified)   [src/agent_runtime/loop.py — _run_agent() appends dry_run instruction to system_prompt before calling agent_loop()]
7. PersistProgramsSkillInput.dry_run    [src/agent_runtime/skills/contracts.py — add field]
8. persist_programs_skill_handler       [src/agent_runtime/skills/impl/common.py — check flag]
9. ingest_program_records_external()    [src/services/crawler.py — add param, skip upsert]
```

Layer 6 detail: when `dry_run=True`, append to the system prompt:
`"IMPORTANT: dry_run mode is active. When calling persist_programs_skill, set dry_run=true in the payload. Do NOT attempt database writes."`

This way the LLM agent will populate `dry_run=true` in the skill call payload,
and the typed `PersistProgramsSkillInput.dry_run` field receives it naturally
through the existing skill dispatch flow (no middleware needed).

### 3.3 `persist_programs_skill` dry-run behavior

In `ingest_program_records_external()`:
- Accept `dry_run: bool = False` parameter
- When `dry_run=True`:
  - Still perform Pydantic model validation (verify data structure)
  - Skip `db.upsert_program()` calls
  - Return same response shape with `imported_count=0`, plus `dry_run=True` and
    `parsed_programs` list containing the validated structured data
- `PersistProgramsSkillOutput` gets two new optional fields:
  `dry_run: bool = False` and `parsed_programs: list[dict] = []`

### 3.4 Max detail candidates — reuse existing field

No new policy field needed. The existing `PolicyProfile.auto_run_max_candidates`
(default 10, range 1–200) already limits how many candidates the agent processes.

The e2e test passes:
```json
{ "policy_profile": { "auto_run_max_candidates": 3 } }
```

This flows through `PolicyProfilePayload` → `merge_policy()` → normalized
`PolicyProfile` → agent context, which the LLM already respects when deciding
how many detail URLs to crawl.

## 4. E2E Script Design

### 4.1 File

`scripts/e2e_agent_smoke.py`

### 4.2 Startup & Preconditions

1. Check `.env` exists in project root → `sys.exit(1)` if missing
2. Load `.env` via dotenv
3. Set `AGENT_ENABLED=true`
4. Randomly select one case from `golden_samples/cases/*/metadata.json`
5. Read `index_url`, `detail_url`, `name` from metadata

**Assumption**: A valid `DATABASE_URL` must be present in `.env` because the
FastAPI app lifespan calls `DatabaseManager().init_db()`. This is acceptable
for a smoke test that assumes a configured development environment. The dry-run
flag prevents any writes to the database.

### 4.3 Server Setup

Use `httpx.ASGITransport(app=app)` + `httpx.AsyncClient` to mount the FastAPI
app in-process. No real TCP port needed.

The ASGI transport triggers lifespan events, so DB init and taxonomy bootstrap
will execute. This is intentional — we want the full server stack running.

### 4.4 Test Execution

**Test 1 — Detail page:**
```json
POST /agent/run
{
  "url": "<detail_url>",
  "univ_slug": "<name lowercase>",
  "year": "<current year>",
  "page_type_hint": "detail",
  "autonomous": true,
  "dry_run": true,
  "runtime": "pydanticai"
}
```

**Test 2 — Index page (max 3 candidates):**
```json
POST /agent/run
{
  "url": "<index_url>",
  "univ_slug": "<name lowercase>",
  "year": "<current year>",
  "page_type_hint": "index",
  "autonomous": true,
  "dry_run": true,
  "runtime": "pydanticai",
  "policy_profile": { "auto_run_max_candidates": 3 }
}
```

Both tests poll `GET /tasks/{task_id}` every 3 seconds with `await asyncio.sleep(3)`
between polls (critical: must yield to event loop so the agent task can progress).
Timeout: 8 minutes per test.

### 4.5 Error Handling

- Task status `FAILED` → immediate error report with detail
- Timeout → report which test timed out
- Unexpected exception → traceback + exit code 1

## 5. Output

### 5.1 Terminal Table

Per-test output:
```
═══ [Detail] UCL — ucl_undergrad_anthropology ═══
Status: DONE | Programs: 1 | Duration: 42.3s

┌────┬──────────────────────────┬────────────────┬──────────┬───────────────────┐
│ #  │ Program Name             │ Faculty        │ Tuition  │ Study Mode        │
├────┼──────────────────────────┼────────────────┼──────────┼───────────────────┤
│ 1  │ Anthropology (Year Ab... │ Social & His.. │ £28,500  │ Full-time / 3 yrs │
└────┴──────────────────────────┴────────────────┴──────────┴───────────────────┘
Deadlines: 2 item(s)  |  Source URL: https://...
```

Final summary:
```
══════════════════════════════════════════
University: UCL (ucl_undergrad_anthropology)
Detail test:  DONE — 1 program(s), 42.3s
Index test:   DONE — 3 program(s), 187.6s
Total time:   229.9s
Results saved: e2e_results/2026-03-20T14-30-00_ucl.json
══════════════════════════════════════════
```

Uses Python stdlib only (no extra dependencies). Long fields truncated with `...`.

### 5.2 JSON Archive

Path: `e2e_results/<timestamp>_<univ_slug>.json`

```json
{
  "timestamp": "2026-03-20T14:30:00",
  "case_id": "ucl_undergrad_anthropology",
  "univ_name": "UCL",
  "year": 2026,
  "detail_test": {
    "status": "DONE",
    "duration_sec": 42.3,
    "url": "https://...",
    "programs": [...]
  },
  "index_test": {
    "status": "DONE",
    "duration_sec": 187.6,
    "url": "https://...",
    "auto_run_max_candidates": 3,
    "programs": [...]
  }
}
```

Directory `e2e_results/` auto-created and added to `.gitignore`.

## 6. Files Changed

| File | Change |
|:-----|:-------|
| `src/api/schemas.py` | Add `dry_run` to `AgentRunRequest` |
| `src/api/server.py` | Pass `dry_run` through to `run_agent_crawl()` |
| `src/services/crawler.py` | Accept + forward `dry_run` in `run_agent_crawl()` and `ingest_program_records_external()` |
| `src/agent_runtime/pydanticai_runtime.py` | Extract `dry_run` from context, pass to loop |
| `src/agent_runtime/loop.py` | Inject dry-run instruction into system prompt when flag is set |
| `src/agent_runtime/skills/contracts.py` | Add `dry_run` to `PersistProgramsSkillInput`; add `dry_run` + `parsed_programs` to `PersistProgramsSkillOutput` |
| `src/agent_runtime/skills/impl/common.py` | Dry-run logic in `persist_programs_skill_handler` / `ingest_program_records_external()` |
| `scripts/e2e_agent_smoke.py` | New: the e2e smoke script |
| `.gitignore` | Add `e2e_results/` |
