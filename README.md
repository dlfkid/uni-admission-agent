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

## � Production Usage (No Code Required)

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
   
   # Start the server
   .\adm-agent.exe serve
   ```

#### macOS / Linux
1. Extract the archive:
   ```bash
   tar -xzf adm-agent-*.tar.gz
   cd adm-agent-*
   ```
2. Run via terminal:
   ```bash
   # Check environment
   ./adm-agent check
   
   # Install browser (required for crawling, only needed once)
   ./adm-agent browser-install
   
   # Start the server
   ./adm-agent serve
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

   # LLM Priority config
   LLM_PRIORITY_LIST=deepseek, gemini, volcengine
   ```
3. Set your `DATABASE_URL` and API keys in `.env`.

## �🚀 Getting Started (Development)
1. `pyenv local 3.12.0`
2. `uv sync`
3. Copy `.env.example` to `.env` and add your API keys.

## 📖 Usage

### CLI Commands

```bash
# Environment check
uv run src/cmd/cli.py check

# Install Playwright browser (only needed once)
uv run src/cmd/cli.py browser-install

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

3.  **Check Release Folder**:
    The script generates a `release/` directory containing:
    -   `adm-agent/`: The standalone executable (backend engine).
    -   `extension.zip`: The packaged Chrome extension.
    -   `README.txt`: Quick start guide for end-users.

## 🤖 Agentic Principles
- **Stealth First:** Never trigger bot detection; emulate human behavior.
- **Markdown-Centric:** Convert HTML to Markdown before LLM processing to save tokens.
- **Verified Output:** All data must pass Pydantic validation before being committed to the database.