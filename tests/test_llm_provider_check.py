"""Tests for the LLM-provider readiness check run by `adm-agent check`.

The audit found that `check` passed even with zero usable LLM keys, so a
non-coder got a false "all green" and then crashed on first crawl. These
tests pin the new behavior: check must fail (or flag) when no real key is
present, and pass when one is.
"""
from __future__ import annotations

import pytest

from src.core.environment import (
    LLMConfigError,
    _check_llm_providers,
)

_ALL_KEYS = [
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "VOLC_API_KEY",
    "CUSTOM_LLM_API_KEY",
]


def _clear_all(monkeypatch) -> None:
    for k in _ALL_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_raises_when_no_provider_key_set(monkeypatch):
    _clear_all(monkeypatch)
    with pytest.raises(LLMConfigError):
        _check_llm_providers()


def test_raises_when_only_placeholder_values_present(monkeypatch):
    """The .env.example ships placeholders like your_deepseek_api_key_here —
    those must NOT count as configured."""
    _clear_all(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "your_deepseek_api_key_here")
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key_here")
    with pytest.raises(LLMConfigError):
        _check_llm_providers()


def test_passes_when_one_real_key_present(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deadbeef-key")
    # Should not raise
    _check_llm_providers()


def test_passes_with_custom_llm_key(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "real-token-123")
    _check_llm_providers()
