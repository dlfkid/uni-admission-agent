# PROJECT CONTEXT: UniAdmission Agent

## 1. Project Goal
Build a trusted, self-updating database of university admission requirements.

**Scope**: Multi-university crawler with intelligent depth exploration and context-aware parsing.

**Key Features**:
- Intelligent crawl depth with Heuristic/Regex scouting (optimized for speed/cost)
- Rolling window sequential chunking for context preservation on large pages
- Multi-provider LLM routing (Google Gemini, DeepSeek, OpenAI, VolcEngine)
- **Phase 2 staged ingestion pipeline** with persisted job/task state and resume-from-stage
- **Phase 3 golden-sample quality system** with offline scoring + CI regression gate
- **Taxonomy-guided program-name resolution** (signal matching + conditional hint injection + high-confidence override)
- Stealth browsing with anti-detection mechanisms
- **Cookie consent auto-dismissal** with JS injection to prevent navigation hijacking
- **Resilient Pydantic validation** with field validators for LLM response edge cases
- **Chrome Extension** with interactive control, real-time monitoring, and database preview
- **Serve ↔ Client browser automation bridge** (`/clients/ws`) for MCP/REST-triggered user-side automation
- **Opt-in Agent Runtime layer** (`legacy` + `pydanticai`) with default-off safety gate
- **Typed Agent Bridge contracts** (`src/agent_bridge`) decoupling runtime orchestration from core services
- **Dual MCP toolsets**: base tools (external-LLM friendly) + conditional `_internal_llm` tools
- **Runtime-aware MCP metadata**: provider resolution + client selection visibility
- **Policy profile precedence and normalization** (`request > client > server`) with warning output
- **Post-persist review-and-patch loop** with stable `program_id` correction path
- **Stand-alone Executable** build for easy distribution

---

## 2. Technology Stack

| Layer | Technology |
|:------|:-----------|
| **Crawling** | `crawl4ai` (v0.4+) + `playwright` + `playwright-stealth` |
| **LLM** | Multi-provider routing: Gemini, DeepSeek, OpenAI, VolcEngine (豆包) |
| **Data Validation** | `pydantic` (v2) with strict schema enforcement |
| **Database** | `sqlmodel` (PostgreSQL default, SQLite fallback) |
| **API / Control** | `fastapi`, `uvicorn`, `mcp` (Model Context Protocol) |
| **Agent Runtime** | `pydantic` typed contracts + runtime factory (`legacy` / `pydanticai`) |
| **Client Bridge** | FastAPI WebSocket + `websockets` runtime client |
| **CLI** | `typer` |
| **Build** | `pyinstaller` (Backend + Client), `npm` (Extension) |
| **Migration** | `alembic` |
| **Env Management** | `uv` package manager, Python 3.12+ |

---

## 3. Core Architecture

### 3.1 Intelligent Crawling Engine (Hybrid)

**Strategy**: Performance-optimized hybrid approach.
1.  **Regex / Heuristic**: Used for high-volume tasks (link extraction, page type detection) to save tokens and latency.
2.  **LLM**: Reserved for complex tasks (content cleaning, structured data extraction).

```
L1: Index Page (course list)
  ↓ Regex Link Extraction → concurrent chunks
L2: Detail Pages (individual programs)
  ↓ LLM Clean & Parse (Rolling Window)
  ↓ parse failure + --continue > 0 → Scout
L3+: Scout-recommended pages
  ↓ Heuristic Page Type Detection (Link Count/Content Signals)
  ↓ Recurse
```

### 3.2 Chrome Extension & API

