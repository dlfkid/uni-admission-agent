"""Tests for the upgrade/version CLI contract — spec §7."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from src.cmd.cli import _find_client_argv, app
from src.services.upgrade.layout import InstallLayout
from src.services.upgrade import default_client_pid_file
from src.services.upgrade.types import BlockedReason, ExitCode, UpgradeResult

runner = CliRunner()


# ── version --json ────────────────────────────────────────────────────


def test_version_json_is_machine_readable() -> None:
    """The staged self-check parses this; its shape is API."""
    with patch("src.cmd.cli.get_current_version", return_value="v0.11.0"), patch(
        "src.cmd.cli.get_platform_info", return_value=("macos", "arm64")
    ):
        result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "v0.11.0"
    assert payload["platform"] == "macos-arm64"


def test_version_without_json_stays_human_readable() -> None:
    with patch("src.cmd.cli.get_current_version", return_value="v0.11.0"):
        result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "v0.11.0"


# ── upgrade exit codes ────────────────────────────────────────────────


def _result(**kwargs) -> UpgradeResult:
    base = UpgradeResult(current_version="v0.10.0", latest_version="v0.11.0")
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def test_upgrade_success_exits_zero() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0


def test_upgrade_blocked_by_running_server_exits_10() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.SERVER_RUNNING,
            next_action="stop_server_then_retry",
            exit_code=int(ExitCode.SERVER_RUNNING),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 10


def test_upgrade_legacy_layout_exits_15() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 15


def test_upgrade_rolled_back_exits_13() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(
            action_taken="rolled_back",
            blocked_reason=BlockedReason.POST_CHECK_FAILED,
            exit_code=int(ExitCode.POST_CHECK_FAILED),
        ),
    ):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 13


def test_upgrade_json_emits_the_documented_fields() -> None:
    with patch(
        "src.cmd.cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ):
        result = runner.invoke(app, ["upgrade", "--json"])
    payload = json.loads(result.stdout)
    for key in (
        "current_version",
        "latest_version",
        "is_newer",
        "asset_available",
        "checksum_verified",
        "action_taken",
        "active_version",
        "previous_version",
        "blocked_reason",
        "next_action",
        "warnings",
    ):
        assert key in payload
    assert "exit_code" not in payload  # internal only


def test_upgrade_check_json_does_not_upgrade() -> None:
    with patch(
        "src.cmd.cli.check_for_updates", return_value=_result(is_newer=True)
    ) as check, patch("src.cmd.cli.perform_upgrade") as perform:
        result = runner.invoke(app, ["upgrade", "--check", "--json"])
    assert result.exit_code == 0
    assert check.called
    assert not perform.called


def test_upgrade_rollback_invokes_rollback_only() -> None:
    with patch(
        "src.cmd.cli.rollback",
        return_value=_result(action_taken="rolled_back", active_version="v0.10.0"),
    ) as rb, patch("src.cmd.cli.perform_upgrade") as perform:
        result = runner.invoke(app, ["upgrade", "--rollback"])
    assert result.exit_code == 0
    assert rb.called
    assert not perform.called


def test_upgrade_check_reports_update_available_when_newer() -> None:
    """A --check that finds a newer version must say so, not "already latest"."""
    with patch(
        "src.cmd.cli.check_for_updates",
        return_value=_result(is_newer=True),
    ):
        result = runner.invoke(app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "Update available" in result.stdout
    assert "Already on latest version" not in result.stdout


def test_upgrade_check_reports_already_latest_when_not_newer() -> None:
    with patch(
        "src.cmd.cli.check_for_updates",
        return_value=_result(is_newer=False),
    ):
        result = runner.invoke(app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "Already on latest version" in result.stdout


def test_upgrade_unexpected_error_emits_json() -> None:
    """The agent is the primary caller; an unexpected failure must stay parseable."""
    with patch("src.cmd.cli.perform_upgrade", side_effect=RuntimeError("boom")):
        result = runner.invoke(app, ["upgrade", "--json"])
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    payload = json.loads(result.stdout)
    assert payload["blocked_reason"] == "unexpected"
    assert payload["action_taken"] == "blocked"
    assert "boom" in payload["warnings"][0]


# ── client CLI parity (spec §3.6) ─────────────────────────────────────


def test_client_upgrade_uses_the_client_layout_and_artifact() -> None:
    """The client must not upgrade itself using the backend's root.

    Also pins the client-specific pid_file/health_url pairing (spec §3.6): a
    running *server* must not block a client upgrade, and the client has no
    health endpoint. A future edit that swapped in the backend's
    default_pid_file()/default_health_url() would cross-block silently
    without this guard.
    """
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ) as perform, patch("src.cmd.client_cli.default_client_layout") as layout:
        result = CliRunner().invoke(client_app, ["upgrade"])

    assert result.exit_code == 0
    assert layout.called
    assert perform.call_args.kwargs["artifact_name"] == "adm-agent-client"
    assert perform.call_args.kwargs["migrate"] is False
    assert perform.call_args.kwargs["pid_file"] == default_client_pid_file()
    assert perform.call_args.kwargs["health_url"] == ""


def test_client_upgrade_verbose_enables_logging() -> None:
    """--verbose is the only debugging lever an agent has for a failed client upgrade."""
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ), patch("src.cmd.client_cli.default_client_layout"), patch(
        "src.cmd.client_cli._configure_client_logging"
    ) as configure_logging:
        result = CliRunner().invoke(client_app, ["upgrade", "--verbose"])
    assert result.exit_code == 0
    assert configure_logging.called


def test_client_upgrade_without_verbose_leaves_logging_untouched() -> None:
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(action_taken="upgraded", active_version="v0.11.0"),
    ), patch("src.cmd.client_cli.default_client_layout"), patch(
        "src.cmd.client_cli._configure_client_logging"
    ) as configure_logging:
        result = CliRunner().invoke(client_app, ["upgrade"])
    assert result.exit_code == 0
    assert not configure_logging.called


def test_client_upgrade_blocked_propagates_the_exit_code() -> None:
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.perform_upgrade",
        return_value=_result(
            action_taken="blocked",
            blocked_reason=BlockedReason.LEGACY_LAYOUT,
            exit_code=int(ExitCode.LEGACY_LAYOUT),
        ),
    ), patch("src.cmd.client_cli.default_client_layout"):
        result = CliRunner().invoke(client_app, ["upgrade"])
    assert result.exit_code == 15


def test_client_upgrade_unexpected_error_emits_json() -> None:
    """Same contract as the backend: an agent driving --json must never get prose."""
    from src.cmd.client_cli import app as client_app

    with patch("src.cmd.client_cli.perform_upgrade", side_effect=RuntimeError("boom")):
        result = CliRunner().invoke(client_app, ["upgrade", "--json"])
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    payload = json.loads(result.stdout)
    assert payload["blocked_reason"] == "unexpected"
    assert payload["action_taken"] == "blocked"
    assert "boom" in payload["warnings"][0]


def test_client_upgrade_check_reports_update_available_when_newer() -> None:
    """Regression guard: the client's prose path must not resurrect the

    string-comparison defect that told every 0.8.x/0.9.x user they were
    current when a newer version existed.
    """
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.check_for_updates",
        return_value=_result(is_newer=True),
    ):
        result = CliRunner().invoke(client_app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "Update available" in result.stdout
    assert "Already on latest version" not in result.stdout


def test_client_upgrade_check_reports_already_latest_when_not_newer() -> None:
    from src.cmd.client_cli import app as client_app

    with patch(
        "src.cmd.client_cli.check_for_updates",
        return_value=_result(is_newer=False),
    ):
        result = CliRunner().invoke(client_app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "Already on latest version" in result.stdout


def test_client_version_json() -> None:
    from src.cmd.client_cli import app as client_app

    with patch("src.cmd.client_cli.get_current_version", return_value="v0.11.0"), patch(
        "src.cmd.client_cli.get_platform_info", return_value=("linux", "x86_64")
    ):
        result = CliRunner().invoke(client_app, ["version", "--json"])
    assert json.loads(result.stdout)["version"] == "v0.11.0"


# ── `up` locates the client under its own layout (spec §3.6) ──────────


def _frozen_at(monkeypatch, exe: Path) -> None:
    """Pretend this process is the frozen backend running from *exe*."""
    monkeypatch.setattr("src.cmd.cli.sys.frozen", True, raising=False)
    monkeypatch.setattr("src.cmd.cli.sys.executable", str(exe), raising=False)


def test_up_finds_the_client_via_its_own_install_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the versioned layout ``sys.executable``'s parent is
    ``versions/<v>/``, which never contains the client — it lives at
    ``~/.adm-agent-client/bin/`` (spec §3.6)."""
    backend_version_dir = tmp_path / ".uni-agent" / "versions" / "v0.11.0"
    backend_version_dir.mkdir(parents=True)
    _frozen_at(monkeypatch, backend_version_dir / "adm-agent")

    layout = InstallLayout(
        root=tmp_path / ".adm-agent-client",
        artifact_name="adm-agent-client",
        windows=False,
    )
    layout.bin_dir.mkdir(parents=True)
    layout.entrypoint_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr("src.cmd.cli.default_client_layout", lambda: layout)

    assert _find_client_argv() == [str(layout.entrypoint_path)]


