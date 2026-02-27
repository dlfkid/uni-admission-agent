#!/usr/bin/env python3
"""
Build & Release script for UniAdmission Agent.

Orchestrates the full build pipeline:
  1. Detect Environment (OS, Arch, Version)
  2. Clean old artefacts
  3. Build the Chrome Extension (npm run build)
  4. Build the Backend Engine (PyInstaller)
  5. Package for Release (Zip/Tar.gz with proper naming)

Usage:
    python scripts/build_dist.py
    python scripts/build_dist.py --skip-frontend-build
"""

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

# Try tomllib for parsing pyproject.toml (Python 3.11+)
if sys.version_info >= (3, 11):
    import tomllib
else:
    # Fallback or error if < 3.11. The project requires >= 3.12 so this is fine.
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_dist")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
EXTENSION_DIR = PROJECT_ROOT / "extension"
SPEC_FILE = PROJECT_ROOT / "adm-agent.spec"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"

# Intermediate build dirs
PI_DIST = PROJECT_ROOT / "dist"
PI_BUILD = PROJECT_ROOT / "build"

# Final release output
RELEASE_ROOT = PROJECT_ROOT / "dist" / "release"

ENGINE_NAME = "adm-agent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path | None = None, label: str = "") -> None:
    """Run a command with live output, raise on failure."""
    tag = f"[{label}] " if label else ""
    logger.info("%sRunning: %s", tag, " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"{tag}Command failed (exit {result.returncode}): {' '.join(cmd)}"
        )


def _ensure_tool(name: str, install_hint: str) -> None:
    """Check that a CLI tool is available on PATH."""
    if shutil.which(name) is None:
        raise EnvironmentError(
            f"'{name}' not found on PATH. Install it first:\n  {install_hint}"
        )


