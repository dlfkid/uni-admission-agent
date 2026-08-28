"""Tests for the upgrade transaction — spec §5, §6.3."""

import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.transaction import (
    check_for_updates,
    default_post_check,
    perform_upgrade,
    rollback,
)
from src.services.upgrade.types import (
    BlockedReason,
    ChecksumMismatchError,
    ExitCode,
    StagedBinaryError,
    UpgradeError,
)

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
        raise ChecksumMismatchError("Artifact checksum mismatch: expected a, got b")

    with patch("src.services.upgrade.transaction.verify_artifact", side_effect=boom):
        result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert result.blocked_reason == BlockedReason.CHECKSUM_MISMATCH
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_download_failure_maps_to_unexpected_not_checksum_mismatch(tmp_path: Path) -> None:
    """A network blip is not a corrupt-artifact verdict — these are API values."""
    layout = _install(tmp_path)

    def boom_downloader(asset: dict, dest_dir: Path) -> Path:
        raise UpgradeError("Failed to download adm-agent-v0.11.0-linux-x86_64.tar.gz: reset")

    result = _run(layout, tmp_path, downloader=boom_downloader)
    assert result.exit_code == ExitCode.UNEXPECTED
    assert result.blocked_reason == BlockedReason.UNEXPECTED
    assert layout.active_version() == "v0.10.0"
    _assert_user_data_intact(tmp_path)


def test_extract_failure_maps_to_unexpected(tmp_path: Path) -> None:
    """Archive corruption is neither a checksum nor a staged-binary verdict."""
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction.safe_extract",
        side_effect=UpgradeError("Failed to extract artifact.tar.gz: bad gzip"),
    ):
        result = _run(layout, tmp_path)
    assert result.exit_code == ExitCode.UNEXPECTED
    assert result.blocked_reason == BlockedReason.UNEXPECTED
    assert layout.active_version() == "v0.10.0"
    _assert_user_data_intact(tmp_path)


def test_staged_binary_message_mentioning_size_is_still_classified_correctly(
    tmp_path: Path,
) -> None:
    """Dispatch is by exception type now, not message text.

    ``verify_staged_binary`` interpolates the candidate binary's own stdout
    into its error; a binary that happens to print "size" or "checksum"
    must not flip this into a checksum-mismatch verdict.
    """
    layout = _install(tmp_path)
    result = _run(
        layout,
        tmp_path,
        staged_binary_error=StagedBinaryError(
            "Staged binary self-check failed (exit 1): reported a size and "
            "checksum mismatch in its own internal diagnostics"
        ),
    )
    assert result.blocked_reason == BlockedReason.STAGED_BINARY_FAILED
    assert result.exit_code == ExitCode.VERIFICATION_FAILED
    assert layout.active_version() == "v0.10.0"


