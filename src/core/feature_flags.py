"""Shared feature-flag helpers.

TODO(phase-b): Migrate ``AGENT_ENABLED``, ``AGENT_RUNTIME``,
``AGENT_ALLOW_INTERNAL_LLM``, and ``AGENT_ALLOW_EXTERNAL_LLM`` into the
project's ``pydantic-settings`` ``Settings`` model so that all env-var
access is type-safe and centralized.
"""

from __future__ import annotations

import os

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def is_truthy_env(name: str, *, default: str = "false") -> bool:
    """Return whether an env var is truthy using project-wide semantics."""
    value = str(os.getenv(name, default)).strip().lower()
    return value in TRUTHY_ENV_VALUES


def is_agent_enabled_env(explicit_flag: bool | None = None) -> bool:
    """Resolve whether agent runtime is enabled from explicit flag/env."""
    if explicit_flag is not None:
        return bool(explicit_flag)
    return is_truthy_env("AGENT_ENABLED", default="true")
