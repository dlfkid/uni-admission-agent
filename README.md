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
│  CLI (Typer)  │──┐
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

## 🚀 Getting Started
1. `pyenv local 3.12.0`
2. `uv sync`
3. Copy `.env.example` to `.env` and add your API keys.

## 📖 Usage

### CLI Commands

```bash
# Environment check
uv run src/cmd/cli.py check

# Import Excel data
#   --name: University slug (a-z0-9-)
#   --year: Academic year (e.g., 2026)
#   --file: Path to XLSX file
#   --llm:  Enable LLM analysis (optional)
uv run src/cmd/cli.py import --name hku --year 2026 --file example/hku-26-27.xlsx

# Import with LLM fallback
uv run src/cmd/cli.py import --name hku --year 2026 --file example/hku-26-27.xlsx --llm

# Export data to Excel
#   --name:   University slug
#   --output: Output file path
#   --year:   Academic year (optional)
uv run src/cmd/cli.py export --name hku --output hku_export.xlsx --year 2026

# Crawl a URL and import admission data
#   --name:      University slug
#   --year:      Academic year
#   --url:       Starting URL
#   --continue:  Extra depth for LLM scouting (default: 0)
uv run src/cmd/cli.py crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes
uv run src/cmd/cli.py crawl --name hku --year 2026 --url https://admissions.hku.hk/programmes --continue 2

# Database status
uv run src/cmd/cli.py status

# Start API + MCP server (default: 0.0.0.0:8910)
uv run src/cmd/cli.py serve
uv run src/cmd/cli.py serve --port 9000
```

### REST API

With the server running (`uv run src/cmd/cli.py serve`):

```bash
# Submit a crawl job (returns task_id)
curl -X POST http://localhost:8910/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://admissions.hku.hk/programmes", "univ_slug": "hku", "year": 2026}'

# Check task status
curl http://localhost:8910/tasks/{task_id}

# Database statistics
curl http://localhost:8910/status

# Query programs
curl "http://localhost:8910/programs?univ_slug=hku&year=2026"
```

### MCP Server

The MCP server is mounted at `/mcp` and exposes two tools:
- **`crawl`** — Crawl a URL and import admission data
- **`db_query`** — Query programs from the database

### Chrome Extension

1. `cd extension && npm install && npm run build`
2. Open `chrome://extensions` → Enable Developer Mode → Load unpacked → select `extension/dist`
3. Navigate to a university admissions page, click the extension icon, enter slug + year, and click **Send to Agent**

## 🤖 Agentic Principles
- **Stealth First:** Never trigger bot detection; emulate human behavior.
- **Markdown-Centric:** Convert HTML to Markdown before LLM processing to save tokens.
- **Verified Output:** All data must pass Pydantic validation before being committed to the database.