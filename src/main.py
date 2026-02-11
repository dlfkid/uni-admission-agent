#!/usr/bin/env python3
"""
UniAdmission Agent - Main Entry Point

Provides CLI interface for the uni-admission-agent project.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import re

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def validate_slug(value: str) -> str:
    """Validate university slug (lowercase, numbers, hyphens only)."""
    if not re.match(r'^[a-z0-9-]+$', value):
        raise argparse.ArgumentTypeError(f"Invalid name '{value}'. Must contain only lowercase letters, numbers, and hyphens.")
    return value


def validate_year(value: str) -> int:
    """Validate academic year (positive integer)."""
    try:
        year = int(value)
        if year <= 0:
            raise ValueError
        return year
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid year '{value}'. Must be a positive integer.")


def _ensure_venv():
    """
    Automatically switch to .venv interpreter if running with system python.
    This ensures dependencies are found without requiring explicit 'uv run'.
    """
    # Check if we are running in a virtual environment
    is_venv = (hasattr(sys, 'real_prefix') or
              (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
    
    if is_venv:
        return

    # Check if .venv exists in project root
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    
    if venv_python.exists():
        # Re-execute the script with the venv python interpreter
        # We use os.execv to replace the current process
        print(f"🔄 Auto-switching to virtual environment: {venv_python}")
        try:
            os.execv(str(venv_python), [str(venv_python)] + sys.argv)
        except OSError as e:
            print(f"⚠️ Failed to switch to virtual environment: {e}")

# Try to switch to venv before importing project modules
_ensure_venv()

from src.core.environment import ensure_ready, EnvironmentError, UVError, DependencyError, PlaywrightError, DatabaseError, DataProcessingError
from src.storage.db_manager import DatabaseManager
from src.storage.importer import ExcelImporter
from src.storage.exporter import ExcelExporter
from src.models.admission import University, Program
from src.scrapers.engine import AdmissionScraper
from sqlmodel import select, func, col, desc


logger = logging.getLogger(__name__)


def cmd_check(args: argparse.Namespace) -> int:
    """
    Handle the 'check' command - run environment checks only.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        ensure_ready(verbose=args.verbose)
        return 0
    except (UVError, DependencyError, PlaywrightError, DatabaseError, DataProcessingError) as e:
        logger.error(f"\n❌ {e}")
        return 1
    except EnvironmentError as e:
        logger.error(f"\n❌ Environment error: {e}")
        return 1


def cmd_import(args: argparse.Namespace) -> int:
    """
    Handle the 'import' command - import data from Excel.
    """
    logger.info(f"Starting import from: {args.file} (LLM: {'Enabled' if args.llm else 'Disabled'})")
    try:
        importer = ExcelImporter(args.file, use_llm=args.llm)
        importer.import_data(univ_slug=args.name, year=args.year)
        return 0
    except Exception as e:
        logger.exception(f"Import failed: {e}")
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    """
    Handle the 'export' command - export data to Excel.
    """
    logger.info(f"Exporting data for {args.name} (Year: {args.year or 'All'}) to {args.output}")
    try:
        exporter = ExcelExporter(args.output)
        exporter.export_data(univ_slug=args.name, year=args.year)
        return 0
    except Exception as e:
        logger.exception(f"Export failed: {e}")
        return 1


