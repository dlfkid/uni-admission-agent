# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for UniAdmission Agent (adm-agent).

Build with:
    uv run python scripts/build_dist.py

NOTE: We use collect_all() extensively because uv-managed venvs have a
package layout that PyInstaller's standard module-graph analysis cannot
reliably discover (e.g. typer/typer-slim split packages).
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None
ROOT = Path(SPECPATH)

# ---------------------------------------------------------------------------
# Force-collect packages that PyInstaller fails to discover in uv venvs
# ---------------------------------------------------------------------------

# Packages whose datas / binaries / hiddenimports must be force-collected.
# collect_all() returns (datas, binaries, hiddenimports) for each package.
_COLLECT_PKGS = [
    # --- CLI ---
    "typer",
    "click",
    "rich",
    # --- Version comparison (upgrade) ---
    "packaging",
    # --- FastAPI / ASGI ---
    "fastapi",
    "starlette",
    "uvicorn",
    # --- Database ---
    "sqlmodel",
    "sqlalchemy",
    "sqlalchemy_utils",
    "psycopg2",
    "aiosqlite",
    "alembic",
    # --- Data processing ---
    "pandas",
    "openpyxl",
    "dateutil",
    # --- LLM ---
    "google.genai",
    "openai",
    # --- Scraping ---
    "playwright",
    "playwright_stealth",
    "crawl4ai",
    # --- Pydantic ---
    "pydantic",
    "pydantic_settings",
    # --- MCP ---
    "mcp",
    # --- PDF ---
    "pymupdf4llm",
    # --- SSL certs (needed for macOS PyInstaller builds) ---
    "certifi",
]

all_datas = []
all_binaries = []
all_hiddenimports = []

for pkg in _COLLECT_PKGS:
    try:
        datas, binaries, hiddenimports = collect_all(pkg)
        all_datas += datas
        all_binaries += binaries
        all_hiddenimports += hiddenimports
    except Exception:
        print(f"WARN: collect_all('{pkg}') failed — skipping")

# ---------------------------------------------------------------------------
# Static datas
# ---------------------------------------------------------------------------

# Prompt templates — bundled into src/agents/prompts/ inside _MEIPASS
all_datas.append(
    (str(ROOT / "src" / "agents" / "prompts" / "*.txt"), "src/agents/prompts")
)

# Alembic configuration and migration scripts
if (ROOT / "alembic.ini").exists():
    all_datas.append((str(ROOT / "alembic.ini"), "."))
if (ROOT / "migrations").exists():
    all_datas.append((str(ROOT / "migrations"), "migrations"))

# .env.example for reference
if (ROOT / ".env.example").exists():
    all_datas.append((str(ROOT / ".env.example"), "."))

# Default taxonomy seed for runtime bootstrap / user extension.
taxonomy_seed = ROOT / "golden_samples" / "programs_names.json"
if taxonomy_seed.exists():
    all_datas.append((str(taxonomy_seed), "golden_samples"))

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(ROOT / "src" / "cmd" / "cli.py")],
    pathex=[str(ROOT)] + sys.path,
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports + [
        # --- Server module (string-imported by uvicorn.run) ---
        "src.api.server",
        "src.api.schemas",
        "src.api.task_manager",
        # --- Project modules ---
        "src.core.paths",
        "src.core.environment",
        "src.core.token_tracker",
        "src.core.matcher",
        "src.core.parser",
        "src.agents.factory",
        "src.agents.cleaner_agent",
        "src.scrapers.engine",
        "src.services.crawler",
        "src.services.migrations",
        "src.services.repair",
        "src.storage.db_manager",
        "src.storage.importer",
        "src.storage.exporter",
        "src.models.admission",
        "src.models.requirement",
        "src.models.scraper_models",
        "src.utils.text",
        "src.utils.pdf_processor",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
        "tests",
        "extension",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="adm-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="adm-agent",
)
