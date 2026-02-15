# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for UniAdmission Agent (adm-agent).

Build with:
    pyinstaller adm-agent.spec
"""

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(ROOT / "src" / "cmd" / "cli.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Prompt templates — bundled into src/agents/prompts/ inside _MEIPASS
        (str(ROOT / "src" / "agents" / "prompts" / "*.txt"), "src/agents/prompts"),
        # .env.example for reference
        (str(ROOT / ".env.example"), ".") if (ROOT / ".env.example").exists() else (None, None),
    ],
    hiddenimports=[
        # --- Typer / CLI ---
        "typer",
        "click",
        # --- FastAPI + ASGI ---
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.responses",
        "starlette.middleware",
        "starlette.middleware.cors",
        "starlette.concurrency",
        # --- Server module (string-imported by uvicorn.run) ---
        "src.api.server",
        "src.api.schemas",
        "src.api.task_manager",
        # --- Database ---
        "sqlmodel",
        "sqlalchemy",
        "sqlalchemy.dialects.postgresql",
        "sqlalchemy_utils",
        "psycopg2",
        "aiosqlite",
        "alembic",
        # --- Data processing ---
        "pandas",
        "openpyxl",
        "dateutil",
        # --- LLM Providers ---
        "google.genai",
        "openai",
        "volcenginesdkcore",
        "volcenginesdkarkruntime",
        # --- Scraping ---
        "playwright",
        "playwright.async_api",
        "playwright_stealth",
        "crawl4ai",
        # --- MCP ---
        "mcp",
        # --- PDF ---
        "pymupdf4llm",
        "fitz",
        # --- Pydantic ---
        "pydantic",
        "pydantic_settings",
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
        "src.storage.db_manager",
        "src.storage.importer",
        "src.storage.exporter",
        "src.models.admission",
        "src.models.scraper_models",
        "src.utils.text",
        "src.utils.pdf_processor",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Strip out things we definitely don't need
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

# Filter out None entries from datas (e.g. when .env.example is missing)
a.datas = [(src, dst, typ) for src, dst, typ in a.datas if src is not None]

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
    # Disable terminal window on macOS — not relevant for CLI tool
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
