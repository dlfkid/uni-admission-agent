"""
Environment validation and setup module.

This module provides functions to validate the runtime environment,
check dependencies, and ensure all required resources are available
before starting the application.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.core.paths import get_bundle_dir, get_data_dir, is_frozen


# ============================================================================
# Custom Exceptions
# ============================================================================

class EnvironmentError(Exception):
    """Base exception for environment-related errors."""
    pass


class UVError(EnvironmentError):
    """Raised when uv package manager is not available or errors occur."""
    
    def __init__(self, message: str = "UV package manager is not available"):
        super().__init__(message)


class DependencyError(EnvironmentError):
    """Raised when dependencies are out of sync with uv.lock."""
    
    def __init__(self, message: str = "Dependencies are not synced"):
        super().__init__(message)


class PlaywrightError(EnvironmentError):
    """Raised when Playwright browsers are not properly installed."""
    
    def __init__(self, message: str = "Playwright browsers not properly installed"):
        super().__init__(message)


class DatabaseError(EnvironmentError):
    """Raised when database connection or configuration fails."""
    
    def __init__(self, message: str = "Database check failed"):
        super().__init__(message)


class DataProcessingError(EnvironmentError):
    """Raised when data processing libraries are missing or misconfigured."""
    
    def __init__(self, message: str = "Data processing check failed"):
        super().__init__(message)


class AIServiceError(EnvironmentError):
    """Raised when AI service libraries (e.g., Google GenAI) are missing."""
    
    def __init__(self, message: str = "AI service check failed"):
        super().__init__(message)


class ScraperError(Exception):
    """Raised when scraping operations fail (network, parsing, etc.)."""

    def __init__(self, message: str = "Scraping operation failed"):
        super().__init__(message)


class LLMProviderError(Exception):
    """Raised when all LLM providers fail (rate limit, unavailable, etc.)."""

    def __init__(self, message: str = "All LLM providers failed"):
        super().__init__(message)


class PDFProcessingError(Exception):
    """Raised when PDF conversion or processing fails."""

    def __init__(self, message: str = "PDF processing failed"):
        super().__init__(message)


class DirectoryError(EnvironmentError):
    """Raised when required directories cannot be created."""
    
    def __init__(self, directory: Path, original_error: Exception):
        self.directory = directory
        self.original_error = original_error
        super().__init__(
            f"Failed to create directory {directory}: {original_error}"
        )


# ============================================================================
# Module Configuration
# ============================================================================

logger = logging.getLogger(__name__)

# Project paths — resolved at runtime for both dev and frozen modes
PROJECT_ROOT = get_bundle_dir()
_data_root = get_data_dir()
DATA_DIRS = [
    _data_root / "raw_markdown",
    _data_root / "processed",
    _data_root / "database",
]


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(verbose: bool = False) -> None:
    """
    Configure logging for the application.
    
    Args:
        verbose: If True, set logging level to DEBUG, otherwise INFO
    """
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True  # Override any existing configuration
    )
    
    logger.debug("Logging configured (verbose mode: %s)", verbose)


# ============================================================================
# Private Helper Functions
# ============================================================================

def _check_uv_command() -> None:
    """
    Check if uv package manager is installed and accessible.
    
    Raises:
        UVError: If uv is not installed or not accessible
    """
    logger.info("Checking uv package manager...")
    
    try:
        result = subprocess.run(
            ['uv', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.debug(f"UV version: {result.stdout.strip()}")
            logger.info("✓ UV package manager is available")
        else:
            raise UVError(
                "UV command failed. "
                "Install with: pip install uv"
            )
    except FileNotFoundError:
        raise UVError(
            "UV package manager not found. "
            "Install with: pip install uv"
        )
    except subprocess.TimeoutExpired:
        raise UVError("UV command check timed out")


def _check_uv_sync() -> None:
    """
    Check if the environment is synced with uv.lock.
    
    Uses 'uv sync --check' to verify dependencies are up to date.
    
    Raises:
        DependencyError: If dependencies are out of sync with uv.lock
    """
    logger.info("Checking dependency sync status...")
    
    try:
        result = subprocess.run(
            ['uv', 'sync', '--check'],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT
        )
        
        if result.returncode == 0:
            logger.info("✓ Dependencies are synced with uv.lock")
        else:
            # uv sync --check failed, environment is out of sync
            logger.error("Dependencies are not synced with uv.lock")
            logger.debug(f"UV sync check output: {result.stderr}")
            raise DependencyError(
                "Dependencies are out of sync. "
                "Run: uv sync"
            )
    except subprocess.TimeoutExpired:
        raise DependencyError("Dependency sync check timed out")
    except FileNotFoundError:
        # This shouldn't happen if _check_uv_command passed, but just in case
        raise UVError("UV command not found")


def _check_playwright() -> None:
    """
    Check if Playwright is installed via verifying browser installation.
    
    Tries these methods in order:
    1. Direct Python API check (Preferred for both Dev & Frozen)
    2. python -m playwright (Dev only)
    3. playwright binary (Dev/System only)
    
    Raises:
        PlaywrightError: If Playwright usage fails
    """
    logger.info("Checking Playwright installation...")
    
    # 0. Frozen/General Mode: Try importing and checking executable path directly
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # We must cast to Path because in some versions it's a string
            browser_path = Path(p.chromium.executable_path)
            if browser_path.exists():
                logger.debug(f"Playwright browser found at: {browser_path}")
                logger.info("✓ Playwright browser found (via API)")
                return
            else:
                logger.warning(f"Playwright browser path does not exist: {browser_path}")
    except Exception as e:
        logger.debug(f"Failed to check via API: {e}")

    # If frozen, we stop here because subprocess calls won't work well
    if is_frozen():
         raise PlaywrightError(
            "Playwright browser not found.\n"
            "Solution 1 (Recommended): Run 'browser-install' command to auto-download Chromium.\n"
            "Solution 2: Install a supported browser (Chrome/Edge) manually.\n"
            "Solution 3: Set 'PLAYWRIGHT_BROWSERS_PATH' env var to your browser's folder."
        )

    # 1. Dev Mode: Try Python module execution
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'playwright', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.debug(f"Playwright (via python module): {result.stdout.strip()}")
            logger.info("✓ Playwright python module found")
            return
    except Exception as e:
        logger.debug(f"Failed to check python module: {e}")

    # 2. Dev Mode: Try identifying binary in PATH
    commands = ['playwright', 'playwright-cli']
    
    for cmd in commands:
        if shutil.which(cmd):
            try:
                result = subprocess.run(
                    [cmd, '--version'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.debug(f"Playwright binary ({cmd}): {result.stdout.strip()}")
                    logger.info(f"✓ Playwright binary found: {cmd}")
                    return
            except Exception as e:
                logger.debug(f"Failed to check binary {cmd}: {e}")

    # If all failed
    raise PlaywrightError(
        "Playwright not found via API, module, or CLI.\n"
        "Solution 1: Run 'browser-install' command to auto-download Chromium.\n"
        "Solution 2: Ensure it is installed in your python environment:\n"
        "  uv pip install playwright && uv run playwright install\n"
        "Or via npm for CLI:\n"
        "  npm install -g @playwright/cli"
    )


def _ensure_directories() -> None:
    """
    Ensure all required directories exist. Create them if they don't.
    
    Raises:
        DirectoryError: If any directory cannot be created
    """
    logger.info("Verifying directory structure...")
    
    for directory in DATA_DIRS:
        try:
            if not directory.exists():
                logger.info(f"Creating directory: {directory}")
                directory.mkdir(parents=True, exist_ok=True)
            else:
                logger.debug(f"Directory exists: {directory}")
        except Exception as e:
            raise DirectoryError(directory, e)
    
    logger.info("✓ Directory structure verified")


def _check_database() -> None:
    """
    Check database connectivity and configuration.
    
    Verifies:
    1. sqlmodel and aiosqlite are importable
    2. SQLite database file path is writable
    3. Connection can be established and executing SELECT 1 works
    
    Raises:
        DatabaseError: If database check fails
    """
    logger.info("Checking database connection...")
    
    # 1. Check imports
    try:
        import aiosqlite
        import sqlalchemy_utils
        from sqlmodel import create_engine, text, Session
    except ImportError as e:
        # Check if we are running in the virtual environment
        is_venv = (hasattr(sys, 'real_prefix') or
                  (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
        
        error_msg = f"Database dependencies missing: {e}"
        if not is_venv:
            error_msg += (
                "\n\n⚠️  It seems you are not running in the virtual environment."
                "\n   Please run with: uv run src/main.py check"
            )
        raise DatabaseError(error_msg)

    # 2. Check path permissions
    db_path = PROJECT_ROOT / "data" / "database" / "admission.db"
    # Ensure parent dir exists (should be handled by _ensure_directories)
    if not db_path.parent.exists():
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise DatabaseError(f"Cannot create database directory: {e}")
            
    # 3. Connection Test
    # Using sync engine for quick check, though app might use async
    db_url = f"sqlite:///{db_path}"
    try:
        engine = create_engine(db_url)
        with Session(engine) as session:
            session.connection().execute(text("SELECT 1"))
        logger.info("✓ Database connection established")
    except Exception as e:
        raise DatabaseError(f"Failed to connect to database: {e}")


def _check_alembic() -> None:
    """
    Check Alembic migrations status if configured.
    
    Checks if migrations directory exists and attempts to report status.
    """
    migrations_dir = PROJECT_ROOT / "migrations"
    
    
    if migrations_dir.exists():
        logger.info("Checking Alembic migrations...")
        # We just verify the directory exists for now as a basic check.
        # Running 'alembic current' might require more config setup which 
        # could be fragile in a simple environment check.
        # Future improvement: integrate actual alembic CLI check.
        logger.info("✓ Migrations directory found")
    else:
        logger.debug("No migrations directory found (skipping check)")


def _check_data_processing() -> None:
    """
    Check availability of data processing libraries.
    
    Verifies:
    1. pandas is importable
    2. openpyxl is importable
    3. dateutil is importable
    """
    logger.info("Checking data processing libraries...")
    try:
        import pandas
        import openpyxl
        import dateutil
        logger.info("✓ Data processing libraries found")
    except ImportError as e:
        # Check if we are running in the virtual environment
        is_venv = (hasattr(sys, 'real_prefix') or
                  (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
        
        error_msg = f"Data processing dependencies missing: {e}"
        if not is_venv:
            error_msg += (
                "\n\n⚠️  It seems you are not running in the virtual environment."
                "\n   Please run with: uv run src/main.py check"
            )
        raise DataProcessingError(error_msg)


def _check_ai_services() -> None:
    """
    Check availability of AI service libraries.
    
    Verifies:
    1. google-genai (google.genai) is importable
    """
    logger.info("Checking AI service libraries...")
    try:
        from google import genai
        import volcenginesdkcore
        logger.info("✓ AI service libraries found")
    except ImportError as e:
        is_venv = (hasattr(sys, 'real_prefix') or
                  (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
        
        error_msg = f"AI service dependencies missing: {e}"
        if not is_venv:
            error_msg += (
                "\n\n⚠️  It seems you are not running in the virtual environment."
                "\n   Please run with: uv run src/main.py check"
            )
        raise AIServiceError(error_msg)


# ============================================================================
# Browser Installation
# ============================================================================

def install_playwright_browser() -> bool:
    """
    Install Playwright Chromium browser.

    Uses Playwright's internal driver to download and install the Chromium
    browser. Works in both dev and frozen (PyInstaller) modes.

    Returns:
        True if installation succeeded

    Raises:
        PlaywrightError: If installation fails
    """
    logger.info("Installing Playwright Chromium browser...")

    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        driver_executable, driver_cli = compute_driver_executable()
        env = get_driver_env()

        # Ensure PLAYWRIGHT_BROWSERS_PATH is propagated for frozen mode
        if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]

        completed = subprocess.run(
            [str(driver_executable), str(driver_cli), "install", "chromium"],
            env=env,
            timeout=600,  # 10 minutes timeout for slow networks
        )

        if completed.returncode == 0:
            logger.info("Playwright Chromium browser installed successfully!")
            return True
        else:
            raise PlaywrightError(
                f"Browser installation failed (exit code {completed.returncode}).\n"
                "Please check your network connection and try again."
            )
    except ImportError:
        raise PlaywrightError(
            "Playwright package not found. Cannot install browser."
        )
    except subprocess.TimeoutExpired:
        raise PlaywrightError(
            "Browser installation timed out.\n"
            "Please check your network connection and try again."
        )
    except PlaywrightError:
        raise
    except Exception as e:
        raise PlaywrightError(f"Browser installation failed: {e}")


# ============================================================================
# Public API
# ============================================================================

def ensure_ready(verbose: bool = False) -> bool:
    """
    Ensure the environment is ready to run the application.
    
    This function performs comprehensive environment validation:
    - Checks uv package manager is installed
    - Verifies dependencies are synced with uv.lock
    - Verifies Playwright browsers are available
    - Creates required directory structure
    
    Args:
        verbose: If True, enable verbose logging (DEBUG level)
        
    Returns:
        True if all checks pass
        
    Raises:
        UVError: If uv package manager is not available
        DependencyError: If dependencies are out of sync
        PlaywrightError: If Playwright is not properly set up
        PlaywrightError: If Playwright is not properly set up
        DatabaseError: If database connection fails
        DataProcessingError: If data libraries are missing
        DirectoryError: If directories cannot be created
        
    Example:
        >>> from src.core.environment import ensure_ready
        >>> try:
        ...     ensure_ready(verbose=True)
        ...     print("Environment ready!")
        ... except EnvironmentError as e:
        ...     print(f"Environment check failed: {e}")
    """
    setup_logging(verbose)
    
    logger.info("=" * 60)
    logger.info("ENVIRONMENT CHECK")
    logger.info("=" * 60)
    
    try:
        # Run all validation checks
        if not is_frozen():
            _check_uv_command()
            _check_uv_sync()
        _check_playwright()
        _ensure_directories()
        _check_database()
        _check_alembic()
        _check_data_processing()
        _check_ai_services()
        
        logger.info("=" * 60)
        logger.info("✅ All environment checks passed!")
        logger.info("=" * 60)
        
        return True
        
    except EnvironmentError:
        logger.info("=" * 60)
        logger.error("❌ Environment check failed")
        logger.info("=" * 60)
        raise
