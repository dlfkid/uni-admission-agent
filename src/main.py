#!/usr/bin/env python3
"""
UniAdmission Agent - Main Entry Point

Provides CLI interface for the uni-admission-agent project.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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

from src.core.environment import ensure_ready, EnvironmentError, UVError, DependencyError, PlaywrightError, DatabaseError


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
    except UVError as e:
        logger.error(f"\n❌ {e}")
        return 1
    except DependencyError as e:
        logger.error(f"\n❌ {e}")
        return 1
    except PlaywrightError as e:
        logger.error(f"\n❌ {e}")
        return 1
    except DatabaseError as e:
        logger.error(f"\n❌ {e}")
        return 1
    except EnvironmentError as e:
        logger.error(f"\n❌ Environment error: {e}")
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
    logger.info("\nPlanned workflow:")
    logger.info("  1. Load target university URLs")
    logger.info("  2. Initialize Playwright with stealth mode")
    logger.info("  3. Scrape admission data")
    logger.info("  4. Extract structured information using LLM")
    logger.info("  5. Store data in SQLite database")
    
    return 0


def main() -> int:
    """
    Main entry point for the CLI application.
    
    Returns:
        Exit code
    """
    parser = argparse.ArgumentParser(
        description="UniAdmission Agent - Automated university admission data scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s check              Run environment checks only
  %(prog)s run                Start the crawling task
  %(prog)s run --verbose      Start crawling with verbose output
        """
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
    parser_check = subparsers.add_parser(
        'check',
        help='Run environment and dependency checks'
    )
    parser_check.set_defaults(func=cmd_check)
    
    # Run command
    parser_run = subparsers.add_parser(
        'run',
        help='Start the crawling task'
    )
    parser_run.set_defaults(func=cmd_run)
    
    # Parse arguments
    args = parser.parse_args()
    
    # Execute the selected command
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.info("\n\nOperation cancelled by user")
        return 130
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
