"""Tests for the upgrade transaction — spec §5, §6.3."""

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.transaction import perform_upgrade, rollback
from src.services.upgrade.types import BlockedReason, ExitCode, UpgradeError

_HEALTH = "http://127.0.0.1:8910/health"


def _install(tmp_path: Path, version: str = "v0.10.0") -> InstallLayout:
    layout = InstallLayout(root=tmp_path, windows=False)
    vdir = layout.version_dir(version)
    (vdir / "_internal").mkdir(parents=True)
    (vdir / "adm-agent").write_text("old")
    layout.activate(version)
    layout.ensure_entrypoint()
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret\n")
    (tmp_path / "admission.db").write_bytes(b"SQLite format 3\x00")
    return layout


def _release(tag: str = "v0.11.0") -> dict:
    # "size" is omitted: the fake downloader below fabricates a real tar.gz
    # whose byte count cannot be predicted here, and `resolve_expected_digest`
    # is stubbed to None throughout — so these tests exercise the same
    # "no SHA256SUMS published" degrade-to-warning path that real pre-gate
    # releases hit, rather than asserting on an artificial size.
    return {
        "tag_name": tag,
        "html_url": "https://example.invalid/r",
        "assets": [
            {
                "name": f"adm-agent-{tag}-linux-x86_64.tar.gz",
                "browser_download_url": "https://example.invalid/a.tar.gz",
                "size": None,
            },
            {
                "name": "SHA256SUMS",
                "browser_download_url": "https://example.invalid/SHA256SUMS",
                "size": None,
            },
        ],
    }


def _fake_downloader(tmp_path: Path, tag: str = "v0.11.0"):
    """Produce an archive whose payload is a plausible new version."""

    def download(asset: dict, dest_dir: Path) -> Path:
        payload = tmp_path / f"payload-{tag}" / "adm-agent"
        (payload / "_internal").mkdir(parents=True, exist_ok=True)
        (payload / "adm-agent").write_text(tag)
        archive = dest_dir / "artifact.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="adm-agent")
        return archive

    return download


def _run(
    layout: InstallLayout,
    tmp_path: Path,
    *,
    release: dict | None = None,
    current_version: str = "v0.10.0",
    platform_info: tuple[str, str] = ("linux", "x86_64"),
    staged_binary_error: Exception | None = None,
    **kwargs,
):
    """Drive perform_upgrade with every external dependency stubbed.

    Environment overrides go through the keyword arguments here — patching
    the same targets *around* this helper would be silently overridden by the
    patches below. ``staged_binary_error`` is the escape hatch for
    ``verify_staged_binary`` specifically, since this helper always stubs it
    to succeed (the fake downloader's payload is not a real executable).
    """
    defaults = dict(
        artifact_name="adm-agent",
        frozen=True,
        pid_file=tmp_path / "server.pid",
        health_url=_HEALTH,
        downloader=_fake_downloader(tmp_path),
        post_check=lambda layout, migrate: [],
    )
    defaults.update(kwargs)
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        return_value=release if release is not None else _release(),
    ), patch(
        "src.services.upgrade.transaction.get_platform_info", return_value=platform_info
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value=current_version
    ), patch(
        "src.services.upgrade.transaction.verify_staged_binary",
        side_effect=staged_binary_error,
        return_value=None,
    ), patch(
        "src.services.upgrade.transaction.resolve_expected_digest", return_value=None
    ), patch(
        "src.services.upgrade.preflight.is_server_running", return_value=False
    ):
        return perform_upgrade(layout, **defaults)


def _assert_user_data_intact(tmp_path: Path) -> None:
    assert (tmp_path / ".env").read_text() == "DEEPSEEK_API_KEY=secret\n"
    assert (tmp_path / "admission.db").read_bytes() == b"SQLite format 3\x00"


# ── happy path ────────────────────────────────────────────────────────