def get_version() -> str:
    """Detect version from Env -> Git -> pyproject.toml."""
    # 1. CI Environment
    env_ver = os.environ.get("GITHUB_REF_NAME")
    if env_ver and env_ver.startswith("v"):
        return env_ver

    # 2. Git Tags
    try:
        git_ver = subprocess.check_output(
            ["git", "describe", "--tags"], cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
        if git_ver:
            return git_ver
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 3. pyproject.toml
    if PYPROJECT_FILE.exists() and tomllib:
        try:
            with open(PYPROJECT_FILE, "rb") as f:
                data = tomllib.load(f)
            ver = data.get("project", {}).get("version")
            if ver:
                return f"v{ver}"
        except Exception:
            logger.warning("Failed to parse pyproject.toml")

    return "v0.0.0-dev"


def get_platform_info() -> tuple[str, str]:
    """Return (os_name, arch_name).
    
    OS: windows, macos, linux
    Arch: x86_64, arm64
    """
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
        arch_name = machine  # Fallback

    return os_name, arch_name


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def clean() -> None:
    """Remove previous build artefacts."""
    logger.info("🧹 Cleaning old build artefacts …")
    for d in (PI_DIST, PI_BUILD):
        if d.exists():
            shutil.rmtree(d)
            logger.info("  Removed %s", d.relative_to(PROJECT_ROOT))
    
    # Ensure release dir exists and is clean-ish (we append to it usually, but let's ensure it exists)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)


def prepare_extension_version(version: str) -> Path:
    """Create a temporary extension directory with updated version numbers."""
    logger.info("📝 Preparing extension with version %s", version)
    
    # Remove 'v' prefix if present
    clean_version = version.lstrip('v')
    
    # Chrome extension version format: up to 4 dot-separated integers (e.g., 0.4.6 or 0.4.6.1)
    # Remove any pre-release identifiers (alpha, beta, rc, etc.)
    import re
    # Match: digits.digits.digits[.digits] and stop at first non-numeric part
    match = re.match(r'^(\d+(?:\.\d+){0,3})', clean_version)
    if match:
        chrome_version = match.group(1)
    else:
        # Fallback: if no valid version found, use a safe default
        logger.warning("Could not parse version '%s', using 0.0.1", clean_version)
        chrome_version = "0.0.1"
    
    logger.info("  → Chrome-compatible version: %s", chrome_version)
    
    # Create temporary extension directory
    temp_ext_dir = PI_DIST / "temp_extension"
    if temp_ext_dir.exists():
        shutil.rmtree(temp_ext_dir)
    
    # Copy extension source to temp directory
    shutil.copytree(EXTENSION_DIR, temp_ext_dir, ignore=shutil.ignore_patterns('node_modules', 'dist', '*.zip'))
    
    # Update package.json in temp directory
    temp_package_json = temp_ext_dir / "package.json"
    if temp_package_json.exists():
        with open(temp_package_json, 'r', encoding='utf-8') as f:
            package_data = json.load(f)
        
        package_data["version"] = chrome_version
        
        with open(temp_package_json, 'w', encoding='utf-8') as f:
            json.dump(package_data, f, indent=4, ensure_ascii=False)
        
        logger.info("  ✅ Updated temp package.json")
    
    # Update manifest.json in temp directory
    temp_manifest = temp_ext_dir / "public" / "manifest.json"
    if temp_manifest.exists():
        with open(temp_manifest, 'r', encoding='utf-8') as f:
            manifest_data = json.load(f)
        
        manifest_data["version"] = chrome_version
        
        with open(temp_manifest, 'w', encoding='utf-8') as f:
            json.dump(manifest_data, f, indent=4, ensure_ascii=False)
        
        logger.info("  ✅ Updated temp manifest.json")
    
    return temp_ext_dir


def build_extension(version: str | None = None) -> Path:
    """Build the Chrome extension and return the path to the zip file."""
    logger.info("🔌 Building Chrome Extension …")
    _ensure_tool("npm", "https://nodejs.org/")
    
    # Determine build directory
    if version:
        # Use temporary directory with updated version
        build_dir = prepare_extension_version(version)
    else:
        # Use original extension directory
        build_dir = EXTENSION_DIR

    _run(["npm", "install"], cwd=build_dir, label="ext")
    _run(["npm", "run", "build"], cwd=build_dir, label="ext")

    # The package script usually zips it? 
    # If not, we should zip the dist folder. 
    # Current assumption: 'npm run build' creates a 'dist' folder. 
    # Let's create the zip manually to be safe and consistent.
    
    dist_dir = build_dir / "dist"
    zip_path = build_dir / "uni-admission-extension.zip"
    
    if not dist_dir.exists():
        # Fallback: maybe the build script already made the zip?
        if zip_path.exists():
             logger.info("  ✅ Extension zip found (pre-built): %s", zip_path.name)
             return zip_path
        raise FileNotFoundError(f"Extension build failed: {dist_dir} not found")

    logger.info("  Zipping extension dist -> %s", zip_path.name)
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", dist_dir)
    
    # Clean up temporary directory if used
    if version and build_dir != EXTENSION_DIR:
        # Copy zip back to original extension directory for consistency
        final_zip_path = EXTENSION_DIR / "uni-admission-extension.zip" 
        shutil.copy2(zip_path, final_zip_path)
        logger.info("  📋 Copied zip to %s", final_zip_path.relative_to(PROJECT_ROOT))
        return final_zip_path
    
    return zip_path


def codesign_macos(target_dir: Path) -> None:
    """Ad-hoc codesign all Mach-O binaries so macOS Gatekeeper won't block them.

    This signs every .so, .dylib and the main executable with an ad-hoc
    identity (`-s -`).  No Apple Developer certificate is required.
    Libraries must be signed *before* the main executable.
    """
    if platform.system().lower() != "darwin":
        return

    if shutil.which("codesign") is None:
        logger.warning("  ⚠️  codesign not found — skipping macOS signing")
        return

    logger.info("🔏 Ad-hoc codesigning macOS binaries in %s …", target_dir.name)

    # Collect all signable files  (.so, .dylib, and the main executable)
    signable: list[Path] = []
    main_exe: Path | None = None

    for path in sorted(target_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in (".so", ".dylib"):
            signable.append(path)
        elif path.name == ENGINE_NAME and path.stat().st_mode & 0o111:
            main_exe = path

    # Sign libraries first, then the main executable
    signed = 0
    for binary in signable:
        try:
            subprocess.run(
                ["codesign", "-s", "-", "--force", "--timestamp",
                 "--preserve-metadata=entitlements,requirements,flags",
                 str(binary)],
                check=True,
                capture_output=True,
            )
            signed += 1
        except subprocess.CalledProcessError as exc:
            logger.warning("  ⚠️  Failed to sign %s: %s", binary.name, exc.stderr.decode().strip())

    if main_exe:
        try:
            subprocess.run(
                ["codesign", "-s", "-", "--force", "--timestamp",
                 "--preserve-metadata=entitlements,requirements,flags",
                 str(main_exe)],
                check=True,
                capture_output=True,
            )
            signed += 1
        except subprocess.CalledProcessError as exc:
            logger.warning("  ⚠️  Failed to sign main exe: %s", exc.stderr.decode().strip())

    logger.info("  ✅ Signed %d binaries", signed)


def _inject_version(version: str) -> str:
    """Write *version* into ``src/__init__.py`` and return the original content."""
    init_file = PROJECT_ROOT / "src" / "__init__.py"
    original = init_file.read_text(encoding="utf-8")
    clean_ver = version.lstrip("v")
    new_content = f'"""Version information for UniAdmission Agent."""\n\n__version__ = "{clean_ver}"\n'
    init_file.write_text(new_content, encoding="utf-8")
    logger.info("  📝 Injected version %s into src/__init__.py", clean_ver)
    return original


def _restore_version(original: str) -> None:
    """Restore ``src/__init__.py`` to its *original* content."""
    init_file = PROJECT_ROOT / "src" / "__init__.py"
    init_file.write_text(original, encoding="utf-8")
    logger.info("  📝 Restored src/__init__.py")


def build_engine(version: str) -> Path:
    """Run PyInstaller. Returns path to the compiled directory.
    
    The detected *version* is injected into ``src/__init__.py`` before
    the build so that the frozen binary reports the correct version.
    """
    logger.info("⚙️  Building Backend Engine via PyInstaller …")

    # Inject version into source before PyInstaller analyses imports
    original_init = _inject_version(version)

    try:
        cmd = [
            sys.executable, "-m", "PyInstaller",
            str(SPEC_FILE),
            "--noconfirm",
            "--clean",
            "--distpath", str(PI_DIST),
            "--workpath", str(PI_BUILD),
        ]

        _run(cmd, cwd=PROJECT_ROOT, label="pyinstaller")
    finally:
        # Always restore the original file so the working tree stays clean
        _restore_version(original_init)

    engine_dir = PI_DIST / ENGINE_NAME
    if not engine_dir.exists():
        raise FileNotFoundError(f"PyInstaller output not found: {engine_dir}")
    
    # Ad-hoc codesign on macOS to prevent Gatekeeper blocks
    codesign_macos(engine_dir)

    logger.info("  ✅ Engine built: %s", engine_dir)
    return engine_dir


def _write_readme(dest: Path, exe_name: str) -> None:
    """Generate a minimal plain-text quick-start guide."""
    content = textwrap.dedent(f"""\
    ╔══════════════════════════════════════════════════════════════╗
    ║               UniAdmission Agent  —  Quick Start            ║
    ╚══════════════════════════════════════════════════════════════╝

    1. PREREQUISITES
    ────────────────
    • PostgreSQL 14+ running.
    • Chromium browser (automatically installed on first run check).

    2. macOS USERS — REMOVE QUARANTINE
    ──────────────────────────────────
    macOS may block the app because it was downloaded from the internet.
    Run this ONCE after extracting:

        xattr -cr .                       (inside this folder)

    3. SETUP
    ────────
    • Copy .env.example to .env and configure DATABASE_URL.
    • Run:
        {exe_name} check

    4. USAGE
    ────────
    {exe_name} serve                     — Start API + MCP server
    {exe_name} crawl --help              — Crawl manually

    5. CHROME EXTENSION
    ────────────────────
    • Unzip extension.zip.
    • Load unpacked in Chrome Developer Mode.
    """)
    (dest / "README.txt").write_text(content, encoding="utf-8")


def package_release(
    engine_dir: Path,
    extension_zip: Path | None,
    version: str,
    os_name: str,
    arch_name: str,
) -> Path:
    """Create the final zip/tar.gz archive."""
    logger.info("📦 Packaging release …")
    
    # Artifact name: adm-agent-{version}-{os}-{arch}
    base_name = f"adm-agent-{version}-{os_name}-{arch_name}"
    
    # Staging directory for the archive content
    staging_dir = PI_DIST / base_name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir()
    
    # 1. Copy Engine
    # If Windows, exe is inside engine_dir. 
    # We want the user to see the files directly in the folder or a subfolder?
    # Standard practice: extract to a folder.
    
    # Copy all content from engine_dir to staging_dir
    shutil.copytree(engine_dir, staging_dir, dirs_exist_ok=True)
    
    # 2. Copy Extension
    if extension_zip and extension_zip.exists():
        shutil.copy2(extension_zip, staging_dir / "extension.zip")
        
    # 3. Copy .env.example
    env_example = PROJECT_ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, staging_dir / ".env.example")
        
    # 4. README
    exe_name = f"{ENGINE_NAME}.exe" if os_name == "windows" else f"./{ENGINE_NAME}"
    _write_readme(staging_dir, exe_name)
    
    # 5. Archive
    # NOTE: Do NOT use Path.with_suffix() here — pathlib treats the dot
    # in version strings like "v0.3" as a file-extension separator, which
    # silently strips the platform suffix and causes name collisions.
    if os_name == "windows":
        final_file = RELEASE_ROOT / f"{base_name}.zip"
        shutil.make_archive(
            str(RELEASE_ROOT / base_name),
            "zip",
            root_dir=PI_DIST,
            base_dir=base_name,
        )
    else:
        # .tar.gz for macOS / Linux — use tarfile to preserve permissions
        # Use GNU_FORMAT to avoid PAX extended headers that confuse some
        # extractors and to keep macOS xattr metadata out of the archive.
        final_file = RELEASE_ROOT / f"{base_name}.tar.gz"
        with tarfile.open(final_file, "w:gz", format=tarfile.GNU_FORMAT) as tar:
            for entry_path in sorted(staging_dir.rglob("*")):
                arcname = str(Path(base_name) / entry_path.relative_to(staging_dir))
                info = tar.gettarinfo(str(entry_path), arcname=arcname)
                # Strip macOS quarantine / resource-fork xattrs
                info.pax_headers = {}
                if entry_path.is_file():
                    with open(entry_path, "rb") as fh:
                        tar.addfile(info, fh)
                else:
                    tar.addfile(info)

    logger.info("  ✅ Created archive: %s", final_file)
    return final_file


def package_extension_release(extension_zip: Path, version: str) -> Path:
    """Create a standalone extension release artifact."""
    logger.info("📦 Packaging extension release …")
    
    # Extension artifact name: uni-admission-extension-{version}.zip
    final_name = f"uni-admission-extension-{version}.zip"
    final_file = RELEASE_ROOT / final_name
    
    # Simply copy the extension zip with the versioned name
    shutil.copy2(extension_zip, final_file)
    
    logger.info("  ✅ Created extension artifact: %s", final_file)
    return final_file


def package_backend_release(
    engine_dir: Path, version: str, os_name: str, arch_name: str
) -> Path:
    """Create a backend-only release artifact.""" 
    logger.info("📦 Packaging backend release …")
    
    # Artifact name: adm-agent-{version}-{os}-{arch}
    base_name = f"adm-agent-{version}-{os_name}-{arch_name}"
    
    # Staging directory for the archive content
    staging_dir = PI_DIST / base_name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir()
    
    # 1. Copy Engine (all content from engine_dir to staging_dir)
    shutil.copytree(engine_dir, staging_dir, dirs_exist_ok=True)
        
    # 2. Copy .env.example
    env_example = PROJECT_ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, staging_dir / ".env.example")
        
    # 3. README (backend-only version)
    exe_name = f"{ENGINE_NAME}.exe" if os_name == "windows" else f"./{ENGINE_NAME}"
    _write_backend_readme(staging_dir, exe_name)
    
    # 4. Archive (same logic as combined package)
    if os_name == "windows":
        final_file = RELEASE_ROOT / f"{base_name}.zip"
        shutil.make_archive(
            str(RELEASE_ROOT / base_name),
            "zip", 
            root_dir=PI_DIST,
            base_dir=base_name,
        )
    else:
        # .tar.gz for macOS / Linux — use tarfile to preserve permissions
        final_file = RELEASE_ROOT / f"{base_name}.tar.gz"
        with tarfile.open(final_file, "w:gz", format=tarfile.GNU_FORMAT) as tar:
            for entry_path in sorted(staging_dir.rglob("*")):
                arcname = str(Path(base_name) / entry_path.relative_to(staging_dir))
                info = tar.gettarinfo(str(entry_path), arcname=arcname)
                info.pax_headers = {}
                if entry_path.is_file():
                    with open(entry_path, "rb") as fh:
                        tar.addfile(info, fh)
                else:
                    tar.addfile(info)

    logger.info("  ✅ Created backend artifact: %s", final_file)
    return final_file


