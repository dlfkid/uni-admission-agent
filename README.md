# UniAdmission Agent

**Autonomous LLM-powered engine for aggregating and synchronizing global university admission requirements into a structured database.**

## 🎯 Overview
This project automates the collection of admission criteria from world-renowned universities. It uses an **Agentic Workflow** to handle dynamic web content, bypass anti-detection mechanisms, and transform unstructured web data into verified JSON schemas.

## 🛠 Tech Stack
- **Engine:** Python 3.12+ (managed by `pyenv`)
- **Intelligence:** Gemini 2.0 Flash / DeepSeek / VolcEngine (豆包)
- **Automation:** Playwright with Stealth Plugin
- **Extraction:** Crawl4AI / Firecrawl (Markdown-first approach)
- **Validation:** Pydantic (Strongly typed schemas)
- **Storage:** PostgreSQL (via SQLModel)
- **API:** FastAPI + MCP Server
- **CLI:** Typer

## 📐 Architecture

```
Entry Points                    Services Layer              Infrastructure
┌──────────────┐
│  CLI (Typer) │──┐
└──────────────┘  │    ┌──────────────────┐    ┌───────────────┐
┌──────────────┐  ├──→ │ src/services/    │──→ │ src/scrapers/ │
│ FastAPI REST │──┤    │   crawler.py     │    │ src/agents/   │
└──────────────┘  │    └──────────────────┘    │ src/storage/  │
┌──────────────┐  │                            │ src/core/     │
│  MCP Server  │──┘                            └───────────────┘
└──────────────┘
┌──────────────┐
│Chrome Plugin │──→ POST /crawl (REST)
└──────────────┘
```

## 📘 Upgrade Changelog
- [Phase 1: Data-Layer Upgrade (fact + dimensions + evidence + versioning)](docs/changelog_phase1_data_layer.md)
- [Phase 2: Execution-Layer Decoupling (ingestion_job/task + staged pipeline + resume)](docs/changelog_phase2_execution_layer.md)
- [Phase 3: Quality System Seed (golden samples + scoring + CI gate)](docs/changelog_phase3_quality_system.md)
- [Consolidated Progress Log](change_log.md)

## ✅ Current Optimization Status (2026-04-06)
- Phase 1 complete: versioned requirement data model and evidence chain are in place.
- Phase 2 complete: crawl flow now runs through staged ingestion pipeline by default, including `--continue > 0` paths.
- Phase 3 seed complete: golden sample collection, offline quality scoring, and CI regression gate are enabled.
- Taxonomy-guided name accuracy is enabled for crawl requests (hint injection + optional high-confidence override).
- Latest benchmark includes 4 cases (UCL/Manchester/Leeds/PolyU) and passes global threshold `0.60`.
- **Agent Runtime (s01–s12)**: Full LLM-driven agent loop with tool dispatch, task DAG, subagents, team coordination, background execution, context compression, and git worktree isolation.
- **Schema-based extraction**: agent auto-fetch now learns per-template CSS selectors from the first detail page, reuses them on sibling pages, and falls back to field-level or full-page LLM extraction when coverage drops.
- Agent streaming boundary:
  - Agent lifecycle progress is available via task events / SSE.
  - Final user-visible agent summary may stream token deltas or fall back to one-shot text.
  - Structured extraction paths remain non-streaming for stability, including cleaner extraction, page-type classification, and name resolution.
- **Agent chat mode**: free-form server-side agent chat is available via `POST /agent/chat`, with the same `/tasks/{id}/events` SSE channel used for thinking/tool/summary updates.
- **Automatic file logging**: backend CLI/server runs now emit rotated timestamped `.txt` logs automatically.

## Production Usage (No Code Required)

If you just want to *use* the agent without writing code, download the latest release for your platform.

### 1. Download
Go to the [Releases Page](../../releases) and download the artifact for your OS:
- **Windows**: `adm-agent-vX.Y.Z-windows-x86_64.zip`
- **macOS**: `adm-agent-vX.Y.Z-macos-arm64.tar.gz` (Apple Silicon) or `x86_64` (Intel)
- **Linux**: `adm-agent-vX.Y.Z-linux-x86_64.tar.gz`

