"""
LLM Provider base types and abstract base class.
"""

import logging
from abc import ABC, abstractmethod
from typing import Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """Unified response from any LLM provider."""

    text: str = Field(..., description="Raw text response from the LLM")
    prompt_tokens: int = Field(default=0, description="Number of input/prompt tokens")
    completion_tokens: int = Field(default=0, description="Number of output/completion tokens")
    total_tokens: int = Field(default=0, description="Total tokens consumed")
    model: str = Field(..., description="Model identifier that generated this response")


class RateLimitError(Exception):
    """Raised when the provider returns 429 (rate limit) or 503 (unavailable)."""

    def __init__(self, provider: str, status_code: int, message: str = ""):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider} returned {status_code}: {message}")


def is_retryable(exc: BaseException) -> bool:
    """Check if an exception is retryable (rate limit or server error)."""
    if isinstance(exc, RateLimitError):
        return True
    # Common error patterns for LLM APIs
    exc_str = str(exc).lower()
    return any(p in exc_str for p in ["429", "503", "rate", "overloaded", "busy"])


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
