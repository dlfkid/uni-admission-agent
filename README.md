# UniAdmission Agent

**Autonomous LLM-powered engine for aggregating and synchronising global university admission requirements into a structured database.**

## Overview

UniAdmission Agent crawls university programme pages, extracts structured admission data, and stores it in a queryable database. It handles dynamic web content and anti-detection using Playwright stealth, converts HTML to Markdown for token-efficient LLM processing, and validates all output through Pydantic schemas.

**Tech stack:** Python 3.12, FastAPI, MCP server, crawl4ai + Playwright, SQLModel / SQLite (default) / PostgreSQL (opt-in), PydanticAI agent runtime, Typer CLI, Chrome extension.

## Architecture

```
Entry Points                    Services Layer                   Infrastructure
┌──────────────┐
│  CLI (Typer) │──┐
└──────────────┘  │    ┌─────────────────────────┐    ┌──────────────────────┐
┌──────────────┐  ├──→ │ src/services/           │──→ │ src/scrapers/        │
│ FastAPI REST │──┤    │   crawler.py            │    │ src/agent_runtime/   │
└──────────────┘  │    │   crawl_strategy/       │    │ src/storage/         │
┌──────────────┐  │    │   (orchestrator,        │    │ src/core/            │
│  MCP Server  │──┘    │    registry, fetch      │    └──────────────────────┘
└──────────────┘       │    ladder, classifier)  │
┌──────────────┐       └─────────────────────────┘
│Chrome Plugin │──→ POST /crawl (REST) / WS /clients/ws
└──────────────┘
```

`src/services/crawl_strategy/` is the deterministic crawl tier — it classifies an index page's layout, selects the right extractor, and dispatches fetch via a ladder (server → client → API). `src/agent_runtime/` contains the PydanticAI-based agent loop (s01–s12).

## Quick Start (Plugin Users)

This repo ships as a **plugin** with a router skill and 4 sub-skills (install / crawl / diagnose / export) plus 5 slash commands. Auto-detects `claude`, `codex`, `opencode`, and `openclaw`.

### One-line install

```bash
git clone https://github.com/dlfkid/uni-admission-agent.git ~/.uni-admission-agent && \
  bash ~/.uni-admission-agent/install-plugin.sh
```

The installer detects which CLI(s) you have and configures each. Safe to re-run — refreshes the install.

### Manual install

**Claude Code** (native plugin + auto-update):
```bash
claude plugin marketplace add https://github.com/dlfkid/uni-admission-agent
claude plugin install uni-admission-agent
```

**Codex / OpenCode / OpenClaw** — symlink the skills:
```bash
# Codex: ~/.agents/skills/   |  OpenCode: ~/.config/opencode/skills/  |  OpenClaw: ~/.openclaw/skills/
git clone https://github.com/dlfkid/uni-admission-agent.git ~/.uni-admission-agent
mkdir -p ~/.agents/skills
for s in using-uni-admission-agent uni-admission-install uni-admission-crawl uni-admission-diagnose uni-admission-export; do
  ln -sfn ~/.uni-admission-agent/skills/$s ~/.agents/skills/$s
done
```
(Adjust target path for OpenCode / OpenClaw.)

### Updates
```bash
# Claude Code
claude plugin update uni-admission-agent
# Others — rerun installer (does git pull + refreshes symlinks)
bash ~/.uni-admission-agent/install-plugin.sh
```

### Slash commands

| Command | What it does |
|---|---|
| `/uni-admission-agent:uni-admission-agent` | Router — describe what you want; plugin routes to install / crawl / diagnose / export. |
| `/uni-admission-agent:install` | Install / upgrade / start the agent (`~/.uni-agent/`, no sudo, SQLite default). |
| `/uni-admission-agent:crawl` | Crawl a university URL — single page, index, or paginated. |
| `/uni-admission-agent:diagnose` | Investigate why a crawl failed — quarantine, audit funnel, stop reasons. |
| `/uni-admission-agent:export` | Export stored data to Excel / CSV, or preview the database. |

### Natural-language entry

Slash commands are optional — the router triggers on any *adm-agent / 抓取大学 / crawl programs* intent:

```
请帮我抓取利兹大学 2026 年的硕士课程，入口 https://courses.leeds.ac.uk/course-search/masters-courses
跑完后汇报：总程序数、stop_reason、quarantine top 3 原因。
```

The router preflights (CLI installed? server running?), routes to install if needed, then to crawl.

## Developer Setup

**Prerequisites:** `pyenv` + Python 3.12, `uv`.

```bash
pyenv local 3.12.0
uv sync
cp .env.example .env   # add your API keys
bash .githooks/install-hooks.sh
```

### CLI entry-point clarification

| Context | Command |
|---|---|
| Development / source checkout | `uni-admission <cmd>` (installed by `uv sync` via `pyproject.toml`) or `uv run python -m src.cmd.cli <cmd>` |
| Packaged binary (releases) | `./adm-agent <cmd>` (macOS/Linux) · `adm-agent <cmd>` (Windows — the installer's `adm-agent.cmd` launcher resolves via `PATHEXT`) |

All examples below use `uni-admission` (dev). For the packaged binary, substitute `./adm-agent` (or `adm-agent` on Windows — no `.exe` needed).

### `.env` minimum

```bash
# At least one LLM provider key is required.
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL_NAME=gemini-2.0-flash

# DeepSeek (optional)
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_NAME=deepseek-chat

# VolcEngine / 豆包 (optional)
VOLC_API_KEY=your_volc_api_key_here
VOLC_MODEL_ID=your_model_endpoint_id

# Custom OpenAI-compatible provider (optional)
CUSTOM_LLM_BASE_URL=https://api.openai.com/v1
CUSTOM_LLM_API_KEY=your_openai_api_key_here
CUSTOM_LLM_MODEL_NAME=gpt-4o-mini

# Provider priority (drag to reorder in Chrome extension)
LLM_PRIORITY_LIST=deepseek, gemini, volcengine, custom

# Agent runtime (enabled by default)
AGENT_ENABLED=true
AGENT_RUNTIME=pydanticai

# DATABASE_URL — leave unset for SQLite (recommended).
# Opt-in to Postgres: postgresql+psycopg2://user:pass@host:port/dbname
```

## CLI Commands

| Command | Description |
|---|---|
| `uni-admission check` | Verify environment |
| `uni-admission llm-config` | Interactive LLM provider wizard |
| `uni-admission browser-install` | Download Chromium (required once) |
| `uni-admission up` | Start server + client together; Ctrl+C stops both |
| `uni-admission serve [--port N]` | Start API + MCP server (default `0.0.0.0:8910`) |
| `uni-admission serve-install [--port N]` | Start server as background daemon |
| `uni-admission serve-stop` | Stop foreground or daemon server |
| `uni-admission crawl --name <slug> --year <Y> --url <url>` | Crawl a URL and import admission data |
| `uni-admission crawl --name hku --year 2026 --url <url> --continue 2` | Extra LLM scouting depth |
| `uni-admission crawl-index <url> [--limit N \| --all]` | Deterministic index harvest (see below) |
| `uni-admission import --name <slug> --year <Y> --file <xlsx>` | Import from Excel (`--llm` for LLM fallback) |
| `uni-admission export --name <slug> --output <file> [--year Y]` | Export to Excel |
| `uni-admission status` | DB URL, university count, program count |
| `uni-admission db-version` | Alembic revision status |
| `uni-admission db-migrate --yes` | Apply pending migrations |
| `uni-admission db-reinit --yes` | Drop + recreate + migrate (destructive) |
| `uni-admission db-export --output <file.zip>` | Export the entire database (all tables) to one portable zip file |
| `uni-admission db-import --file <file.zip> [--yes] [--force]` | Import a database snapshot produced by db-export (target must be empty unless `--force`) |
| `uni-admission repair --auto` | Auto-repair migration failures |
| `uni-admission ingestion-jobs [--limit N]` | List recent ingestion jobs |
| `uni-admission ingestion-resume --job <uid> --stage <stage>` | Resume failed job |
| `uni-admission golden-collect [--overwrite]` | Collect quality golden samples |
| `uni-admission quality-score [--threshold 0.60]` | Run quality scoring gate |
| `uni-admission taxonomy-export [--output <file>] [--include-learned] [--min-confidence 0.90]` | Export taxonomy snapshot |
| `uni-admission quarantine list --university <slug> [--year Y]` | List quarantined extractions |
| `uni-admission quarantine clear --university <slug> [--reason <r>]` | Clear quarantine entries |
| `uni-admission programs delete --university <slug> [--year Y] [--yes]` | Batch-delete program snapshots for a university, optionally scoped to one year. Preview-only without `--yes`. |
| `uni-admission audit list --university <slug> [--year Y] [--limit N]` | Inspect index→detail funnel |
| `uni-admission audit drill <id>` | Drill into one audit row |
| `uni-admission crawl-summary --university <slug> [--year Y]` | Post-crawl summary (LLM-CLI friendly) |
| `uni-admission diagnostics clear --university <slug> [--year Y]` | Wipe quarantine + audit records |
| `uni-admission upgrade [--check \| --force \| --rollback \| --json] [--migrate/--no-migrate]` | Update the backend, or return to the previous version. Atomic: a failed upgrade leaves the install unchanged. `--migrate` (default) runs the post-upgrade DB migration; `--no-migrate` skips it. |
| `uni-admission version [--verbose]` | Show version |
| `uni-admission help [--verbose]` | Show help |

## crawl-index / crawl-strategy

`crawl-index` is the deterministic programme-name harvesting tier, separate from the LLM crawl pipeline:

```bash
uni-admission crawl-index <url>              # auto-detect layout, extract names
uni-admission crawl-index <url> --limit 50   # stop after 50 names
uni-admission crawl-index <url> --all        # paginate to end
uni-admission crawl-index <url> --json       # machine-readable output
```

Internally, `src/services/crawl_strategy/` classifies each index page's layout (heading-link, inline-degree, merged-columns, blob, JSON-API) and dispatches via a fetch ladder (server fetch → client browser → API). A registry pins proven strategies for known universities:

| Domain | Fetch mode | Extract kind |
|---|---|---|
| `courses.leeds.ac.uk` | Server | Heading link (paginated) |
| `www.ucl.ac.uk` | Client browser | Inline degree links |
| `www.manchester.ac.uk` | Client browser | Merged columns |
| `www.polyu.edu.hk` | Client browser | Blob |
| `study.nus.edu.sg` | Salesforce Apex API | JSON API (full catalogue, one POST) |

Unknown universities fall back to automatic classification. To add a new university: add a row to `src/services/crawl_strategy/registry.py` + a golden sample.

## Database

`uni-admission` supports two backends. **SQLite is the default and requires no setup.**

| | **SQLite** *(default)* | **PostgreSQL** *(opt-in)* |
|---|---|---|
| `DATABASE_URL` | leave unset / commented out | `postgresql+psycopg2://user:pass@host:port/dbname` |
| Install needed | none (stdlib) | `psycopg2-binary` + running Postgres |
| Data location | `./data/admission.db` (dev) · `~/.uni-agent/admission.db` (binary) · `%USERPROFILE%\.uni-agent\admission.db` (Windows binary) | your Postgres instance |
| Schema bootstrap | `SQLModel.metadata.create_all()` at startup | Alembic migrations to `head` at startup |
| Best for | local single-user, demos | multi-process, shared deployments |

**SQLite PRAGMAs applied per-connection:**

| PRAGMA | Value | Why |
|---|---|---|
| `journal_mode` | `WAL` | Readers don't block writers |
| `busy_timeout` | `5000 ms` | Wait instead of failing on transient locks |
| `foreign_keys` | `ON` | SQLite default is off; we enforce referential integrity |
| `synchronous` | `NORMAL` | Safe under WAL, faster than `FULL` |

**Platform notes:**
- macOS / Linux / Windows — same `.db` format, fully portable.
- Do **not** put the `.db` file in a synced folder (iCloud, OneDrive, Dropbox, Google Drive) — sync clients race `fsync` and can corrupt SQLite.
- SQLite file-locking is unreliable over SMB / NFS — use a local disk path.

**Postgres:** set `DATABASE_URL` and restart. Alembic runs automatically. Create the database first: `createdb uni_admission`. To switch back to SQLite, unset `DATABASE_URL`; the two engines keep separate state.

## Server & REST API

```bash
uni-admission serve              # start API + MCP server at 0.0.0.0:8910
uni-admission serve --port 9000
uni-admission serve-install      # background daemon
uni-admission serve-stop         # stop foreground or daemon
```

`serve` writes `~/.adm-agent/server.pid`; `serve-stop` sends SIGTERM and removes it.  
Agent runtime (`pydanticai`) is enabled by default; override with `AGENT_ENABLED=false` or `AGENT_RUNTIME=legacy`.

**REST endpoints (summary):**

| Method | Path | Description |
|---|---|---|
| `POST` | `/crawl` | Submit a crawl job; returns `task_id` |
| `POST` | `/analyze` | Analyse an entry page for link candidates |
| `GET` | `/tasks/{id}` | Poll task status |
| `GET` | `/tasks/{id}/events` | SSE stream — agent progress / thinking / summary |
| `POST` | `/agent/run` | Agent orchestration request |
| `POST` | `/agent/chat` | Free-form server-side agent chat |
| `POST` | `/agent/review/confirm` | Confirm low-confidence onhold candidates |
| `GET` | `/clients` | List connected browser-automation clients |
| `GET` | `/status` | Database statistics |
| `GET` | `/programs` | Query programs (`?univ_slug=hku&year=2026`) |
| `GET` | `/universities` | List universities |
| `POST` | `/export` | Export programs to XLSX |

Full request/response schemas: visit **`http://localhost:8910/docs`** (FastAPI Swagger UI) while the server is running.

## MCP Server

The MCP server is mounted at `/mcp`. Tools are grouped into two sets:

- **Base toolset** (always): `analyze`, `crawl`, `crawl_detail_batch`, `ingest`, `db_query`, `runtime_status`, `program_patch`, `program_patch_batch`, `help`. All page-understanding tools (`analyze`, `crawl`, `crawl_detail_batch`) always use the server's own configured LLM — there is no caller-selectable "use a different LLM" mode. (An earlier design registered a parallel `*_internal_llm` toolset for that; it was removed — 7 of 9 variants were byte-identical aliases with zero behavioral difference, and the two that did differ used a deterministic heuristic, not "the caller's LLM", so the duplication bought nothing.)
- **Agent toolset** (default, unless agent runtime is disabled): `agent_run`, `agent_review_confirm`.

`ingest` is the one deliberate exception: it persists **already-structured** program data from any source (the caller did its own extraction by whatever means, a bulk backfill, a different pipeline) without running any server-side LLM extraction — that is a generically useful capability independent of the "which LLM" question, not a caller-driven mode of `analyze`/`crawl`.

**Recommended MCP interactive flow:**

1. Call `runtime_status` — inspect available runtime path (`client_available`, `internal_llm_available` — whether the server's LLM is actually configured, so `analyze`/`crawl` calls will succeed).
2. Call `analyze` as the **single entrypoint**. Read `page_type_detected`, `requires_user_confirmation`, `next_step_options`.
3. If `requires_user_confirmation=true`, ask the user whether to proceed with detected `index` / `detail`.
4. Follow the selected next-step path:
   - **detail path:** `crawl`
   - **index, already have structured data:** select candidates → structure data yourself → `ingest`
   - **index, want the server to extract it:** `crawl_detail_batch`
5. Apply user corrections via `program_patch` / `program_patch_batch` (partial-failure safe).

**Crawl decision details:**
- Missing `year` blocks with `requires_user_input=true`, `missing_fields=["year"]`.
- Taxonomy thresholds: candidate keep `>= 0.75`; auto-run `>= 0.92` with `<= 10` candidates.
- `agent_run` auto-processes high-confidence candidates; low-confidence items return as `onhold_items` for confirmation via `agent_review_confirm`.

## adm-agent-client

`adm-agent-client` is a user-side browser-automation bridge. It connects a local machine to a remote `serve` instance so MCP/REST callers can drive a real browser without an extension.

```bash
uv run src/cmd/client_cli.py init           # configure host/port/client-name
uv run src/cmd/client_cli.py status
uv run src/cmd/client_cli.py start --continuous    # foreground (PID → ~/.adm-agent-client/client.pid)
uv run src/cmd/client_cli.py start-install  # background daemon (log → ~/.adm-agent-client/client.log)
uv run src/cmd/client_cli.py stop [--force]
uv run src/cmd/client_cli.py chat           # interactive agent chat via SSE
uv run src/cmd/client_cli.py bootstrap --target claude --emit-prompt   # copy-paste LLM setup prompt
```

- WebSocket bridge: `ws://<serve-host>:<serve-port>/clients/ws`
- Default batch size: 4 (override: `ADM_AGENT_CLIENT_DETAIL_LIMIT`).
- Custom fetch command: set `ADM_AGENT_CLIENT_FETCH_CMD` to a template with `{url}` and `{page_type_hint}`; command must output JSON to stdout.

## Frontend (Chrome Extension + Web UI)

The frontend is a single Vite bundle that ships in two forms:

- **Chrome Extension** — load `frontend/dist/` as an unpacked extension; auto-detects the current tab URL, supports multi-tab automation.
- **Web UI** — the same bundle served by the backend at `http://<host>:<port>/ui/`; no extension install required.

Source layout: `frontend/src/{shared,extension,web}/` — `shared/` holds UI common to both targets; `extension/` is extension-only (background service worker); `web/` is reserved for web-only entries.

**Build & load:**

```bash
cd frontend
npm install       # first time only
npm run build
# Outputs: frontend/dist/ (unpacked bundle) + frontend/uni-admission-extension.zip
```

Load the extension: Chrome → `chrome://extensions` → **Developer mode** → **Load unpacked** → select `frontend/dist`.

Or skip the extension: run `uni-admission serve`, then open the `Web UI` URL printed at startup.

**Features:** configure LLM keys and DB URL via the gear icon; set per-task taxonomy overrides; preview stored programs (filter by university / year); export to XLSX. The popup defaults to the agent orchestration path (`POST /agent/run`); falls back to the analyze/manual-selection flow if agent runtime is disabled.

## Build & Distribution

```bash
uv add --dev pyinstaller         # install build tooling
python scripts/build_dist.py     # build all artifacts
# Optional flags:
python scripts/build_dist.py --client-only
python scripts/build_dist.py --separate-artifacts
```

Output in `release/`:
- `adm-agent/` — standalone backend binary.
- `adm-agent-client/` — standalone user-side browser-automation client.
- `extension.zip` — packaged Chrome extension.
- `README.txt` — quick-start guide for end-users.

**Platform notes for the packaged binary:**
- **macOS:** run `xattr -cr /path/to/adm-agent` after extracting; if "System cannot verify the developer" appears go to **Settings → Privacy & Security → Allow Anyway**.
- **Windows:** first run may trigger SmartScreen; choose **More info → Run
  anyway**. The command is `adm-agent` (the installer writes a
  `adm-agent.cmd` launcher that resolves the active version).
- **Linux:** ensure the executable bit (`chmod +x adm-agent`).

Release note template: `docs/release_notes_template.md`.

## Development

### Git Hooks

```bash
bash .githooks/install-hooks.sh   # install all hooks
# Or manually:
cp .githooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

**pre-push** runs `pylint` before every push. Use `git push --no-verify` to bypass (not recommended).

```bash
uv run pylint src/ scripts/    # run linting manually
```

### Testing & Coverage

```bash
uv run pytest                                      # all tests (excluding integration)
uv run pytest -v                                   # verbose
uv run pytest tests/test_cleaner_validators.py     # single file
uv run pytest -x                                   # stop at first failure

uv run pytest --cov=src --cov-report=term          # with coverage
uv run pytest --cov=src --cov-report=html          # HTML report (open htmlcov/index.html)
uv run pytest --cov=src --cov-report=term-missing  # show missing lines
```

Coverage configuration: see `[tool.coverage]` in `pyproject.toml`. Source: `src/`; excluded: `tests/`, `__pycache__/`.

### CI/CD

GitHub Actions runs tests with coverage on every push. Coverage reports are uploaded to Codecov (if configured); view the summary in the GitHub Actions job summary.

### Troubleshooting: "Playwright browser not found"

```bash
uni-admission browser-install           # recommended — downloads Chromium automatically
# Or, if you have uv available:
uv run playwright install chromium
# Or set a custom path:
export PLAYWRIGHT_BROWSERS_PATH=/path/to/ms-playwright
uni-admission serve
```