### 2. Installation & Run

#### Windows
1. Unzip the file.
2. Open `cmd` or `PowerShell` in the unzipped folder.
3. Run:
   ```powershell
   # Check environment
   .\adm-agent.exe check

   # Install browser (required for crawling, only needed once)
   .\adm-agent.exe browser-install

   # Start host + client together (recommended for single-machine use)
   .\adm-agent.exe up
   # Press Ctrl+C to stop both processes cleanly.

   # --- Advanced: run host and client separately ---
   # Start the server only
   .\adm-agent.exe serve

   # Stop the running server (from another terminal)
   .\adm-agent.exe serve-stop
   ```

#### macOS / Linux
1. Extract the archive:
   ```bash
   tar -xzf adm-agent-*.tar.gz
   cd adm-agent-*
   ```
2. Run via terminal:
   ```bash

   # For Mac OS you need run this first to override the safety control
   xattr -cr /path/to/your/adm-agent

   # Check environment
   ./adm-agent check

   # Install browser (required for crawling, only needed once)
   ./adm-agent browser-install

   # Start host + client together (recommended for single-machine use)
   ./adm-agent up
   # Press Ctrl+C to stop both processes cleanly.

   # --- Advanced: run host and client separately ---
   # Start the server only
   ./adm-agent serve

   # Stop the running server (from another terminal)
   ./adm-agent serve-stop
   ```
   > **macOS Note**: If you see "System cannot verify the developer", go to **Settings > Privacy & Security** and click "Allow Anyway".

### 3. Setup
The agent needs a database connection.
1. Make sure you have **PostgreSQL** running.
2. Create a `.env` file in the same folder as the executable. You can copy the content below:
   ```bash
   # PostgreSQL Connection URL
   # Format: postgresql+psycopg2://user:password@host:port/dbname
   DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/uni_admission

   # Gemini APIKey config
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL_NAME=gemini-2.0-flash

   # DeepSeek APIKey config
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   DEEPSEEK_MODEL_NAME=deepseek-chat

   # VolcEngine (豆包) config
   VOLC_API_KEY=your_volc_api_key_here
   VOLC_MODEL_ID=your_model_endpoint_id

   # Custom LLM Provider (OpenAI-compatible API)
   CUSTOM_LLM_BASE_URL=https://api.openai.com/v1
   CUSTOM_LLM_API_KEY=your_openai_api_key_here
   CUSTOM_LLM_MODEL_NAME=gpt-4o-mini

   # LLM Priority config (drag to reorder in Chrome extension)
   # Supported providers: deepseek, gemini, volcengine, custom
   LLM_PRIORITY_LIST=deepseek, gemini, volcengine, custom

   # Agent runtime (enabled by default; can still be overridden explicitly)
   AGENT_ENABLED=true
   AGENT_RUNTIME=pydanticai
   AGENT_ALLOW_INTERNAL_LLM=true
   AGENT_ALLOW_EXTERNAL_LLM=true
   ```
3. Set your `DATABASE_URL` and API keys in `.env`.

## 🚀 Getting Started (Development)
1. `pyenv local 3.12.0`
2. `uv sync`
3. Copy `.env.example` to `.env` and add your API keys.
4. Install Git hooks for code quality: `bash .githooks/install-hooks.sh`
## 📖 Usage

### CLI Commands

