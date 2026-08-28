"""Gates that must pass before any bytes are downloaded (spec §9)."""
from __future__ import annotations

import os
import subprocess
import sys
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


def _windows_process_alive(pid: int) -> bool:
    """Liveness probe for Windows that never signals the target.

    ``os.kill`` on Windows is *not* a signal API: for anything other than
    ``CTRL_C_EVENT`` / ``CTRL_BREAK_EVENT`` it calls ``TerminateProcess``,
    so the POSIX ``os.kill(pid, 0)`` idiom would hard-kill the very server
    it is asked to probe (and, with a recycled stale PID, an unrelated
    user process). ``OpenProcess`` + ``GetExitCodeProcess`` observes the
    process without touching it; ``tasklist`` is the fallback when the
    Win32 API is unreachable.
    """
    alive = _windows_process_alive_via_api(pid)
    if alive is not None:
        return alive
    return _windows_process_alive_via_tasklist(pid)


def _windows_process_alive_via_api(pid: int) -> bool | None:
    """``None`` when the Win32 API could not be consulted at all."""
    # pylint: disable=import-outside-toplevel
    import ctypes

    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            # Access denied proves the process exists; anything else means
            # there is no such process.
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # handle opened, so it exists; assume alive
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _windows_process_alive_via_tasklist(pid: int) -> bool:
    """Shell-out fallback; a failed query is reported as *not* alive."""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return str(pid) in proc.stdout


def _posix_process_alive(pid: int) -> bool:
    """POSIX liveness via a signal-0 probe (no signal is actually delivered)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def is_process_alive(pid: int, *, windows: bool | None = None) -> bool:
    """True when *pid* names a live process.

    Valid PIDs are strictly positive integers. Invalid PIDs (0, negative, or
    too large to signal) are treated as non-existent.

    *windows* is injectable so both platform branches are testable on one
    host, exactly as :attr:`InstallLayout.windows` already is.
    """
    if pid <= 0:
        return False
    if windows is None:
        windows = sys.platform == "win32"
    if not windows:
        return _posix_process_alive(pid)
    try:
        return _windows_process_alive(pid)
    except OverflowError:
        return False


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