The system exposes a REST API and MCP server for external control.
-   **Server**: `src/api/server.py` (FastAPI)
-   **Protocol**: HTTP + SSE (Server-Sent Events) for real-time logs
-   **Extension**: Vite/TypeScript-based UI in `extension/` directory.
    -   Connects to `http://localhost:8910`
    -   Displays real-time logs and token usage
    -   Manages crawler configuration
    -   **Database Preview**: Browse stored programs with filtering by university/year
    -   **Two-phase crawl**: Analyze index pages → select links → crawl detail pages
    -   **Per-task taxonomy controls**: enable/disable matching, low/high thresholds, top-k hints, override toggle
    -   **Export to Excel**: Download program data via REST API

### 3.3 Data Flow

```mermaid
flowchart LR
    A[Web Page] -->|crawl4ai| B[Markdown]
    B -->|Regex/Heuristic| C{Page Type?}
    C -->|Index| D[Extract Links]
    C -->|Detail| E[LLM Router]
    E -->|Clean & Parse| F[Pydantic Model]
    F -->|Validation| G[SQLModel ORM]
    G -->|Upsert| H[PostgreSQL]
```

### 3.4 Phase 2 Execution Pipeline (Decoupled + Resumable)

The crawl execution layer is staged and persisted:
- `fetch_raw`
- `extract_structured`
- `validate_rules`
- `persist_versioned`

Each run creates an `ingestion_job` with stage-level `ingestion_task` records, enabling:
- bounded retry and poison handling
- deterministic stage trace
- resume from the first unfinished stage or an explicit stage override
- reuse of successful upstream stage outputs

### 3.5 Phase 3 Quality System (Seed)

The project includes a seed quality framework for offline regression checks:
- golden manifest: `golden_samples/manifest.json`
- snapshot collector: `scripts/collect_golden_samples.py`
- scoring runner: `scripts/score_golden_samples.py`
- CI gate: `.github/workflows/ci.yml` fails when quality threshold is not met

Current seed set includes 4 benchmark universities (UCL, Manchester, Leeds, PolyU).

### 3.6 Taxonomy Runtime Matching

At server startup, taxonomy seed sync is attempted from:
- `golden_samples/program_names/cleaned_programs_names.json`

During `extract_structured`, detail-page name resolution now uses:
- signal priority: selected anchor text → URL tokens → heading fallback
- low-threshold gating for cleaner prompt hint injection
- high-threshold gating for optional canonical-name override
- trace output in `extra_metadata.taxonomy_match`

Per-request overrides are supported on `/crawl`:
- `taxonomy_enabled`
- `taxonomy_low_threshold`
- `taxonomy_high_threshold`
- `taxonomy_hint_top_k`
- `taxonomy_override_enabled`
- `selected_link_texts`

### 3.7 Serve-Client Browser Automation

`crawl` now supports browser provider selection:
- `browser_provider`: `auto` / `server` / `client`
- `client_id`: optional target connected client
- `strict_client`: fail when no client is available (no fallback)

`serve` maintains connected user-side clients:
- `GET /clients` for live client status
- `WS /clients/ws` for register / heartbeat / rpc request/response

Client dispatch flow:
1. External caller uses existing `crawl` (REST or MCP)
2. `browser_provider=auto|client` resolves through connected client when available
3. Client executes external browser command (e.g. client), returns HTML/`detail_pages_batch`
4. Existing ingestion pipeline consumes returned payload (no extension dependency required)

### 3.8 MCP Dual Toolset + Decision Policy

MCP now exposes two toolsets:
- Base tools (always registered): `analyze`, `crawl`, `crawl_detail_batch`, `db_query`, `runtime_status`, `program_patch`, `program_patch_batch`, `help`
- Internal LLM tools (conditional): `analyze_internal_llm`, `crawl_internal_llm`, `crawl_detail_batch_internal_llm`

Runtime introspection:
- `runtime_status` exposes `client_available`, `client_count`, `client_ids`, `internal_llm_available`, `default_browser_provider_resolved`
- Crawl/analyze payloads include `resolved_browser_provider` and `client_id_used`