**Unix (macOS/Linux):**
```bash
# Environment check
./adm-agent check

# Configure LLM provider (interactive wizard)
./adm-agent llm-config

# Install Playwright browser (only needed once)
./adm-agent browser-install

# Start host + client together (one-command local launcher; Ctrl+C stops both)
#   --host           Bind address (default: 127.0.0.1)
#   --port           Port number (default: 8910)
#   --health-timeout Seconds to wait for server health (default: 20)
#   --skip-client    Start only the server (no client)
./adm-agent up

# Import Excel data
#   --name: University slug (a-z0-9-)
#   --year: Academic year (e.g., 2026)
#   --file: Path to XLSX file
#   --llm:  Enable LLM analysis (optional)
./adm-agent import --name hku --year 2026 --file example/hku-26-27.xlsx

# Import with LLM fallback
./adm-agent import --name hku --year 2026 --file example/hku-26-27.xlsx --llm

# Export data to Excel
#   --name:   University slug
#   --output: Output file path
#   --year:   Academic year (optional)
./adm-agent export --name hku --output hku_export.xlsx --year 2026

# Check for backend updates
./adm-agent upgrade --check

# Update backend to latest version
./adm-agent upgrade

# Force update even if already on latest version
./adm-agent upgrade --force

# Apply database migrations
./adm-agent db-migrate --yes

# Destructive reset: drop + recreate + migrate
./adm-agent db-reinit --yes

# Show database migration revision status
./adm-agent db-version

# Auto-repair migration failures with rollback safety
./adm-agent repair --auto

# List recent Phase 2 ingestion jobs
./adm-agent ingestion-jobs --limit 20

# Resume a failed ingestion job
./adm-agent ingestion-resume --job <job_uid> --stage validate_rules

# Collect Phase 3 golden sample snapshots
./adm-agent golden-collect --overwrite

# Run Phase 3 quality scoring (fails on regression threshold)
./adm-agent quality-score --threshold 0.60

# Export current taxonomy snapshot (optionally include learned names)
./adm-agent taxonomy-export --output golden_samples/program_names/cleaned_programs_names.json --include-learned --min-confidence 0.90

# List extractions that failed the quality gate (per-university diagnostic)
./adm-agent quarantine list --university hku --year 2026

# Clear quarantine entries for one university (optionally filter by reason)
#   Reasons: empty_name, name_too_short, noise_name, empty_shell
./adm-agent quarantine clear --university hku
./adm-agent quarantine clear --university hku --reason empty_shell

# Show current version
./adm-agent version

# Show detailed version information
./adm-agent version --verbose

# Show comprehensive help
./adm-agent help

# Show detailed help with examples  
./adm-agent help --verbose
```

**Windows:**
```powershell
# Environment check
.\adm-agent.exe check

# Install Playwright browser (only needed once)
.\adm-agent.exe browser-install

# Start host + client together (one-command local launcher; Ctrl+C stops both)
.\adm-agent.exe up

# Import Excel data
.\adm-agent.exe import --name hku --year 2026 --file example/hku-26-27.xlsx

# Import with LLM fallback
.\adm-agent.exe import --name hku --year 2026 --file example/hku-26-27.xlsx --llm

# Export data to Excel
.\adm-agent.exe export --name hku --output hku_export.xlsx --year 2026

# Check for backend updates
.\adm-agent.exe upgrade --check

# Update backend to latest version
.\adm-agent.exe upgrade

# Force update even if already on latest version
.\adm-agent.exe upgrade --force

# Apply database migrations
.\adm-agent.exe db-migrate --yes

# Destructive reset: drop + recreate + migrate
.\adm-agent.exe db-reinit --yes

# Show database migration revision status
.\adm-agent.exe db-version

# Auto-repair migration failures with rollback safety
.\adm-agent.exe repair --auto

# List recent Phase 2 ingestion jobs
.\adm-agent.exe ingestion-jobs --limit 20

# Resume a failed ingestion job
.\adm-agent.exe ingestion-resume --job <job_uid> --stage validate_rules

# Collect Phase 3 golden sample snapshots
.\adm-agent.exe golden-collect --overwrite

# Run Phase 3 quality scoring (fails on regression threshold)
.\adm-agent.exe quality-score --threshold 0.60

# Export current taxonomy snapshot (optionally include learned names)
.\adm-agent.exe taxonomy-export --output golden_samples/program_names/cleaned_programs_names.json --include-learned --min-confidence 0.90

# List extractions that failed the quality gate (per-university diagnostic)
.\adm-agent.exe quarantine list --university hku --year 2026

# Clear quarantine entries for one university (optionally filter by reason)
.\adm-agent.exe quarantine clear --university hku
.\adm-agent.exe quarantine clear --university hku --reason empty_shell

# Show current version
.\adm-agent.exe version

# Show detailed version information
.\adm-agent.exe version --verbose

# Show comprehensive help
.\adm-agent.exe help

# Show detailed help with examples
.\adm-agent.exe help --verbose
```

