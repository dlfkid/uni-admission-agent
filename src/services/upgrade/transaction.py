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
    entry: Path, *args: str, timeout: int
) -> subprocess.CompletedProcess:
    """Run one post-check subprocess, converting infra failures to
    :class:`UpgradeError` (mirrors ``staging.verify_staged_binary``).

    A hung process (``TimeoutExpired``) or a missing/non-executable entry
    point (``OSError``) is exactly the kind of "unrecoverable migration"
    situation spec §6.3 wants rolled back — it must never propagate as a
    bare, untyped exception out of ``perform_upgrade``.
    """
    try:
        return subprocess.run(
            [str(entry), *args], capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(
            f"Could not run '{entry.name} {' '.join(args)}': {exc}"
        ) from exc


def default_post_check(layout: InstallLayout, migrate: bool) -> list[str]:
    """Run ``check`` (warn only) then ``db-migrate`` (fatal) — spec §6.3.

    Backend-only: ``adm-agent-client`` has neither command, and running the
    activated client binary with backend arguments would fail spuriously.
    """
    if layout.artifact_name != "adm-agent":
        return []

    warnings: list[str] = []
    entry = layout.entrypoint_path

    check = _run_post_check_step(entry, "check", timeout=600)
    if check.returncode != 0:
        warnings.append(
            "Post-upgrade environment check reported problems (not caused by "
            f"the upgrade; not rolled back): {check.stdout.strip()[:400]}"
        )

    if not migrate:
        return warnings

    migration = _run_post_check_step(entry, "db-migrate", "--yes", timeout=1800)
    if migration.returncode == 0:
        return warnings

    repair = _run_post_check_step(entry, "repair", "--auto", timeout=1800)
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

    previous = layout.active_version()
    new_version = result.latest_version
    layout.staging_dir.mkdir(parents=True, exist_ok=True)
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
    target = _place_new_version(layout, new_version, payload)
    shutil.rmtree(staging, ignore_errors=True)
    layout.activate(new_version)
    layout.ensure_entrypoint()

    # 7. post-check
    try:
        result.warnings.extend(post_check(layout, migrate))
    except Exception as exc:  # pylint: disable=broad-except
        # post_check is caller-injected (the default shells out via
        # subprocess); any failure here — typed or not — must still route
        # through the spec §6.3 rollback decision, never propagate raw.
        genuinely_rollable = bool(previous) and previous != new_version
        if genuinely_rollable:
            layout.activate(previous)
            layout.ensure_entrypoint()
            shutil.rmtree(target, ignore_errors=True)
            result.action_taken = "rolled_back"
            result.previous_version = new_version
        else:
            # Nothing distinct to roll back to — either a first-ever install
            # (no previous) or a same-version `--force` re-install (previous
            # is literally the version we just activated). Leave it active
            # and warn plainly rather than falsely claiming a rollback.
            result.action_taken = "blocked"
            result.previous_version = ""
            if previous == new_version:
                message = (
                    "Post-check failed but the previous version is the same "
                    "release just (re)installed; there is nothing distinct "
                    "to roll back to. The new version remains active."
                )
            else:
                message = (
                    "Post-check failed but there is no previous version to "
                    "roll back to; the new version remains active."
                )
            result.warnings.append(message)
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
    layout.prune(keep=keep)

    result.action_taken = "upgraded"
    result.active_version = new_version
    result.previous_version = previous or ""
    return result


def _place_new_version(layout: InstallLayout, new_version: str, payload: Path) -> Path:
    """Move the verified *payload* into ``versions/<new_version>`` atomically.

    Never removes an existing directory at the target path before the
    replacement is ready to take its place — that directory may be the
    currently active, currently running version (spec §5's atomicity claim
    depends on this: a same-version ``--force`` re-install must not leave a
    window where the active version's directory does not exist on disk).
    """
    target = layout.version_dir(new_version)
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = layout.versions_dir / f".{new_version}.incoming-{os.getpid()}"
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.move(str(payload), str(incoming))

    if target.exists():
        displaced = layout.versions_dir / f".{new_version}.replaced-{os.getpid()}"
        if displaced.exists():
            shutil.rmtree(displaced)
        os.replace(target, displaced)
        os.replace(incoming, target)
        shutil.rmtree(displaced, ignore_errors=True)
    else:
        os.replace(incoming, target)
    return target


def _newest_other_installed(layout: InstallLayout, *, exclude: str) -> str | None:
    """Newest installed version other than *exclude*, or ``None``."""
    for version in layout.installed_versions():
        if version != exclude:
            return version
    return None


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


def rollback(layout: InstallLayout) -> UpgradeResult:
    """Repoint to the newest retained version older than the active one.

    Deliberately not just "any other installed version": after a manual
    rollback, a *newer* version is typically still retained on disk so the
    user can move forward again without re-downloading (spec §3.2) — a
    second ``rollback()`` call must not roll *forward* onto it.
    """
    active = layout.active_version()
    active_parsed = parse_tag(active) if active else None

    older = None
    for version in layout.installed_versions():
        if version == active:
            continue
        version_parsed = parse_tag(version)
        if active_parsed is not None and version_parsed is not None:
            if version_parsed < active_parsed:
                older = version
                break
        # Either side is unparseable: ordering can't be proven, so don't
        # guess — skip rather than risk rolling forward.

    if older is None:
        raise UpgradeError("Cannot roll back: no previous version is installed")

    layout.activate(older)
    layout.ensure_entrypoint()
    return UpgradeResult(
        current_version=active or "",
        action_taken="rolled_back",
        active_version=older,
        previous_version=active or "",
    )


def _blocked(
    result: UpgradeResult, reason: str, code: ExitCode, message: str
) -> UpgradeResult:
    result.action_taken = "blocked"
    result.blocked_reason = reason
    result.exit_code = int(code)
    result.warnings.append(message)
    return result