def test_staged_binary_failure_leaves_the_pointer_untouched(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    result = _run(
        layout,
        tmp_path,
        staged_binary_error=StagedBinaryError(
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


def test_post_check_failure_reports_empty_previous_version_when_none_existed(
    tmp_path: Path,
) -> None:
    """`previous_version` is API; it must not lie about there being one."""
    layout = InstallLayout(root=tmp_path, windows=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret\n")

    def failing_post_check(layout, migrate):
        raise UpgradeError("boom")

    result = _run(
        layout, tmp_path, current_version="v0.0.0-dev", post_check=failing_post_check
    )
    assert result.previous_version == ""


# ── force-reinstalling the active version (Critical 1) ─────────────────


def test_force_reinstall_of_active_version_survives_post_check_failure(
    tmp_path: Path,
) -> None:
    """`--force` onto the already-active tag must never delete it in place.

    Reproduces the scenario the reviewer flagged live: force=True with
    current == latest means previous == new_version == target. A naive
    rmtree-then-move, followed by an unconditional rollback delete, wiped
    the version directory out from under the still-active pointer.
    """
    layout = _install(tmp_path, version="v0.11.0")
    before = (layout.version_dir("v0.11.0") / "adm-agent").read_text()

    def failing_post_check(layout, migrate):
        raise UpgradeError("boom")

    result = _run(
        layout,
        tmp_path,
        current_version="v0.11.0",
        force=True,
        post_check=failing_post_check,
    )
    assert result.exit_code == ExitCode.POST_CHECK_FAILED
    # There is nothing distinct to roll back to — the same tag was already
    # active — so this must not be misreported as a successful rollback.
    assert result.action_taken == "blocked"
    assert result.previous_version == ""
    assert layout.active_version() == "v0.11.0"
    # The version directory must still exist, on disk, with real content —
    # not deleted out from under the pointer that still names it.
    version_dir = layout.version_dir("v0.11.0")
    assert version_dir.is_dir()
    assert (version_dir / "adm-agent").exists()
    _assert_user_data_intact(tmp_path)
    assert before == "old"  # sanity: this was the pre-upgrade content


def test_force_reinstall_of_active_version_succeeds_and_replaces_content(
    tmp_path: Path,
) -> None:
    """A successful same-version --force reinstall still swaps the bits in."""
    layout = _install(tmp_path, version="v0.11.0")
    result = _run(layout, tmp_path, current_version="v0.11.0", force=True)
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
    # Content actually replaced (the fake downloader writes the tag as the
    # binary's content), proving the swap — not a no-op — took place.
    assert (layout.version_dir("v0.11.0") / "adm-agent").read_text() == "v0.11.0"
    _assert_user_data_intact(tmp_path)


# ── settle-step retention when the former pointer is unknown (Minor 3) ──


def test_prune_falls_back_to_newest_other_version_when_previous_is_unknown(
    tmp_path: Path,
) -> None:
    """A missing/corrupt pointer must not cause every other version to be
    pruned away — the newest surviving one should still be kept."""
    layout = _install(tmp_path, version="v0.9.0")
    # Simulate a second, older stray install alongside the active one, then
    # break the pointer so `active_version()` reports None.
    stray = layout.version_dir("v0.8.0")
    (stray / "_internal").mkdir(parents=True)
    (stray / "adm-agent").write_text("stray")
    layout.pointer_path.unlink()

    result = _run(layout, tmp_path, current_version="v0.0.0-dev")
    assert result.exit_code == ExitCode.OK
    assert layout.active_version() == "v0.11.0"
    # The newest other installed version (v0.9.0) is kept even though the
    # broken pointer meant `previous` could not be determined directly.
    assert layout.version_dir("v0.9.0").exists()


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


def test_rollback_never_rolls_forward_onto_a_retained_newer_version(
    tmp_path: Path,
) -> None:
    """A second rollback() must not undo the first (Minor 1).

    After rolling back to v0.10.0, v0.11.0 is deliberately retained (so the
    user can move forward again without re-downloading). A naive "pick any
    other installed version" would roll *forward* onto it here.
    """
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    rollback(layout)
    assert layout.active_version() == "v0.10.0"

    with pytest.raises(UpgradeError, match="no previous version"):
        rollback(layout)
    assert layout.active_version() == "v0.10.0"


# ── default_post_check (spec §6.3 — Important 4) ────────────────────────


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_default_post_check_warns_on_check_failure_but_does_not_raise(
    tmp_path: Path,
) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    responses = [_completed(1, stdout="Chromium is not installed"), _completed(0)]
    with patch(
        "src.services.upgrade.transaction.subprocess.run", side_effect=responses
    ):
        warnings = default_post_check(layout, migrate=True)
    assert any("Chromium is not installed" in w for w in warnings)


def test_default_post_check_migration_failure_recovered_by_repair_warns(
    tmp_path: Path,
) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    responses = [_completed(0), _completed(1, stderr="locked"), _completed(0)]
    with patch(
        "src.services.upgrade.transaction.subprocess.run", side_effect=responses
    ):
        warnings = default_post_check(layout, migrate=True)
    assert any("auto-repair recovered it" in w for w in warnings)


def test_default_post_check_migration_and_repair_both_fail_raises(
    tmp_path: Path,
) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    responses = [_completed(0), _completed(1, stderr="disk full"), _completed(1, stderr="nope")]
    with patch(
        "src.services.upgrade.transaction.subprocess.run", side_effect=responses
    ):
        with pytest.raises(UpgradeError, match="auto-repair could not recover"):
            default_post_check(layout, migrate=True)


def test_default_post_check_migrate_false_skips_migration_entirely(
    tmp_path: Path,
) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    with patch(
        "src.services.upgrade.transaction.subprocess.run", return_value=_completed(0)
    ) as mock_run:
        warnings = default_post_check(layout, migrate=False)
    assert warnings == []
    assert mock_run.call_count == 1  # only "check", never db-migrate/repair


def test_default_post_check_non_backend_artifact_short_circuits(tmp_path: Path) -> None:
    """The client artifact has neither `check` nor `db-migrate`."""
    layout = InstallLayout(root=tmp_path, artifact_name="adm-agent-client", windows=False)
    with patch("src.services.upgrade.transaction.subprocess.run") as mock_run:
        warnings = default_post_check(layout, migrate=True)
    assert warnings == []
    mock_run.assert_not_called()


def test_default_post_check_hung_check_raises_upgrade_error(tmp_path: Path) -> None:
    """A timed-out `check` must surface as UpgradeError, not TimeoutExpired
    (Important 2): an untyped exception here would escape perform_upgrade's
    rollback handling entirely."""
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    with patch(
        "src.services.upgrade.transaction.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="adm-agent check", timeout=600),
    ):
        with pytest.raises(UpgradeError):
            default_post_check(layout, migrate=True)


def test_default_post_check_missing_binary_raises_upgrade_error(tmp_path: Path) -> None:
    """A missing/non-executable entry point raises OSError from subprocess;
    that must also come out as UpgradeError (Important 2)."""
    layout = InstallLayout(root=tmp_path, windows=False)
    layout.bin_dir.mkdir(parents=True)
    responses = [_completed(0), OSError("no such file or directory")]
    with patch(
        "src.services.upgrade.transaction.subprocess.run", side_effect=responses
    ):
        with pytest.raises(UpgradeError):
            default_post_check(layout, migrate=True)


def test_non_upgrade_error_from_post_check_still_triggers_rollback(
    tmp_path: Path,
) -> None:
    """Important 2, exercised through perform_upgrade: even if a caller's
    post_check leaks a non-UpgradeError, spec §6.3's rollback must still
    happen rather than the exception propagating out of perform_upgrade."""
    layout = _install(tmp_path)

    def leaky_post_check(layout, migrate):
        raise subprocess.TimeoutExpired(cmd="adm-agent db-migrate", timeout=1800)

    result = _run(layout, tmp_path, post_check=leaky_post_check)
    assert result.exit_code == ExitCode.POST_CHECK_FAILED
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()


# ── check_for_updates (Important 4) ─────────────────────────────────────


def test_check_for_updates_reports_newer_version_available() -> None:
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release", return_value=_release()
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value="v0.10.0"
    ), patch(
        "src.services.upgrade.transaction.get_platform_info",
        return_value=("linux", "x86_64"),
    ):
        result = check_for_updates()
    assert result.is_newer is True
    assert result.latest_version == "v0.11.0"
    assert result.asset_available is True
    assert result.exit_code == ExitCode.OK


def test_check_for_updates_reports_up_to_date() -> None:
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release", return_value=_release()
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value="v0.11.0"
    ), patch(
        "src.services.upgrade.transaction.get_platform_info",
        return_value=("linux", "x86_64"),
    ):
        result = check_for_updates()
    assert result.is_newer is False
    assert result.exit_code == ExitCode.OK


def test_check_for_updates_blocks_on_unparseable_version() -> None:
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        return_value=_release(tag="latest"),
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value="v0.10.0"
    ):
        result = check_for_updates()
    assert result.blocked_reason == BlockedReason.UNPARSEABLE_VERSION
    assert result.exit_code == ExitCode.VERIFICATION_FAILED


def test_check_for_updates_reports_asset_unavailable_for_platform() -> None:
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release", return_value=_release()
    ), patch(
        "src.services.upgrade.transaction.get_current_version", return_value="v0.10.0"
    ), patch(
        "src.services.upgrade.transaction.get_platform_info",
        return_value=("linux", "riscv64"),
    ):
        result = check_for_updates()
    assert result.asset_available is False
    assert result.is_newer is True  # still reported even though unusable


