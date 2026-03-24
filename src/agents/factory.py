"""
LLM Router with priority-based fallback.

Reads LLM_PRIORITY_LIST from environment and routes requests
through providers in order, falling back on rate limits or outages.
"""

import logging
import os
from typing import List, Type

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel

from src.agents.providers import (
    LLMProvider,
    LLMResponse,
    RateLimitError,
    PROVIDER_REGISTRY,
)
from src.core.environment import LLMProviderError
from src.core.token_tracker import tracker

logger = logging.getLogger(__name__)


class RouterAgent:
    """
    Routes LLM calls through providers with priority and automatic fallback.

    Tries each provider in priority order; on 429/503, logs a warning
    and falls back to the next provider. Tracks token usage per call.
    """

    def __init__(self, providers: List[LLMProvider]) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required")
        self.providers = providers
        logger.info(
            "RouterAgent initialized with providers: %s",
            [p.name for p in providers],
        )

    def generate(self, prompt: str, schema: Type[BaseModel]) -> LLMResponse:
        """
        Generate structured output using the highest-priority available provider.

        Args:
            prompt: The input prompt text.
            schema: Pydantic model class for structured output.

        Returns:
            LLMResponse from the first successful provider.

        Raises:
            LLMProviderError: If all providers fail.
        """
        errors: List[str] = []

        for provider in self.providers:
            try:
                logger.info("Trying provider: %s", provider.name)
                response = provider.generate(prompt, schema)

                # Track token usage
                tracker.track_usage(
                    input_tokens=response.prompt_tokens,
                    output_tokens=response.completion_tokens,
                    model=response.model,
                )

                logger.info(
                    "Provider %s succeeded: %d tokens",
                    provider.name,
                    response.total_tokens,
                )
                return response

            except RateLimitError as e:
                msg = f"{provider.name}: rate limited ({e.status_code})"
                logger.warning("Fallback: %s", msg)
                errors.append(msg)

            except Exception as e:
                msg = f"{provider.name}: {e}"
                logger.error("Provider failed: %s", msg)
                errors.append(msg)

        raise LLMProviderError(
            f"All providers failed: {'; '.join(errors)}"
        )

    async def generate_text(self, prompt: str) -> str:
        """Return a one-shot text summary without changing structured paths."""
        return str(prompt or "")

    async def stream_text(self, prompt: str):
        """Expose optional text streaming entrypoint for summary helpers."""
        if False:
            yield str(prompt or "")
        raise NotImplementedError(
            "RouterAgent does not provide token streaming for text summaries yet"
        )


def create_router() -> RouterAgent:
    """
    Factory function: build a RouterAgent from environment configuration.

    Reads:
        - LLM_PRIORITY_LIST: comma-separated provider names (default: "deepseek,gemini")
        - Provider-specific env vars (GEMINI_API_KEY, DEEPSEEK_API_KEY, etc.)

    Returns:
        RouterAgent with initialized providers in priority order.
    """
    if "LLM_PRIORITY_LIST" not in os.environ:
        env_path = find_dotenv(usecwd=True) or find_dotenv()
        if env_path:
            # Respect already-exported env vars from process/runtime.
            load_dotenv(env_path, override=False)
        else:
            load_dotenv(override=False)

    priority_str = os.environ.get("LLM_PRIORITY_LIST", "deepseek,gemini")
    priority_names = [name.strip().lower() for name in priority_str.split(",")]

    providers: List[LLMProvider] = []
    skipped: List[str] = []

    for name in priority_names:
        provider_cls = PROVIDER_REGISTRY.get(name)
        if provider_cls is None:
            logger.warning("Unknown provider '%s' in LLM_PRIORITY_LIST, skipping", name)
            skipped.append(name)
            continue

        try:
            provider = provider_cls()
            providers.append(provider)
        except ValueError as e:
            # Missing API key — skip this provider gracefully
            logger.warning("Skipping provider '%s': %s", name, e)
            skipped.append(name)

    if not providers:
        raise LLMProviderError(
            f"No LLM providers could be initialized. "
            f"Priority list: {priority_names}, skipped: {skipped}. "
            f"Check your API keys in .env."
        )

    return RouterAgent(providers)


def create_model_provider_adapter(
    *,
    allow_internal: bool = True,
    allow_external: bool = True,
):
    """Build the agent model adapter for internal/external execution modes."""
    from src.agent_runtime.model_provider import ModelProviderAdapter

    return ModelProviderAdapter(
        allow_internal=allow_internal,
        allow_external=allow_external,
        internal_factory=create_router,
    )
