"""
LLM Provider package.

This package provides a unified interface for various LLM providers (Gemini, DeepSeek, VolcEngine).

To add a new provider:
1. Create a new file in this directory (e.g., `myprovider.py`).
2. Implement a class inheriting from `LLMProvider` (defined in `base.py`).
3. Add the provider class to `PROVIDER_REGISTRY` in this file.
"""

from typing import Type

from .base import LLMProvider, LLMResponse, RateLimitError
from .gemini import GeminiProvider
from .deepseek import DeepSeekProvider
from .volcengine import VolcEngineProvider

# --- Provider Registry ---

PROVIDER_REGISTRY: dict[str, Type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "volcengine": VolcEngineProvider,
}

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "RateLimitError",
    "GeminiProvider",
    "DeepSeekProvider",
    "VolcEngineProvider",
    "PROVIDER_REGISTRY",
]