def cmd_crawl(args: argparse.Namespace) -> int:
    """
    Handle the 'crawl' command - crawl a URL and import data.
    """
    logger.info(
        f"Starting crawl: {args.url} "
        f"(University: {args.name}, Year: {args.year})"
    )
    try:
        scraper = AdmissionScraper()
        imported = asyncio.run(
            scraper.crawl_and_clean(
                url=args.url,
                univ_slug=args.name,
                year=args.year,
            )
        )
        logger.info(f"Crawl complete: {imported} programs imported")
        return 0
    except Exception as e:
        logger.exception(f"Crawl failed: {e}")
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """
    Handle the 'status' command - show database statistics.
    """
    try:
        db = DatabaseManager()
        with db.get_session() as session:
            # Count queries
            print("\nDatabase Status:")
            univ_count = session.exec(select(func.count()).select_from(University)).one()
            prog_count = session.exec(select(func.count()).select_from(Program)).one()
            print(f"  Universities: {univ_count}")
            print(f"  Programs:     {prog_count}")
            
            # Detailed breakdown
            univs = session.exec(select(University)).all()
            if univs:
                print("\nBreakdown by University:")
                for u in univs:
                    print(f"  - {u.name} ({u.slug}):")
                    # Group by year
                    # Use col() to help type checkers understand Program.academic_year is a Column
                    stmt = select(Program.academic_year, func.count()).where(Program.university_id == u.id).group_by(col(Program.academic_year)).order_by(desc(col(Program.academic_year)))
                    stats = session.exec(stmt).all()
                    
                    if not stats:
                        print("      (No programs)")
                    
                    for year, count in stats:
                        print(f"      {year}: {count} programs")
        return 0
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    """
    Handle the 'run' command - start the crawling task.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Run pre-flight environment check
    logger.info("Running pre-flight environment check...")
    try:
        ensure_ready(verbose=args.verbose)
    except EnvironmentError as e:
        logger.error("\n❌ Environment check failed. Please fix the issues above.")
        logger.error(f"Error: {e}")
        return 1
    
    # Start crawling
    logger.info("\n" + "=" * 60)
    logger.info("STARTING CRAWL")
    logger.info("=" * 60)
    
    # TODO: Implement actual crawling logic
    logger.info("Crawling functionality will be implemented here.")
    
    return 0


from src.core.token_tracker import tracker

def main() -> int:
    """
    Main entry point for the CLI application.
    
    Returns:
        Exit code
    """
    # Initialize basic logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize Database (Auto-heal)
    try:
        pass 
    except Exception:
        pass

    epilog_text = """
Examples:
  %(prog)s check                                        Run environment checks only
  %(prog)s run                                          Start the crawling task
  %(prog)s import --name hku --year 2026 --file f.xlsx  Import data from Excel
  %(prog)s crawl --name hku --year 2026 --url <URL>     Crawl and import from URL
  %(prog)s status                                       Show database statistics
    """

    parser = argparse.ArgumentParser(
        description="UniAdmission Agent - Automated university admission data scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_text
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )
    
    subparsers = parser.add_subparsers(
        title='commands',
        description='Available commands',
        dest='command',
        required=True
    )
    
    # Check command
    parser_check = subparsers.add_parser('check', help='Run environment and dependency checks')
    parser_check.set_defaults(func=cmd_check)
    
    # Run command
    parser_run = subparsers.add_parser('run', help='Start the crawling task')
    parser_run.set_defaults(func=cmd_run)
    
    # Import command
    parser_import = subparsers.add_parser('import', help='Import university data from Excel')
    parser_import.add_argument('--name', required=True, type=validate_slug, help='University Slug (a-z0-9-)')
    parser_import.add_argument('--year', required=True, type=validate_year, help='Academic Year (e.g., 2026)')
    parser_import.add_argument('--file', required=True, help='Path to XLSX file')
    parser_import.add_argument('--llm', action='store_true', help='Enable LLM analysis for missing data')
    parser_import.set_defaults(func=cmd_import)

    # Export command
    parser_export = subparsers.add_parser('export', help='Export university data to Excel')
    parser_export.add_argument('--name', required=True, type=validate_slug, help='University Slug (a-z0-9-)')
    parser_export.add_argument('--year', type=validate_year, help='Academic Year (Optional, default all)')
    parser_export.add_argument('--output', required=True, help='Output XLSX file path')
    parser_export.set_defaults(func=cmd_export)
    
    # Crawl command
    parser_crawl = subparsers.add_parser('crawl', help='Crawl a URL and import admission data')
    parser_crawl.add_argument('--name', required=True, type=validate_slug, help='University Slug (a-z0-9-)')
    parser_crawl.add_argument('--year', required=True, type=validate_year, help='Academic Year (e.g., 2026)')
    parser_crawl.add_argument('--url', required=True, help='Starting URL to crawl')
    parser_crawl.set_defaults(func=cmd_crawl)

    # Status command
    parser_status = subparsers.add_parser('status', help='Show database status')
    parser_status.set_defaults(func=cmd_status)
    
    # Parse arguments
    args = parser.parse_args()
    
    # Global Init logic (Requirement: Ensure DB init)
    if args.command in ['import', 'status', 'run', 'export', 'crawl']:
        try:
             DatabaseManager().init_db()
        except Exception as e:
             if args.verbose:
                 logger.warning(f"Database auto-init warning: {e}")
    
    # Execute the selected command
    try:
        result = args.func(args)
        # Log token usage summary if any
        if args.command in ['import', 'run', 'crawl']:
             tracker.log_summary()
        return result
    except KeyboardInterrupt:
        logger.info("\n\nOperation cancelled by user")
        return 130
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
