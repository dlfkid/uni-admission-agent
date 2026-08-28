"""Gates that must pass before any bytes are downloaded (spec §9)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.types import BlockedReason, ExitCode


@dataclass(frozen=True)
class PreflightBlock:
    """A refusal to proceed, carrying everything the agent needs to route."""

    reason: str
    exit_code: int
    message: str
    next_action: str


def is_process_alive(pid: int) -> bool:
    """True when *pid* names a live process.

    Valid PIDs are strictly positive integers. Invalid PIDs (0, negative, or
    too large to signal) are treated as non-existent.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def _probe_health(health_url: str) -> bool:
    if not health_url:
        return False  # artifacts without a health endpoint (e.g. the client)
    try:
        with urlopen(health_url, timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def is_server_running(pid_file: Path, health_url: str) -> bool:
    """True when a server is serving. A stale PID file must not block."""
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text().strip())
            # Validate PID: must be strictly positive and within signalling range
            if pid > 0 and is_process_alive(pid):
                return True
        except (ValueError, OSError, OverflowError):
            # Unreadable, garbage, or out-of-range PID file: fall through to health probe
            pass
    return _probe_health(health_url)


def run_preflight(
    layout: InstallLayout,
    *,
    frozen: bool,
    pid_file: Path,
    health_url: str,
) -> PreflightBlock | None:
    """Return a block, or ``None`` when the upgrade may proceed."""
    if not frozen:
        return PreflightBlock(
            reason=BlockedReason.NOT_FROZEN,
            exit_code=int(ExitCode.NOT_FROZEN),
            message=(
                "Self-upgrade only applies to packaged installs. "
                "This is a source checkout — update with git and uv sync."
            ),
            next_action="update_source_checkout_with_git",
        )

    if layout.is_legacy():
        return PreflightBlock(
            reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
            message=(
                "This install predates versioned layouts. Re-run the installer "
                "once to migrate; your .env and database are preserved."
            ),
            next_action="reinstall_to_migrate_layout",
        )

    if is_server_running(pid_file, health_url):
        return PreflightBlock(
            reason=BlockedReason.SERVER_RUNNING,
            exit_code=int(ExitCode.SERVER_RUNNING),
            message="The server is running. Stop it, then run the upgrade again.",
            next_action="stop_server_then_retry",
        )

    return None
