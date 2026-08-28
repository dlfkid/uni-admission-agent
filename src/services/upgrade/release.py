"""GitHub release resolution (spec §6.1, §7.1)."""
from __future__ import annotations

import json
import os
import platform
import ssl
from urllib.request import Request, urlopen

from src.services.upgrade.types import UpgradeError

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - depends on the frozen bundle
    _SSL_CONTEXT = ssl.create_default_context()

GITHUB_REPO = "dlfkid/uni-admission-agent"
DEFAULT_API_BASE = "https://api.github.com/repos"
CHECKSUMS_ASSET_NAME = "SHA256SUMS"


def release_api_base() -> str:
    """Release API root. Overridable for the release gate and unit tests."""
    return os.environ.get("ADM_AGENT_RELEASE_API_BASE", DEFAULT_API_BASE).rstrip("/")


def latest_release_url() -> str:
    return f"{release_api_base()}/{GITHUB_REPO}/releases/latest"


def fetch_latest_release() -> dict:
    """Fetch the release marked latest. Raises :class:`UpgradeError`."""
    try:
        with urlopen(latest_release_url(), timeout=30, context=_SSL_CONTEXT) as response:
            if response.status != 200:
                raise UpgradeError(f"Release API returned status {response.status}")
            return json.loads(response.read().decode())
    except UpgradeError:
        raise
    except Exception as exc:
        raise UpgradeError(f"Failed to fetch release information: {exc}") from exc


def fetch_text(url: str) -> str:
    """Fetch a small text asset (used for SHA256SUMS)."""
    try:
        with urlopen(Request(url), timeout=60, context=_SSL_CONTEXT) as response:
            return response.read().decode()
    except Exception as exc:
        raise UpgradeError(f"Failed to fetch {url}: {exc}") from exc


def get_platform_info() -> tuple[str, str]:
    """Return ``(os_name, arch_name)`` used in release artifact filenames."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_name = "macos"
    elif system == "windows":
        os_name = "windows"
    else:
        os_name = "linux"

    if machine in ("amd64", "x86_64"):
        arch_name = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch_name = "arm64"
    else:
        arch_name = machine

    return os_name, arch_name


def find_release_asset(
    release: dict,
    os_name: str,
    arch_name: str,
    artifact_name: str = "adm-agent",
) -> dict | None:
    """Find the asset matching ``<artifact>-<tag>-<os>-<arch>.<ext>``."""
    version = release.get("tag_name", "unknown")
    extension = ".zip" if os_name == "windows" else ".tar.gz"
    expected = f"{artifact_name}-{version}-{os_name}-{arch_name}{extension}"
    for asset in release.get("assets", []):
        if asset.get("name") == expected:
            return asset
    return None


def find_checksums_asset(release: dict) -> dict | None:
    """Find the ``SHA256SUMS`` asset. Absent on releases predating the gate."""
    for asset in release.get("assets", []):
        if asset.get("name") == CHECKSUMS_ASSET_NAME:
            return asset
    return None


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output into ``{filename: digest}``."""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition(" ")
        name = name.strip().lstrip("*")
        if digest and name:
            parsed[name] = digest
    return parsed
