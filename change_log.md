# Change Log (Consolidated)

## 2026-03-11

### PydanticAI Agent Runtime Evolution (Opt-In)
- Added explicit default-off agent switch:
  - `is_agent_enabled()` runtime helper
  - CLI: `serve --agent` and `serve --dry-run`
  - env: `AGENT_ENABLED=true|false`
- Added runtime abstraction and selection:
  - `AgentRuntime` typed contract
  - `LegacyRuntime` and `PydanticAIRuntime`
  - runtime factory via `AGENT_RUNTIME=legacy|pydanticai`
- Added automatic runtime fallback:
  - `PydanticAIRuntime` falls back to `LegacyRuntime` on runtime exceptions

### Agent Bridge + Skill Contract Layer
- Added `src/agent_bridge` typed contracts and wrappers:
  - `ServeToolBridge` (`analyze_page`)
  - `ClientAutomationBridge` (`fetch_browser_payload`)
- Added typed skill contracts and registry (`src/agent_runtime/skills`):
  - required skill names registered (`analyze_page_skill`, `crawl_detail_batch_skill`, etc.)
  - validated `input_model` / `output_model` execution path

### Policy Profile + Provider Adapter
- Added agent policy merge/normalization:
  - precedence: `request > client > server`
  - threshold clamp and warning reporting
- Added model provider adapter for internal/external modes:
  - explicit `AgentConfigError` for disabled modes
  - factory integration helper in `src/agents/factory.py`

### Agent API/MCP Entrypoints
- Added REST endpoint: `POST /agent/run` (returns task id, gated by `AGENT_ENABLED`)
- Added MCP tool: `agent_run` (registered only when `AGENT_ENABLED=true`)
- Kept existing REST/MCP base tool behavior unchanged when agent runtime is disabled

### Client Policy Transport
- Added `ClientPolicyProfile` and TOML persistence in client config
- Added client runtime payload propagation:
  - websocket RPC `rpc_result.payload.policy_profile`
- Added `client fetch --json` payload enrichment with local policy profile

## 2026-03-07

### MCP Dual Toolset + Review Loop
- Added dual MCP registration model:
  - base toolset always registered (`analyze`, `crawl`, `crawl_detail_batch`, `db_query`, `runtime_status`, `program_patch`, `program_patch_batch`, `help`)
  - `_internal_llm` toolset registered only when internal router is available.
- Added `runtime_status` runtime introspection payload:
  - `client_available`, `client_count`, `client_ids`, `internal_llm_available`, `default_browser_provider_resolved`.
- Standardized provider metadata on MCP crawl/analyze responses:
  - `resolved_browser_provider`
  - `client_id_used`
- Added index decision policy for MCP crawl:
  - year gating (`requires_user_input`, `missing_fields=["year"]`)
  - taxonomy keep threshold `0.75`
  - taxonomy auto-run threshold `0.92`
  - auto-run only when retained candidates `<= 10`, otherwise user review.
- Added post-persist review payload and correction path:
  - crawl result now includes `review_token` + ordered `review_items` with stable `program_id`
  - added `program_patch` and `program_patch_batch` with partial-failure reporting (`updated_count`, `failed_items`, `summary`).

### Serve ↔ Client Browser Automation
- Added `crawl` browser provider controls: `browser_provider`, `client_id`, `strict_client`.
- Added serve-side client bridge:
  - `GET /clients`
  - `WS /clients/ws` (register, heartbeat, rpc result/error relay)
- Added client registry + RPC broker primitives with timeout/failure handling.
- Added browser provider orchestration in crawler service (`auto|server|client` with fallback).

### New Client Runtime
- Added `adm-agent-client` CLI (`init`, `status`, `start`, `bootstrap`).
- Added websocket runtime loop to receive RPC requests and return browser payloads.
- Added external fetch command template support via `ADM_AGENT_CLIENT_FETCH_CMD`.
- Added LLM bootstrap prompt templates for `codex`, `claude`, `openclaw`, `generic`.

### Build/Distribution
- Added `adm-agent-client.spec`.
- Extended `scripts/build_dist.py`:
  - `--client-only`
  - `--skip-client-build`
  - separate client artifact packaging.

## 2026-03-06

### CLI / Upgrade Compatibility
- Added destructive `db-reinit` maintenance command (drop DB, recreate DB, migrate to head).
- Kept backend upgrade delivery path unchanged: `adm-agent upgrade` continues to trigger
  post-upgrade `db-migrate` by default.

## 2026-03-03

### Overall Progress
- Phase 1 (Data Layer): completed.
- Phase 2 (Execution Layer): completed.
- Phase 3 (Quality System Seed): completed and gated in CI.

### Phase 2 Highlights
- Added staged ingestion orchestration with persisted `ingestion_job` / `ingestion_task`.
- Added retry scheduling, poison handling, and resume-from-stage.
- Unified `continue_depth > 0` flow into the Phase 2 pipeline (legacy fallback removed).
- Exposed job operations via API and CLI:
  - `ingestion-jobs`
  - `ingestion-resume`

### Phase 3 Highlights
- Added golden sample framework:
  - `golden_samples/manifest.json`
  - per-case snapshot directories
  - expected outputs per case
- Added tooling:
  - `scripts/collect_golden_samples.py`
  - `scripts/score_golden_samples.py`
  - CLI commands: `golden-collect`, `quality-score`
- Added CI quality gate using offline snapshots and threshold checks.
- Improved offline scoring robustness:
  - context-aware tuition candidate ranking
  - reduced false positives from non-tuition numeric signals (e.g. IELTS scores)

### Current Seed Benchmark
- Golden cases: 3 (UCL, Manchester, Leeds)
- Quality report: `golden_samples/reports/quality_report.json`
- Latest seed run: global pass at threshold `0.60`

### References
- `docs/changelog_phase1_data_layer.md`
- `docs/changelog_phase2_execution_layer.md`
- `docs/changelog_phase3_quality_system.md`
