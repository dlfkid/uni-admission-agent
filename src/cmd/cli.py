#!/usr/bin/env python3
"""
UniAdmission Agent — Typer CLI

Provides command-line interface for crawling, importing, exporting,
and querying university admission data. All commands delegate to
``src.services.crawler`` for business logic.

Usage:
    uv run src/cmd/cli.py crawl --name hku --year 2026 --url <URL>
    uv run src/cmd/cli.py import --name hku --year 2026 --file f.xlsx
    uv run src/cmd/cli.py export --name hku --output out.xlsx
    uv run src/cmd/cli.py status
    uv run src/cmd/cli.py serve
"""

import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Optional

import typer

# Ensure project root on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Must run before any Playwright import to redirect browser lookup
from src.core.paths import configure_playwright_path  # noqa: E402

configure_playwright_path()

from src.core.token_tracker import tracker
from src.services.crawler import (
    check_environment,
    crawl_url,
    export_data,
    get_db_status,
    import_file,
)
from src.services.upgrade import check_for_updates, upgrade_backend
from src.core.environment import install_playwright_browser
from src.storage.db_manager import DatabaseManager


# ---------------------------------------------------------------------------
#  Help/Usage Information
# ---------------------------------------------------------------------------

def get_help_text() -> str:
    """Generate comprehensive CLI help text."""
    help_text = """
╔══════════════════════════════════════════════════════════════╗
║                UniAdmission Agent — CLI Reference           ║
╚══════════════════════════════════════════════════════════════╝

UNIVERSITY DATA MANAGEMENT:
    crawl      Crawl university admission pages and import data
    import     Import data from Excel files 
    export     Export data to Excel format
    
DATABASE & STATUS:
    status     Show database statistics and connection info
    check      Run environment and dependency checks
    
SERVER OPERATIONS:
    serve      Start API + MCP server (default: 0.0.0.0:8910)
    serve-stop Stop running server instance
    
SYSTEM MAINTENANCE:
    upgrade    Check for and install backend updates
    version    Show current version information
    browser-install  Install Playwright Chromium browser
    
USAGE EXAMPLES:
    ./adm-agent crawl --name hku --year 2026 --url https://admissions.hku.hk/
    ./adm-agent import --name hku --year 2026 --file data.xlsx --llm
    ./adm-agent export --name hku --output report.xlsx --year 2026
    ./adm-agent serve --port 9000
    ./adm-agent upgrade --check
    ./adm-agent status
    
For detailed help on any command:
    ./adm-agent <command> --help
    
For current version info:
    ./adm-agent version --verbose
"""
    return help_text.strip()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  PID-file helpers (used by `serve` / `serve-stop`)
# ---------------------------------------------------------------------------

_PID_DIR = Path.home() / ".adm-agent"
_PID_FILE = _PID_DIR / "server.pid"


