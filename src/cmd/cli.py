#!/usr/bin/env python3
# pylint: disable=too-many-lines
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
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy_utils import create_database, database_exists, drop_database

# Ensure project root on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Must run before any Playwright import to redirect browser lookup
from src.core.paths import configure_playwright_path, get_data_dir  # noqa: E402

configure_playwright_path()

from src.core.token_tracker import tracker
from src.core.feature_flags import is_agent_enabled_env
from src.services.crawler import (
    check_environment,
    crawl_url,
    export_data,
    get_ingestion_job,
    get_db_status,
    import_file,
    list_ingestion_jobs,
    resume_crawl_job,
    count_programs_by_scope,
    delete_programs_by_scope,
)
from src.services.golden_samples import collect_golden_samples
from src.services.quality_scoring import score_manifest
from src.services.upgrade import check_for_updates, upgrade_backend
from src.services.migrations import (
    MigrationError,
    get_migration_status,
    run_db_migrations,
)
from src.services.repair import RepairError, run_auto_repair
from src.services.subject_taxonomy import (
    bootstrap_subject_taxonomy,
    get_subject_taxonomy_service,
)
from src.core.environment import install_playwright_browser
from src.storage.db_manager import DatabaseManager
from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy import fetch_adapters
from src.services.crawl_strategy.types import CrawlRange
from src.storage.db_portability import DatabaseNotEmptyError, export_database, import_database


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
    crawl        Crawl university admission pages and import data
    crawl-index  Classify an index page and extract program names (deterministic tier)
    import       Import data from Excel files
    export       Export data to Excel format
    
DATABASE & STATUS:
    status     Show database statistics and connection info
    check      Run environment and dependency checks
    ingestion-jobs   Show recent Phase 2 ingestion jobs
    ingestion-resume Resume a failed Phase 2 ingestion job
    golden-collect   Collect Phase 3 golden sample snapshots
    quality-score    Run Phase 3 quality scoring and threshold checks
    taxonomy-export  Export canonical taxonomy names to JSON
    quarantine list  List extractions that failed the quality gate
    quarantine clear Remove quarantine entries for one university (optional reason filter)
    audit list       Inspect index→detail extraction funnel (raw → filtered → extracted)
    audit drill      Show URLs dropped at each filter stage for one audit row
    crawl-summary    One-shot summary of the most recent crawl (for LLM CLI / quick scan)
    diagnostics clear One-shot wipe of quarantine + audit data for one university
    programs delete  Batch-delete program snapshots for one university (preview unless --yes)
    db-export        Export the entire database to one portable zip file
    db-import        Import a database snapshot produced by db-export

LLM CONFIGURATION:
    llm-config Interactive wizard to configure LLM providers
    
    
SERVER OPERATIONS:
    up              Start host + client together (one-command local launcher)
    serve           Start API + MCP server (default: 0.0.0.0:8910)
    serve-install   Start the server as a background daemon
    serve-stop      Stop running server instance
    runtime-status  Show server runtime status including connected clients
    
SYSTEM MAINTENANCE:
    upgrade    Check for and install backend updates
    db-migrate Apply Alembic database migrations
    db-reinit  Drop, recreate, and migrate database (destructive)
    db-version Show Alembic database revision status
    repair     Auto-repair DB migration failures with rollback safety
    version    Show current version information
    browser-install  Install Playwright Chromium browser
    
USAGE EXAMPLES:
    ./adm-agent crawl --name hku --year 2026 --url https://admissions.hku.hk/
    ./adm-agent import --name hku --year 2026 --file data.xlsx --llm
    ./adm-agent export --name hku --output report.xlsx --year 2026
    ./adm-agent serve --port 9000
    ./adm-agent upgrade --check
    ./adm-agent db-version
    ./adm-agent db-migrate --yes
    ./adm-agent db-reinit --yes
    ./adm-agent repair --auto
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


