"""Tests for server config parsing — _parse_structured_config / _update_env_file_structured."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.api.schemas import StructuredConfig
from src.api.server import _parse_structured_config, _update_env_file_structured


# ── _parse_structured_config ──────────────────────────────────────────


def test_parse_empty_config(tmp_path: Path) -> None:
    """Missing .env → empty config."""
    env_path = tmp_path / ".env"
    with patch("src.api.server._get_env_path", return_value=env_path):
        cfg = _parse_structured_config()
    assert cfg.database_url == ""
    assert cfg.llm_priority == []


def test_parse_full_config(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DATABASE_URL=postgresql://localhost/test\n"
        "LLM_PRIORITY_LIST=gemini, deepseek, doubao\n"
        "GEMINI_API_KEY=gkey123\n"
        "GEMINI_MODEL=gemini-2.0-flash\n"
        "DEEPSEEK_API_KEY=dkey456\n"
    )
    with patch("src.api.server._get_env_path", return_value=env_path):
        cfg = _parse_structured_config()
    assert cfg.database_url == "postgresql://localhost/test"
    assert cfg.llm_priority == ["gemini", "deepseek", "doubao"]
    assert cfg.providers["gemini"]["GEMINI_API_KEY"] == "gkey123"
    assert cfg.providers["deepseek"]["DEEPSEEK_API_KEY"] == "dkey456"


def test_parse_config_with_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# Database configuration\n"
        "DATABASE_URL=postgresql://localhost/db\n"
        "\n"
        "# LLM settings\n"
        "LLM_PRIORITY_LIST=gemini\n"
    )
    with patch("src.api.server._get_env_path", return_value=env_path):
        cfg = _parse_structured_config()
    assert cfg.database_url == "postgresql://localhost/db"
    assert cfg.llm_priority == ["gemini"]


# ── _update_env_file_structured ───────────────────────────────────────


def test_update_preserves_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# My database\n"
        "DATABASE_URL=old_url\n"
        "# Provider keys\n"
        "GEMINI_API_KEY=old_key\n"
    )
    new_config = StructuredConfig(
        database_url="new_url",
        llm_priority=["gemini"],
        providers={"gemini": {"GEMINI_API_KEY": "new_key"}},
    )
    with patch("src.api.server._get_env_path", return_value=env_path):
        _update_env_file_structured(new_config)

    content = env_path.read_text()
    assert "# My database" in content
    assert "# Provider keys" in content
    assert "DATABASE_URL=new_url" in content
    assert "GEMINI_API_KEY=new_key" in content
    assert "old_url" not in content


def test_update_adds_new_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DATABASE_URL=existing\n")
    new_config = StructuredConfig(
        database_url="existing",
        llm_priority=["deepseek"],
        providers={"deepseek": {"DEEPSEEK_API_KEY": "dk123"}},
    )
    with patch("src.api.server._get_env_path", return_value=env_path):
        _update_env_file_structured(new_config)

    content = env_path.read_text()
    assert "DEEPSEEK_API_KEY=dk123" in content
    assert "LLM_PRIORITY_LIST=deepseek" in content


def test_update_creates_backup(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DATABASE_URL=test\n")
    new_config = StructuredConfig(
        database_url="updated",
        llm_priority=[],
        providers={},
    )
    with patch("src.api.server._get_env_path", return_value=env_path):
        _update_env_file_structured(new_config)

    backup = env_path.with_suffix(".env.bak")
    assert backup.exists()
    assert "DATABASE_URL=test" in backup.read_text()
