"""Tests for src.core.paths — path resolution for dev and frozen modes."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.paths import (
    configure_playwright_path,
    get_bundle_dir,
    get_data_dir,
    get_prompts_dir,
    is_frozen,
)


# ── is_frozen ─────────────────────────────────────────────────────────


def test_is_frozen_false() -> None:
    assert is_frozen() is False


def test_is_frozen_true() -> None:
    with patch.object(sys, "frozen", True, create=True):
        assert is_frozen() is True


# ── get_bundle_dir ────────────────────────────────────────────────────


def test_get_bundle_dir_dev() -> None:
    result = get_bundle_dir()
    # In dev mode, should point to project root (parent of src/core/paths.py)
    assert result.is_dir()
    assert (result / "src").exists()


def test_get_bundle_dir_frozen() -> None:
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", "/tmp/fake_meipass", create=True):
        result = get_bundle_dir()
        assert result == Path("/tmp/fake_meipass")


# ── get_data_dir ──────────────────────────────────────────────────────


def test_get_data_dir_dev() -> None:
    result = get_data_dir()
    assert result.name == "data"
    # Parent should equal the bundle dir
    assert result.parent == get_bundle_dir()


def test_get_data_dir_frozen(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    with patch.object(sys, "frozen", True, create=True), \
         patch("src.core.paths.Path.home", return_value=fake_home):
        result = get_data_dir()
        assert result == fake_home / ".uni-agent"
        assert result.exists()


# ── get_prompts_dir ───────────────────────────────────────────────────


def test_get_prompts_dir() -> None:
    result = get_prompts_dir()
    assert result.name == "prompts"
    assert "src" in str(result)
    assert "agents" in str(result)


# ── configure_playwright_path ─────────────────────────────────────────


def test_configure_playwright_path_not_frozen() -> None:
    """In dev mode, should be a no-op."""
    original = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    configure_playwright_path()
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == original


def test_configure_playwright_path_frozen_macos(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    with patch.object(sys, "frozen", True, create=True), \
         patch("src.core.paths.Path.home", return_value=fake_home), \
         patch.dict(os.environ, env, clear=True), \
         patch("src.core.paths.sys.platform", "darwin"):
        configure_playwright_path()
        expected = str(fake_home / "Library" / "Caches" / "ms-playwright")
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == expected


def test_configure_playwright_path_respects_existing_env() -> None:
    """If PLAYWRIGHT_BROWSERS_PATH is already set, do not override."""
    with patch.object(sys, "frozen", True, create=True), \
         patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/custom/path"}):
        configure_playwright_path()
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/custom/path"