### 4. Troubleshooting

**Error: "Playwright browser not found"**

If you see this error when running the executable, it means the required Chromium browser is missing.

**Solution 1: Run `browser-install` command (Recommended)**
```bash
# Windows
.\adm-agent.exe browser-install

# macOS / Linux
./adm-agent browser-install
```
This will automatically download and install the Chromium browser.

**Solution 2: Install Browsers Manually**
If you have Python installed:
```bash
pip install playwright
playwright install chromium
```

**Solution 3: Use Custom Path**
If you already have Playwright browsers installed elsewhere, set the environment variable:
```bash
export PLAYWRIGHT_BROWSERS_PATH=/path/to/ms-playwright
./adm-agent serve
```

### Crawling

**Unix (macOS/Linux):**
```bash
# Crawl a URL and import admission data
#   --name:      University slug (a-z0-9-)
#   --year:      Academic year
#   --url:       Starting URL
#   --continue:  Extra depth for LLM scouting (default: 0)
./adm-agent crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes
./adm-agent crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes --continue 2
```

**Windows:**
```powershell
.\adm-agent.exe crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes
.\adm-agent.exe crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes --continue 2
```

### Database Status

**Unix (macOS/Linux):**
```bash
./adm-agent status
```

**Windows:**
```powershell
.\adm-agent.exe status
```

### Database Migrations

Use Alembic migration commands to keep schema in sync after upgrades:

```bash
# Check revision status
./adm-agent db-version

# Migrate to latest schema
./adm-agent db-migrate --yes

# Destructive reset (drops all rows)
./adm-agent db-reinit --yes
```

`upgrade` runs `db-migrate --yes` by default after a successful backend update.
If migration fails during upgrade, the agent automatically runs `repair --auto`
to rollback to a safe data state.
`db-reinit` is a manual maintenance command and does not change the default
upgrade delivery path (`upgrade` → `db-migrate`).

### Server

**Unix (macOS/Linux):**
```bash
# Start the API + MCP server (default: 0.0.0.0:8910)
./adm-agent serve
./adm-agent serve --port 9000
./adm-agent serve --dry-run

# Start server as a background daemon (does not occupy the terminal)
./adm-agent serve-install
./adm-agent serve-install --port 9000

# Stop a running server (works for both serve and serve-install)
./adm-agent serve-stop
```

**Windows:**
```powershell
# Start the API + MCP server (default: 0.0.0.0:8910)
.\adm-agent.exe serve
.\adm-agent.exe serve --port 9000
.\adm-agent.exe serve --dry-run

# Start server as a background daemon
.\adm-agent.exe serve-install
.\adm-agent.exe serve-install --port 9000

# Stop a running server (works for both serve and serve-install)
.\adm-agent.exe serve-stop
```

`serve` writes a PID file to `~/.adm-agent/server.pid`. `serve-stop` reads that file
and sends a termination signal to the process, then removes the PID file. If the server
is not running, `serve-stop` exits cleanly with an informational message.

`serve-install` launches `serve` as a background daemon process and redirects output to
`~/.adm-agent/server.log`. The daemon keeps running after the terminal is closed.
`serve-stop` terminates both foreground and daemon server instances.

Agent runtime is **enabled by default** for normal server startup. You can still
override it explicitly with `AGENT_ENABLED=false` for compatibility or debugging.
Runtime mode can be selected via `AGENT_RUNTIME=legacy|pydanticai` (default `pydanticai`),
and `pydanticai` mode automatically falls back to `legacy` on runtime errors.

