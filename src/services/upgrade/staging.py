"""Download, extract and verify a candidate version before activation.

Everything here happens in ``staging/``; the live install is untouched until
:mod:`src.services.upgrade.transaction` moves the verified tree into
``versions/`` and repoints (spec §5).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.services.upgrade.types import UpgradeError

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


@dataclass
class ChecksumOutcome:
    """Result of artifact verification (spec §6.1)."""

    verified: bool
    warnings: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(
    path: Path,
    expected_digest: str | None,
    expected_size: int | None,
) -> ChecksumOutcome:
    """Verify a downloaded artifact.

    A present-and-mismatched digest is always fatal. A *missing* digest
    degrades to an exact size check plus a warning, because hard-failing
    would make every pre-SHA256SUMS release un-upgradable — reintroducing
    the symptom this work exists to remove.
    """
    actual_size = path.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise UpgradeError(
            f"Downloaded artifact size {actual_size} does not match the "
            f"published size {expected_size} — download was truncated"
        )

    if expected_digest is None:
        return ChecksumOutcome(
            verified=False,
            warnings=[
                "This release publishes no SHA256SUMS; verified size only. "
                "Integrity is not cryptographically confirmed."
            ],
        )

    actual = sha256_of(path)
    if actual.lower() != expected_digest.lower():
        raise UpgradeError(
            f"Artifact checksum mismatch: expected {expected_digest}, got {actual}"
        )
    return ChecksumOutcome(verified=True)


def _assert_within(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    resolved_target = (root / target).resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise UpgradeError(f"Archive member escapes the extraction root: {target}")


def safe_extract(archive: Path, dest: Path) -> Path:
    """Extract *archive* into *dest*; return its single top-level directory."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    _assert_within(dest, Path(member))
                zf.extractall(dest)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                for member in tf.getmembers():
                    _assert_within(dest, Path(member.name))
                tf.extractall(dest, filter="data")
    except UpgradeError:
        raise
    except Exception as exc:
        raise UpgradeError(f"Failed to extract {archive.name}: {exc}") from exc

    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise UpgradeError(
            f"Unexpected archive structure in {archive.name}: "
            f"expected one top-level directory, found {len(entries)}"
        )
    return entries[0]


def clear_quarantine(path: Path) -> None:
    """Strip the macOS quarantine xattr so Gatekeeper allows the self-check.

    Without this every staged-binary verification fails on macOS. Best effort:
    a missing ``xattr`` binary is not an upgrade failure.
    """
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-cr", str(path)], check=False, capture_output=True, timeout=60
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not clear quarantine attribute: %s", exc)


def verify_staged_binary(
    staged_dir: Path,
    expected_version: str,
    executable_name: str,
) -> None:
    """Run the staged binary and confirm it reports *expected_version*.

    This is the gate that matters most: it catches truncated downloads,
    wrong-architecture artifacts, a missing ``_internal`` and broken builds
    while the live install is still untouched.
    """
    executable = staged_dir / executable_name
    if not executable.is_file():
        raise UpgradeError(f"Staged executable {executable_name} not found")
    executable.chmod(0o755)
    clear_quarantine(staged_dir)

    try:
        proc = subprocess.run(
            [str(executable), "version", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        raise UpgradeError(f"Staged binary could not be executed: {exc}") from exc

    if proc.returncode != 0:
        raise UpgradeError(
            f"Staged binary self-check failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    try:
        reported = json.loads(proc.stdout)["version"]
    except Exception as exc:
        raise UpgradeError(
            f"Staged binary produced unparseable version output: {proc.stdout!r}"
        ) from exc

    if reported != expected_version:
        raise UpgradeError(
            f"Staged binary reports {reported}, expected {expected_version}"
        )
