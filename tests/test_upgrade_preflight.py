"""Tests for upgrade preflight gates — spec §9."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.preflight import (
    _windows_process_alive_via_tasklist,
    is_process_alive,
    is_server_running,
    run_preflight,
)
from src.services.upgrade.types import BlockedReason, ExitCode

_HEALTH = "http://127.0.0.1:8910/health"


def _versioned(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(root=tmp_path, windows=False)
    vdir = layout.version_dir("v0.10.0")
    (vdir / "_internal").mkdir(parents=True)
    (vdir / "adm-agent").write_text("x")
    layout.activate("v0.10.0")
    return layout


# ── server detection ──────────────────────────────────────────────────


def test_live_pid_file_means_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text(str(os.getpid()))
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is True


def test_stale_pid_file_does_not_block(tmp_path: Path) -> None:
    """A leftover PID file from a crashed server must not pin the user."""
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("999999")
    with patch("src.services.upgrade.preflight.is_process_alive", return_value=False), patch(
        "src.services.upgrade.preflight._probe_health", return_value=False
    ):
        assert is_server_running(pid_file, _HEALTH) is False


def test_health_probe_alone_means_running(tmp_path: Path) -> None:
    """A server started without a PID file still blocks."""
    with patch("src.services.upgrade.preflight._probe_health", return_value=True):
        assert is_server_running(tmp_path / "absent.pid", _HEALTH) is True


def test_unreadable_pid_file_is_ignored(tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("not-a-pid")
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is False


def test_oversized_pid_does_not_crash(tmp_path: Path) -> None:
    """An out-of-range numeric PID must not crash; fall through to health probe."""
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("99999999999999999999999999")
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is False


def test_pid_zero_is_not_alive(tmp_path: Path) -> None:
    """PID 0 is a process-group signal, not a valid process signal."""
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("0")
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is False


def test_negative_pid_is_not_alive(tmp_path: Path) -> None:
    """Negative PIDs are process-group signals, not valid process signals."""
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("-1")
    with patch("src.services.upgrade.preflight._probe_health", return_value=False):
        assert is_server_running(pid_file, _HEALTH) is False


# ── platform split: probing must never signal on Windows ──────────────


def test_windows_liveness_probe_never_calls_os_kill() -> None:
    """On Windows ``os.kill(pid, 0)`` calls ``TerminateProcess`` — it would
    hard-kill the very server it is asked to probe (spec §9)."""
    with patch("src.services.upgrade.preflight.os.kill") as killer, patch(
        "src.services.upgrade.preflight._windows_process_alive", return_value=True
    ):
        assert is_process_alive(4321, windows=True) is True
    killer.assert_not_called()


def test_posix_liveness_probe_still_signals_zero() -> None:
    """POSIX behaviour is unchanged: a signal-0 probe is the correct idiom."""
    with patch("src.services.upgrade.preflight.os.kill") as killer:
        assert is_process_alive(4321, windows=False) is True
    killer.assert_called_once_with(4321, 0)


@pytest.mark.parametrize("windows", [False, True])
def test_invalid_pids_are_rejected_before_any_probe(windows: bool) -> None:
    """Non-positive PIDs are rejected on both platforms without touching
    ``os.kill`` or any Win32 call."""
    with patch("src.services.upgrade.preflight.os.kill") as killer, patch(
        "src.services.upgrade.preflight._windows_process_alive"
    ) as win_probe:
        assert is_process_alive(0, windows=windows) is False
        assert is_process_alive(-1, windows=windows) is False
    killer.assert_not_called()
    win_probe.assert_not_called()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError(), False),
        (OverflowError(), False),
    ],
)
def test_posix_probe_error_mapping_is_preserved(exc: Exception, expected: bool) -> None:
    """ProcessLookupError → dead, PermissionError → alive, others → dead."""
    with patch("src.services.upgrade.preflight.os.kill", side_effect=exc):
        assert is_process_alive(4321, windows=False) is expected


def test_windows_probe_falls_back_to_tasklist_when_api_unavailable() -> None:
    """The Win32 branch degrades to ``tasklist``, still without signalling."""
    with patch(
        "src.services.upgrade.preflight._windows_process_alive_via_api", return_value=None
    ), patch(
        "src.services.upgrade.preflight._windows_process_alive_via_tasklist", return_value=True
    ), patch(
        "src.services.upgrade.preflight.os.kill"
    ) as killer:
        assert is_process_alive(4321, windows=True) is True
    killer.assert_not_called()


def test_tasklist_fallback_reads_the_pid_out_of_the_listing() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="adm-agent.exe   4321 Console   1   90,000 K\n", stderr=""
    )
    with patch("src.services.upgrade.preflight.subprocess.run", return_value=completed):
        assert _windows_process_alive_via_tasklist(4321) is True


def test_tasklist_fallback_reports_absent_pid_as_dead() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="INFO: No tasks are running.\n", stderr=""
    )
    with patch("src.services.upgrade.preflight.subprocess.run", return_value=completed):
        assert _windows_process_alive_via_tasklist(4321) is False


def test_tasklist_fallback_survives_a_missing_tasklist_binary() -> None:
    with patch("src.services.upgrade.preflight.subprocess.run", side_effect=OSError("gone")):
        assert _windows_process_alive_via_tasklist(4321) is False


# ── gate ordering ─────────────────────────────────────────────────────


def test_source_checkout_blocks_with_14(tmp_path: Path) -> None:
    block = run_preflight(
        _versioned(tmp_path), frozen=False, pid_file=tmp_path / "p", health_url=_HEALTH
    )
    assert block is not None
    assert block.exit_code == ExitCode.NOT_FROZEN
    assert block.reason == BlockedReason.NOT_FROZEN


def test_legacy_layout_blocks_with_15(tmp_path: Path) -> None:
    (tmp_path / "bin" / "_internal").mkdir(parents=True)
    layout = InstallLayout(root=tmp_path, windows=False)
    with patch("src.services.upgrade.preflight.is_server_running", return_value=False):
        block = run_preflight(
            layout, frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
        )
    assert block.exit_code == ExitCode.LEGACY_LAYOUT
    assert "reinstall" in block.next_action


def test_running_server_blocks_with_10(tmp_path: Path) -> None:
    with patch("src.services.upgrade.preflight.is_server_running", return_value=True):
        block = run_preflight(
            _versioned(tmp_path), frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
        )
    assert block.exit_code == ExitCode.SERVER_RUNNING
    assert block.next_action == "stop_server_then_retry"


def test_clean_install_passes(tmp_path: Path) -> None:
    with patch("src.services.upgrade.preflight.is_server_running", return_value=False):
        assert (
            run_preflight(
                _versioned(tmp_path), frozen=True, pid_file=tmp_path / "p", health_url=_HEALTH
            )
            is None
        )