#### Agent Capabilities (s01–s12)

When `pydanticai` runtime is active, the agent loop provides a full capability stack:

| Capability | Description |
|:-----------|:------------|
| **s01 Agent Loop** | Core `while True` loop — LLM decides tool calls, exits on final answer |
| **s02 Tool Dispatch** | Multi-tool dispatch via `SkillRegistry` with Pydantic-typed inputs |
| **s03 Todo Manager** | In-memory task tracking with exactly-one-in-progress rule and nag reminders |
| **s04 Subagents** | `task` tool spawns child agent loops with isolated context |
| **s05 Skill Loading** | Two-layer knowledge: short descriptions in system prompt + on-demand `load_skill` |
| **s06 Context Compact** | Three-layer compression: micro-compact, auto-compact (>50k tokens), manual `compact` tool |
| **s07 Task System** | File-persisted task DAG with `blockedBy`/`blocks` dependency edges |
| **s08 Background Tasks** | Async skill execution with notification queue injection |
| **s09 Agent Teams** | Persistent teammates with JSONL mailbox communication |
| **s10 Team Protocols** | Request-response FSM for structured coordination between teammates |
| **s11 Autonomous Agents** | WORK/IDLE lifecycle with inbox polling, task claiming, idle timeout |
| **s12 Worktree Isolation** | Git worktree per task with lifecycle event logging |

When running behind a reverse proxy (e.g. Cloudflare Tunnel), `serve` enables
`proxy_headers=True` and `forwarded_allow_ips="*"` so the original client IP is preserved.

### REST API

With the server running (`uv run src/cmd/cli.py serve`):

```bash
# Submit a crawl job (returns task_id)
curl -X POST http://localhost:8910/crawl \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://admissions.hku.hk/programmes",
    "univ_slug": "hku",
    "year": 2026,
    "taxonomy_enabled": true,
    "taxonomy_low_threshold": 0.8,
    "taxonomy_high_threshold": 0.92,
    "taxonomy_hint_top_k": 3,
    "taxonomy_override_enabled": true
  }'

# Force user-side browser automation client (no extension required)
curl -X POST http://localhost:8910/crawl \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.manchester.ac.uk/study/masters/courses/list/?k=&s=All",
    "univ_slug": "uom",
    "year": 2026,
    "browser_provider": "client",
    "strict_client": true,
    "candidate_taxonomy_filter_enabled": true,
    "candidate_taxonomy_filter_threshold": 0.8,
    "candidate_taxonomy_filter_top_k": 20
  }'

# Analyze page for link selection (two-phase crawl)
curl -X POST http://localhost:8910/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/courses", "html_content": "<html>...</html>"}'

# Check task status
curl http://localhost:8910/tasks/{task_id}

# Stream task events over SSE (agent progress / thinking / summary deltas)
curl http://localhost:8910/tasks/{task_id}/events

# Run agent orchestration (enabled by default unless explicitly disabled)
curl -X POST http://localhost:8910/agent/run \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/courses",
    "univ_slug": "hku",
    "year": 2026,
    "runtime": "pydanticai",
    "policy_profile": {
      "batch_size": 4,
      "taxonomy_auto_threshold": 0.92
    }
  }'

# Start a free-form agent chat task (server-side LLM only)
curl -X POST http://localhost:8910/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Summarize the current crawl/runtime architecture for me."
  }'

# Confirm low-confidence onhold selections for one finished agent task
# (unselected indices are discarded by default)
curl -X POST http://localhost:8910/agent/review/confirm \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "<agent_task_id>",
    "selection_text": "continue 3,6,18"
  }'

# List connected browser-automation clients
curl http://localhost:8910/clients

# Database statistics
curl http://localhost:8910/status

# Query programs (with detailed fields: study_options, deadlines, requirements, source_url)
curl "http://localhost:8910/programs?univ_slug=hku&year=2026"

# List all universities
curl http://localhost:8910/universities

# Export programs to Excel
curl -X POST http://localhost:8910/export \
  -H "Content-Type: application/json" \
  -d '{"univ_slug": "hku", "year": 2026}' \
  --output hku_export.xlsx
```

