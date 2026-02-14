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
import re
import sys
from pathlib import Path
from typing import Optional

import typer

# Ensure project root on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.core.token_tracker import tracker
from src.services.crawler import (
    check_environment,
    crawl_url,
    export_data,
    get_db_status,
    import_file,
)
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="uni-admission",
    help="UniAdmission Agent — Automated university admission data scraper",
    add_completion=False,
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
    _init_db(verbose)

    try:
        import uvicorn

        typer.echo(f"🚀 Starting server on {host}:{port}")
        uvicorn.run(
            "src.api.server:app",
            host=host,
            port=port,
            reload=False,
            log_level="debug" if verbose else "info",
        )
    except ImportError:
        typer.echo("❌ uvicorn not installed. Run: uv add uvicorn[standard]", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
