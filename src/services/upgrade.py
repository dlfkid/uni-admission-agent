#!/usr/bin/env python3
"""
UniAdmission Agent Upgrade Service

Handles automatic updates for the backend executable by:
1. Checking GitHub releases for newer versions
2. Downloading platform-specific backend artifacts
3. Replacing the current executable with the new one
4. Preserving user configuration and data

Note: This only handles backend updates. Chrome extension updates
are handled separately by users downloading from GitHub releases.
"""

import json
import logging
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen, Request

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    # Fallback: use default context (may fail on macOS PyInstaller builds)
    _SSL_CONTEXT = ssl.create_default_context()

logger = logging.getLogger(__name__)

# GitHub configuration
GITHUB_REPO = "dlfkid/uni-admission-agent"
GITHUB_API_BASE = "https://api.github.com/repos"
GITHUB_RELEASE_API = f"{GITHUB_API_BASE}/{GITHUB_REPO}/releases"


class UpgradeError(Exception):
    """Raised when upgrade operations fail."""
    pass


def get_current_version() -> str:
    """Get the current version of the backend executable.

    The build script injects the git-tag version into ``src/__init__.__version__``
    before PyInstaller bundles the package, so a simple import works in both
    frozen (PyInstaller) and normal development contexts.
    """
    try:
        from src import __version__
        ver = __version__
        return ver if ver.startswith("v") else f"v{ver}"
    except ImportError:
        pass
    return "v0.0.0-dev"


