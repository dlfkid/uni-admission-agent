"""Tests for staging: checksum, safe extraction, staged self-check — spec §6."""

import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from src.services.upgrade.staging import (
    ChecksumOutcome,
    safe_extract,
    verify_artifact,
    verify_staged_binary,
)
from src.services.upgrade.types import UpgradeError


def _write_archive(tmp_path: Path, top: str = "adm-agent") -> Path:
    payload = tmp_path / "payload" / top
    (payload / "_internal").mkdir(parents=True)
    (payload / "adm-agent").write_text("binary")
    archive = tmp_path / "artifact.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname=top)
    return archive


# ── checksum (spec §6.1) ──────────────────────────────────────────────


def test_matching_digest_verifies(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    outcome = verify_artifact(blob, expected_digest=digest, expected_size=5)
    assert outcome == ChecksumOutcome(verified=True, warnings=[])


def test_mismatched_digest_is_always_fatal(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    with pytest.raises(UpgradeError, match="checksum"):
        verify_artifact(blob, expected_digest="0" * 64, expected_size=5)


def test_missing_digest_degrades_to_size_with_a_warning(tmp_path: Path) -> None:
    """Old releases have no SHA256SUMS; blocking them would re-pin users."""
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    outcome = verify_artifact(blob, expected_digest=None, expected_size=5)
    assert outcome.verified is False
    assert any("SHA256SUMS" in w for w in outcome.warnings)


def test_missing_digest_with_wrong_size_is_fatal(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"hello")
    with pytest.raises(UpgradeError, match="size"):
        verify_artifact(blob, expected_digest=None, expected_size=999)


# ── extraction ────────────────────────────────────────────────────────


def test_safe_extract_returns_the_single_top_level_dir(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path)
    dest = tmp_path / "out"
    top = safe_extract(archive, dest)
    assert top.name == "adm-agent"
    assert (top / "_internal").is_dir()


def test_safe_extract_rejects_traversal_members(tmp_path: Path) -> None:
    """A member escaping the extraction root must abort the upgrade."""
    evil = tmp_path / "evil.tar.gz"
    victim = tmp_path / "outside.txt"
    victim.write_text("original")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(victim, arcname="../outside.txt")
    with pytest.raises(UpgradeError):
        safe_extract(evil, tmp_path / "out")
    assert victim.read_text() == "original"


def test_safe_extract_rejects_a_truncated_archive(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path)
    data = archive.read_bytes()
    archive.write_bytes(data[: len(data) // 2])
    with pytest.raises(UpgradeError):
        safe_extract(archive, tmp_path / "out")


# ── staged self-check (spec §6.2) ─────────────────────────────────────


def _fake_binary(staged: Path, name: str, stdout: str, exit_code: int = 0) -> None:
    staged.mkdir(parents=True, exist_ok=True)
    exe = staged / name
    exe.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stdout}\nEOF\nexit {exit_code}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_reporting_the_expected_version_passes(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", json.dumps({"version": "v0.11.0"}))
    verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_with_a_wrong_version_aborts(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", json.dumps({"version": "v0.9.0"}))
    with pytest.raises(UpgradeError, match="expected v0.11.0"):
        verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_staged_binary_that_exits_non_zero_aborts(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    _fake_binary(staged, "adm-agent", "boom", exit_code=3)
    with pytest.raises(UpgradeError):
        verify_staged_binary(staged, expected_version="v0.11.0", executable_name="adm-agent")


def test_staged_binary_missing_aborts(tmp_path: Path) -> None:
    with pytest.raises(UpgradeError, match="not found"):
        verify_staged_binary(tmp_path, expected_version="v0.11.0", executable_name="adm-agent")