### MCP Server

The MCP server is mounted at `/mcp`.

Base toolset (always registered):
- **`analyze`** — Analyze entry page and return candidate detail links (external-LLM friendly path).
- **`crawl_detail_batch`** — Crawl user-selected detail links in batches via client browser automation.
- **`crawl`** — Crawl a URL and import admission data. Supports `page_type_hint=auto|index|detail`.
- **`ingest`** — Persist caller-LLM structured program records directly (no server-side LLM extraction).
- **`db_query`** — Query programs from the database.
- **`runtime_status`** — Report live runtime capability (`client_available`, `client_count`, `client_ids`, `internal_llm_available`, `default_browser_provider_resolved`).
- **`program_patch`** — Patch one persisted `program_id` from user feedback.
- **`program_patch_batch`** — Batch patch multiple `program_id` items with partial-failure reporting.
- **`help`** — Return CLI help text and command overview.

Agent toolset (registered by default unless agent runtime is explicitly disabled):
- **`agent_run`** — Execute one agent orchestration request (`runtime=legacy|pydanticai`). Supports `autonomous=true` for fully autonomous mode (server-side LLM drives all decisions) or `autonomous=false` (default) for external-LLM-driven mode where the calling LLM controls orchestration. Accepts optional `client_id` to target a specific browser client.
- **`agent_review_confirm`** — Confirm low-confidence onhold indices for an existing `agent_run` task.

Internal-LLM toolset (registered only when server-side LLM is available):
- **`analyze_internal_llm`**
- **`crawl_detail_batch_internal_llm`**
- **`crawl_internal_llm`**
- **`ingest_internal_llm`**
- **`db_query_internal_llm`**
- **`runtime_status_internal_llm`**
- **`program_patch_internal_llm`**
- **`program_patch_batch_internal_llm`**
- **`help_internal_llm`**

Decision and correction flow in `crawl`:
- Missing `year` is blocked with:
  - `requires_user_input=true`
  - `missing_fields=["year"]`
  - prompt asking user to confirm year (e.g., 2026)
- For index pages, taxonomy thresholds are applied:
  - candidate keep threshold: `>= 0.75`
  - auto-run threshold: `>= 0.92`
  - auto-run max candidate count: `<= 10`
- Response includes structured decision fields:
  - `auto_ready`
  - `requires_user_review`
  - `decision_reason`
  - `candidates` (for review flows)

Provider metadata is standardized in tool responses:
- `resolved_browser_provider`
- `client_id_used` (if client path is selected)

Post-persist review loop:
- Crawl responses include `review_token` and ordered `review_items` with stable `program_id`.
- Apply user corrections via `program_patch` / `program_patch_batch`.
- Batch patch returns `updated_count`, `failed_items`, and `summary` (no all-or-nothing abort).

Agent onhold batch-review loop:
- `agent_run` auto-processes high-confidence candidates.
- Low-confidence candidates are returned in `onhold_items`, sorted by `confidence` descending with dynamic indices (`1..N`).
- Confirm via REST `POST /agent/review/confirm` or MCP `agent_review_confirm` using either `selection_text` or explicit `selected_indices`.
- Unselected `onhold_items` are discarded by default.

Recommended MCP interactive flow (single entrypoint):
1. Call `runtime_status` to inspect available runtime path.
2. Call `analyze` as the **only entrypoint**. Read:
   - `page_type_detected`
   - `requires_user_confirmation`
   - `next_step_options`
3. If `requires_user_confirmation=true`, ask user whether to continue with detected `index/detail`.
4. Follow selected next-step tool path:
   - `detail` path: `crawl` or `crawl_internal_llm`
   - `index` + external LLM path: select candidates, externally structure data, then `ingest`
   - `index` + server LLM path: `crawl_detail_batch_internal_llm` (or `crawl_detail_batch`)