def test_successful_upgrade_activates_and_retains_previous(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
    assert layout.version_dir("v0.10.0").exists()  # last-good retained
    _assert_user_data_intact(tmp_path)


def test_no_upgrade_when_already_current(tmp_path: Path) -> None:
    layout = _install(tmp_path, version="v0.11.0")
    result = _run(layout, tmp_path, current_version="v0.11.0")
    assert result.action_taken == "none"
    assert result.is_newer is False
    assert result.exit_code == ExitCode.OK


# ── pre-activation failures leave nothing changed ─────────────────────


def test_checksum_failure_leaves_the_pointer_untouched(tmp_path: Path) -> None:
    layout = _install(tmp_path)

    def boom(path, expected_digest, expected_size):
        raise UpgradeError("Artifact checksum mismatch: expected a, got b")

    with patch("src.services.upgrade.transaction.verify_artifact", side_effect=boom):
        result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.CHECKSUM_MISMATCH
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_staged_binary_failure_leaves_the_pointer_untouched(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(
        layout,
        tmp_path,
        staged_binary_error=UpgradeError(
            "Staged binary reports v0.9.0, expected v0.11.0"
        ),
    )
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.STAGED_BINARY_FAILED
    assert layout.active_version() == "v0.10.0"
    _assert_user_data_intact(tmp_path)


def test_staging_is_cleaned_up_after_failure(tmp_path: Path) -> None:
    """A failed attempt must not leave half-extracted payloads behind."""
    layout = _install(tmp_path)
    _run(layout, tmp_path, staged_binary_error=UpgradeError("nope"))
    leftovers = list(layout.staging_dir.iterdir()) if layout.staging_dir.exists() else []
    assert leftovers == []


def test_missing_platform_asset_blocks_with_11(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path, platform_info=("linux", "riscv64"))
    assert result.exit_code == ExitCode.NO_ASSET_FOR_PLATFORM
    assert layout.active_version() == "v0.10.0"


def test_unparseable_version_blocks_with_12(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(layout, tmp_path, release=_release(tag="latest"))
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.UNPARSEABLE_VERSION
    assert layout.active_version() == "v0.10.0"


# ── post-activation asymmetry (spec §6.3) ─────────────────────────────


def test_migration_failure_rolls_back_and_deletes_the_bad_version(tmp_path: Path) -> None:
    layout = _install(tmp_path)

    def failing_post_check(layout, migrate):
        raise UpgradeError("migration failed and repair --auto could not fix it")

    result = _run(layout, tmp_path, post_check=failing_post_check)
    assert result.exit_code == ExitCode.POST_CHECK_FAILED
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    # An automatically rolled-back version is proven bad; it is removed.
    assert not layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_post_check_failure_with_no_previous_version_blocks_without_rollback(
    tmp_path: Path,
) -> None:
    """A first-ever install has nothing to roll back to."""
    layout = InstallLayout(root=tmp_path, windows=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret\n")
    (tmp_path / "admission.db").write_bytes(b"SQLite format 3\x00")

    def failing_post_check(layout, migrate):
        raise UpgradeError("migration failed and repair --auto could not fix it")

    result = _run(
        layout,
        tmp_path,
        current_version="v0.0.0-dev",
        post_check=failing_post_check,
    )
    assert result.exit_code == ExitCode.POST_CHECK_FAILED
    assert result.action_taken == "blocked"
    assert result.blocked_reason == BlockedReason.POST_CHECK_FAILED
    # Nothing to roll back to: the new version stays active.
    assert layout.active_version() == "v0.11.0"
    assert any("no previous version" in w.lower() for w in result.warnings)
    _assert_user_data_intact(tmp_path)


def test_check_warnings_do_not_roll_back(tmp_path: Path) -> None:
    """A missing Chromium is an environment problem; rolling back won't fix it."""
    layout = _install(tmp_path)
    result = _run(
        layout,
        tmp_path,
        post_check=lambda layout, migrate: ["Chromium is not installed"],
    )
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
    assert "Chromium is not installed" in result.warnings


# ── manual rollback (spec §3.2 retention) ─────────────────────────────


def test_manual_rollback_keeps_the_version_rolled_back_from(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    assert layout.active_version() == "v0.11.0"

    result = rollback(layout)
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    # Retained so the user can move forward again without re-downloading.
    assert layout.version_dir("v0.11.0").exists()


def test_rollback_without_a_previous_version_errors(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with pytest.raises(UpgradeError, match="no previous version"):
        rollback(layout)