Interactive decision policy for index pages:
- Year is mandatory before crawl execution (`requires_user_input`, `missing_fields=["year"]`)
- Taxonomy candidate keep threshold: `0.75`
- Taxonomy auto-run threshold: `0.92`
- Auto-run only when retained candidate count `<= 10`; otherwise require user review

Review-and-correction loop:
- Crawl responses include `review_token` and ordered `review_items` with stable `program_id`
- User corrections are applied via `program_patch` / `program_patch_batch`
- Batch patch returns partial failures without aborting successful updates

### 3.9 Opt-in Agent Runtime (PydanticAI Evolution)

Agent orchestration is integrated as an optional runtime layer and does not replace
existing crawl/analyze entrypoints.

Enablement and runtime selection:
- Default: disabled (`AGENT_ENABLED=false`)
- CLI explicit enable: `serve --agent`
- Runtime mode: `AGENT_RUNTIME=legacy|pydanticai` (default `legacy`)
- Model mode gates:
  - `AGENT_ALLOW_INTERNAL_LLM=true|false`
  - `AGENT_ALLOW_EXTERNAL_LLM=true|false`

Entrypoints:
- REST: `POST /agent/run`
- MCP: `agent_run` tool (registered only when `AGENT_ENABLED=true`)

Safety and fallback:
- `PydanticAIRuntime` failure automatically falls back to `LegacyRuntime`
- Base REST/MCP tools remain unchanged when agent mode is disabled

Policy profile behavior:
- Merge precedence: `request overrides > client profile > server defaults`
- Invalid values are normalized with `warnings` in merge result
- Client runtime can attach local `policy_profile` to browser RPC payloads

---

## 4. Build & Distribution System

The project supports a fully automated build pipeline to generate standalone artifacts.

### 4.1 Artifacts
-   **Backend Engine**: `adm-agent` (Single-directory executable via PyInstaller)
-   **Client Engine**: `adm-agent-client` (Single-directory executable via PyInstaller)
-   **Frontend**: `extension/uni-admission-extension.zip` (Chrome Extension)

### 4.2 Build Process
Managed by `scripts/build_dist.py`:
1.  `npm run build` (Extension) → `dist/` + `.zip`
2.  `pyinstaller adm-agent.spec` (Backend) → `dist/adm-agent/`
3.  `pyinstaller adm-agent-client.spec` (Client) → `dist/adm-agent-client/`
4.  **Assembly** → `release/` folder with everything needed.

### 4.3 Path Resolution
`src/core/paths.py` handles runtime path resolution transparently:
-   **Dev Mode**: Uses source tree (`src/`, `data/`).
-   **Frozen Mode**: Uses `sys._MEIPASS` for bundled assets and `~/.uni-agent/` for writable data.

---

## 5. Directory Structure

```
uni-admission-agent/
├── src/
│   ├── agent_bridge/       # Typed bridge contracts for serve/client orchestration
│   ├── agent_runtime/      # Agent runtime abstraction, skills, policy, provider adapter
│   ├── agents/             # LLM logic (Router, Cleaner)
│   ├── api/                # FastAPI + MCP Server
│   ├── cmd/                # CLI Entry Points
│   ├── core/               # Core utilities (paths, env, config)
│   ├── models/             # DB & Pydantic schemas
│   ├── scrapers/           # Crawling logic (Engine, Browser)
│   ├── services/           # Business logic (Crawler Service)
│   ├── storage/            # DB Manager, Import/Export
│   └── utils/              # Text/PDF processors
├── extension/              # Chrome Extension source
├── scripts/                # Build & Maintenance scripts
├── tests/                  # Pytest suite
├── data/                   # Default data storage (dev mode)
├── migrations/             # Alembic migrations
├── adm-agent.spec          # PyInstaller config
├── adm-agent-client.spec   # PyInstaller config (client)
├── pyproject.toml          # Project config
└── README.md               # User guide
```

---

## 6. CLI Usage

All commands are available via `uv run src/cmd/cli.py` or the `adm-agent` executable.