def get_platform_info() -> tuple[str, str]:
    """Return (os_name, arch_name) for artifact matching."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize OS
    if system == "darwin":
        os_name = "macos"
    elif system == "windows":
        os_name = "windows"
    else:
        os_name = "linux"

    # Normalize Arch
    if machine in ("amd64", "x86_64"):
        arch_name = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch_name = "arm64"
    else:
        arch_name = machine

    return os_name, arch_name


def get_latest_release() -> dict:
    """Fetch latest release information from GitHub API."""
    api_url = f"{GITHUB_RELEASE_API}/latest"
    
    try:
        with urlopen(api_url, timeout=30, context=_SSL_CONTEXT) as response:
            if response.status != 200:
                raise UpgradeError(f"GitHub API returned status {response.status}")
            
            data = json.loads(response.read().decode())
            return data
    except UpgradeError:
        raise
    except Exception as e:
        raise UpgradeError(f"Failed to fetch release information: {e}")


def find_backend_asset(release_data: dict, os_name: str, arch_name: str) -> dict | None:
    """Find matching backend asset for current platform."""
    assets = release_data.get("assets", [])
    version = release_data.get("tag_name", "unknown")
    
    # Expected filename pattern: adm-agent-{version}-{os}-{arch}.{ext}
    expected_name = f"adm-agent-{version}-{os_name}-{arch_name}"
    extension = ".zip" if os_name == "windows" else ".tar.gz"
    expected_filename = f"{expected_name}{extension}"
    
    for asset in assets:
        if asset["name"] == expected_filename:
            return asset
    
    return None


def download_and_extract(asset: dict, target_dir: Path) -> Path:
    """Download and extract the backend asset to target directory."""
    download_url = asset["browser_download_url"]
    filename = asset["name"]
    
    logger.info(f"Downloading {filename} from {download_url}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        download_file = temp_path / filename
        
        # Download
        try:
            req = Request(download_url)
            with urlopen(req, timeout=300, context=_SSL_CONTEXT) as resp:
                with open(download_file, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
        except Exception as e:
            raise UpgradeError(f"Failed to download {filename}: {e}")
        
        # Extract
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir()
        
        try:
            if download_file.suffix == ".zip":
                with zipfile.ZipFile(download_file, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
            else:  # .tar.gz
                with tarfile.open(download_file, 'r:gz') as tar_ref:
                    tar_ref.extractall(extract_dir)
        except Exception as e:
            raise UpgradeError(f"Failed to extract {filename}: {e}")
        
        # Find the extracted folder (should be single top-level dir)
        extracted_items = list(extract_dir.iterdir())
        if len(extracted_items) != 1 or not extracted_items[0].is_dir():
            raise UpgradeError(f"Unexpected archive structure in {filename}")
        
        source_dir = extracted_items[0]
        
        # Copy contents to target
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        
        for item in source_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, target_dir / item.name)
            else:
                shutil.copy2(item, target_dir / item.name)
        
        # Find the executable
        executable_name = "adm-agent.exe" if platform.system().lower() == "windows" else "adm-agent"
        executable_path = target_dir / executable_name
        
        if not executable_path.exists():
            raise UpgradeError(f"Executable {executable_name} not found in extracted files")
        
        # Set executable permissions on Unix
        if platform.system().lower() != "windows":
            executable_path.chmod(0o755)
        
        return executable_path


def backup_current_executable() -> Path:
    """Create a backup of the current executable."""
    current_exe = Path(sys.executable)
    backup_path = current_exe.with_suffix(f"{current_exe.suffix}.backup")
    
    try:
        shutil.copy2(current_exe, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path
    except Exception as e:
        raise UpgradeError(f"Failed to create backup: {e}")


def replace_executable(new_exe: Path, backup_path: Path) -> None:
    """Replace current executable with new one, with rollback on failure.""" 
    current_exe = Path(sys.executable)
    
    try:
        # On Windows, may need to rename instead of overwrite
        if platform.system().lower() == "windows":
            temp_name = current_exe.with_suffix(".old")
            if temp_name.exists():
                temp_name.unlink()
            current_exe.rename(temp_name)
            shutil.copy2(new_exe, current_exe)
            temp_name.unlink()  # Remove the old version
        else:
            shutil.copy2(new_exe, current_exe)
            
        logger.info(f"Successfully replaced executable: {current_exe}")
        
        # Remove backup if successful
        if backup_path.exists():
            backup_path.unlink()
            
    except Exception as e:
        # Attempt rollback
        logger.error(f"Failed to replace executable: {e}")
        try:
            if backup_path.exists():
                shutil.copy2(backup_path, current_exe)
                logger.info("Restored from backup")
                backup_path.unlink()
        except Exception as rollback_error:
            logger.error(f"Rollback also failed: {rollback_error}")
            
        raise UpgradeError(f"Failed to replace executable: {e}")


def check_for_updates(verbose: bool = False) -> dict:
    """Check for available updates without downloading."""
    current_version = get_current_version()
    
    if verbose:
        logger.info(f"Current version: {current_version}")
    
    try:
        latest_release = get_latest_release()
        latest_version = latest_release.get("tag_name", "unknown")
        
        if verbose:
            logger.info(f"Latest version: {latest_version}")
        
        # Simple version comparison (comparing tag names)
        is_newer = latest_version != current_version and latest_version > current_version
        
        os_name, arch_name = get_platform_info()
        asset = find_backend_asset(latest_release, os_name, arch_name)
        
        return {
            "current_version": current_version,
            "latest_version": latest_version,
            "is_newer": is_newer,
            "asset_available": asset is not None,
            "asset": asset,
            "release_url": latest_release.get("html_url"),
        }
    except Exception as e:
        if verbose:
            logger.error(f"Failed to check for updates: {e}")
        return {
            "current_version": current_version,
            "latest_version": "unknown",
            "is_newer": False,
            "asset_available": False,
            "error": str(e)
        }


def upgrade_backend(force: bool = False, verbose: bool = False) -> bool:
    """Perform backend upgrade if newer version available."""
    logger.info("🔍 Checking for updates...")
    
    update_info = check_for_updates(verbose=verbose)
    
    if "error" in update_info:
        raise UpgradeError(f"Update check failed: {update_info['error']}")
    
    current_version = update_info["current_version"]
    latest_version = update_info["latest_version"]
    
    if not update_info["is_newer"] and not force:
        logger.info(f"✅ Already on latest version: {current_version}")
        return False
    
    if not update_info["asset_available"]:
        os_name, arch_name = get_platform_info()
        raise UpgradeError(
            f"No backend asset found for {os_name}-{arch_name}. "
            "Check GitHub releases for manual download options."
        )
    
    asset = update_info["asset"]
    logger.info(f"🎯 Updating from {current_version} to {latest_version}")
    
    # Create temporary directory for new version
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Download and extract
        logger.info("⬇️  Downloading new version...")
        new_executable = download_and_extract(asset, temp_path / "new_version")
        
        # Backup current version
        logger.info("💾 Creating backup...")
        backup_path = backup_current_executable()
        
        try:
            # Replace executable
            logger.info("🔄 Installing update...")
            replace_executable(new_executable, backup_path)
            
            logger.info(f"✅ Successfully upgraded to {latest_version}")
            logger.info("ℹ️  You may need to restart the server for changes to take effect.")
            return True
            
        except Exception as e:
            logger.error(f"❌ Upgrade failed: {e}")
            raise
