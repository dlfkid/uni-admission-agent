#!/usr/bin/env python3
"""
Build & Release script for UniAdmission Agent.

Orchestrates the full build pipeline:
  1. Clean old artefacts
  2. Build the Chrome Extension  (npm run build)
  3. Build the Backend Engine    (PyInstaller)
  4. Assemble a ``release/`` folder ready for distribution

Usage:
    python scripts/build_dist.py          # full pipeline
    python scripts/build_dist.py --skip-extension   # backend only
"""

import argparse
import logging
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

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
RELEASE_DIR = PROJECT_ROOT / "release"

# PyInstaller output (default for --distpath / --workpath)
PI_DIST = PROJECT_ROOT / "dist"
PI_BUILD = PROJECT_ROOT / "build"

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


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def clean() -> None:
    """Remove previous build artefacts."""
    logger.info("🧹 Cleaning old build artefacts …")
    for d in (RELEASE_DIR, PI_DIST, PI_BUILD):
        if d.exists():
            shutil.rmtree(d)
            logger.info("  Removed %s", d.relative_to(PROJECT_ROOT))


def build_extension() -> Path:
    """Build the Chrome extension and return the path to the zip file.

    Returns:
        Path to ``extension/uni-admission-extension.zip``.
    """
    logger.info("🔌 Building Chrome Extension …")
    _ensure_tool("npm", "https://nodejs.org/")

    _run(["npm", "install"], cwd=EXTENSION_DIR, label="ext")
    _run(["npm", "run", "build"], cwd=EXTENSION_DIR, label="ext")

    zip_path = EXTENSION_DIR / "uni-admission-extension.zip"
    if not zip_path.exists():
        raise FileNotFoundError(
            f"Expected extension zip not found: {zip_path}"
        )
    logger.info("  ✅ Extension zip ready: %s", zip_path.name)
    return zip_path


def build_engine() -> Path:
    """Run PyInstaller from the ``.spec`` file.

    Returns:
        Path to the ``dist/adm-agent/`` directory.
    """
    logger.info("⚙️  Building Backend Engine via PyInstaller …")
    
    # We run PyInstaller via the current python interpreter to ensure it detects
    # the packages installed in the current environment (e.g. .venv).
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPEC_FILE),
        "--noconfirm",
        "--clean",
        "--distpath", str(PI_DIST),
        "--workpath", str(PI_BUILD),
    ]

    if not SPEC_FILE.exists():
        raise FileNotFoundError(f"Spec file not found: {SPEC_FILE}")

    _run(
        cmd,
        cwd=PROJECT_ROOT,
        label="pyinstaller",
    )

    engine_dir = PI_DIST / ENGINE_NAME
    if not engine_dir.exists():
        raise FileNotFoundError(
            f"PyInstaller output not found: {engine_dir}"
        )
    logger.info("  ✅ Engine built: %s", engine_dir)
    return engine_dir


def _write_readme(dest: Path) -> None:
    """Generate a minimal plain-text quick-start guide."""
    is_windows = platform.system() == "Windows"
    exe_name = f"{ENGINE_NAME}.exe" if is_windows else f"./{ENGINE_NAME}"

    content = textwrap.dedent(f"""\
    ╔══════════════════════════════════════════════════════════════╗
    ║               UniAdmission Agent  —  Quick Start            ║
    ╚══════════════════════════════════════════════════════════════╝

    1. PREREQUISITES
    ────────────────
    • PostgreSQL 14+ running and accessible.
    • Chromium browser for Playwright:
        {exe_name} check
      If Playwright reports missing browsers, run:
        playwright install chromium

    2. CONFIGURATION
    ────────────────
    Create a  .env  file next to the executable (or in ~/.uni-agent/)
    with at least:

        DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/uni_admission
        GEMINI_API_KEY=your-key-here

    3. COMMANDS
    ───────────
    {exe_name} check                     — Verify environment
    {exe_name} status                    — Show database stats
    {exe_name} serve                     — Start API + MCP server (port 8910)
    {exe_name} crawl --name hku --year 2026 --url <URL>
    {exe_name} import --name hku --year 2026 --file data.xlsx

    4. CHROME EXTENSION
    ────────────────────
    • Unzip  extension.zip  into a folder.
    • Open chrome://extensions → Developer mode → Load unpacked → pick
      the unzipped folder.
    • Click the extension icon to interact with the running server.

    5. DATA STORAGE
    ────────────────
    Logs and local data are stored in:
        {"%%APPDATA%%\\\\uni-agent\\\\" if is_windows else "~/.uni-agent/"}
    """)
    readme_path = dest / "README.txt"
    readme_path.write_text(content, encoding="utf-8")
    logger.info("  Wrote %s", readme_path.name)


def assemble_release(
    engine_dir: Path,
    extension_zip: Path | None = None,
) -> Path:
    """Copy built artefacts into ``release/``."""
    logger.info("📦 Assembling release folder …")
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # --- Engine (copy entire directory) ---
    dest_engine = RELEASE_DIR / ENGINE_NAME
    if dest_engine.exists():
        shutil.rmtree(dest_engine)
    shutil.copytree(engine_dir, dest_engine)
    logger.info("  Copied engine → %s", dest_engine.relative_to(PROJECT_ROOT))

    # --- Extension zip ---
    if extension_zip and extension_zip.exists():
        dest_zip = RELEASE_DIR / "extension.zip"
        shutil.copy2(extension_zip, dest_zip)
        logger.info("  Copied extension → %s", dest_zip.name)

    # --- .env.example ---
    env_example = PROJECT_ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, RELEASE_DIR / ".env.example")
        logger.info("  Copied .env.example")

    # --- README.txt ---
    _write_readme(RELEASE_DIR)

    logger.info("✅  Release assembled at: %s", RELEASE_DIR)
    return RELEASE_DIR


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build & package UniAdmission Agent for distribution.",
    )
    parser.add_argument(
        "--skip-extension",
        action="store_true",
        help="Skip building the Chrome extension.",
    )
    args = parser.parse_args()

    try:
        clean()

        extension_zip: Path | None = None
        if not args.skip_extension:
            extension_zip = build_extension()

        engine_dir = build_engine()
        assemble_release(engine_dir, extension_zip)

        logger.info("🎉 Build complete!  Check the  release/  folder.")
    except Exception as exc:
        logger.error("❌ Build failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