```bash
# Start Server (API + MCP)
uv run src/cmd/cli.py serve --port 8910

# Crawl
uv run src/cmd/cli.py crawl --name hku --year 2026 --url <URL>

# Import/Export
uv run src/cmd/cli.py import --file data.xlsx ...
uv run src/cmd/cli.py export --output data.xlsx ...

# Check Status/Env
uv run src/cmd/cli.py status
uv run src/cmd/cli.py check
uv run src/cmd/cli.py db-version
uv run src/cmd/cli.py db-migrate --yes
uv run src/cmd/cli.py db-reinit --yes  # destructive reset

# Phase 2 operations
uv run src/cmd/cli.py ingestion-jobs --limit 20
uv run src/cmd/cli.py ingestion-resume --job <job_uid> --stage validate_rules

# Phase 3 operations
uv run src/cmd/cli.py golden-collect --overwrite
uv run src/cmd/cli.py quality-score --threshold 0.60
uv run src/cmd/cli.py taxonomy-export --output golden_samples/program_names/cleaned_programs_names.json --include-learned --min-confidence 0.90
```

Default backend upgrade delivery remains `upgrade` followed by `db-migrate`.
`db-reinit` is a manual maintenance operation and is not part of the automatic
upgrade path.

---

## 7. Configuration

Managed via `.env` (loaded at runtime). Use the interactive wizard for easy setup:

```bash
# Interactive LLM configuration wizard
uv run src/cmd/cli.py llm-config
```

Manual `.env` configuration:

```bash
# LLM Credentials
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash-exp

DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_NAME=deepseek-chat

VOLC_API_KEY=...
VOLC_MODEL_ID=...
VOLC_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

CUSTOM_LLM_BASE_URL=https://api.openai.com/v1
CUSTOM_LLM_API_KEY=...
CUSTOM_LLM_MODEL_NAME=gpt-4o-mini

# Provider Priority (comma-separated, highest first)
# Supported: deepseek, gemini, volcengine, custom
LLM_PRIORITY_LIST=deepseek,gemini,volcengine,custom

# Database
DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/uni_admission
```

**Chrome Extension Configuration:**
- Open extension popup → Click ⚙️ Config button
- Database URL: Set PostgreSQL connection string
- LLM Priority: Drag providers to reorder (including custom)
- Custom provider fields automatically expand in the list

---

## 8. Recent Updates & Bug Fixes

### 8.0 Optimization Roadmap Status (2026-03-03)

- **Phase 1**: complete (data layer versioning + evidence chain)
- **Phase 2**: complete (staged execution pipeline + resume + continue-depth unified path)
- **Phase 3**: complete for seed quality gates + taxonomy-guided name accuracy + PolyU benchmark coverage

### 8.1 Custom LLM Provider Integration (2026-02-28)

**Feature**: Support for any OpenAI-compatible API endpoint as a custom LLM provider.
- Added `CustomLLMProvider` class implementing the `LLMProvider` interface
- Registered `custom` in `PROVIDER_REGISTRY` alongside deepseek/gemini/volcengine
- Custom can be positioned anywhere in priority list via drag-and-drop in Chrome extension
- Configuration fields: `CUSTOM_LLM_BASE_URL`, `CUSTOM_LLM_API_KEY`, `CUSTOM_LLM_MODEL_NAME`

**CLI Wizard** (`uv run src/cmd/cli.py llm-config`):
- Interactive prompt to select provider (DeepSeek/Gemini/Volcengine/Custom)
- Collects provider-specific parameters
- Saves to `.env` and automatically sets new provider as highest priority
- Supports Ollama, OpenRouter, or any OpenAI-compatible local/remote endpoint

**Architecture Changes**:
- `custom` is now a first-class provider in `RouterAgent` priority routing
- Frontend unified: custom provider appears in llm-list alongside built-in providers
- Backend `.env` parsing handles `CUSTOM_LLM_*` keys via `PROVIDER_PREFIXES`