def _find_pid_by_port(port: int) -> "Optional[int]":
    """Find the PID of a process listening on the given port.

    Works on macOS/Linux (via lsof) and Windows (via netstat).
    Returns None if no process is found or the lookup fails.
    """
    import platform

    try:
        if platform.system() == "Windows":
            # Windows: netstat -ano | findstr :PORT
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        return int(parts[-1])
        else:
            # macOS/Linux: lsof -ti:PORT
            result = subprocess.run(
                ["lsof", f"-ti:{port}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                # May return multiple PIDs; take the first
                first_pid = result.stdout.strip().split()[0]
                return int(first_pid)
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return None


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
    # Activate file logging — writes all output to timestamped .txt files
    from src.core.file_logger import setup_file_logging
    setup_file_logging()


def _init_db(verbose: bool = False) -> None:
    """Ensure database is initialised and schema is migrated."""
    try:
        DatabaseManager().init_db()
    except Exception as e:
        if verbose:
            logger.warning("Database auto-init warning: %s", e)
        return

    try:
        status = get_migration_status()
        if status["pending"]:
            logger.info(
                "Pending DB migration detected (%s -> %s), applying...",
                status["current_revision"] or "unversioned",
                status["head_revision"],
            )
            # Surface to the user via stdout — a schema ALTER can take
            # tens of seconds to minutes (especially the first run after
            # an upgrade), and silent blocking reads as a hang.
            typer.echo(
                f"🔧 Applying database migration "
                f"({status['current_revision'] or 'unversioned'} → "
                f"{status['head_revision']})… this can take a minute, please wait."
            )
            run_db_migrations(verbose=verbose)
            typer.echo("✅ Database schema up to date.")
    except MigrationError as e:
        if verbose:
            logger.warning("Database migration warning: %s", e)
    except Exception as e:  # pragma: no cover - defensive logging path
        if verbose:
            logger.warning("Unexpected migration warning: %s", e)

    try:
        bootstrap_subject_taxonomy()
    except Exception as e:
        if verbose:
            logger.warning("Subject taxonomy bootstrap warning: %s", e)


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
    page_type: str = typer.Option(
        "auto", "--page-type", help="Page type: auto, index, or detail"
    ),
    export_md: bool = typer.Option(
        False, "--export-md", help="Export crawled markdown files"
    ),
    export_path: str = typer.Option(
        None, "--export-path", help="Path to export markdown files"
    ),
    browser_provider: str = typer.Option(
        "auto",
        "--browser-provider",
        help="Browser provider: auto, server, or client",
    ),
    client_id: Optional[str] = typer.Option(
        None,
        "--client-id",
        help="Optional target client id when using browser-provider=client",
    ),
    strict_client: bool = typer.Option(
        False,
        "--strict-client",
        help="Fail if client browser automation is unavailable (no server fallback)",
    ),
    candidate_taxonomy_filter_enabled: bool = typer.Option(
        False,
        "--candidate-taxonomy-filter-enabled",
        help="Enable taxonomy scoring filter for index/auto candidate links",
    ),
    candidate_taxonomy_filter_threshold: float = typer.Option(
        0.75,
        "--candidate-taxonomy-filter-threshold",
        help="Minimum taxonomy score to keep candidate links (0~1)",
    ),
    candidate_taxonomy_filter_top_k: int = typer.Option(
        30,
        "--candidate-taxonomy-filter-top-k",
        help="Maximum candidate links retained after taxonomy filter",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="只爬取 index 页发现的前 N 门课程（含详情入库）。"),
    crawl_all: bool = typer.Option(
        False, "--all", help="爬取发现的全部课程（有安全上限）。"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Crawl a URL and import admission data."""
    _setup_logging(verbose)
    name = _validate_slug(name)
    year = _validate_year(year)

    if crawl_all and limit is not None:
        typer.echo("Error: --limit 和 --all 互斥，只能选一个。", err=True)
        raise typer.Exit(code=1)

    _init_db(verbose)

    if export_md and not export_path:
        typer.echo("Error: --export-path is required when --export-md is enabled", err=True)
        raise typer.Exit(code=1)
    
    if page_type not in ["auto", "index", "detail"]:
        typer.echo(f"Error: --page-type must be one of: auto, index, detail", err=True)
        raise typer.Exit(code=1)
    
    if browser_provider not in ["auto", "server", "client"]:
        typer.echo(
            "Error: --browser-provider must be one of: auto, server, client",
            err=True,
        )
        raise typer.Exit(code=1)
    if candidate_taxonomy_filter_threshold < 0 or candidate_taxonomy_filter_threshold > 1:
        typer.echo(
            "Error: --candidate-taxonomy-filter-threshold must be between 0 and 1",
            err=True,
        )
        raise typer.Exit(code=1)
    if candidate_taxonomy_filter_top_k < 1:
        typer.echo(
            "Error: --candidate-taxonomy-filter-top-k must be >= 1",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Crawling: {url}  (univ={name}, year={year}, depth={continue_depth}, type={page_type})")
    if export_md:
        typer.echo(f"  Export MD: enabled → {export_path}")
    
    try:
        result = asyncio.run(
            crawl_url(
                url=url, 
                univ_slug=name, 
                year=year, 
                continue_depth=continue_depth,
                page_type_hint=page_type,
                export_md=export_md,
                export_path=export_path,
                html_content=None,  # CLI doesn't provide pre-rendered HTML
                browser_provider=browser_provider,
                client_id=client_id,
                strict_client=strict_client,
                candidate_taxonomy_filter_enabled=candidate_taxonomy_filter_enabled,
                candidate_taxonomy_filter_threshold=candidate_taxonomy_filter_threshold,
                candidate_taxonomy_filter_top_k=candidate_taxonomy_filter_top_k,
                limit=limit,
                crawl_all=crawl_all,
            )
        )
        typer.echo(f"✅ Crawl complete: {result.imported_count} programs imported")
        tracker.log_summary()
    except Exception as e:
        logger.exception("Crawl failed: %s", e)
        raise typer.Exit(code=1)


def _resolve_crawl_range(*, limit: Optional[int], all_: bool) -> CrawlRange:
    """Map CLI --limit/--all to a CrawlRange. Mutually exclusive."""
    if all_ and limit is not None:
        raise ValueError("--limit and --all are mutually exclusive")
    if all_:
        return CrawlRange.all_()
    if limit is not None:
        return CrawlRange.of(limit)
    return CrawlRange.default()


@app.command(name="crawl-index")
def crawl_index_cmd(
    index_url: str = typer.Argument(..., help="University programme index URL"),
    names_only: bool = typer.Option(True, "--names-only/--with-details",
                                    help="Names only (default) or also crawl details"),
    report_out: Optional[str] = typer.Option(None, "--report-out",
                                             help="Directory for phenomenon report zips"),
    as_json: bool = typer.Option(False, "--json", help="Print outcome as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="抓取前 N 门课程名字（翻页直到 N）。"),
    all_: bool = typer.Option(
        False, "--all", help="抓取全部（翻页到底，有安全上限）。"),
) -> None:
    """Classify an index page and crawl program names (deterministic tier)."""
    _setup_logging(verbose)
    import dataclasses
    import json as _json
    from datetime import datetime, timezone

    del names_only  # detail crawl is a future plan; names-only for now
    out_dir = report_out or str(get_data_dir() / "reports")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    crawl_range = _resolve_crawl_range(limit=limit, all_=all_)
    outcome = crawl_index(
        index_url,
        crawl_range=crawl_range,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        api_fetch=fetch_adapters.api_fetch,
        report_out=out_dir, timestamp=timestamp,
    )
    if as_json:
        payload = dataclasses.asdict(outcome)
        payload.pop("items", None)
        typer.echo(_json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(outcome.message_for_user)
        for name in outcome.names:
            typer.echo(f"  - {name}")


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
@app.command(name="runtime-status")
def runtime_status(
    host: str = typer.Option("127.0.0.1", help="Server host"),
    port: int = typer.Option(8910, help="Server port"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show server runtime status (requires running server)."""
    import requests

    url = f"http://{host}:{port}/status"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()

        typer.echo(f"\n🚀 Server Runtime Status ({host}:{port})")
        typer.echo(f"  Connected Clients: {data.get('client_count', 0)}")
        if data.get("client_ids"):
            typer.echo(f"  Active Client IDs: {', '.join(data['client_ids'])}")
        
        typer.echo(f"\n📊 Database Stats:")
        typer.echo(f"  Universities: {data.get('university_count', 0)}")
        typer.echo(f"  Programs:     {data.get('program_count', 0)}")
        
    except requests.exceptions.ConnectionError:
        typer.echo(f"❌ Could not connect to server at {url}. Is the server running?", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"❌ Failed to get runtime status: {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="ingestion-jobs")
def ingestion_jobs_cmd(
    limit: int = typer.Option(20, help="Number of recent jobs to show"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List recent Phase 2 ingestion jobs."""
    _setup_logging(verbose)
    _init_db(verbose)

    try:
        rows = list_ingestion_jobs(limit=limit)
        if not rows:
            typer.echo("No ingestion jobs found.")
            return
        for row in rows:
            typer.echo(
                f"{row['job_uid']}  {row['status']:<10}  "
                f"{row['univ_slug']}/{row['academic_year']}  "
                f"{row.get('current_stage') or '-'}"
            )
    except Exception as e:
        logger.error("Failed to list ingestion jobs: %s", e)
        raise typer.Exit(code=1)


@app.command(name="ingestion-resume")
def ingestion_resume_cmd(
    job_id: str = typer.Option(..., "--job", help="Ingestion job UID"),
    stage: Optional[str] = typer.Option(
        None,
        "--stage",
        help="Optional stage override (fetch_raw/extract_structured/validate_rules/persist_versioned)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Resume a failed/poisoned Phase 2 ingestion job."""
    _setup_logging(verbose)
    _init_db(verbose)

    job = get_ingestion_job(job_id)
    if not job:
        typer.echo(f"❌ Job not found: {job_id}", err=True)
        raise typer.Exit(code=1)

    try:
        result = asyncio.run(
            resume_crawl_job(
                job_uid=job_id,
                resume_from_stage=stage,
            )
        )
        typer.echo(
            f"✅ Resume complete: {result.imported_count} programs imported "
            f"(job={result.ingestion_job_id})"
        )
    except Exception as e:
        logger.exception("Resume failed: %s", e)
        raise typer.Exit(code=1)


@app.command(name="golden-collect")
def golden_collect_cmd(
    manifest: str = typer.Option(
        "golden_samples/manifest.json",
        help="Path to golden manifest JSON",
    ),
    output_root: str = typer.Option(
        "golden_samples/cases",
        help="Directory to store collected snapshots",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing snapshot files",
    ),
    timeout: int = typer.Option(40, help="Network timeout in seconds"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Collect Phase 3 golden sample HTML/Markdown snapshots."""
    _setup_logging(verbose)
    try:
        result = collect_golden_samples(
            manifest_path=manifest,
            output_root=output_root,
            overwrite=overwrite,
            timeout_seconds=timeout,
        )
        typer.echo(
            f"✅ Golden collect done: collected={result['collected']} failed={result['failures']}"
        )
    except Exception as e:
        logger.exception("Golden collection failed: %s", e)
        raise typer.Exit(code=1)


@app.command(name="quality-score")
def quality_score_cmd(
    manifest: str = typer.Option(
        "golden_samples/manifest.json",
        help="Path to golden manifest JSON",
    ),
    base_dir: str = typer.Option(
        "golden_samples/cases",
        help="Directory containing golden sample snapshots",
    ),
    report: str = typer.Option(
        "golden_samples/reports/quality_report.json",
        help="Output report JSON path",
    ),
    threshold: float = typer.Option(0.60, help="Global mean score threshold"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run Phase 3 quality scoring and apply threshold gates."""
    _setup_logging(verbose)
    try:
        result = score_manifest(
            manifest_path=manifest,
            base_dir=base_dir,
            output_report_path=report,
            global_threshold=threshold,
        )
        aggregate = result.get("aggregate") or {}
        typer.echo(
            "Quality score "
            f"mean={aggregate.get('mean_score')} "
            f"pass={aggregate.get('passed_case_count')}/{aggregate.get('case_count')}"
        )
        typer.echo(f"Report: {report}")
        if not result.get("global_pass", False):
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("Quality scoring failed: %s", e)
        raise typer.Exit(code=1)


@app.command(name="taxonomy-export")
def taxonomy_export_cmd(
    output: str = typer.Option(
        "golden_samples/program_names/cleaned_programs_names.json",
        "--output",
        help="Output taxonomy JSON path",
    ),
    include_learned: bool = typer.Option(
        False,
        "--include-learned",
        help="Include learned names in addition to seed names",
    ),
    min_confidence: float = typer.Option(
        0.9,
        "--min-confidence",
        help="Minimum confidence for learned names",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export current subject taxonomy to JSON."""
    _setup_logging(verbose)
    _init_db(verbose)
    try:
        service = get_subject_taxonomy_service()
        result = service.export_to_json(
            output_path=output,
            include_learned=include_learned,
            min_confidence=min_confidence,
        )
        typer.echo(
            f"✅ Taxonomy export complete: {result['exported_count']} names → {result['path']}"
        )
    except Exception as e:
        logger.exception("Taxonomy export failed: %s", e)
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8910, help="Port number"),
    agent: bool = typer.Option(
        False,
        "--agent",
        help="Force-enable agent runtime for this server process (default: enabled)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate options and print mode without starting the server",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the FastAPI + MCP server."""
    _setup_logging(verbose)

    if agent:
        os.environ["AGENT_ENABLED"] = "true"

    agent_enabled = is_agent_enabled_env()
    typer.echo(f"Agent enabled: {agent_enabled}")

    if dry_run:
        typer.echo("Dry run mode: skipping browser/db checks and server startup.")
        return

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
        # Display URL the user can click. When the server binds 0.0.0.0,
        # the human-meaningful address is 127.0.0.1 — don't put
        # "http://0.0.0.0/" in front of users.
        display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        typer.echo(f"   🌐 Web UI:  http://{display_host}:{port}/ui/")
        typer.echo(f"   📚 API docs: http://{display_host}:{port}/docs")
        typer.echo(f"   🩺 Health:   http://{display_host}:{port}/health")
        typer.echo(f"   PID file: {_PID_FILE}")
        _write_pid_file()
        try:
            uvicorn.run(
                "src.api.server:app",
                host=host,
                port=port,
                reload=False,
                log_level="debug" if verbose else "info",
                proxy_headers=True,
                forwarded_allow_ips="*",
            )
        finally:
            _remove_pid_file()
    except ImportError:
        typer.echo("❌ uvicorn not installed. Run: uv add uvicorn[standard]", err=True)
        raise typer.Exit(code=1)


def _build_base_cmd() -> list[str]:
    """Return the base argv to re-invoke this CLI (handles PyInstaller too)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, sys.argv[0]]


_LOG_FILE = _PID_DIR / "server.log"


@app.command(name="serve-install")
def serve_install(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8910, help="Port number"),
    agent: bool = typer.Option(
        False,
        "--agent",
        help="Force-enable agent runtime for this server process (default: enabled)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the server as a background daemon.

    Launches ``serve`` in a detached process so it persists after the
    terminal is closed.  Use ``serve-stop`` to terminate it.
    """
    # Refuse if server is already running
    existing_pid = _read_pid_file()
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)
            typer.echo(
                f"⚠️  Server already running (PID {existing_pid}). "
                "Stop it first with: serve-stop"
            )
            raise typer.Exit(code=1)
        except (ProcessLookupError, OSError):
            _remove_pid_file()

    cmd = _build_base_cmd() + ["serve", "--host", host, "--port", str(port)]
    if agent:
        cmd.append("--agent")
    if verbose:
        cmd.append("--verbose")

    _PID_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(_LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )

    typer.echo(f"🚀 Server daemon started (PID {proc.pid})")
    typer.echo(f"   Log: {_LOG_FILE}")
    typer.echo("   Stop: adm-agent serve-stop")


@app.command(name="serve-stop")
def serve_stop(
    port: int = typer.Option(8910, help="Port to check if PID file is missing"),
    force: bool = typer.Option(False, "--force", "-f", help="Force kill (SIGKILL) if SIGTERM fails"),
) -> None:
    """Stop a running server that was started with ``serve``.

    First checks the PID file. If the PID file is missing or stale,
    falls back to searching for a process listening on the given port.
    Use --force to send SIGKILL instead of SIGTERM.
    """
    pid = _read_pid_file()
    source = "PID file"

    # Fallback: find by port if PID file is missing
    if pid is None:
        pid = _find_pid_by_port(port)
        source = f"port {port}"
        if pid is None:
            typer.echo("ℹ️  No running server found.")
            typer.echo(f"   Checked: {_PID_FILE} (not present)")
            typer.echo(f"   Checked: port {port} (no process listening)")
            raise typer.Exit(code=0)

    # Verify the process is actually alive
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        typer.echo(f"ℹ️  Server process (PID {pid}) is not running. Removing stale PID file.")
        _remove_pid_file()
        raise typer.Exit(code=0)

    # Send termination signal
    sig = signal.SIGKILL if force else signal.SIGTERM
    sig_name = "SIGKILL" if force else "SIGTERM"
    try:
        os.kill(pid, sig)
        typer.echo(f"✅ {sig_name} sent to server (PID {pid}, found via {source})")
        _remove_pid_file()
    except (ProcessLookupError, PermissionError, OSError) as exc:
        typer.echo(f"❌ Failed to stop server (PID {pid}): {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
#  `up` — single-machine launcher (host + client together)
# ---------------------------------------------------------------------------


def _find_client_argv() -> list[str]:
    """Return argv prefix to invoke the client CLI.

    Handles PyInstaller frozen builds (sibling binary) and dev mode
    (re-invoke Python on client_cli.py).
    """
    if getattr(sys, "frozen", False):
        exe_name = "adm-agent-client.exe" if os.name == "nt" else "adm-agent-client"
        exe_dir = Path(sys.executable).parent
        candidates = [
            exe_dir / exe_name,
            exe_dir.parent / "adm-agent-client" / exe_name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return [str(candidate)]
        raise FileNotFoundError(
            "Could not locate adm-agent-client binary. Looked at: "
            + ", ".join(str(c) for c in candidates)
        )
    client_script = Path(__file__).parent / "client_cli.py"
    return [sys.executable, str(client_script)]


def _wait_for_health(url: str, timeout: float) -> bool:
    """Poll the server's /health endpoint until it responds or timeout."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _ensure_client_initialized(server_url: str) -> None:
    """If client config is missing, create a default one pointing at server_url."""
    from src.client.config import (
        ClientConfig,
        ClientPolicyProfile,
        ensure_client_id,
        load_client_config,
        save_client_config,
    )

    if load_client_config() is not None:
        return

    import platform as _platform

    name = _platform.node().strip() or "adm-agent-client"
    config = ClientConfig(
        server_url=server_url,
        client_name=name,
        client_id=ensure_client_id(None),
        workdir=str(Path.cwd()),
        policy_profile=ClientPolicyProfile(),
    )
    path = save_client_config(config)
    typer.echo(f"   Auto-initialized client config: {path}")
    typer.echo(f"   Client ID: {config.client_id}")


def _stream_child_output(proc: subprocess.Popen, prefix: str) -> None:
    """Read child stdout line-by-line and echo with prefix. Blocks until EOF."""
    if proc.stdout is None:
        return
    for raw in proc.stdout:
        line = raw.rstrip()
        if line:
            typer.echo(f"{prefix} {line}")


@app.command()
def up(
    host: str = typer.Option(
        "127.0.0.1",
        help="Bind address for the server (default: 127.0.0.1 for single-machine use)",
    ),
    port: int = typer.Option(8910, help="Port number for the server"),
    health_timeout: float = typer.Option(
        20.0, help="Seconds to wait for server /health before giving up"
    ),
    skip_client: bool = typer.Option(
        False, "--skip-client", help="Start only the server (do not launch client)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start host (serve) and client together — one-command launcher for local use.

    Spawns ``adm-agent serve`` and ``adm-agent-client start --continuous`` as
    managed subprocesses, waits for the server's /health endpoint, then streams
    both logs with [host]/[client] prefixes. Ctrl+C terminates both cleanly.
    """
    import threading

    _setup_logging(verbose)

    # Pre-flight: browser
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser_path = Path(p.chromium.executable_path)
            if not browser_path.exists():
                raise FileNotFoundError(f"Browser not found at: {browser_path}")
            typer.echo(f"✅ Browser ready: {browser_path}")
    except Exception as exc:  # pylint: disable=broad-except
        typer.echo("❌ Playwright browser not available.", err=True)
        typer.echo(f"   {exc}", err=True)
        typer.echo("👉 Run: adm-agent browser-install", err=True)
        raise typer.Exit(code=1)

    # Pre-flight: DB
    _init_db(verbose)

    # Build server URL (use 127.0.0.1 for client even if host bound to 0.0.0.0)
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    server_url = f"http://{probe_host}:{port}"
    health_url = f"{server_url}/health"
    ws_url = f"ws://{probe_host}:{port}"

    # Ensure client config exists
    if not skip_client:
        _ensure_client_initialized(server_url)

    # Spawn server
    server_cmd = _build_base_cmd() + ["serve", "--host", host, "--port", str(port)]
    if verbose:
        server_cmd.append("--verbose")

    typer.echo(f"🚀 Starting server: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(
        server_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    host_thread = threading.Thread(
        target=_stream_child_output, args=(server_proc, "[host]  "), daemon=True
    )
    host_thread.start()

    typer.echo(f"⏳ Waiting for {health_url} ...")
    if not _wait_for_health(health_url, health_timeout):
        typer.echo("❌ Server failed to become healthy in time.", err=True)
        _terminate_children([server_proc])
        raise typer.Exit(code=1)
    typer.echo("✅ Server is healthy.")
    typer.echo(f"   🌐 Web UI:  {server_url}/ui/")
    typer.echo(f"   📚 API docs: {server_url}/docs")

    client_proc: subprocess.Popen | None = None
    client_thread: threading.Thread | None = None

    if not skip_client:
        try:
            client_argv = _find_client_argv()
        except FileNotFoundError as exc:
            typer.echo(f"❌ {exc}", err=True)
            _terminate_children([server_proc])
            raise typer.Exit(code=1)

        client_cmd = client_argv + ["start", "--continuous"]
        typer.echo(f"🚀 Starting client: {' '.join(client_cmd)}")
        client_proc = subprocess.Popen(
            client_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        client_thread = threading.Thread(
            target=_stream_child_output, args=(client_proc, "[client]"), daemon=True
        )
        client_thread.start()
        typer.echo(f"📡 Client connecting to {ws_url}/clients/ws")

    children = [server_proc] + ([client_proc] if client_proc else [])
    stop_requested = threading.Event()

    def _handle_signal(signum, _frame):  # noqa: ARG001
        if not stop_requested.is_set():
            typer.echo(f"\n🛑 Received signal {signum}, shutting down...")
            stop_requested.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Wait loop: exit when any child dies or a stop is requested
    try:
        while not stop_requested.is_set():
            for proc in children:
                if proc.poll() is not None:
                    name = "server" if proc is server_proc else "client"
                    typer.echo(
                        f"⚠️  {name} exited unexpectedly (code {proc.returncode})",
                        err=True,
                    )
                    stop_requested.set()
                    break
            time.sleep(0.5)
    finally:
        _terminate_children(children)

    typer.echo("👋 Shutdown complete.")


def _terminate_children(children: list[subprocess.Popen]) -> None:
    """Send SIGTERM to children, wait 3s, escalate to SIGKILL if still alive."""
    for proc in children:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in children):
            return
        time.sleep(0.1)

    for proc in children:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


@app.command()
def db_version(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Show Alembic migration revision status."""
    _setup_logging(verbose)
    try:
        status = get_migration_status()
        typer.echo(f"📦 Current DB revision: {status['current_revision'] or 'unversioned'}")
        typer.echo(f"📦 Target DB revision:  {status['head_revision']}")
        if status["pending"]:
            typer.echo("⚠️  Migrations pending. Run: adm-agent db-migrate")
        else:
            typer.echo("✅ Database schema is up to date.")
    except Exception as e:
        typer.echo(f"❌ Failed to read migration status: {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="db-migrate")
def db_migrate(
    revision: str = typer.Option("head", "--revision", help="Target alembic revision"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Apply Alembic migrations to the configured database."""
    _setup_logging(verbose)

    if not yes:
        confirm = typer.confirm(
            f"Apply database migration to revision '{revision}'?",
            default=True,
        )
        if not confirm:
            typer.echo("ℹ️  Migration cancelled.")
            raise typer.Exit(code=0)

    try:
        result = run_db_migrations(revision=revision, verbose=verbose)
        typer.echo(f"📦 Before revision: {result['before_revision'] or 'unversioned'}")
        typer.echo(f"📦 After revision:  {result['after_revision'] or 'unversioned'}")
        if result["legacy_bootstrap"]:
            typer.echo("ℹ️  Legacy schema detected and stamped before migration.")
        if result["pending"]:
            typer.echo("⚠️  Database not fully up to head. Re-run db-migrate.")
            raise typer.Exit(code=1)
        typer.echo("✅ Database migration completed.")
    except Exception as e:
        typer.echo(f"❌ Database migration failed: {e}", err=True)
        typer.echo("👉 Run 'adm-agent repair --auto' to rollback and recover automatically.", err=True)
        raise typer.Exit(code=1)


@app.command(name="db-reinit")
def db_reinit(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Drop and recreate the configured database, then migrate to head."""
    _setup_logging(verbose)

    # DatabaseManager.init_db() (the path every other command uses) falls
    # back to a default SQLite URL when DATABASE_URL is unset — SQLite is
    # the documented zero-config default (see uni-admission-install SKILL.md
    # §1.6). This command used to hard-fail instead of matching that
    # fallback, so it was unusable for exactly the setup most users have.
    db_url = os.getenv("DATABASE_URL") or DatabaseManager._default_sqlite_url()

    if not yes:
        confirm = typer.confirm(
            "This will permanently delete all existing DB data. Continue?",
            default=False,
        )
        if not confirm:
            typer.echo("ℹ️  Database reinitialization cancelled.")
            raise typer.Exit(code=0)

    try:
        if database_exists(db_url):
            typer.echo("🗑️  Dropping existing database...")
            drop_database(db_url)
        typer.echo("🧱 Creating fresh database...")
        create_database(db_url)
        result = run_db_migrations(db_url=db_url, revision="head", verbose=verbose)
        typer.echo(f"📦 Before revision: {result['before_revision'] or 'unversioned'}")
        typer.echo(f"📦 After revision:  {result['after_revision'] or 'unversioned'}")
        if result["pending"]:
            typer.echo("⚠️  Database not fully up to head after reinit.")
            raise typer.Exit(code=1)
        typer.echo("✅ Database reinitialization completed.")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"❌ Database reinitialization failed: {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="db-export")
def db_export(
    output: str = typer.Option(..., "--output", help="Output zip file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Export the entire database (all tables) to one portable zip file."""
    _setup_logging(verbose)
    _init_db(verbose)

    typer.echo(f"Exporting database → {output}")
    try:
        row_counts = export_database(output)
    except Exception as e:
        typer.echo(f"❌ Database export failed: {e}", err=True)
        raise typer.Exit(code=1)

    total = sum(row_counts.values())
    typer.echo(f"✅ Exported {total} rows across {len(row_counts)} tables → {output}")


@app.command(name="db-import")
def db_import(
    file: str = typer.Option(..., "--file", help="Zip file produced by db-export"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    force: bool = typer.Option(
        False, "--force", help="Proceed even if the target database is not empty"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Import a database snapshot produced by db-export.

    Assumes the target database is empty — refuses otherwise unless
    --force is passed (which skips the check, not a merge: a real
    conflict still surfaces as a constraint-violation error). Runs
    pending migrations to head before writing any data.
    """
    _setup_logging(verbose)
    # Skip _init_db()'s taxonomy auto-seed — it would falsify the "target is
    # empty" check below. import_database() migrates the schema itself.
    DatabaseManager().init_db()

    if not yes:
        confirm = typer.confirm(
            f"This will import data from {file!r} into the currently "
            "configured database. Continue?",
            default=False,
        )
        if not confirm:
            typer.echo("ℹ️  Database import cancelled.")
            raise typer.Exit(code=0)

    try:
        row_counts = import_database(file, force=force)
    except DatabaseNotEmptyError as e:
        typer.echo(f"❌ {e}", err=True)
        typer.echo("👉 Re-run with --force to proceed anyway.", err=True)
        raise typer.Exit(code=1)
    except MigrationError as e:
        typer.echo(f"❌ Database migration failed: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"❌ Database import failed: {e}", err=True)
        raise typer.Exit(code=1)

    total = sum(row_counts.values())
    typer.echo(f"✅ Imported {total} rows across {len(row_counts)} tables from {file}")


@app.command()
def repair(
    auto: bool = typer.Option(False, "--auto", help="Run automatic repair workflow"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Repair database state automatically with migration rollback safety."""
    _setup_logging(verbose)

    if not auto:
        typer.echo("Use --auto to run non-interactive repair.")
        raise typer.Exit(code=1)

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        typer.echo("❌ DATABASE_URL is not configured.", err=True)
        raise typer.Exit(code=1)

    try:
        result = run_auto_repair(db_url=db_url, verbose=verbose)
        typer.echo("✅ Repair completed.")
        if result["migration_result"]["pending"]:
            typer.echo("⚠️  Schema still not at head; retry repair or contact support.")
            raise typer.Exit(code=1)
        if not result["health_after"]["ok"]:
            typer.echo("⚠️  Database is reachable but missing core tables after repair.", err=True)
            raise typer.Exit(code=1)
        typer.echo("ℹ️  Database is healthy and migration-compatible.")
    except RepairError as e:
        typer.echo("❌ Automatic repair failed.", err=True)
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"❌ Unexpected repair failure: {e}", err=True)
        raise typer.Exit(code=1)


def _print_upgrade_check(update_info: dict) -> None:
    if "error" in update_info:
        typer.echo(f"❌ Failed to check for updates: {update_info['error']}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"📋 Current version: {update_info['current_version']}")
    typer.echo(f"📋 Latest version:  {update_info['latest_version']}")

    if not update_info.get("is_newer"):
        typer.echo("✅ Already on latest version.")
        return
    if update_info.get("asset_available"):
        typer.echo("🎯 Update available! Run 'upgrade' without --check to install.")
        return

    typer.echo("⚠️  Update available but no compatible asset found.")
    release_url = update_info.get("release_url")
    if release_url:
        typer.echo(f"   Manual download: {release_url}")


def _run_cli_subcommand(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _print_subprocess_logs(proc: subprocess.CompletedProcess[str], verbose: bool) -> None:
    if not verbose:
        return
    if proc.stdout:
        typer.echo(proc.stdout.strip())
    if proc.stderr:
        typer.echo(proc.stderr.strip(), err=True)


def _run_migration_after_upgrade(verbose: bool) -> None:
    typer.echo("🔄 Running database migration...")
    migrate_proc = _run_cli_subcommand(["db-migrate", "--yes"])
    if migrate_proc.returncode == 0:
        typer.echo("✅ Database migration completed.")
        return

    typer.echo("⚠️  Upgrade succeeded but DB migration failed.", err=True)
    _print_subprocess_logs(migrate_proc, verbose)
    typer.echo("🛠 Attempting automatic rollback repair...")

    repair_proc = _run_cli_subcommand(["repair", "--auto"])
    if repair_proc.returncode == 0:
        typer.echo("✅ Auto-repair succeeded. Data restored to a safe state.")
        return

    typer.echo("❌ Auto-repair failed. Please run: adm-agent repair --auto", err=True)
    _print_subprocess_logs(repair_proc, verbose)
    raise typer.Exit(code=1)


def _perform_upgrade(force: bool, migrate: bool, verbose: bool) -> None:
    if not upgrade_backend(force=force, verbose=verbose):
        typer.echo("ℹ️  No upgrade needed.")
        return

    typer.echo("🎉 Upgrade completed successfully!")
    if migrate:
        _run_migration_after_upgrade(verbose)
    typer.echo("ℹ️  Restart the server if it's currently running.")


@app.command()
def upgrade(
    check_only: bool = typer.Option(False, "--check", help="Only check for updates, don't install"),
    force: bool = typer.Option(False, "--force", help="Force upgrade even if already on latest version"),
    migrate: bool = typer.Option(True, "--migrate/--no-migrate", help="Run DB migration after backend upgrade"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Check for and install backend updates from GitHub releases."""
    _setup_logging(verbose)

    try:
        if check_only:
            _print_upgrade_check(check_for_updates(verbose=verbose))
            return
        _perform_upgrade(force=force, migrate=migrate, verbose=verbose)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"❌ Upgrade failed: {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="llm-config")
def llm_config() -> None:
    """Interactive LLM provider configuration wizard."""
    from dotenv import dotenv_values
    from pathlib import Path
    
    typer.echo("╔══════════════════════════════════════════╗")
    typer.echo("║   LLM Provider Configuration Wizard     ║")
    typer.echo("╚══════════════════════════════════════════╝\n")
    
    # Step 1: Choose provider
    typer.echo("Available LLM providers:")
    typer.echo("  1. DeepSeek")
    typer.echo("  2. Gemini")
    typer.echo("  3. Volcengine (Doubao)")
    typer.echo("  4. Custom (OpenAI-compatible API)\n")
    
    choice = typer.prompt("Select a provider (1-4)", type=int)
    
    if choice not in [1, 2, 3, 4]:
        typer.echo("❌ Invalid choice", err=True)
        raise typer.Exit(code=1)
    
    provider_map = {
        1: ("deepseek", "DeepSeek"),
        2: ("gemini", "Gemini"),
        3: ("volcengine", "Volcengine"),
        4: ("custom", "Custom"),
    }
    
    provider_key, provider_name = provider_map[choice]
    
    typer.echo(f"\n📝 Configuring {provider_name}...\n")
    
    # Step 2: Collect provider-specific parameters
    config_updates = {}
    
    if provider_key == "deepseek":
        api_key = typer.prompt("DeepSeek API Key")
        model = typer.prompt("Model name", default="deepseek-chat")
        base_url = typer.prompt("Base URL", default="https://api.deepseek.com")
        
        config_updates["DEEPSEEK_API_KEY"] = api_key
        config_updates["DEEPSEEK_MODEL_NAME"] = model
        config_updates["DEEPSEEK_BASE_URL"] = base_url
        
    elif provider_key == "gemini":
        api_key = typer.prompt("Google Gemini API Key")
        model = typer.prompt("Model name", default="gemini-2.0-flash-exp")
        
        config_updates["GEMINI_API_KEY"] = api_key
        config_updates["GEMINI_MODEL"] = model
        
    elif provider_key == "volcengine":
        api_key = typer.prompt("Volcengine API Key")
        model_id = typer.prompt("Model ID (endpoint ID)")
        base_url = typer.prompt("Base URL", default="https://ark.cn-beijing.volces.com/api/v3")
        region = typer.prompt("Region", default="cn-beijing")
        
        config_updates["VOLC_API_KEY"] = api_key
        config_updates["VOLC_MODEL_ID"] = model_id
        config_updates["VOLC_BASE_URL"] = base_url
        config_updates["VOLC_REGION"] = region
        
    elif provider_key == "custom":
        base_url = typer.prompt("Base URL (e.g., https://api.openai.com/v1)")
        api_key = typer.prompt("API Key (leave empty if not required)", default="")
        model_name = typer.prompt("Model name", default="gpt-4o-mini")
        
        config_updates["CUSTOM_LLM_BASE_URL"] = base_url
        config_updates["CUSTOM_LLM_API_KEY"] = api_key
        config_updates["CUSTOM_LLM_MODEL_NAME"] = model_name
    
    # Step 3: Update .env file
    typer.echo("\n💾 Saving configuration to .env...")
    
    try:
        from dotenv import find_dotenv
        env_path = Path(find_dotenv() or ".env")
        
        # Read existing content
        if env_path.exists():
            existing = dotenv_values(env_path)
            lines = env_path.read_text(encoding="utf-8").splitlines()
        else:
            existing = {}
            lines = []
        
        # Update config values
        updated_keys = set()
        new_lines = []
        
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in config_updates:
                    new_lines.append(f"{key}={config_updates[key]}")
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # Add new keys
        for key, value in config_updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}")
        
        # Step 4: Update LLM_PRIORITY_LIST (put new provider first)
        current_priority = existing.get("LLM_PRIORITY_LIST", "deepseek,gemini")
        priority_list = [p.strip() for p in current_priority.split(",") if p.strip()]
        
        # Remove provider if already in list
        if provider_key in priority_list:
            priority_list.remove(provider_key)
        
        # Add to front
        priority_list.insert(0, provider_key)
        new_priority = ", ".join(priority_list)
        
        # Update priority in lines
        priority_updated = False
        for i, line in enumerate(new_lines):
            if line.strip().startswith("LLM_PRIORITY_LIST="):
                new_lines[i] = f"LLM_PRIORITY_LIST={new_priority}"
                priority_updated = True
                break
        
        if not priority_updated:
            new_lines.append(f"LLM_PRIORITY_LIST={new_priority}")
        
        # Write back
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        
        typer.echo(f"✅ Configuration saved successfully!")
        typer.echo(f"✅ {provider_name} set as highest priority")
        typer.echo(f"\nLLM Priority Order: {new_priority}")
        typer.echo("\n💡 Tip: Restart the server for changes to take effect.")
        
    except Exception as e:
        typer.echo(f"❌ Failed to save configuration: {e}", err=True)
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
#  Programs subcommands
# ---------------------------------------------------------------------------

programs_app = typer.Typer(
    name="programs",
    help="Manage stored program records.",
    add_completion=False,
)
app.add_typer(programs_app)


@programs_app.command(name="delete")
def programs_delete(
    university: str = typer.Option(
        ..., "--university", "-u",
        help="University slug to delete programs for (required).",
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y", help="Academic year filter.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip the preview and execute the delete.",
    ),
) -> None:
    """Batch-delete program snapshots for a university, optionally scoped to one year.

    Without --yes, only previews the affected count — no data is deleted.
    Deletes each matching program and its child rows (requirements, deadlines,
    study options, requirement versions), collapsing any program catalog left
    with zero remaining programs. University/quarantine/audit records are
    never touched by this command.
    """
    if not yes:
        scope = count_programs_by_scope(university, year)
        if not scope.count:
            suffix = f" in {year}" if year is not None else ""
            typer.echo(f"No programs found for {university!r}{suffix}.")
            return
        typer.echo(
            f"⚠️  This will delete {scope.count} programs across years {scope.years} "
            f"for {university!r}. Re-run with --yes to confirm."
        )
        return

    scope = delete_programs_by_scope(university, year)
    typer.echo(f"✅ Deleted {scope.count} programs for {university!r}.")


# ---------------------------------------------------------------------------
#  Quarantine subcommands
# ---------------------------------------------------------------------------

quarantine_app = typer.Typer(
    name="quarantine",
    help="Inspect extraction results that failed the quality gate.",
    add_completion=False,
)
app.add_typer(quarantine_app)


@quarantine_app.command(name="list")
def quarantine_list(
    university: Optional[str] = typer.Option(
        None, "--university", "-u", help="University slug filter"
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y", help="Academic year filter"
    ),
) -> None:
    """List quarantined program extractions.

    Quarantine entries are extracted programs that failed the quality
    gate (empty shells, noise names, missing identifying content) and
    therefore did NOT make it into the main `program` table.
    """
    db_manager = DatabaseManager()
    entries = db_manager.list_quarantine(university_slug=university, year=year)
    if not entries:
        typer.echo("No quarantine entries.")
        return

    for entry in entries:
        typer.echo(
            f"[{entry.id}] {entry.university_slug} {entry.academic_year} "
            f"reason={entry.quarantine_reason} "
            f"name={entry.extracted_name!r} "
            f"url={entry.source_url}"
        )


@quarantine_app.command(name="clear")
def quarantine_clear(
    university: str = typer.Option(
        ..., "--university", "-u",
        help="University slug to clear quarantine for (required).",
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r",
        help="Optional reason filter (empty_name, name_too_short, noise_name, empty_shell).",
    ),
) -> None:
    """Delete quarantine entries for one university.

    Without ``--reason``, deletes all entries for the given university.
    With ``--reason``, deletes only matching entries. The ``--university``
    flag is required — there is intentionally no way to nuke the whole
    quarantine table from the CLI.
    """
    from src.services.quality_gate import QuarantineReason

    reason_enum = None
    if reason is not None:
        try:
            reason_enum = QuarantineReason(reason)
        except ValueError:
            valid = ", ".join(r.value for r in QuarantineReason)
            typer.echo(
                f"❌ Invalid reason {reason!r}. Valid values: {valid}",
                err=True,
            )
            raise typer.Exit(code=1)

    db_manager = DatabaseManager()
    deleted = db_manager.clear_quarantine(
        university_slug=university, reason=reason_enum
    )
    suffix = f" (reason={reason_enum.value})" if reason_enum else ""
    typer.echo(f"Deleted {deleted} quarantine entries for {university}{suffix}.")


# ---------------------------------------------------------------------------
#  Audit subcommands — index → detail funnel diagnostics
# ---------------------------------------------------------------------------

audit_app = typer.Typer(
    name="audit",
    help="Inspect index→detail extraction funnel records.",
    add_completion=False,
)
app.add_typer(audit_app)


@audit_app.command(name="list")
def audit_list(
    university: Optional[str] = typer.Option(
        None, "--university", "-u", help="University slug filter"
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y", help="Academic year filter"
    ),
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, max=200,
        help="Maximum rows to show (newest first)",
    ),
) -> None:
    """Show index→detail funnel rows: raw links → filtered → extracted.

    Useful for answering "the index had 10 programs, why did only 3 land
    in the DB?" — each row shows where in the funnel programs were lost.
    """
    db_manager = DatabaseManager()
    entries = db_manager.list_extraction_audit(
        university_slug=university, year=year, limit=limit
    )
    if not entries:
        typer.echo("No audit records.")
        return

    for entry in entries:
        recovered_note = (
            f" rescued={entry.recovered_count}" if entry.recovered_count else ""
        )
        stop_note = (
            f" stop={entry.pagination_stop_reason}"
            if entry.pagination_stop_reason
            else ""
        )
        typer.echo(
            f"[{entry.id}] {entry.university_slug} {entry.academic_year}  "
            f"raw={entry.raw_link_count} → "
            f"filtered={entry.llm_filtered_count}{recovered_note} → "
            f"candidates={entry.candidate_count} → "
            f"extracted={entry.extracted_count} "
            f"(quarantined={entry.quarantined_count}){stop_note}  "
            f"url={entry.index_url}"
        )


@audit_app.command(name="drill")
def audit_drill(
    audit_id: int = typer.Argument(..., help="Audit row id (from `audit list`)"),
) -> None:
    """Show which specific URLs were dropped at each filter stage.

    Answers "did we miss a real program?" — for a given audit row, lists
    every link that was filtered out, grouped by the stage that rejected
    it (llm_filter or taxonomy_filter).
    """
    db_manager = DatabaseManager()
    links = db_manager.list_audit_dropped_links(audit_id=audit_id)
    if not links:
        typer.echo("No dropped links recorded for this audit.")
        return

    grouped: dict = {}
    for link in links:
        grouped.setdefault(link.stage_dropped, []).append(link)

    for stage, items in grouped.items():
        typer.echo(f"\n{stage}  ({len(items)} dropped):")
        for link in items:
            anchor = f" [{link.anchor_text}]" if link.anchor_text else ""
            typer.echo(f"  {link.url}{anchor}")


# ---------------------------------------------------------------------------
#  crawl-summary — single-shot post-crawl summary for LLM CLI consumption
# ---------------------------------------------------------------------------


_ANOMALOUS_STOP_REASONS = {"url_drift", "decreasing_yield", "quality_failed"}


@app.command(name="crawl-summary")
def crawl_summary(
    university: str = typer.Option(
        ..., "--university", "-u",
        help="University slug to summarize (required).",
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y", help="Optional academic year filter.",
    ),
) -> None:
    """Print a one-shot summary of the most recent crawl for a university.

    Designed for LLM CLI consumption (Claude Code, Codex, Gemini CLI via
    the uni-admission-crawl skill): combines the latest audit row + the
    quarantine reason breakdown into a single block the model can quote
    directly to the user.
    """
    from collections import Counter

    db_manager = DatabaseManager()
    audits = db_manager.list_extraction_audit(
        university_slug=university, year=year, limit=1
    )
    if not audits:
        suffix = f" in {year}" if year else ""
        typer.echo(f"No recent crawl recorded for {university}{suffix}.")
        return

    audit = audits[0]
    q_entries = db_manager.list_quarantine(university_slug=university, year=year)
    q_breakdown = Counter(e.quarantine_reason for e in q_entries)

    stop = audit.pagination_stop_reason or "n/a"
    warn = "  ⚠️" if stop in _ANOMALOUS_STOP_REASONS else ""
    recovered_line = (
        f"  Recovered: rescued={audit.recovered_count} "
        f"(brought back by critique retry)\n"
        if audit.recovered_count
        else ""
    )

    typer.echo(
        f"Latest crawl: {audit.university_slug} {audit.academic_year}\n"
        f"  Index URL: {audit.index_url}\n"
        f"  Funnel:    raw={audit.raw_link_count} → "
        f"filtered={audit.llm_filtered_count} → "
        f"candidates={audit.candidate_count} → "
        f"extracted={audit.extracted_count}\n"
        f"  Quarantined: {audit.quarantined_count}\n"
        f"{recovered_line}"
        f"  Stop reason: {stop}{warn}"
    )

    if q_breakdown:
        typer.echo("\nQuarantine breakdown:")
        for reason, count in sorted(q_breakdown.items(), key=lambda x: -x[1]):
            typer.echo(f"  {reason}: {count}")
        typer.echo(
            f"\nReview details:\n"
            f"  adm-agent quarantine list --university {university}"
            + (f" --year {year}" if year else "")
        )
    else:
        typer.echo("\nNo quarantine entries — all extracted programs passed the quality gate.")


# ---------------------------------------------------------------------------
#  Diagnostics subcommands — unified cleanup for one university
# ---------------------------------------------------------------------------

diagnostics_app = typer.Typer(
    name="diagnostics",
    help="Bulk diagnostic-data operations spanning quarantine + audit.",
    add_completion=False,
)
app.add_typer(diagnostics_app)


@diagnostics_app.command(name="clear")
def diagnostics_clear(
    university: str = typer.Option(
        ..., "--university", "-u",
        help="University slug to clear all diagnostic data for (required).",
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y",
        help="Optional academic year filter. If omitted, ALL years are cleared.",
    ),
) -> None:
    """Clear quarantine entries + audit funnel rows for one university.

    One command to give a university a clean diagnostic slate — wipes
    both `program_quarantine` AND `extraction_audit` (with cascade
    delete of `extraction_audit_link`). The main `program` table is
    NOT touched — this affects diagnostic data only.

    `--university` is required (no bulk clear-all from the CLI).
    """
    db_manager = DatabaseManager()
    result = db_manager.clear_diagnostics(university_slug=university, year=year)
    year_note = f" year={year}" if year else ""
    typer.echo(
        f"Cleared diagnostics for {university}{year_note}:\n"
        f"  quarantine entries deleted: {result['quarantine_deleted']}\n"
        f"  audit rows deleted:         {result['audits_deleted']}\n"
        f"  audit link rows deleted:    {result['links_deleted']}"
    )


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
