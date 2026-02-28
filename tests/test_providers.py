"""Tests for LLM provider implementations with fully mocked API calls.

All tests use MagicMock to avoid real LLM calls and token consumption.
"""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from pydantic import BaseModel

from src.agents.providers.base import (
    LLMResponse,
    RateLimitError,
    is_retryable,
)


# ── Test Schema ─────────────────────────────────────────────────────


class _TestSchema(BaseModel):
    name: str = ""
    value: int = 0


# ── base.py tests ───────────────────────────────────────────────────


def test_llm_response_creation() -> None:
    resp = LLMResponse(
        text='{"name":"test"}', prompt_tokens=10,
        completion_tokens=5, total_tokens=15, model="test-model",
    )
    assert resp.text == '{"name":"test"}'
    assert resp.total_tokens == 15


def test_rate_limit_error_attributes() -> None:
    err = RateLimitError("gemini", 429, "Too many requests")
    assert err.provider == "gemini"
    assert err.status_code == 429
    assert "429" in str(err)


def test_is_retryable_rate_limit() -> None:
    assert is_retryable(RateLimitError("test", 429)) is True


def test_is_retryable_str_patterns() -> None:
    assert is_retryable(Exception("rate limit exceeded")) is True
    assert is_retryable(Exception("429 Too Many Requests")) is True
    assert is_retryable(Exception("503 overloaded")) is True
    assert is_retryable(Exception("server busy")) is True


def test_is_retryable_normal_error() -> None:
    assert is_retryable(ValueError("invalid input")) is False


# ── DeepSeek provider tests ─────────────────────────────────────────


class TestDeepSeekProvider:
    """Tests for DeepSeek provider with mocked OpenAI client."""

    def test_init_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        from src.agents.providers.deepseek import DeepSeekProvider
        with pytest.raises(ValueError, match="API key is required"):
            DeepSeekProvider(api_key="")

    def test_init_custom_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with patch("openai.OpenAI"):
            from src.agents.providers.deepseek import DeepSeekProvider
            provider = DeepSeekProvider(
                api_key="sk-test",
                base_url="https://custom.api.com",
                model_id="custom-model",
            )
            assert provider.name == "deepseek"
            assert provider.model_id == "custom-model"
            assert provider.base_url == "https://custom.api.com"

    def test_generate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        mock_client = MagicMock()
        mock_usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        mock_message = SimpleNamespace(content='{"name":"test","value":42}')
        mock_choice = SimpleNamespace(message=mock_message)
        mock_response = SimpleNamespace(choices=[mock_choice], usage=mock_usage)
        mock_client.chat.completions.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            from src.agents.providers.deepseek import DeepSeekProvider
            provider = DeepSeekProvider(api_key="sk-test")
            resp = provider.generate("extract data", _TestSchema)

        assert resp.text == '{"name":"test","value":42}'
        assert resp.prompt_tokens == 100
        assert resp.completion_tokens == 50
        assert resp.total_tokens == 150

    def test_generate_empty_choices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        mock_client = MagicMock()
        mock_response = SimpleNamespace(choices=[], usage=None)
        mock_client.chat.completions.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            from src.agents.providers.deepseek import DeepSeekProvider
            provider = DeepSeekProvider(api_key="sk-test")
            resp = provider.generate("prompt", _TestSchema)

        assert resp.text == ""
        assert resp.total_tokens == 0

    def test_generate_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        mock_client = MagicMock()
        # Create a mock that raises RateLimitError on the OpenAI side
        from openai import RateLimitError as OpenAIRateLimitError

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        rate_err = OpenAIRateLimitError(
            message="Rate limited",
            response=mock_response,
            body=None,
        )
        mock_client.chat.completions.create.side_effect = rate_err

        with patch("openai.OpenAI", return_value=mock_client):
            from src.agents.providers.deepseek import DeepSeekProvider
            provider = DeepSeekProvider(api_key="sk-test")
            # Disable tenacity retries for testing
            provider.generate = provider.generate.__wrapped__.__get__(provider)
            with pytest.raises(RateLimitError):
                provider.generate("test", _TestSchema)


# ── Gemini provider tests ───────────────────────────────────────────