def _write_pid_file() -> None:
    """Persist the current process PID so ``serve-stop`` can find it."""
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _read_pid_file() -> "Optional[int]":
    """Return the PID stored in the PID file, or *None* if unavailable."""
    if not _PID_FILE.exists():
        return None
    try:
        return int(_PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _remove_pid_file() -> None:
    """Delete the PID file, ignoring errors."""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


app = typer.Typer(
    name="uni-admission",
    help="UniAdmission Agent — Automated university admission data scraper",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


# ---------------------------------------------------------------------------
#  Validators
# ---------------------------------------------------------------------------

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _validate_slug(value: str) -> str:
    """Validate university slug (lowercase, numbers, hyphens only)."""
    if not _SLUG_PATTERN.match(value):
        raise typer.BadParameter(
            f"Invalid slug '{value}'. Must contain only lowercase letters, "
            "numbers, and hyphens."
        )
    return value


def _validate_year(value: int) -> int:
    """Validate academic year (positive integer)."""
    if value <= 0:
        raise typer.BadParameter(
            f"Invalid year '{value}'. Must be a positive integer."
        )
    return value


# ---------------------------------------------------------------------------
#  Shared setup
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> None:
    """Configure root logging."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _init_db(verbose: bool = False) -> None:
    """Ensure database is initialised."""
    try:
        DatabaseManager().init_db()
    except Exception as e:
        if verbose:
            logger.warning("Database auto-init warning: %s", e)


# ---------------------------------------------------------------------------
#  Commands
# ---------------------------------------------------------------------------


@app.command()
def check(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Run environment and dependency checks."""
    _setup_logging(verbose)
    try:
        check_environment(verbose=verbose)
        typer.echo("✅ All checks passed")
    except Exception as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="browser-install")
def browser_install(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Download and install the Playwright Chromium browser for crawling."""
    _setup_logging(verbose)

    typer.echo("🔍 Checking browser status...")

    # Check if already installed
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser_path = Path(p.chromium.executable_path)
            if browser_path.exists():
                typer.echo(f"✅ Browser already installed at: {browser_path}")
                typer.echo("No action needed.")
                return
    except Exception:
        pass

    typer.echo("📥 Downloading Chromium browser (this may take a few minutes)...")

    try:
        install_playwright_browser()
        typer.echo("✅ Browser installed successfully!")
    except Exception as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="import")
def import_cmd(
    name: str = typer.Option(..., help="University slug (a-z0-9-)"),
    year: int = typer.Option(..., help="Academic year (e.g. 2026)"),
    file: str = typer.Option(..., help="Path to XLSX / PDF file"),
    llm: bool = typer.Option(False, help="Enable LLM analysis for missing data"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import university data from an Excel or PDF file."""
    _setup_logging(verbose)
    name = _validate_slug(name)
    year = _validate_year(year)
    _init_db(verbose)

    typer.echo(f"Importing from: {file}  (LLM: {'Enabled' if llm else 'Disabled'})")
    try:
        result = import_file(file_path=file, univ_slug=name, year=year, use_llm=llm)
        typer.echo(f"✅ Import complete: {result.source_file}")
        tracker.log_summary()
    except Exception as e:
        logger.exception("Import failed: %s", e)
        raise typer.Exit(code=1)


@app.command()
def export(
    name: str = typer.Option(..., help="University slug"),
    output: str = typer.Option(..., help="Output XLSX file path"),
    year: Optional[int] = typer.Option(None, help="Academic year (default: all)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export university data to Excel."""
    _setup_logging(verbose)
    name = _validate_slug(name)
    _init_db(verbose)

    typer.echo(f"Exporting {name} (Year: {year or 'All'}) → {output}")
    try:
        export_data(univ_slug=name, output_path=output, year=year)
        typer.echo("✅ Export complete")
    except Exception as e:
        logger.exception("Export failed: %s", e)
        raise typer.Exit(code=1)


@app.command()
def crawl(
    name: str = typer.Option(..., help="University slug (a-z0-9-)"),
    year: int = typer.Option(..., help="Academic year"),
    url: str = typer.Option(..., help="Starting URL to crawl"),
    continue_depth: int = typer.Option(
        0, "--continue", help="Extra depth for LLM scouting"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Crawl a URL and import admission data."""
    _setup_logging(verbose)
    name = _validate_slug(name)
    year = _validate_year(year)
    _init_db(verbose)

    typer.echo(f"Crawling: {url}  (univ={name}, year={year}, depth={continue_depth})")
    try:
        result = asyncio.run(
            crawl_url(url=url, univ_slug=name, year=year, continue_depth=continue_depth)
        )
        typer.echo(f"✅ Crawl complete: {result.imported_count} programs imported")
        tracker.log_summary()
    except Exception as e:
        logger.exception("Crawl failed: %s", e)
        raise typer.Exit(code=1)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show database statistics."""
    _setup_logging(verbose)
    _init_db(verbose)

    try:
        result = get_db_status()
        typer.echo(f"\nDatabase Status:")
        typer.echo(f"  Universities: {result.university_count}")
        typer.echo(f"  Programs:     {result.program_count}")

        if result.universities:
            typer.echo("\nBreakdown by University:")
            for u in result.universities:
                typer.echo(f"  - {u.name} ({u.slug}):")
                if not u.year_breakdown:
                    typer.echo("      (No programs)")
                for yr, count in u.year_breakdown.items():
                    typer.echo(f"      {yr}: {count} programs")
    except Exception as e:
        logger.error("Failed to get status: %s", e)
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8910, help="Port number"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the FastAPI + MCP server."""
    _setup_logging(verbose)
    
    # Pre-flight check: Ensure browser is available
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser_path = Path(p.chromium.executable_path)
            if not browser_path.exists():
                raise FileNotFoundError(f"Browser executable not found at: {browser_path}")
            typer.echo(f"✅ Found browser at: {browser_path}")
    except Exception as e:
        typer.echo("❌ Critical Error: Playwright browser not found!", err=True)
        typer.echo(f"   Details: {e}", err=True)
        typer.echo("\n👉 Solution 1 (Recommended): Run the browser-install command:", err=True)
        typer.echo("   adm-agent browser-install", err=True)
        typer.echo("\n👉 Solution 2: Install browsers (if you have python/uv installed):", err=True)
        typer.echo("   uv run playwright install chromium", err=True)
        typer.echo("\n👉 Solution 3: Set custom path via environment variable:", err=True)
        typer.echo("   export PLAYWRIGHT_BROWSERS_PATH=/path/to/ms-playwright", err=True)
        raise typer.Exit(code=1)

    _init_db(verbose)

    try:
        import uvicorn

        typer.echo(f"🚀 Starting server on {host}:{port}")
        typer.echo(f"   PID file: {_PID_FILE}")
        _write_pid_file()
        try:
            uvicorn.run(
                "src.api.server:app",
                host=host,
                port=port,
                reload=False,
                log_level="debug" if verbose else "info",
            )
        finally:
            _remove_pid_file()
    except ImportError:
        typer.echo("❌ uvicorn not installed. Run: uv add uvicorn[standard]", err=True)
        raise typer.Exit(code=1)


@app.command(name="serve-stop")
def serve_stop() -> None:
    """Stop a running server that was started with ``serve``."""
    pid = _read_pid_file()
    if pid is None:
        typer.echo("ℹ️  No running server found (PID file not present).")
        typer.echo(f"   Expected: {_PID_FILE}")
        raise typer.Exit(code=0)

    # Verify the process is actually alive
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        typer.echo(f"ℹ️  Server process (PID {pid}) is not running. Removing stale PID file.")
        _remove_pid_file()
        raise typer.Exit(code=0)

    # Send termination signal
    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(f"✅ Stop signal sent to server (PID {pid})")
        _remove_pid_file()
    except (ProcessLookupError, PermissionError, OSError) as exc:
        typer.echo(f"❌ Failed to stop server (PID {pid}): {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def upgrade(
    check_only: bool = typer.Option(False, "--check", help="Only check for updates, don't install"),
    force: bool = typer.Option(False, "--force", help="Force upgrade even if already on latest version"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Check for and install backend updates from GitHub releases."""
    _setup_logging(verbose)
    
    try:
        if check_only:
            # Only check for updates
            update_info = check_for_updates(verbose=verbose)
            
            current = update_info["current_version"]
            latest = update_info["latest_version"]
            
            if "error" in update_info:
                typer.echo(f"❌ Failed to check for updates: {update_info['error']}", err=True)
                raise typer.Exit(code=1)
            
            typer.echo(f"📋 Current version: {current}")
            typer.echo(f"📋 Latest version:  {latest}")
            
            if update_info["is_newer"]:
                if update_info["asset_available"]:
                    typer.echo("🎯 Update available! Run 'upgrade' without --check to install.")
                else:
                    typer.echo("⚠️  Update available but no compatible asset found.")
                    if "release_url" in update_info:
                        typer.echo(f"   Manual download: {update_info['release_url']}")
            else:
                typer.echo("✅ Already on latest version.")
        else:
            # Perform upgrade
            if upgrade_backend(force=force, verbose=verbose):
                typer.echo("🎉 Upgrade completed successfully!")
                typer.echo("ℹ️  Restart the server if it's currently running.")
            else:
                typer.echo("ℹ️  No upgrade needed.")
                
    except Exception as e:
        typer.echo(f"❌ Upgrade failed: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def version(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed version info"),
) -> None:
    """Display current version information."""
    try:
        from src.services.upgrade import get_current_version
        current = get_current_version()
        
        if verbose:
            from src.services.upgrade import get_platform_info
            os_name, arch_name = get_platform_info()
            typer.echo(f"UniAdmission Agent {current}")
            typer.echo(f"Platform: {os_name}-{arch_name}")
            typer.echo(f"Python: {sys.version}")
            typer.echo(f"Executable: {sys.executable}")
        else:
            typer.echo(current)
    except Exception as e:
        typer.echo(f"❌ Failed to get version: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def help(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed help with examples"),
) -> None:
    """Show comprehensive CLI help and usage information."""
    help_text = get_help_text()
    
    if verbose:
        help_text += "\n\n" + """
DETAILED COMMAND OPTIONS:

crawl:
    --name      University slug (a-z0-9-) 
    --year      Academic year (e.g., 2026)
    --url       Starting URL to crawl
    --continue  Extra depth for LLM scouting (default: 0)
    
import:
    --name      University slug
    --year      Academic year
    --file      Path to XLSX file
    --llm       Enable LLM analysis (optional)
    
export:
    --name      University slug
    --output    Output file path
    --year      Academic year (optional)
    
serve:
    --host      Host address (default: 0.0.0.0)
    --port      Port number (default: 8910)
    --verbose   Debug logging
    
upgrade:
    --check     Only check for updates, don't install
    --force     Force upgrade even if already latest
    --verbose   Show detailed progress
        """
    
    typer.echo(help_text)


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