def test_up_falls_back_to_the_client_current_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install whose ``bin/`` shim has not been written yet is still found
    through ``current/``."""
    backend_version_dir = tmp_path / ".uni-agent" / "versions" / "v0.11.0"
    backend_version_dir.mkdir(parents=True)
    _frozen_at(monkeypatch, backend_version_dir / "adm-agent")

    root = tmp_path / ".adm-agent-client"
    layout = InstallLayout(root=root, artifact_name="adm-agent-client", windows=False)
    version_dir = layout.version_dir("v0.11.0")
    version_dir.mkdir(parents=True)
    (version_dir / "adm-agent-client").write_text("#!/bin/sh\n")
    layout.activate("v0.11.0")
    monkeypatch.setattr("src.cmd.cli.default_client_layout", lambda: layout)

    assert _find_client_argv() == [str(root / "current" / "adm-agent-client")]


def test_up_reports_every_location_it_looked_at_when_the_client_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_version_dir = tmp_path / ".uni-agent" / "versions" / "v0.11.0"
    backend_version_dir.mkdir(parents=True)
    _frozen_at(monkeypatch, backend_version_dir / "adm-agent")

    layout = InstallLayout(
        root=tmp_path / ".adm-agent-client",
        artifact_name="adm-agent-client",
        windows=False,
    )
    monkeypatch.setattr("src.cmd.cli.default_client_layout", lambda: layout)

    with pytest.raises(FileNotFoundError) as excinfo:
        _find_client_argv()
    assert ".adm-agent-client" in str(excinfo.value)


def test_up_wraps_the_windows_cmd_shim_in_the_command_processor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``.cmd`` is not a PE image; ``CreateProcess`` cannot exec it."""
    backend_version_dir = tmp_path / ".uni-agent" / "versions" / "v0.11.0"
    backend_version_dir.mkdir(parents=True)
    _frozen_at(monkeypatch, backend_version_dir / "adm-agent.exe")

    layout = InstallLayout(
        root=tmp_path / ".adm-agent-client",
        artifact_name="adm-agent-client",
        windows=True,
    )
    layout.bin_dir.mkdir(parents=True)
    layout.entrypoint_path.write_text("@echo off\n")
    monkeypatch.setattr("src.cmd.cli.default_client_layout", lambda: layout)

    assert _find_client_argv() == ["cmd.exe", "/c", str(layout.entrypoint_path)]
