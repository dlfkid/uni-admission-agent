"""The upgrade transaction (spec §5).

Ordering is the whole design: everything that can fail is done in
``staging/`` first, so any pre-activation failure leaves the install
byte-identical. Only then does the pointer move.
"""
from __future__ import annotations

import logging
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
    ExitCode,
    UnparseableVersionError,
    UpgradeError,
    UpgradeResult,
)
from src.services.upgrade.versions import is_newer

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


def default_post_check(layout: InstallLayout, migrate: bool) -> list[str]:
    """Run ``check`` (warn only) then ``db-migrate`` (fatal) — spec §6.3.

    Backend-only: ``adm-agent-client`` has neither command, and running the
    activated client binary with backend arguments would fail spuriously.
    """
    if layout.artifact_name != "adm-agent":
        return []

    warnings: list[str] = []
    entry = layout.entrypoint_path

    check = subprocess.run(
        [str(entry), "check"], capture_output=True, text=True, check=False, timeout=600
    )
    if check.returncode != 0:
        warnings.append(
            "Post-upgrade environment check reported problems (not caused by "
            f"the upgrade; not rolled back): {check.stdout.strip()[:400]}"
        )

    if not migrate:
        return warnings

    migration = subprocess.run(
        [str(entry), "db-migrate", "--yes"],
        capture_output=True, text=True, check=False, timeout=1800,
    )
    if migration.returncode == 0:
        return warnings

    repair = subprocess.run(
        [str(entry), "repair", "--auto"],
        capture_output=True, text=True, check=False, timeout=1800,
    )
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

    try:
        # 3. stage
        archive = downloader(asset, staging)
        # 4. verify artifact
        digest = resolve_expected_digest(release, asset["name"])
        outcome = verify_artifact(archive, digest, asset.get("size"))
        result.checksum_verified = outcome.verified
        result.warnings.extend(outcome.warnings)
        payload = safe_extract(archive, staging / "extracted")
        # 5. verify binary
        verify_staged_binary(payload, new_version, layout.executable_name)
    except UpgradeError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        reason = (
            BlockedReason.CHECKSUM_MISMATCH
            if "checksum" in str(exc).lower() or "size" in str(exc).lower()
            else BlockedReason.STAGED_BINARY_FAILED
        )
        return _blocked(result, reason, ExitCode.VERIFICATION_FAILED, str(exc))

    # 6. activate
    target = layout.version_dir(new_version)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(payload), str(target))
    shutil.rmtree(staging, ignore_errors=True)
    layout.activate(new_version)
    layout.ensure_entrypoint()

    # 7. post-check
    try:
        result.warnings.extend(post_check(layout, migrate))
    except UpgradeError as exc:
        if previous:
            # A previous version exists: prove the new one bad and roll back.
            layout.activate(previous)
            layout.ensure_entrypoint()
            shutil.rmtree(target, ignore_errors=True)
            result.action_taken = "rolled_back"
        else:
            # Nothing to roll back to (first-ever install) — leave it active
            # and warn plainly rather than falsely claiming a rollback.
            result.action_taken = "blocked"
            result.warnings.append(
                "Post-check failed but there is no previous version to roll "
                "back to; the new version remains active."
            )
        result.blocked_reason = BlockedReason.POST_CHECK_FAILED
        result.next_action = "inspect_logs_then_retry"
        result.exit_code = int(ExitCode.POST_CHECK_FAILED)
        result.active_version = layout.active_version() or ""
        result.previous_version = new_version
        result.warnings.append(str(exc))
        return result

    # 8. settle
    keep = [v for v in (new_version, previous) if v][:RETAIN_VERSIONS]
    layout.prune(keep=keep)

    result.action_taken = "upgraded"
    result.active_version = new_version
    result.previous_version = previous or ""
    return result


def rollback(layout: InstallLayout) -> UpgradeResult:
    """Repoint to the retained previous version (spec §5)."""
    active = layout.active_version()
    candidates = [v for v in layout.installed_versions() if v != active]
    if not candidates:
        raise UpgradeError("Cannot roll back: no previous version is installed")

    target = candidates[0]
    layout.activate(target)
    layout.ensure_entrypoint()
    return UpgradeResult(
        current_version=active or "",
        action_taken="rolled_back",
        active_version=target,
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
