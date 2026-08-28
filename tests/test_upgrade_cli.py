"""Tests for the upgrade/version CLI contract — spec §7."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd.cli import app
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


# ── client CLI parity (spec §3.6) ─────────────────────────────────────


def test_client_upgrade_uses_the_client_layout_and_artifact() -> None:
    """The client must not upgrade itself using the backend's root."""
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


def test_client_version_json() -> None:
    from src.cmd.client_cli import app as client_app

    with patch("src.cmd.client_cli.get_current_version", return_value="v0.11.0"), patch(
        "src.cmd.client_cli.get_platform_info", return_value=("linux", "x86_64")
    ):
        result = CliRunner().invoke(client_app, ["version", "--json"])
    assert json.loads(result.stdout)["version"] == "v0.11.0"