def test_check_for_updates_blocks_on_fetch_failure() -> None:
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        side_effect=UpgradeError("network unreachable"),
    ):
        result = check_for_updates()
    assert result.blocked_reason == BlockedReason.UNEXPECTED
    assert result.exit_code == ExitCode.UNEXPECTED


# ── preflight wiring end to end (Important 4) ───────────────────────────


def test_perform_upgrade_blocks_with_14_when_not_frozen(tmp_path: Path) -> None:
    """Exit code 14 must reach the caller without any network resolution."""
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        side_effect=AssertionError("must not resolve a release before the frozen gate"),
    ):
        result = perform_upgrade(
            layout,
            frozen=False,
            pid_file=tmp_path / "server.pid",
            health_url=_HEALTH,
        )
    assert result.exit_code == ExitCode.NOT_FROZEN
    assert result.blocked_reason == BlockedReason.NOT_FROZEN
    assert layout.active_version() == "v0.10.0"


def test_perform_upgrade_blocks_with_10_when_server_running(tmp_path: Path) -> None:
    """Exit code 10 must reach the caller without any network resolution."""
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.preflight.is_server_running", return_value=True
    ), patch(
        "src.services.upgrade.transaction.fetch_latest_release",
        side_effect=AssertionError("must not resolve a release while the server is up"),
    ):
        result = perform_upgrade(
            layout,
            frozen=True,
            pid_file=tmp_path / "server.pid",
            health_url=_HEALTH,
        )
    assert result.exit_code == ExitCode.SERVER_RUNNING
    assert result.blocked_reason == BlockedReason.SERVER_RUNNING
    assert layout.active_version() == "v0.10.0"