5. Ask user for corrections if needed, then apply `program_patch` / `program_patch_batch`.

### `adm-agent-client` (Extension Optional)

When external LLMs call MCP/REST directly, users may not have extension pages open.  
Use `adm-agent-client` to connect the user's machine to `serve` and execute browser automation.

**Quickstart (source mode):**
```bash
uv run src/cmd/client_cli.py init
uv run src/cmd/client_cli.py status
uv run src/cmd/client_cli.py start --continuous
uv run src/cmd/client_cli.py stop
uv run src/cmd/client_cli.py chat

# Or run as a background daemon (does not occupy the terminal)
uv run src/cmd/client_cli.py start-install
uv run src/cmd/client_cli.py stop

uv run src/cmd/client_cli.py version --verbose
uv run src/cmd/client_cli.py upgrade --check
```

For non-developer users, command-line launch is recommended (double-clicking executables may close immediately with no visible logs).

`start --continuous` writes PID file: `~/.adm-agent-client/client.pid`  
`stop` reads that PID file and sends SIGTERM (or SIGKILL with `--force`).

`start-install` launches `start --continuous` as a background daemon process and
redirects output to `~/.adm-agent-client/client.log`. Use `stop` to terminate it.

**Client bridge endpoint:**
- WebSocket: `ws://<serve-host>:<serve-port>/clients/ws`
- Status API: `GET /clients`

**Browser fetch behavior:**
- Default: built-in `adm-agent-client fetch` drives local Chrome/Edge via CDP (no Playwright required on client side).
- Default detail batch size is `4` (`ADM_AGENT_CLIENT_DETAIL_LIMIT` can override).
- Optional override: set env var `ADM_AGENT_CLIENT_FETCH_CMD` to a custom command template.
- Template placeholders: `{url}`, `{page_type_hint}`
- Command must output JSON to stdout (e.g. `{"html_content":"..."}` or `{"detail_pages_batch":[...]}`)
- Client can carry local policy profile; when configured, RPC responses include `policy_profile`.

Default fetch command:
```bash
uv run src/cmd/client_cli.py fetch --url "https://example.edu/list" --page-type index --json
```

Optional override example:
```bash
export ADM_AGENT_CLIENT_FETCH_CMD='adm-agent-client fetch --url "{url}" --page-type "{page_type_hint}" --json'
```

**Interactive agent chat:**
- `uv run src/cmd/client_cli.py chat`
- Connects to the configured serve endpoint, submits `POST /agent/chat`, then renders live `/tasks/{task_id}/events` SSE output in the terminal.

### Runtime Logs

- Backend CLI / server commands automatically write rotated timestamped `.txt` logs.
- In source mode, logs are written to the current working directory.
- In bundled executable mode, logs are written beside the executable.
- `adm-agent-client start-install` still writes daemon logs to `~/.adm-agent-client/client.log`.

### Platform Permissions

- **macOS**: after unzip, run `xattr -cr .` once in extracted folder.
- **Windows**: first run may show SmartScreen; choose "More info" → "Run anyway".
- **Linux**: ensure executable bit (`chmod +x adm-agent-client`).

### LLM Bootstrap Prompt (Codex / Claude / OpenClaw)

Generate copy-paste setup prompt:

```bash
uv run src/cmd/client_cli.py bootstrap --target codex --emit-prompt
uv run src/cmd/client_cli.py bootstrap --target claude --emit-prompt
uv run src/cmd/client_cli.py bootstrap --target openclaw --emit-prompt
```

Minimal OpenClaw message (copy-paste):

```text
请在当前环境完成 adm-agent-client 配置并上线：1) 运行 adm-agent-client init（host=<SERVE_HOST>, port=<SERVE_PORT>, client_name=当前机器名）；2) 运行 adm-agent-client status 并确认配置可读取；3) 运行 adm-agent-client start --continuous；4) 若连接失败先检查 host/port 与本机防火墙。请逐步执行并回显每一步结果。
```