def _write_backend_readme(dest: Path, exe_name: str) -> None:
    """Generate a backend-only README with extension download instructions."""
    content = textwrap.dedent(f"""\
    ╔══════════════════════════════════════════════════════════════╗
    ║               UniAdmission Agent Backend  —  Quick Start    ║
    ╚══════════════════════════════════════════════════════════════╝

    1. PREREQUISITES
    ────────────────
    • PostgreSQL 14+ running.
    • Chrome extension: Download separately from GitHub Releases

    2. macOS USERS — REMOVE QUARANTINE
    ──────────────────────────────────
    macOS may block the app because it was downloaded from the internet.
    Run this ONCE after extracting:

        xattr -cr .                       (inside this folder)

    3. SETUP
    ────────
    • Copy .env.example to .env and configure DATABASE_URL.
    • Run:
        {exe_name} check

    4. USAGE
    ────────
    {exe_name} serve                     — Start API + MCP server
    {exe_name} crawl --help              — Crawl manually
    {exe_name} upgrade                   — Auto-update backend
    {exe_name} version                   — Show current version

    5. CHROME EXTENSION
    ────────────────────
    • Download uni-admission-extension-vX.X.X.zip from GitHub releases
    • Unzip and load unpacked in Chrome Developer Mode
    • Extension and backend can be updated independently
    """)
    (dest / "README.txt").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="UniAdmission Agent Build Script")
    parser.add_argument("--skip-extension", action="store_true", help="Skip extension build")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Use existing extension zip")
    parser.add_argument("--extension-only", action="store_true", help="Build only Chrome extension")
    parser.add_argument("--backend-only", action="store_true", help="Build only backend executable")
    parser.add_argument("--separate-artifacts", action="store_true", help="Create separate extension and backend artifacts")
    args = parser.parse_args()

    try:
        # 1. Detect Environment
        version = get_version()
        os_name, arch = get_platform_info()
        logger.info(f"🚀 Starting Build: {version} on {os_name}-{arch}")

        clean()

        # 2. Extension-only build
        if args.extension_only:
            logger.info("📦 Building Chrome Extension only")
            extension_zip = build_extension(version)
            # Package extension separately
            package_extension_release(extension_zip, version)
            logger.info("✅ Extension build completed")
            return

        # 3. Backend-only build  
        if args.backend_only:
            logger.info("⚙️ Building Backend only")
            engine_dir = build_engine(version)
            # Package backend without extension
            package_backend_release(engine_dir, version, os_name, arch)
            logger.info("✅ Backend build completed")
            return

        # 4. Separate artifacts mode
        if args.separate_artifacts:
            logger.info("📦 Building separate artifacts")
            
            # Build extension
            extension_zip = None
            if not args.skip_extension:
                if args.skip_frontend_build:
                    zip_path = EXTENSION_DIR / "uni-admission-extension.zip"
                    if zip_path.exists():
                        extension_zip = zip_path
                        logger.info("  Using existing extension zip")
                    else:
                        logger.warning("  ⚠️ Extension zip not found, will build new one")
                        extension_zip = build_extension(version)
                else:
                    extension_zip = build_extension(version)
                
                if extension_zip:
                    package_extension_release(extension_zip, version)

            # Build backend
            engine_dir = build_engine(version)
            package_backend_release(engine_dir, version, os_name, arch)
            logger.info("✅ Separate artifacts build completed")
            return

        # 5. Legacy combined build (default)
        logger.info("📦 Building combined artifacts (legacy mode)")
        # Extension
        extension_zip: Path | None = None
        if not args.skip_extension:
            if args.skip_frontend_build:
                # Look for existing
                zip_path = EXTENSION_DIR / "uni-admission-extension.zip"
                if zip_path.exists():
                    extension_zip = zip_path
                    logger.info("  Using existing extension zip")
                else:
                    logger.warning("  ⚠️ Extension zip not found, skipping inclusion")
            else:
                extension_zip = build_extension(version)

        # Engine
        engine_dir = build_engine(version)

        # Package (combined)
        package_release(engine_dir, extension_zip, version, os_name, arch)
        logger.info("✅ Combined build completed")

    except Exception as exc:
        logger.error("❌ Build failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
