"""The upgrade transaction (spec §5).

Ordering is the whole design: everything that can fail is done in
``staging/`` first, so any pre-activation failure leaves the install
byte-identical. Only then does the pointer move.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from src.services.upgrade.layout import InstallLayout
from src.services.upgrade.locking import UpgradeInProgressError, install_lock
from src.services.upgrade.preflight import run_preflight
from src.services.upgrade.release import (
    fetch_latest_release,
    fetch_text,
    find_checksums_asset,
    find_release_asset,
    get_platform_info,
    parse_checksums,
)
from src.services.upgrade.staging import (
    safe_extract,
    verify_artifact,
    verify_staged_binary,
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
from src.services.upgrade.versions import is_newer, parse_tag

logger = logging.getLogger(__name__)

RETAIN_VERSIONS = 2


def get_current_version() -> str:
    """Current version, injected into ``src.__version__`` at build time."""
    try:
        from src import __version__

        return __version__ if __version__.startswith("v") else f"v{__version__}"
    except ImportError:
        return "v0.0.0-dev"


def resolve_expected_digest(release: dict, asset_name: str) -> str | None:
    """Digest for *asset_name*, or ``None`` on releases without SHA256SUMS."""
    checksums_asset = find_checksums_asset(release)
    if checksums_asset is None:
        return None
    text = fetch_text(checksums_asset["browser_download_url"])
    return parse_checksums(text).get(asset_name)


def default_downloader(asset: dict, dest_dir: Path) -> Path:
    """Stream a release asset into *dest_dir*."""
    from urllib.request import Request, urlopen

    from src.services.upgrade.release import _SSL_CONTEXT

    target = dest_dir / asset["name"]
    try:
        with urlopen(Request(asset["browser_download_url"]), timeout=300,
                     context=_SSL_CONTEXT) as response:
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except Exception as exc:
        raise UpgradeError(f"Failed to download {asset['name']}: {exc}") from exc
    return target


def _run_post_check_step(
    layout: InstallLayout, *args: str, timeout: int
) -> subprocess.CompletedProcess:
    """Run one post-check subprocess, converting infra failures to
    :class:`UpgradeError` (mirrors ``staging.verify_staged_binary``).

    A hung process (``TimeoutExpired``) or a missing/non-executable entry
    point (``OSError``) is exactly the kind of "unrecoverable migration"
    situation spec §6.3 wants rolled back — it must never propagate as a
    bare, untyped exception out of ``perform_upgrade``.

    Invokes via :meth:`InstallLayout.spawn_argv` rather than
    ``[str(layout.entrypoint_path), *args]`` directly: on Windows the entry
    point is a ``.cmd`` shim, which ``CreateProcess`` cannot exec as a PE
    image, so it must be routed through ``cmd.exe /c``.
    """
    try:
        return subprocess.run(
            layout.spawn_argv(*args), capture_output=True, text=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(
            f"Could not run '{layout.entrypoint_path.name} {' '.join(args)}': {exc}"
        ) from exc


def default_post_check(layout: InstallLayout, migrate: bool) -> list[str]:
    """Run ``check`` (warn only) then ``db-migrate`` (fatal) — spec §6.3.

    Backend-only: ``adm-agent-client`` has neither command, and running the
    activated client binary with backend arguments would fail spuriously.
    """
    if layout.artifact_name != "adm-agent":
        return []

    warnings: list[str] = []

    check = _run_post_check_step(layout, "check", timeout=600)
    if check.returncode != 0:
        warnings.append(
            "Post-upgrade environment check reported problems (not caused by "
            f"the upgrade; not rolled back): {check.stdout.strip()[:400]}"
        )

    if not migrate:
        return warnings

    migration = _run_post_check_step(layout, "db-migrate", "--yes", timeout=1800)
    if migration.returncode == 0:
        return warnings

    repair = _run_post_check_step(layout, "repair", "--auto", timeout=1800)
    if repair.returncode == 0:
        warnings.append("Database migration failed but auto-repair recovered it.")
        return warnings

    raise UpgradeError(
        "Database migration failed and auto-repair could not recover: "
        f"{migration.stderr.strip()[:400]}"
    )


def check_for_updates(artifact_name: str = "adm-agent") -> UpgradeResult:
    """Resolve the latest release without changing anything."""
    result = UpgradeResult(current_version=get_current_version())
    try:
        release = fetch_latest_release()
    except UpgradeError as exc:
        result.blocked_reason = BlockedReason.UNEXPECTED
        result.action_taken = "blocked"
        result.exit_code = int(ExitCode.UNEXPECTED)
        result.warnings.append(str(exc))
        return result

    result.latest_version = release.get("tag_name", "")
    try:
        result.is_newer = is_newer(result.current_version, result.latest_version)
    except UnparseableVersionError as exc:
        result.blocked_reason = BlockedReason.UNPARSEABLE_VERSION
        result.action_taken = "blocked"
        result.exit_code = int(ExitCode.VERIFICATION_FAILED)
        result.warnings.append(str(exc))
        return result

    os_name, arch = get_platform_info()
    result.asset_available = (
        find_release_asset(release, os_name, arch, artifact_name) is not None
    )
    return result


# pylint: disable=too-many-locals,too-many-return-statements
def perform_upgrade(
    layout: InstallLayout,
    *,
    artifact_name: str = "adm-agent",
    force: bool = False,
    migrate: bool = True,
    frozen: bool = True,
    pid_file: Path,
    health_url: str,
    downloader: Callable[[dict, Path], Path] | None = None,
    post_check: Callable[[InstallLayout, bool], list[str]] | None = None,
) -> UpgradeResult:
    """Execute the eight-step transaction. Never raises for expected failures."""
    downloader = downloader or default_downloader
    post_check = post_check or default_post_check

    result = UpgradeResult(
        current_version=get_current_version(),
        active_version=layout.active_version() or "",
    )

    block = run_preflight(layout, frozen=frozen, pid_file=pid_file, health_url=health_url)
    if block is not None:
        result.action_taken = "blocked"
        result.blocked_reason = block.reason
        result.next_action = block.next_action
        result.exit_code = block.exit_code
        result.warnings.append(block.message)
        return result

    # 2. resolve
    try:
        release = fetch_latest_release()
    except UpgradeError as exc:
        return _blocked(result, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, str(exc))

    result.latest_version = release.get("tag_name", "")
    try:
        result.is_newer = is_newer(result.current_version, result.latest_version)
    except UnparseableVersionError as exc:
        return _blocked(
            result, BlockedReason.UNPARSEABLE_VERSION, ExitCode.VERIFICATION_FAILED, str(exc)
        )

    if not result.is_newer and not force:
        result.action_taken = "none"
        return result

    os_name, arch = get_platform_info()
    asset = find_release_asset(release, os_name, arch, artifact_name)
    if asset is None:
        result.asset_available = False
        return _blocked(
            result,
            BlockedReason.NO_ASSET_FOR_PLATFORM,
            ExitCode.NO_ASSET_FOR_PLATFORM,
            f"No {artifact_name} build published for {os_name}-{arch}.",
        )
    result.asset_available = True

    def _run_locked() -> UpgradeResult:
        """The mutating half of the transaction, run under the lock."""
        # Read the active version *inside* the lock. Resolving it earlier
        # would let a concurrent upgrade finish between the read and the
        # acquire, leaving this run carrying a stale `previous`: the
        # same-version refusal would be bypassed, `_place_new_version` would
        # displace a version that is now active, and a post-check failure
        # would roll back somebody else's successful upgrade.
        previous = layout.active_version()
        if result.latest_version and result.latest_version == previous:
            return _refuse_same_version_reinstall(result, previous)

        new_version = result.latest_version
        layout.staging_dir.mkdir(parents=True, exist_ok=True)
        result.warnings.extend(sweep_stale_staging(layout))
        staging = Path(tempfile.mkdtemp(dir=layout.staging_dir))

        # 3. stage
        try:
            archive = downloader(asset, staging)
        except UpgradeError as exc:
            return _stage_failed(result, staging, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, exc)

        # 4. verify artifact
        try:
            digest = resolve_expected_digest(release, asset["name"])
            outcome = verify_artifact(archive, digest, asset.get("size"))
        except ChecksumMismatchError as exc:
            return _stage_failed(
                result, staging, BlockedReason.CHECKSUM_MISMATCH, ExitCode.VERIFICATION_FAILED, exc
            )
        except UpgradeError as exc:
            return _stage_failed(result, staging, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, exc)
        result.checksum_verified = outcome.verified
        result.warnings.extend(outcome.warnings)

        try:
            payload = safe_extract(archive, staging / "extracted")
        except UpgradeError as exc:
            return _stage_failed(result, staging, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, exc)

        # 5. verify binary
        try:
            verify_staged_binary(payload, new_version, layout.executable_name)
        except StagedBinaryError as exc:
            return _stage_failed(
                result, staging, BlockedReason.STAGED_BINARY_FAILED, ExitCode.VERIFICATION_FAILED, exc
            )
        except UpgradeError as exc:
            return _stage_failed(result, staging, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, exc)

        # 6. activate — never delete an existing target in place: it may be the
        # currently active, currently running version (e.g. a same-version
        # `--force` re-install). Swap it into position with atomic renames so a
        # valid directory always occupies `target`.
        # Any failure in this phase — a permission error, a full disk, an AV
        # scanner holding a file — must still leave the install as it was and
        # return a structured result, never propagate raw to the CLI.
        target: Path | None = None
        switched = False
        try:
            target = _place_new_version(layout, new_version, payload)
            shutil.rmtree(staging, ignore_errors=True)
            layout.activate(new_version)
            switched = True
            layout.ensure_entrypoint()
        except Exception as exc:  # pylint: disable=broad-except
            return _activation_failed(
                layout, result, staging, target, previous, switched=switched, exc=exc
            )

        # 7. post-check
        try:
            result.warnings.extend(post_check(layout, migrate))
        except Exception as exc:  # pylint: disable=broad-except
            # post_check is caller-injected (the default shells out via
            # subprocess); any failure here — typed or not — must still route
            # through the spec §6.3 rollback decision, never propagate raw.
            # `previous != new_version` is guaranteed by the same-version refusal
            # above; only "no previous version at all" (a first-ever install)
            # reaches the else branch.
            if previous:
                layout.activate(previous)
                layout.ensure_entrypoint()
                shutil.rmtree(target, ignore_errors=True)
                result.action_taken = "rolled_back"
                result.previous_version = new_version
            else:
                # A first-ever install has nothing to roll back to. Leave it
                # active and warn plainly rather than falsely claiming a rollback.
                result.action_taken = "blocked"
                result.previous_version = ""
                result.warnings.append(
                    "Post-check failed but there is no previous version to "
                    "roll back to; the new version remains active."
                )
            result.blocked_reason = BlockedReason.POST_CHECK_FAILED
            result.next_action = "inspect_logs_then_retry"
            result.exit_code = int(ExitCode.POST_CHECK_FAILED)
            result.active_version = layout.active_version() or ""
            result.warnings.append(str(exc))
            return result

        # 8. settle — keep active + one previous. If the former pointer was
        # missing or corrupt, fall back to the newest other installed version
        # rather than pruning everything down to just the new one.
        keep_previous = previous or _newest_other_installed(layout, exclude=new_version)
        keep = [v for v in (new_version, keep_previous) if v][:RETAIN_VERSIONS]
        try:
            layout.prune(keep=keep)
        except Exception as exc:  # pylint: disable=broad-except
            # Spec §5 step 8: "log a warning only". The pointer has already moved
            # and the post-check has already passed, so the upgrade succeeded —
            # a Windows file lock or an AV scanner blocking rmtree must not be
            # reported as failure, or the agent retries an upgrade that already
            # happened. The stale directory is swept on the next run.
            result.warnings.append(
                f"Upgraded successfully, but could not prune old versions: {exc}. "
                "They will be cleaned up on the next upgrade."
            )

        result.action_taken = "upgraded"
        result.active_version = new_version
        result.previous_version = previous or ""
        return result

    # The lock spans the sweep through settle/rollback. Without it a
    # concurrent run's sweep would delete this one's in-flight staging
    # tree, and a later interleaving would let one process prune the very
    # version the other just activated.
    try:
        with install_lock(layout.root):
            return _run_locked()
    except UpgradeInProgressError as exc:
        result.next_action = "wait_for_other_upgrade_then_retry"
        return _blocked(
            result,
            BlockedReason.UPGRADE_IN_PROGRESS,
            ExitCode.UPGRADE_IN_PROGRESS,
            str(exc),
        )


def sweep_stale_staging(layout: InstallLayout) -> list[str]:
    """Delete scratch trees left behind by hard-killed earlier runs.

    Every step of the transaction cleans up its own ``staging/<tmp>``, but a
    process killed mid-download cannot — and each stranded tree is a full
    copy of a several-hundred-MB artifact that nothing else ever removes.
    Safe by construction: ``staging/`` holds only this function's own
    scratch directories, and spec §3.2 lists it among the four paths
    ``upgrade`` is permitted to write.

    Best effort — a sweep failure is reported as a warning and never blocks
    the upgrade.
    """
    warnings: list[str] = []
    if not layout.staging_dir.is_dir():
        return warnings
    for entry in layout.staging_dir.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            warnings.append(f"Could not remove stale staging entry {entry.name}: {exc}")
    return warnings


def _select_rollback_target(layout: InstallLayout, active: str | None) -> str | None:
    """Newest installed version strictly older than *active*, or ``None``.

    Deliberately not "any other installed version": after a manual rollback a
    *newer* version is typically still retained so the user can move forward
    again, and a second rollback must not roll onto it. When either side's tag
    is unparseable the ordering cannot be proven, so the candidate is skipped
    rather than guessed at.
    """
    active_parsed = parse_tag(active) if active else None
    for version in layout.installed_versions():
        if version == active:
            continue
        version_parsed = parse_tag(version)
        if active_parsed is not None and version_parsed is not None:
            if version_parsed < active_parsed:
                return version
    return None


def _refuse_same_version_reinstall(result: UpgradeResult, active: str) -> UpgradeResult:
    """Refuse to re-install the version that is currently active.

    Spec §3.3 rejected the shadow-directory approach precisely because
    renaming or replacing a directory that contains a running (or
    memory-mapped) executable fails on Windows. ``versions/<active>`` is
    exactly such a directory — the upgrading process is executing from it —
    so ``--force`` onto the active tag must not try to swap it into place.

    Reported as "nothing to do" rather than a failure: exit ``0`` is
    documented in spec §7 as "upgraded, **or already current**", and the
    requested end state (this version active) already holds. Nothing on
    disk is touched. ``next_action`` points at the re-install path, which
    is the documented recovery for a damaged install.
    """
    result.action_taken = "none"
    result.active_version = active
    result.next_action = "reinstall_to_replace_active_version"
    result.warnings.append(
        f"{active} is already the active version. Re-installing it in place "
        "would mean replacing the directory this process is running from, "
        "which is not safe on every platform — so nothing was changed. If "
        "the install is damaged, re-run the installer: it adds a new "
        "versions/ entry and repoints, leaving .env and the database alone."
    )
    return result


def _place_new_version(layout: InstallLayout, new_version: str, payload: Path) -> Path:
    """Move the verified *payload* into ``versions/<new_version>`` atomically.

    The target is never the active version — ``perform_upgrade`` refuses
    that case up front (see :func:`_refuse_same_version_reinstall`) — so a
    stale directory left by an earlier interrupted run can simply be
    removed before the rename.
    """
    target = layout.version_dir(new_version)
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = layout.versions_dir / f".{new_version}.incoming-{os.getpid()}"
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.move(str(payload), str(incoming))

    if target.exists():
        shutil.rmtree(target)
    os.replace(incoming, target)
    return target


def _newest_other_installed(layout: InstallLayout, *, exclude: str) -> str | None:
    """Newest installed version other than *exclude*, or ``None``."""
    for version in layout.installed_versions():
        if version != exclude:
            return version
    return None


def _activation_failed(
    layout: InstallLayout,
    result: UpgradeResult,
    staging: Path,
    target: Path | None,
    previous: str | None,
    *,
    switched: bool,
    exc: Exception,
) -> UpgradeResult:
    """Undo a partially-completed activation (spec §5 step 6).

    The pointer switch is atomic, but placing the payload and rewriting the
    entry point are not free of failure. If the switch already happened we
    put it back; either way the new version directory goes, so the install
    ends up exactly as it started.
    """
    shutil.rmtree(staging, ignore_errors=True)

    # Track the pointer and the entry point separately. The entry point is
    # written atomically and always resolves *through* the pointer, so a
    # failure rewriting it after the pointer is already back does not mean
    # the rollback failed — reporting both as one flag turned a recovered
    # install into a scary "could not restore" message.
    pointer_restored = not switched
    entrypoint_rewritten = True
    if switched:
        pointer_restored, entrypoint_rewritten = _restore_pointer(
            layout, previous, result
        )

    if target is not None and pointer_restored:
        shutil.rmtree(target, ignore_errors=True)

    result.active_version = layout.active_version() or ""
    if pointer_restored:
        result.next_action = "retry_upgrade"
        message = f"Activation failed and the install was left unchanged: {exc}"
    else:
        result.next_action = "rollback_then_inspect"
        message = (
            f"Activation failed and could not be undone: {exc}. The install is "
            f"in a mixed state — {result.active_version or 'no version'} is "
            "active and the new version directory was kept. Inspect it before "
            "retrying."
        )
    if pointer_restored and not entrypoint_rewritten:
        result.warnings.append(
            "The previous version is active again, but rewriting the stable "
            "entry point failed. It still resolves through the restored "
            "pointer; re-run the upgrade to have it rewritten."
        )
    return _blocked(result, BlockedReason.UNEXPECTED, ExitCode.UNEXPECTED, message)


def _restore_pointer(
    layout: InstallLayout, previous: str | None, result: UpgradeResult
) -> tuple[bool, bool]:
    """Put the pointer back. Returns ``(pointer_restored, entrypoint_rewritten)``."""
    try:
        if previous:
            layout.activate(previous)
        else:
            # Nothing was installed before, so "unchanged" means no pointer at
            # all rather than one aimed at a version we are about to delete.
            pointer = layout.pointer_path
            if pointer.exists() or pointer.is_symlink():
                pointer.unlink()
    except Exception as exc:  # pylint: disable=broad-except
        result.warnings.append(
            f"Could not restore the previous version after a failed "
            f"activation: {exc}. Run 'adm-agent upgrade --rollback'."
        )
        return False, False

    try:
        layout.ensure_entrypoint()
    except Exception as exc:  # pylint: disable=broad-except
        result.warnings.append(f"Entry point rewrite failed after restore: {exc}")
        return True, False
    return True, True


def _stage_failed(
    result: UpgradeResult,
    staging: Path,
    reason: str,
    code: ExitCode,
    exc: Exception,
) -> UpgradeResult:
    """Clean up ``staging/`` and report a pre-activation failure.

    Every pre-activation exit shares this shape: nothing has been activated
    yet, so the only cleanup owed is the scratch directory.
    """
    shutil.rmtree(staging, ignore_errors=True)
    return _blocked(result, reason, code, str(exc))


def rollback(
    layout: InstallLayout,
    *,
    migrate: bool = True,
    post_check: Callable[[InstallLayout, bool], list[str]] | None = None,
) -> UpgradeResult:
    """Repoint to the newest retained version older than the active one.

    Deliberately not just "any other installed version": after a manual
    rollback, a *newer* version is typically still retained on disk so the
    user can move forward again without re-downloading (spec §3.2) — a
    second ``rollback()`` call must not roll *forward* onto it.

    Spec §5 requires the §6.3 post-check to run after the repoint, so that
    rolling back across a schema migration still attempts ``db-migrate``
    and its ``repair --auto`` fallback instead of silently leaving an old
    binary pointed at a newer database.

    The post-check here is **warn-only**, unlike the upgrade path: a
    failure cannot undo the repoint, because there is no rolling back a
    rollback — the version just left is the one the user is escaping. All
    failures are surfaced as warnings on the result instead.
    """
    post_check = post_check or default_post_check

    # A rollback moves the pointer, so it takes the same install-root lock an
    # upgrade does — otherwise it can repoint out from under a concurrent
    # upgrade that is mid-activation. The target is chosen *inside* the lock
    # for the same reason: choosing it first would let a concurrent upgrade
    # change what "active" and "newest older" mean before the repoint lands.
    with install_lock(layout.root):
        active = layout.active_version()
        older = _select_rollback_target(layout, active)
        if older is None:
            raise UpgradeError("Cannot roll back: no previous version is installed")

        layout.activate(older)
        layout.ensure_entrypoint()
        result = UpgradeResult(
            current_version=active or "",
            action_taken="rolled_back",
            active_version=older,
            previous_version=active or "",
        )

        try:
            result.warnings.extend(post_check(layout, migrate))
        except Exception as exc:  # pylint: disable=broad-except
            result.warnings.append(
                "Post-rollback check failed. The rollback itself stands — "
                f"{older} is active — but verify the database before continuing: {exc}"
            )
    return result


def _blocked(
    result: UpgradeResult, reason: str, code: ExitCode, message: str
) -> UpgradeResult:
    result.action_taken = "blocked"
    result.blocked_reason = reason
    result.exit_code = int(code)
    result.warnings.append(message)
    return result