### Chrome Extension

The extension provides a UI to interact with the agent.

**Build & Install:**
1.  **Build the extension package**:
    ```bash
    cd extension
    npm install  # First time only
    npm run build
    ```
    This will generate:
    - `extension/dist/`: The unpackaged extension folder.
    - `extension/uni-admission-extension.zip`: A ready-to-share zip file.

2.  **Load into Chrome**:
    - Open Chrome and navigate to `chrome://extensions`.
    - Enable **Developer mode** (top right toggle).
    - Click **Load unpacked**.
    - Select the `extension/dist` folder.

**Usage:**
- Click the extension icon in your browser toolbar.
- Configure settings (database URL, LLM keys) via the gear icon.
- Enter a university slug (e.g., `hku`) and year, then start crawling.
- Adjust per-task taxonomy overrides in popup (enable, low/high thresholds, top-k hints, override toggle).
- The popup now defaults to the agent orchestration path via `POST /agent/run`. If agent runtime is explicitly disabled on the server, the popup falls back to the older analyze/manual-selection flow.
- **Preview Database** (👁 icon): Browse stored programs with filters by university and year.
- **Export to Excel** (📥 icon): Download program data as XLSX files.

## 📦 Build & Distribution

To package the agent for distribution (standalone executable + extension zip):

1.  **Install PyInstaller**:
    ```bash
    pip install pyinstaller
    ```

2.  **Run the Build Script**:
    ```bash
    python scripts/build_dist.py
    ```
    Optional:
    ```bash
    python scripts/build_dist.py --client-only
    python scripts/build_dist.py --separate-artifacts
    ```

3.  **Check Release Folder**:
    The script generates a `release/` directory containing:
    -   `adm-agent/`: The standalone executable (backend engine).
    -   `adm-agent-client/`: The standalone user-side browser automation client (when built).
    -   `extension.zip`: The packaged Chrome extension.
    -   `README.txt`: Quick start guide for end-users.

Release note template (three separate artifacts): `docs/release_notes_template.md`

## 🤖 Agentic Principles
- **Stealth First:** Never trigger bot detection; emulate human behavior.
- **Markdown-Centric:** Convert HTML to Markdown before LLM processing to save tokens.
- **Verified Output:** All data must pass Pydantic validation before being committed to the database.

## 🔧 Development Tools

### Git Hooks
The project includes Git hooks to maintain code quality:

```bash
# Install all Git hooks
bash .githooks/install-hooks.sh

# Or install manually
cp .githooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

**Available Hooks:**
- **pre-push**: Runs `pylint` checks before pushing to remote repository

**Hook Behavior:**
- ✅ **Pass**: Push proceeds normally
- ❌ **Fail**: Push is blocked with error details
- 🚫 **Bypass**: Use `git push --no-verify` to skip hooks (not recommended)

**Testing Hooks:**
```bash
# Test hook directly
.git/hooks/pre-push

# Check code quality manually
uv run pylint src/ scripts/
```

### Testing & Coverage

**Run Tests:**
```bash
# Run all tests (excluding integration tests)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_cleaner_validators.py

# Stop at first failure
uv run pytest -x
```

**Test Coverage:**
```bash
# Run tests with coverage report
uv run pytest --cov=src --cov-report=term

# Generate HTML coverage report
uv run pytest --cov=src --cov-report=html
# Open htmlcov/index.html in browser

# Show missing lines in terminal
uv run pytest --cov=src --cov-report=term-missing

# Generate multiple reports (XML for CI, HTML for local)
uv run pytest --cov=src --cov-report=term --cov-report=xml --cov-report=html
```

**Coverage Configuration:**
- Source: `src/` directory
- Excluded: `tests/`, `__pycache__/`
- Configuration: See `[tool.coverage]` in `pyproject.toml`

**CI/CD:**
- GitHub Actions automatically runs tests with coverage on every push
- Coverage reports are uploaded to Codecov (if configured)
- View coverage summary in GitHub Actions job summary
