"""Atomic, verified self-upgrade for the packaged backend.

See ``docs/superpowers/specs/2026-08-27-upgrade-reliability-design.md``.
"""
from __future__ import annotations

from pathlib import Path

from src.core.paths import get_data_dir, is_frozen
from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.preflight import PreflightBlock, is_process_alive, run_preflight
from src.services.upgrade.release import get_platform_info
from src.services.upgrade.transaction import (
    check_for_updates,
    get_current_version,
    perform_upgrade,
    rollback,
)
from src.services.upgrade.types import (
    BlockedReason,
    ChecksumMismatchError,
    ExitCode,
    StagedBinaryError,
    UnparseableVersionError,
    UpgradeError,
    UpgradeResult,
)

DEFAULT_PORT = 8910


def default_install_layout() -> InstallLayout:
    """Backend layout, rooted at the frozen-mode data dir (spec §3.1)."""
    return InstallLayout(root=get_data_dir(), artifact_name="adm-agent")


def default_client_layout() -> InstallLayout:
    """Client layout, rooted at its existing config home (spec §3.6)."""
    return InstallLayout(
        root=Path.home() / ".adm-agent-client", artifact_name="adm-agent-client"
    )


def default_pid_file() -> Path:
    """PID file written by ``serve`` / ``serve-install``."""
    return Path.home() / ".adm-agent" / "server.pid"


def default_client_pid_file() -> Path:
    """PID file written by ``adm-agent-client start`` / ``start-install``.

    The client is a separate process from the server: a running server must
    not block a client upgrade, and vice versa.
    """
    return Path.home() / ".adm-agent-client" / "client.pid"


def default_health_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/health"


__all__ = [
    "BlockedReason",
    "ChecksumMismatchError",
    "ExitCode",
    "InstallLayout",
    "PreflightBlock",
    "StagedBinaryError",
    "UnparseableVersionError",
    "UpgradeError",
    "UpgradeResult",
    "check_for_updates",
    "default_client_layout",
    "default_client_pid_file",
    "default_health_url",
    "default_install_layout",
    "default_pid_file",
    "get_current_version",
    "get_platform_info",
    "is_frozen",
    "is_process_alive",
    "perform_upgrade",
    "rollback",
    "run_preflight",
]