class TestGeminiProvider:
    """Tests for Gemini provider with mocked google-genai client."""

    def test_init_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from src.agents.providers.gemini import GeminiProvider
        with pytest.raises(ValueError, match="API key is required"):
            GeminiProvider(api_key="")

    def test_init_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        mock_genai = MagicMock()
        with patch.dict(sys.modules, {"google": MagicMock(genai=mock_genai), "google.genai": mock_genai}):
            from src.agents.providers.gemini import GeminiProvider
            provider = GeminiProvider(api_key="test-gem-key")
            assert provider.name == "gemini"
            mock_genai.Client.assert_called_once_with(api_key="test-gem-key")

    def test_generate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        mock_usage = SimpleNamespace(prompt_token_count=80, candidates_token_count=40)
        mock_response = SimpleNamespace(
            text='{"name":"gemini_result","value":99}',
            usage_metadata=mock_usage,
        )
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_types = MagicMock()
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }):
            from src.agents.providers.gemini import GeminiProvider
            provider = GeminiProvider(api_key="test-key")
            resp = provider.generate("extract", _TestSchema)

        assert resp.text == '{"name":"gemini_result","value":99}'
        assert resp.prompt_tokens == 80
        assert resp.completion_tokens == 40

    def test_generate_no_usage_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        mock_response = SimpleNamespace(text='{"name":"ok"}', usage_metadata=None)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_types = MagicMock()
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }):
            from src.agents.providers.gemini import GeminiProvider
            provider = GeminiProvider(api_key="k")
            resp = provider.generate("p", _TestSchema)

        assert resp.prompt_tokens == 0
        assert resp.completion_tokens == 0


# ── Custom LLM provider tests ───────────────────────────────────────


class TestCustomLLMProvider:
    """Tests for CustomLLMProvider with mocked OpenAI client."""

    def test_init_requires_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
        from src.agents.providers.custom import CustomLLMProvider
        with pytest.raises(ValueError, match="base URL is required"):
            CustomLLMProvider(base_url="")

    def test_init_with_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
        with patch("openai.OpenAI"):
            from src.agents.providers.custom import CustomLLMProvider
            provider = CustomLLMProvider(
                api_key="key", base_url="http://localhost:8080", model_id="local-model",
            )
            assert provider.name == "custom"
            assert provider.model_id == "local-model"

    def test_generate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)

        mock_client = MagicMock()
        mock_usage = SimpleNamespace(prompt_tokens=20, completion_tokens=10)
        mock_message = SimpleNamespace(content='{"name":"custom","value":1}')
        mock_choice = SimpleNamespace(message=mock_message)
        mock_response = SimpleNamespace(choices=[mock_choice], usage=mock_usage)
        mock_client.chat.completions.create.return_value = mock_response

        with patch("openai.OpenAI", return_value=mock_client):
            from src.agents.providers.custom import CustomLLMProvider
            provider = CustomLLMProvider(base_url="http://local:8080")
            resp = provider.generate("prompt", _TestSchema)

        assert resp.text == '{"name":"custom","value":1}'
        assert resp.total_tokens == 30


# ── VolcEngine provider tests ───────────────────────────────────────


class TestVolcEngineProvider:
    """Tests for VolcEngine provider with mocked Ark client."""

    def test_init_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOLC_API_KEY", raising=False)
        from src.agents.providers.volcengine import VolcEngineProvider
        with pytest.raises((ValueError, ImportError)):
            VolcEngineProvider()

    def test_init_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOLC_API_KEY", "test-volc-key")
        monkeypatch.setenv("VOLC_MODEL_ID", "doubao-test")

        try:
            import volcenginesdkarkruntime  # noqa: F401
        except ImportError:
            pytest.skip("volcenginesdkarkruntime not installed")

        from src.agents.providers.volcengine import VolcEngineProvider
        provider = VolcEngineProvider()
        assert provider.name == "volcengine"
        assert provider.model_id == "doubao-test"

    def test_generate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOLC_API_KEY", "test-key")
        monkeypatch.setenv("VOLC_MODEL_ID", "ep-test")

        try:
            import volcenginesdkarkruntime  # noqa: F401
        except ImportError:
            pytest.skip("volcenginesdkarkruntime not installed")

        from src.agents.providers.volcengine import (
            VolcEngineProvider,
            ArkChatCompletion,
        )

        mock_usage = SimpleNamespace(prompt_tokens=50, completion_tokens=25)
        mock_message = SimpleNamespace(content='{"name":"volc","value":7}')
        mock_choice = SimpleNamespace(message=mock_message)

        # Create a proper ArkChatCompletion mock
        mock_response = MagicMock(spec=ArkChatCompletion)
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        provider = VolcEngineProvider()
        provider.client = MagicMock()
        provider.client.chat.completions.create.return_value = mock_response

        resp = provider.generate("test", _TestSchema)
        assert resp.text == '{"name":"volc","value":7}'
        assert resp.total_tokens == 75
