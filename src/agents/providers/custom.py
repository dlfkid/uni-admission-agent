"""
Custom LLM provider supporting any OpenAI-compatible API.
"""

import os
import json
import logging
from typing import Optional, Type

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .base import LLMProvider, LLMResponse, RateLimitError, is_retryable

logger = logging.getLogger(__name__)


class CustomLLMProvider(LLMProvider):
    """Custom LLM provider for user-configured OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("CUSTOM_LLM_API_KEY") or ""
        self.base_url = (
            base_url
            or os.environ.get("CUSTOM_LLM_BASE_URL")
            or ""
        )
        self.model_id = (
            model_id
            or os.environ.get("CUSTOM_LLM_MODEL_NAME")
            or "gpt-4o-mini"
        )

        if not self.base_url:
            raise ValueError("Custom LLM base URL is required (CUSTOM_LLM_BASE_URL)")

        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key or "no-key", base_url=self.base_url)

    @property
    def name(self) -> str:
        return "custom"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(is_retryable),
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
