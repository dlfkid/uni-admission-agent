"""Tests for release resolution and checksum parsing — spec §6.1, §7.1."""

import json
from unittest.mock import patch

import pytest

from src.services.upgrade.release import (
    find_checksums_asset,
    find_release_asset,
    get_platform_info,
    latest_release_url,
    parse_checksums,
    release_api_base,
)


def _release(*names: str) -> dict:
    return {
        "tag_name": "v0.11.0",
        "html_url": "https://example.invalid/r",
        "assets": [
            {"name": n, "browser_download_url": f"https://example.invalid/{n}", "size": 10}
            for n in names
        ],
    }


# ── endpoint override (spec §7.1) ─────────────────────────────────────


def test_default_endpoint_is_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADM_AGENT_RELEASE_API_BASE", raising=False)
    assert release_api_base() == "https://api.github.com/repos"
    assert latest_release_url().endswith("/dlfkid/uni-admission-agent/releases/latest")


def test_endpoint_override_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The release gate verifies an unpublished version, so this must redirect."""
    monkeypatch.setenv("ADM_AGENT_RELEASE_API_BASE", "http://127.0.0.1:9/repos/")
    assert release_api_base() == "http://127.0.0.1:9/repos"
    assert latest_release_url().startswith("http://127.0.0.1:9/repos/")


# ── asset matching ────────────────────────────────────────────────────


def test_find_release_asset_matches_platform_artifact() -> None:
    release = _release(
        "adm-agent-v0.11.0-macos-arm64.tar.gz",
        "adm-agent-v0.11.0-linux-x86_64.tar.gz",
    )
    asset = find_release_asset(release, "macos", "arm64")
    assert asset is not None
    assert asset["name"] == "adm-agent-v0.11.0-macos-arm64.tar.gz"


def test_find_release_asset_uses_zip_on_windows() -> None:
    release = _release("adm-agent-v0.11.0-windows-x86_64.zip")
    assert find_release_asset(release, "windows", "x86_64") is not None


def test_find_release_asset_returns_none_when_absent() -> None:
    release = _release("adm-agent-v0.11.0-macos-arm64.tar.gz")
    assert find_release_asset(release, "linux", "arm64") is None


def test_find_release_asset_selects_the_client_artifact() -> None:
    release = _release(
        "adm-agent-v0.11.0-linux-x86_64.tar.gz",
        "adm-agent-client-v0.11.0-linux-x86_64.tar.gz",
    )
    asset = find_release_asset(release, "linux", "x86_64", artifact_name="adm-agent-client")
    assert asset["name"] == "adm-agent-client-v0.11.0-linux-x86_64.tar.gz"


def test_get_platform_info_normalises() -> None:
    with patch("platform.system", return_value="Darwin"), patch(
        "platform.machine", return_value="arm64"
    ):
        assert get_platform_info() == ("macos", "arm64")


# ── checksums ─────────────────────────────────────────────────────────


def test_find_checksums_asset() -> None:
    release = _release("adm-agent-v0.11.0-macos-arm64.tar.gz", "SHA256SUMS")
    assert find_checksums_asset(release)["name"] == "SHA256SUMS"


def test_find_checksums_asset_absent_on_old_releases() -> None:
    """Releases predating the gate have no SHA256SUMS (spec §6.1)."""
    assert find_checksums_asset(_release("adm-agent-v0.11.0-macos-arm64.tar.gz")) is None


def test_parse_checksums_reads_sha256sum_format() -> None:
    text = (
        "abc123  adm-agent-v0.11.0-macos-arm64.tar.gz\n"
        "def456 *adm-agent-v0.11.0-windows-x86_64.zip\n"
        "\n"
    )
    parsed = parse_checksums(text)
    assert parsed["adm-agent-v0.11.0-macos-arm64.tar.gz"] == "abc123"
    assert parsed["adm-agent-v0.11.0-windows-x86_64.zip"] == "def456"
