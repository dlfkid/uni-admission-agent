"""
Core package for UniAdmission Agent.

This package contains core infrastructure modules including environment
validation, configuration, and shared utilities.
"""

from .environment import ensure_ready

__all__ = ['ensure_ready']
