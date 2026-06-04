"""Tests for .env discovery — especially the installed-binary case where
the .env lives at ~/.uni-agent/.env but the user runs adm-agent from an
unrelated working directory.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.storage import db_helpers


def _clear_marker(monkeypatch) -> None:
    monkeypatch.delenv("UNI_ADMISSION_ENV_MARKER", raising=False)


def test_loads_home_uni_agent_env_when_cwd_has_none(monkeypatch, tmp_path):
    """When no .env is discoverable from cwd, fall back to ~/.uni-agent/.env.

    Simulates the frozen-binary case: there is no project ``.env`` anywhere
    in the search tree, so ``find_dotenv`` returns "" and the home fallback
    must kick in.
    """
    _clear_marker(monkeypatch)

    # Fake HOME with a ~/.uni-agent/.env containing a marker var.
    fake_home = tmp_path / "home"
    uni_dir = fake_home / ".uni-agent"
    uni_dir.mkdir(parents=True)
    (uni_dir / ".env").write_text("UNI_ADMISSION_ENV_MARKER=from_home\n", encoding="utf-8")

    # Frozen binary has no discoverable project .env — force both
    # find_dotenv variants to come up empty.
    monkeypatch.setattr(db_helpers, "find_dotenv", lambda *a, **k: "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    db_helpers.load_database_env()

    assert os.environ.get("UNI_ADMISSION_ENV_MARKER") == "from_home"


def test_cwd_env_takes_precedence_over_home(monkeypatch, tmp_path):
    """A project .env in cwd should win over ~/.uni-agent/.env (dev mode)."""
    _clear_marker(monkeypatch)

    fake_home = tmp_path / "home"
    uni_dir = fake_home / ".uni-agent"
    uni_dir.mkdir(parents=True)
    (uni_dir / ".env").write_text("UNI_ADMISSION_ENV_MARKER=from_home\n", encoding="utf-8")

    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / ".env").write_text("UNI_ADMISSION_ENV_MARKER=from_cwd\n", encoding="utf-8")
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    db_helpers.load_database_env()

    assert os.environ.get("UNI_ADMISSION_ENV_MARKER") == "from_cwd"
