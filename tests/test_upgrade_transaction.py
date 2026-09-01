"""Tests for the upgrade transaction — spec §5, §6.3."""

import contextlib
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.locking import UpgradeInProgressError, install_lock
from src.services.upgrade.transaction import (
    _restore_pointer,
    check_for_updates,
    default_post_check,
    perform_upgrade,
    rollback,
    sweep_stale_staging,
)
from src.services.upgrade.types import (
    BlockedReason,
    ChecksumMismatchError,
    ExitCode,
    StagedBinaryError,
    UpgradeError,
    UpgradeResult,
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
    defaults = {
        "artifact_name": "adm-agent",
        "frozen": True,
        "pid_file": tmp_path / "server.pid",
        "health_url": _HEALTH,
        "downloader": _fake_downloader(tmp_path),
        "post_check": lambda layout, migrate: [],
    }
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


# ── install-root mutual exclusion ─────────────────────────────────────


def test_a_concurrent_upgrade_is_refused_before_anything_is_swept(
    tmp_path: Path,
) -> None:
    """The sweep deletes every staging tree it finds, so a second upgrade
    must be turned away before it reaches that point rather than deleting
    the first one's in-flight download."""
    layout = _install(tmp_path)
    inflight = layout.staging_dir / "tmp-inflight"
    inflight.mkdir(parents=True)

    with install_lock(layout.root):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.UPGRADE_IN_PROGRESS
    assert result.blocked_reason == BlockedReason.UPGRADE_IN_PROGRESS
    assert result.next_action == "wait_for_other_upgrade_then_retry"
    assert inflight.exists(), "the other run's staging tree was swept"
    assert layout.active_version() == "v0.10.0"
    _assert_user_data_intact(tmp_path)


def test_state_is_re_read_inside_the_lock_not_before_it(tmp_path: Path) -> None:
    """Resolving the active version before acquiring the lock leaves this run
    carrying a stale ``previous``: another upgrade can finish in between, and
    this one would then displace a version that is now active and roll back
    somebody else's success on a post-check failure."""
    layout = _install(tmp_path)
    # The version a concurrent upgrade is about to activate.
    other = layout.version_dir("v0.11.0")
    (other / "_internal").mkdir(parents=True)
    (other / "adm-agent").write_text("new")

    real_lock = install_lock

    @contextlib.contextmanager
    def lock_then_someone_else_finishes(root):
        with real_lock(root) as handle:
            # Exactly the interleaving the lock is supposed to serialise: the
            # other upgrade completed while this one waited to acquire.
            layout.activate("v0.11.0")
            yield handle

    with patch(
        "src.services.upgrade.transaction.install_lock",
        lock_then_someone_else_finishes,
    ):
        result = _run(layout, tmp_path)

    # Seen from inside the lock, the requested version is already active.
    assert result.action_taken == "none"
    assert result.next_action == "reinstall_to_replace_active_version"
    assert layout.active_version() == "v0.11.0"
    _assert_user_data_intact(tmp_path)


def _lock_is_free(layout: InstallLayout) -> bool:
    """Releasability, not file absence — the lock file is kept by design."""
    try:
        with install_lock(layout.root):
            return True
    except UpgradeInProgressError:
        return False


def test_the_lock_is_released_after_a_successful_upgrade(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    assert _run(layout, tmp_path).action_taken == "upgraded"
    assert _lock_is_free(layout)


def test_the_lock_is_released_after_a_failed_upgrade(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction._place_new_version",
        side_effect=OSError("permission denied"),
    ):
        _run(layout, tmp_path)
    assert _lock_is_free(layout)


def test_rollback_contention_returns_exit_16_not_a_generic_failure(
    tmp_path: Path,
) -> None:
    """The forward path maps lock contention to upgrade_in_progress/16; letting
    rollback leak the exception would have the CLI's broad handler report a
    generic exit 1, breaking the stable routing contract §7 documents."""
    layout = _install(tmp_path)
    with install_lock(layout.root):
        result = rollback(layout, migrate=False, post_check=lambda layout, migrate: [])
    assert result.exit_code == ExitCode.UPGRADE_IN_PROGRESS
    assert result.blocked_reason == BlockedReason.UPGRADE_IN_PROGRESS
    assert result.next_action == "wait_for_other_upgrade_then_retry"


# ── activation-phase compensation (spec §5 step 6) ────────────────────


def test_entrypoint_failure_after_the_switch_restores_the_previous_pointer(
    tmp_path: Path,
) -> None:
    """The pointer switch is atomic, but the entry-point rewrite that follows
    it can still fail on permissions, a full disk or an AV lock. When it does,
    the install must end up exactly as it started rather than stranded on a
    version whose command was never wired up."""
    layout = _install(tmp_path)
    calls = {"n": 0}
    real_ensure = InstallLayout.ensure_entrypoint

    def flaky_ensure(self):
        calls["n"] += 1
        if calls["n"] == 1:  # the post-activation write
            raise OSError("disk full")
        return real_ensure(self)

    with patch.object(InstallLayout, "ensure_entrypoint", flaky_ensure):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.UNEXPECTED
    assert result.action_taken == "blocked"
    assert result.next_action == "retry_upgrade"
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    assert layout.entrypoint_path.exists()
    _assert_user_data_intact(tmp_path)


def test_placement_failure_before_the_switch_changes_nothing(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with patch(
        "src.services.upgrade.transaction._place_new_version",
        side_effect=OSError("permission denied"),
    ):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.UNEXPECTED
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    assert not any(layout.staging_dir.iterdir())
    _assert_user_data_intact(tmp_path)


def test_an_entrypoint_rewrite_failure_during_restore_is_not_a_failed_restore(
    tmp_path: Path,
) -> None:
    """The entry point resolves *through* the pointer and is written
    atomically, so failing to rewrite it after the pointer is already back
    does not mean the rollback failed. Reporting both as one flag turned a
    recovered install into a "could not restore" scare."""
    layout = _install(tmp_path)

    def always_fails(self):
        raise OSError("disk full")

    with patch.object(InstallLayout, "ensure_entrypoint", always_fails):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.UNEXPECTED
    assert result.next_action == "retry_upgrade"
    assert layout.active_version() == "v0.10.0"
    assert not layout.version_dir("v0.11.0").exists()
    assert any("Entry point rewrite failed" in w for w in result.warnings)
    assert not any("left unchanged" not in w and "mixed state" in w for w in result.warnings)
    _assert_user_data_intact(tmp_path)


def test_a_failed_pointer_restore_is_reported_honestly(tmp_path: Path) -> None:
    """If the pointer itself cannot be put back the install really is mixed;
    the message must say so rather than claiming nothing changed, and the
    new version directory must be kept for inspection."""
    layout = _install(tmp_path)
    calls = {"n": 0}
    real_activate = InstallLayout.activate

    def flaky_activate(self, version):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_activate(self, version)  # the forward switch succeeds
        raise OSError("disk full")  # the restore does not

    with patch.object(InstallLayout, "ensure_entrypoint", side_effect=OSError("boom")), \
         patch.object(InstallLayout, "activate", flaky_activate):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.ROLLBACK_FAILED
    assert result.blocked_reason == BlockedReason.ROLLBACK_FAILED
    assert result.next_action == "rollback_then_inspect"
    assert any("--rollback" in w for w in result.warnings)
    assert any("mixed state" in w for w in result.warnings)
    # Kept, not deleted: the user needs it to recover from.
    assert layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


# ── settle failures are warnings, not failures (spec §5 step 8) ───────


def test_prune_failure_after_a_successful_upgrade_only_warns(tmp_path: Path) -> None:
    """The pointer has moved and the post-check passed, so the upgrade
    succeeded. A locked directory must not be reported as failure, or the
    agent retries an upgrade that already happened."""
    layout = _install(tmp_path)
    with patch.object(
        InstallLayout, "prune", side_effect=OSError("directory in use")
    ):
        result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert result.active_version == "v0.11.0"
    assert any("could not prune" in w for w in result.warnings)
    _assert_user_data_intact(tmp_path)


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


def test_a_failing_post_check_rollback_still_returns_a_structured_result(
    tmp_path: Path,
) -> None:
    """The automatic rollback can itself fail on permissions, a full disk or an
    AV lock. Unguarded, that raw exception escaped every structured result:
    the CLI reported a generic exit 1 instead of post_check_failed, and the
    new version was left active with no indication of the mixed state."""
    layout = _install(tmp_path)
    calls = {"n": 0}
    real_activate = InstallLayout.activate

    def forward_ok_restore_fails(self, version):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_activate(self, version)
        raise OSError("permission denied")

    def failing_post_check(layout, migrate):
        raise UpgradeError("migration failed and repair --auto could not fix it")

    with patch.object(InstallLayout, "activate", forward_ok_restore_fails):
        result = _run(layout, tmp_path, post_check=failing_post_check)

    # NOT 13: the skill routes on the exit code alone and defines 13 as
    # "upgraded then rolled back", i.e. the user is back on a working version.
    assert result.exit_code == ExitCode.ROLLBACK_FAILED
    assert result.exit_code != ExitCode.POST_CHECK_FAILED
    assert result.blocked_reason == BlockedReason.ROLLBACK_FAILED
    assert result.action_taken == "blocked"
    assert result.next_action == "rollback_then_inspect"
    assert any("rollback did not complete" in w for w in result.warnings)
    # The new version stays: it is what the user has to recover from.
    assert layout.version_dir("v0.11.0").exists()
    _assert_user_data_intact(tmp_path)


def test_no_previous_version_to_roll_back_to_is_not_reported_as_recovered(
    tmp_path: Path,
) -> None:
    """A first-ever install whose post-check fails ends up with the new version
    active and nothing to return to. That is the same user-visible state as a
    failed rollback, so it must not report 13 — the skill routes on the exit
    code alone and 13 means the user is back on a working version.

    `--rollback` is not the remedy here either: there is nothing behind this
    install to return to, so next_action stays "inspect_logs_then_retry".
    """
    layout = InstallLayout(root=tmp_path, windows=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret\n")
    (tmp_path / "admission.db").write_bytes(b"SQLite format 3\x00")
    assert layout.active_version() is None  # nothing installed yet

    def failing_post_check(layout, migrate):
        raise UpgradeError("migration failed and repair --auto could not fix it")

    result = _run(layout, tmp_path, post_check=failing_post_check)

    assert result.exit_code == ExitCode.ROLLBACK_FAILED
    assert result.exit_code != ExitCode.POST_CHECK_FAILED
    assert result.blocked_reason == BlockedReason.ROLLBACK_FAILED
    assert result.action_taken == "blocked"
    # No previous version exists, so --rollback would fail; don't suggest it.
    assert result.next_action == "inspect_logs_then_retry"
    assert layout.active_version() == "v0.11.0"
    assert any("no previous version to roll back to" in w for w in result.warnings)
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


# ── force onto the already-active version (Important 8) ────────────────


def test_force_onto_the_active_version_refuses_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """Spec §3.3 rejected renaming a directory containing a running
    executable because it fails on Windows. ``versions/<active>`` is exactly
    that directory, so a same-version ``--force`` must refuse rather than
    swap it into place."""
    layout = _install(tmp_path, version="v0.11.0")
    version_dir = layout.version_dir("v0.11.0")
    before = (version_dir / "adm-agent").read_text()

    result = _run(layout, tmp_path, current_version="v0.11.0", force=True)

    # Exit 0: spec §7 documents it as "upgraded, or already current", and
    # the requested end state already holds.
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "none"
    assert result.active_version == "v0.11.0"
    assert result.next_action == "reinstall_to_replace_active_version"
    assert any("already the active version" in w for w in result.warnings)

    # Nothing on disk moved: same pointer, same bytes, no scratch dirs.
    assert layout.active_version() == "v0.11.0"
    assert (version_dir / "adm-agent").read_text() == before == "old"
    assert not list(layout.versions_dir.glob(".v0.11.0.*"))
    _assert_user_data_intact(tmp_path)


def test_force_onto_the_active_version_downloads_nothing(tmp_path: Path) -> None:
    """The refusal happens before staging, so the install is untouched and
    no several-hundred-MB artifact is fetched."""
    layout = _install(tmp_path, version="v0.11.0")

    def exploding_downloader(_asset: dict, _dest: Path) -> Path:
        raise AssertionError("must not download when refusing")

    result = _run(
        layout,
        tmp_path,
        current_version="v0.11.0",
        force=True,
        downloader=exploding_downloader,
    )
    assert result.action_taken == "none"
    assert not layout.staging_dir.exists()


def test_force_onto_a_different_version_still_upgrades(tmp_path: Path) -> None:
    """The refusal is narrow: only the *active* tag is protected."""
    layout = _install(tmp_path, version="v0.10.0")
    result = _run(layout, tmp_path, current_version="v0.11.0", force=True)
    assert result.exit_code == ExitCode.OK
    assert result.action_taken == "upgraded"
    assert layout.active_version() == "v0.11.0"
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


def _quiet_post_check(_layout: InstallLayout, _migrate: bool) -> list[str]:
    """A post-check that passes, so rollback tests assert on retention only."""
    return []


def test_manual_rollback_keeps_the_version_rolled_back_from(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    assert layout.active_version() == "v0.11.0"

    result = rollback(layout, post_check=_quiet_post_check)
    assert result.action_taken == "rolled_back"
    assert layout.active_version() == "v0.10.0"
    # Retained so the user can move forward again without re-downloading.
    assert layout.version_dir("v0.11.0").exists()


def test_rollback_without_a_previous_version_errors(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    with pytest.raises(UpgradeError, match="no previous version"):
        rollback(layout, post_check=_quiet_post_check)


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
    rollback(layout, post_check=_quiet_post_check)
    assert layout.active_version() == "v0.10.0"

    with pytest.raises(UpgradeError, match="no previous version"):
        rollback(layout, post_check=_quiet_post_check)
    assert layout.active_version() == "v0.10.0"


# ── rollback re-runs the §6.3 post-check, warn-only (spec §5) ─────────


def test_rollback_runs_the_post_check_on_the_restored_version(
    tmp_path: Path,
) -> None:
    """Spec §5: rolling back across a schema migration must still attempt
    db-migrate / repair --auto rather than leaving an old binary pointed at
    a newer database."""
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    seen: list[tuple[str, bool]] = []

    def recording(active_layout: InstallLayout, migrate: bool) -> list[str]:
        seen.append((active_layout.active_version() or "", migrate))
        return ["migration ran"]

    result = rollback(layout, post_check=recording)

    # Ran once, after the repoint, against the version rolled back *to*.
    assert seen == [("v0.10.0", True)]
    assert "migration ran" in result.warnings


def test_rollback_honours_no_migrate(tmp_path: Path) -> None:
    layout = _install(tmp_path)
    _run(layout, tmp_path)
    seen: list[bool] = []

    def recording(_layout: InstallLayout, migrate: bool) -> list[str]:
        seen.append(migrate)
        return []

    rollback(layout, migrate=False, post_check=recording)
    assert seen == [False]


def test_rollback_post_check_failure_is_a_warning_not_an_undo(
    tmp_path: Path,
) -> None:
    """Controller ruling: warn-only. There is no rolling back a rollback —
    the version just left is the one the user is escaping — so a failing
    post-check must never re-point forward."""
    layout = _install(tmp_path)
    _run(layout, tmp_path)

    def failing(_layout: InstallLayout, _migrate: bool) -> list[str]:
        raise UpgradeError("db-migrate could not downgrade the schema")

    result = rollback(layout, post_check=failing)

    assert result.action_taken == "rolled_back"
    assert result.active_version == "v0.10.0"
    assert layout.active_version() == "v0.10.0"
    assert result.exit_code == int(ExitCode.OK)
    assert any("db-migrate could not downgrade" in w for w in result.warnings)


def test_rollback_survives_an_untyped_post_check_exception(tmp_path: Path) -> None:
    """post_check shells out; any leaked exception must still be a warning."""
    layout = _install(tmp_path)
    _run(layout, tmp_path)

    def exploding(_layout: InstallLayout, _migrate: bool) -> list[str]:
        raise RuntimeError("boom")

    result = rollback(layout, post_check=exploding)

    assert layout.active_version() == "v0.10.0"
    assert any("boom" in w for w in result.warnings)


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


# ── stale staging sweep (Minor 2) ──────────────────────────────────────


def test_stale_staging_trees_are_swept_before_a_new_run(tmp_path: Path) -> None:
    """A hard-killed run strands a full artifact copy that nothing removes."""
    layout = _install(tmp_path)
    stranded = layout.staging_dir / "tmpabc123"
    (stranded / "extracted" / "adm-agent").mkdir(parents=True)
    (stranded / "adm-agent-v0.10.0-linux-x86_64.tar.gz").write_bytes(b"x" * 4096)
    orphan_file = layout.staging_dir / "leftover.part"
    orphan_file.write_bytes(b"y")

    result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.OK
    assert not stranded.exists()
    assert not orphan_file.exists()
    # The sweep does not reach outside staging/.
    _assert_user_data_intact(tmp_path)
    assert layout.version_dir("v0.10.0").is_dir()


def test_sweep_is_a_no_op_when_staging_is_absent(tmp_path: Path) -> None:
    layout = InstallLayout(root=tmp_path, windows=False)
    assert sweep_stale_staging(layout) == []


def test_sweep_reports_a_failure_as_a_warning_and_continues(tmp_path: Path) -> None:
    """A sweep failure must never block the upgrade it precedes."""
    layout = InstallLayout(root=tmp_path, windows=False)
    (layout.staging_dir / "tmpabc123").mkdir(parents=True)

    with patch(
        "src.services.upgrade.transaction.shutil.rmtree",
        side_effect=OSError("permission denied"),
    ):
        warnings = sweep_stale_staging(layout)

    assert len(warnings) == 1
    assert "tmpabc123" in warnings[0]


def test_a_stale_target_directory_from_an_interrupted_run_is_replaced(
    tmp_path: Path,
) -> None:
    """``versions/<new>`` can already exist from a run killed after the move
    but before the pointer switch. It is never the active version (that case
    is refused up front), so it must simply be replaced.
    """
    layout = _install(tmp_path, version="v0.10.0")
    stale = layout.version_dir("v0.11.0")
    (stale / "_internal").mkdir(parents=True)
    (stale / "adm-agent").write_text("half-written")
    (stale / "garbage-from-the-killed-run").write_text("x")

    result = _run(layout, tmp_path)

    assert result.exit_code == ExitCode.OK
    assert layout.active_version() == "v0.11.0"
    assert (stale / "adm-agent").read_text() == "v0.11.0"
    assert not (stale / "garbage-from-the-killed-run").exists()
    assert not list(layout.versions_dir.glob(".v0.11.0.*"))
    _assert_user_data_intact(tmp_path)


def test_the_restore_hint_names_the_artifact_being_upgraded(tmp_path: Path) -> None:
    """A client user told to run `adm-agent upgrade --rollback` would be
    pointed at ~/.uni-agent — a different install from the one in a mixed
    state, which they would then roll back by accident."""
    layout = InstallLayout(
        root=tmp_path, artifact_name="adm-agent-client", windows=False
    )
    vdir = layout.version_dir("v0.10.0")
    (vdir / "_internal").mkdir(parents=True)
    (vdir / "adm-agent-client").write_text("old")
    layout.activate("v0.10.0")

    result = UpgradeResult()
    with patch.object(
        InstallLayout, "activate", side_effect=OSError("permission denied")
    ):
        restored, _ = _restore_pointer(layout, "v0.10.0", result)

    assert restored is False
    hint = next(w for w in result.warnings if "--rollback" in w)
    assert "adm-agent-client upgrade --rollback" in hint
    assert "'adm-agent upgrade --rollback'" not in hint