### 8.2 Anti-Crawling Resilience (2026-02-27)

**Problem**: Cookie consent banners caused navigation interference during crawling.
- `simulate_user=True` in crawl4ai triggered clicks on cookie "Options" buttons
- Browser navigated away from target pages to privacy policies
- Result: 0 programs extracted from valid detail pages

**Solution** (`src/scrapers/engine.py`):
- Disabled `simulate_user` to prevent unintended interactions
- Enabled `remove_overlay_elements=True` to auto-remove cookie banners
- Injected custom JS to dismiss consent dialogs by clicking "Accept/OK" buttons
- Added `delay_before_return_html=2.0` to wait for page stabilization

### 8.2 LLM Response Validation (2026-02-27)

**Problem**: LLM occasionally returned `null` for list fields, causing Pydantic validation errors.
- `ParsedProgramData.study_options` and `deadlines` expected `List[...]`
- LLM returned `null` instead of `[]` when no data found
- Validation failed: `Input should be a valid array [type=list_type]`

**Solution** (`src/agents/cleaner_agent.py`):
- Added `@field_validator` for `study_options` and `deadlines`
- Automatically coerces `None` → `[]` during validation
- Prevents downstream crashes while maintaining type safety

### 8.3 Database Preview UI (2026-02-27)

**New Feature**: Chrome Extension now includes a database preview modal.

**API Enhancements**:
- `ProgramResponse` enriched with `study_options`, `deadlines`, `source_url`
- `GET /programs` returns complete program details for preview
- Supports filtering by `univ_slug` and `year` parameters

**Extension Features**:
- Preview button (👁) in header beside Export
- Filter panel: University slug (autocomplete) + Year + Search button
- Program count badge displays total results
- Card-based list with:
  - Program name + group code
  - Faculty, tuition, study mode/duration tags
  - Collapsible deadline list (round, date, description)
  - Clickable source URL link

# Database
DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/uni_admission
```

---

## 8. Recent Updates & Bug Fixes

### 8.1 Anti-Crawling Resilience (2026-02-27)

**Problem**: Cookie consent banners caused navigation interference during crawling.
- `simulate_user=True` in crawl4ai triggered clicks on cookie "Options" buttons
- Browser navigated away from target pages to privacy policies
- Result: 0 programs extracted from valid detail pages

**Solution** (`src/scrapers/engine.py`):
- Disabled `simulate_user` to prevent unintended interactions
- Enabled `remove_overlay_elements=True` to auto-remove cookie banners
- Injected custom JS to dismiss consent dialogs by clicking "Accept/OK" buttons
- Added `delay_before_return_html=2.0` to wait for page stabilization

### 8.2 LLM Response Validation (2026-02-27)

**Problem**: LLM occasionally returned `null` for list fields, causing Pydantic validation errors.
- `ParsedProgramData.study_options` and `deadlines` expected `List[...]`
- LLM returned `null` instead of `[]` when no data found
- Validation failed: `Input should be a valid array [type=list_type]`

**Solution** (`src/agents/cleaner_agent.py`):
- Added `@field_validator` for `study_options` and `deadlines`
- Automatically coerces `None` → `[]` during validation
- Prevents downstream crashes while maintaining type safety

### 8.3 Database Preview UI (2026-02-27)

**New Feature**: Chrome Extension now includes a database preview modal.

**API Enhancements**:
- `ProgramResponse` enriched with `study_options`, `deadlines`, `source_url`
- `GET /programs` returns complete program details for preview
- Supports filtering by `univ_slug` and `year` parameters

**Extension Features**:
- Preview button (👁) in header beside Export
- Filter panel: University slug (autocomplete) + Year + Search button
- Program count badge displays total results
- Card-based list with:
  - Program name + group code
  - Faculty, tuition, study mode/duration tags
  - Collapsible deadline list (round, date, description)
  - Clickable source URL link
