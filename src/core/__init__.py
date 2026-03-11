"""
Core package for UniAdmission Agent.

This package contains core infrastructure modules including environment
validation, configuration, and shared utilities.
"""

from .environment import ensure_ready, install_playwright_browser
from .async_utils import run_sync

__all__ = ['ensure_ready', 'install_playwright_browser', 'run_sync']
