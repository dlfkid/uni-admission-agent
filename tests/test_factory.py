"""Tests for src/agents/factory.py – RouterAgent and create_router."""

import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from src.agents.providers.base import LLMProvider, LLMResponse, RateLimitError
from src.agents.factory import RouterAgent, create_router
from src.core.environment import LLMProviderError


# ── Helpers ──────────────────────────────────────────────────────────


class _DummySchema(BaseModel):
    value: str = ""


def _make_provider(
    name: str = "mock",
    *,
    response_text: str = '{"value":"ok"}',
    side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock LLM provider."""
    provider = MagicMock(spec=LLMProvider)
    provider.name = name
    if side_effect:
        provider.generate.side_effect = side_effect
    else:
        provider.generate.return_value = LLMResponse(
            text=response_text,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            model=f"mock-{name}",
        )
    return provider


# ── RouterAgent.__init__ ─────────────────────────────────────────────


def test_router_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="At least one"):
        RouterAgent(providers=[])


def test_router_init_with_providers() -> None:
    p = _make_provider("a")
    router = RouterAgent(providers=[p])
    assert len(router.providers) == 1


# ── RouterAgent.generate ─────────────────────────────────────────────


def test_generate_first_provider_succeeds() -> None:
    p1 = _make_provider("primary")
    p2 = _make_provider("fallback")
    router = RouterAgent(providers=[p1, p2])

    resp = router.generate("hello", _DummySchema)

    assert resp.text == '{"value":"ok"}'
    p1.generate.assert_called_once()
    p2.generate.assert_not_called()


def test_generate_falls_back_on_rate_limit() -> None:
    p1 = _make_provider("primary", side_effect=RateLimitError("primary", 429, "busy"))
    p2 = _make_provider("fallback")
    router = RouterAgent(providers=[p1, p2])

    resp = router.generate("hello", _DummySchema)

    assert resp.text == '{"value":"ok"}'
    p1.generate.assert_called_once()
    p2.generate.assert_called_once()


def test_generate_falls_back_on_generic_error() -> None:
    p1 = _make_provider("primary", side_effect=RuntimeError("API down"))
    p2 = _make_provider("fallback")
    router = RouterAgent(providers=[p1, p2])

    resp = router.generate("hello", _DummySchema)

    assert resp.text == '{"value":"ok"}'


def test_generate_all_fail_raises_llm_provider_error() -> None:
    p1 = _make_provider("a", side_effect=RateLimitError("a", 429))
    p2 = _make_provider("b", side_effect=RuntimeError("boom"))
    router = RouterAgent(providers=[p1, p2])

    with pytest.raises(LLMProviderError, match="All providers failed"):
        router.generate("hello", _DummySchema)


def test_generate_tracks_tokens() -> None:
    """Token tracker should be called on success."""
    p = _make_provider("tok")
    router = RouterAgent(providers=[p])

    with patch("src.agents.factory.tracker") as mock_tracker:
        router.generate("prompt", _DummySchema)
        mock_tracker.track_usage.assert_called_once_with(
            input_tokens=10,
            output_tokens=5,
            model="mock-tok",
        )


# ── create_router (factory function) ────────────────────────────────


def test_create_router_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_router should use LLM_PRIORITY_LIST from env."""
    monkeypatch.setenv("LLM_PRIORITY_LIST", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-123")

    router = create_router()
    assert len(router.providers) == 1
    assert router.providers[0].name == "deepseek"


def test_create_router_skips_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRIORITY_LIST", "nonexistent,deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    router = create_router()
    assert len(router.providers) == 1
    assert router.providers[0].name == "deepseek"


def test_create_router_skips_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider with missing API key should be skipped."""
    monkeypatch.setenv("LLM_PRIORITY_LIST", "gemini,deepseek")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    router = create_router()
    assert len(router.providers) == 1
    assert router.providers[0].name == "deepseek"


def test_create_router_no_providers_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRIORITY_LIST", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LLMProviderError, match="No LLM providers"):
        create_router()


def test_create_router_multiple_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRIORITY_LIST", "deepseek,gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "gem-key")

    router = create_router()
    assert len(router.providers) == 2
    names = [p.name for p in router.providers]
    assert names == ["deepseek", "gemini"]
