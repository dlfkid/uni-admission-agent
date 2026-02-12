"""
LLM Provider abstraction layer.

Defines a unified interface for LLM providers (Gemini, DeepSeek, etc.)
with structured output support and token usage tracking.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional, Type

from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

logger = logging.getLogger(__name__)


# --- Response Model ---


class LLMResponse(BaseModel):
    """Unified response from any LLM provider."""

    text: str = Field(..., description="Raw text response from the LLM")
    prompt_tokens: int = Field(default=0, description="Number of input/prompt tokens")
    completion_tokens: int = Field(default=0, description="Number of output/completion tokens")
    total_tokens: int = Field(default=0, description="Total tokens consumed")
    model: str = Field(..., description="Model identifier that generated this response")


# --- Rate Limit Detection ---


class RateLimitError(Exception):
    """Raised when the provider returns 429 (rate limit) or 503 (unavailable)."""

    def __init__(self, provider: str, status_code: int, message: str = ""):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider} returned {status_code}: {message}")


def _is_retryable(exc: BaseException) -> bool:
    """Check if an exception is retryable (rate limit or server error)."""
    if isinstance(exc, RateLimitError):
        return True
    # google-genai raises google.api_core.exceptions for 429/503
    exc_str = str(exc).lower()
    return "429" in exc_str or "503" in exc_str or "rate" in exc_str


# --- Abstract Provider ---


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and tracking."""
        ...

    @abstractmethod
    def generate(self, prompt: str, schema: Type[BaseModel]) -> LLMResponse:
        """
        Generate structured JSON output from a prompt.

        Args:
            prompt: The input prompt text.
            schema: Pydantic model class defining the expected output structure.

        Returns:
            LLMResponse with the text, token usage, and model info.

        Raises:
            RateLimitError: If the provider returns 429 or 503.
            Exception: For other API errors.
        """
        ...


# --- Gemini Provider ---


class GeminiProvider(LLMProvider):
    """Google Gemini provider using google-genai SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        )
        self.model_id = (
            model_id
            or os.environ.get("GEMINI_MODEL_NAME")
            or "gemini-2.0-flash"
        )

        if not self.api_key:
            raise ValueError("Gemini API key is required (GOOGLE_API_KEY or GEMINI_API_KEY)")

        from google import genai
        self.client = genai.Client(api_key=self.api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(_is_retryable),
    )
    def generate(self, prompt: str, schema: Type[BaseModel]) -> LLMResponse:
        from google.genai import types

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
        except Exception as e:
            if _is_retryable(e):
                raise RateLimitError(self.name, 429, str(e)) from e
            raise

        response_text = response.text or ""
        prompt_tokens = 0
        completion_tokens = 0

        if response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count or 0
            completion_tokens = response.usage_metadata.candidates_token_count or 0

        return LLMResponse(
            text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=self.model_id,
        )


# --- DeepSeek Provider ---


class DeepSeekProvider(LLMProvider):
    """DeepSeek provider using OpenAI-compatible API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        self.base_url = (
            base_url
            or os.environ.get("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        )
        self.model_id = (
            model_id
            or os.environ.get("DEEPSEEK_MODEL_NAME")
            or "deepseek-chat"
        )

        if not self.api_key:
            raise ValueError("DeepSeek API key is required (DEEPSEEK_API_KEY)")

        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    @property
    def name(self) -> str:
        return "deepseek"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(_is_retryable),
    )
    def generate(self, prompt: str, schema: Type[BaseModel]) -> LLMResponse:
        from openai import RateLimitError as OpenAIRateLimitError, APIStatusError

        # Build system message with JSON schema instruction
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        system_msg = (
            "You are a data extraction assistant. "
            "Return ONLY valid JSON matching this schema:\n"
            f"{schema_json}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
        except OpenAIRateLimitError as e:
            raise RateLimitError(self.name, 429, str(e)) from e
        except APIStatusError as e:
            if e.status_code in (429, 503):
                raise RateLimitError(self.name, e.status_code, str(e)) from e
            raise

        # Extract response
        choice = response.choices[0] if response.choices else None
        response_text = ""
        if choice and choice.message and choice.message.content:
            response_text = choice.message.content

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0

        return LLMResponse(
            text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=self.model_id,
        )


# --- Provider Registry ---

PROVIDER_REGISTRY: dict[str, Type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
}
