# PROJECT CONTEXT: UniAdmission Agent

## 1. Project Goal
Build a trusted, self-updating database of university admission requirements.

**Scope**: Multi-university crawler with intelligent depth exploration and context-aware parsing.

**Key Features**:
- Intelligent crawl depth with Heuristic/Regex scouting (optimized for speed/cost)
- Rolling window sequential chunking for context preservation on large pages
- Multi-provider LLM routing (Google Gemini, DeepSeek, OpenAI, VolcEngine)
- Stealth browsing with anti-detection mechanisms
- **Chrome Extension** for interactive control and monitoring
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
| **CLI** | `typer` |
| **Build** | `pyinstaller` (Backend), `npm` (Extension) |
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
-   **Extension**: React/Vite-based UI in `extension/` directory.
    -   Connects to `http://localhost:8910`
    -   Displays real-time logs and token usage
    -   Manages crawler configuration

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

---

## 4. Build & Distribution System

The project supports a fully automated build pipeline to generate standalone artifacts.

### 4.1 Artifacts
-   **Backend Engine**: `adm-agent` (Single-directory executable via PyInstaller)
-   **Frontend**: `extension/uni-admission-extension.zip` (Chrome Extension)

### 4.2 Build Process
Managed by `scripts/build_dist.py`:
1.  `npm run build` (Extension) → `dist/` + `.zip`
2.  `pyinstaller adm-agent.spec` (Backend) → `dist/adm-agent/`
3.  **Assembly** → `release/` folder with everything needed.

### 4.3 Path Resolution
`src/core/paths.py` handles runtime path resolution transparently:
-   **Dev Mode**: Uses source tree (`src/`, `data/`).
-   **Frozen Mode**: Uses `sys._MEIPASS` for bundled assets and `~/.uni-agent/` for writable data.

---

## 5. Directory Structure

```
uni-admission-agent/
├── src/
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
```

---

## 7. Configuration

Managed via `.env` (loaded at runtime).

```bash
# LLM Credentials
GOOGLE_GENAI_API_KEY=...
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
VOLC_API_KEY=...

# Provider Priority
LLM_PROVIDER_PRIORITY=deepseek,google,openai,volcengine

# Database
DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/uni_admission
```
