"""
Environment validation and setup module.

This module provides functions to validate the runtime environment,
check dependencies, and ensure all required resources are available
before starting the application.
"""

import importlib.util
import logging
import subprocess
from pathlib import Path
from typing import List


# ============================================================================
# Custom Exceptions
# ============================================================================

class EnvironmentError(Exception):
    """Base exception for environment-related errors."""
    pass


class DependencyError(EnvironmentError):
    """Raised when required Python packages are missing."""
    
    def __init__(self, missing_packages: List[str]):
        self.missing_packages = missing_packages
        package_list = '\n  - '.join(missing_packages)
        super().__init__(
            f"Missing {len(missing_packages)} required package(s):\n  - {package_list}"
        )


class PlaywrightError(EnvironmentError):
    """Raised when Playwright browsers are not properly installed."""
    
    def __init__(self, message: str = "Playwright browsers not properly installed"):
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

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirement.txt"
DATA_DIRS = [
    PROJECT_ROOT / "data" / "raw_markdown",
    PROJECT_ROOT / "data" / "processed",
    PROJECT_ROOT / "data" / "database"
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

def _check_package_installed(package_name: str) -> bool:
    """
    Check if a Python package is installed.
    
    Args:
        package_name: Name of the package to check
        
    Returns:
        True if package is installed, False otherwise
    """
    # Map package names to their import names
    import_name_map = {
        'python-dotenv': 'dotenv',
        'playwright-extra': 'playwright_extra'
    }
    
    import_name = import_name_map.get(package_name, package_name)
    spec = importlib.util.find_spec(import_name)
    return spec is not None


def _check_dependencies() -> None:
    """
    Check if all required dependencies are installed.
    
    Raises:
        DependencyError: If any required packages are missing
        FileNotFoundError: If requirements.txt is not found
    """
    logger.info("Checking Python dependencies...")
    
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"Requirements file not found: {REQUIREMENTS_FILE}")
    
    missing_packages = []
    
    with open(REQUIREMENTS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Extract package name (handle version specifiers)
            package_name = line.split('==')[0].split('>=')[0].split('<=')[0].strip()
            
            if not _check_package_installed(package_name):
                missing_packages.append(package_name)
                logger.warning(f"Missing package: {package_name}")
            else:
                logger.debug(f"Package installed: {package_name}")
    
    if missing_packages:
        logger.error(f"Found {len(missing_packages)} missing package(s)")
        raise DependencyError(missing_packages)
    
    logger.info("✓ All Python dependencies are installed")


def _check_playwright() -> None:
    """
    Check if Playwright browser binaries are installed.
    
    Raises:
        PlaywrightError: If Playwright browsers are not properly installed
    """
    logger.info("Checking Playwright browser binaries...")
    
    try:
        # Check if playwright CLI is accessible
        result = subprocess.run(
            ['playwright', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.debug(f"Playwright version: {result.stdout.strip()}")
        else:
            raise PlaywrightError("Playwright CLI not accessible")
        
        # Check if browsers are installed
        result = subprocess.run(
            ['playwright', 'install', '--dry-run'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # If dry-run shows installations needed, browsers might not be installed
        if "chromium" in result.stdout.lower() or "firefox" in result.stdout.lower():
            raise PlaywrightError(
                "Playwright browsers not fully installed. "
                "Run: playwright install"
            )
        
        logger.info("✓ Playwright browsers are installed")
        
    except FileNotFoundError:
        raise PlaywrightError(
            "Playwright CLI not found. "
            "Install with: pip install playwright && playwright install"
        )
    except subprocess.TimeoutExpired:
        raise PlaywrightError("Playwright check timed out")


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


# ============================================================================
# Public API
# ============================================================================

def ensure_ready(verbose: bool = False) -> bool:
    """
    Ensure the environment is ready to run the application.
    
    This function performs comprehensive environment validation:
    - Checks all Python dependencies are installed
    - Verifies Playwright browsers are available
    - Creates required directory structure
    
    Args:
        verbose: If True, enable verbose logging (DEBUG level)
        
    Returns:
        True if all checks pass
        
    Raises:
        DependencyError: If required Python packages are missing
        PlaywrightError: If Playwright is not properly set up
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
        _check_dependencies()
        _check_playwright()
        _ensure_directories()
        
        logger.info("=" * 60)
        logger.info("✅ All environment checks passed!")
        logger.info("=" * 60)
        
        return True
        
    except EnvironmentError:
        logger.info("=" * 60)
        logger.error("❌ Environment check failed")
        logger.info("=" * 60)
        raise
