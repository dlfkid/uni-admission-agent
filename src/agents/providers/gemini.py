"""
Google Gemini provider implementation.
"""

import os
import logging
from typing import Optional, Type

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .base import LLMProvider, LLMResponse, RateLimitError, is_retryable

logger = logging.getLogger(__name__)


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
        retry=retry_if_exception(is_retryable),
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
            if is_retryable(e):
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
