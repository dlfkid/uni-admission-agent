"""
Centralised path resolution for dev and frozen (PyInstaller) environments.

All modules that need to locate data files, prompts, or writable
directories should import helpers from this module instead of using
``Path(__file__)`` directly.  This ensures the application works both
when running from source (``uv run``) and when packaged as a
standalone binary via PyInstaller.
"""

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return *True* when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def configure_playwright_path() -> None:
    """Set ``PLAYWRIGHT_BROWSERS_PATH`` so the frozen app uses system browsers.

    When Playwright is bundled by PyInstaller, its default browser lookup
    resolves to ``_internal/playwright/driver/.local-browsers/``, which is
    inside the read-only bundle.  We redirect it to the system's default
    Playwright cache so that ``playwright install chromium`` works as expected.

    The standard cache locations are:

    * **macOS** — ``~/Library/Caches/ms-playwright``
    * **Linux** — ``~/.cache/ms-playwright``
    * **Windows** — ``%LOCALAPPDATA%\\ms-playwright``
    """
    if not is_frozen():
        return
    # Respect an explicit override from the user
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return

    home = Path.home()
    if sys.platform == "darwin":
        browsers = home / "Library" / "Caches" / "ms-playwright"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        browsers = Path(local) / "ms-playwright"
    else:  # Linux / other POSIX
        browsers = home / ".cache" / "ms-playwright"

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)


def get_bundle_dir() -> Path:
    """Root of the PyInstaller bundle, or the project root in dev mode."""
    if is_frozen():
        # PyInstaller unpacks data files here
        return Path(getattr(sys, "_MEIPASS", "."))
    # Dev mode: <project>/src/core/paths.py → two parents up = <project>
    return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """Writable directory for database, logs, and user data.

    * **Dev mode** — ``<project>/data/``
    * **Frozen mode** — ``~/.uni-agent/`` (always writable, even if the
      ``.exe`` lives in a read-only location)
    """
    if is_frozen():
        data = Path.home() / ".uni-agent"
        data.mkdir(parents=True, exist_ok=True)
        return data
    return get_bundle_dir() / "data"


def get_prompts_dir() -> Path:
    """Directory containing LLM prompt templates (``*.txt``).

    Inside a PyInstaller bundle the prompts are extracted to
    ``_MEIPASS/src/agents/prompts/``.
    """
    return get_bundle_dir() / "src" / "agents" / "prompts"
