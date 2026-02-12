"""
Token usage tracker with per-model breakdown and cost estimation.

Singleton class that accumulates token usage across multiple LLM providers
and produces a unified cost summary.
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Per-model cost rates (USD per 1M tokens)
COST_TABLE: dict[str, dict[str, float]] = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.0-flash-exp": {"input": 0.10, "output": 0.40},
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    # 火山豆包模型 (USD per 1M tokens, 实际价格以方舟控制台为准)
    "doubao-pro-32k": {"input": 0.80, "output": 2.00},
    "doubao-lite-32k": {"input": 0.30, "output": 0.60},
}

# Fallback rate for unknown models
_DEFAULT_COST: dict[str, float] = {"input": 0.50, "output": 1.00}


class _ModelUsage:
    """Accumulates token counts for a single model."""

    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost(self, model: str) -> float:
        rates = COST_TABLE.get(model, _DEFAULT_COST)
        input_cost = (self.input_tokens / 1_000_000) * rates["input"]
        output_cost = (self.output_tokens / 1_000_000) * rates["output"]
        return input_cost + output_cost


class TokenTracker:
    """
    Singleton tracker for multi-model token usage and cost estimation.

    Thread-safe for concurrent operations.
    """

    _instance: Optional["TokenTracker"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TokenTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized: bool = True
        self._usage: dict[str, _ModelUsage] = {}
        self._data_lock = threading.Lock()

    def track_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
    ) -> None:
        """Record token usage from an API call."""
        model_key = model or "unknown"
        with self._data_lock:
            if model_key not in self._usage:
                self._usage[model_key] = _ModelUsage()
            self._usage[model_key].input_tokens += input_tokens
            self._usage[model_key].output_tokens += output_tokens

    def get_summary(self) -> str:
        """Return a formatted multi-model cost summary."""
        with self._data_lock:
            if not self._usage:
                return "\n💰 Token Usage Summary: No LLM calls recorded."

            lines: list[str] = [
                "",
                "💰 Token Usage Summary:",
                "-" * 60,
                f"{'Model':<25} {'Input':>10} {'Output':>10} {'Total':>10} {'Cost':>10}",
                "-" * 60,
            ]

            grand_input = 0
            grand_output = 0
            grand_cost = 0.0

            for model, usage in sorted(self._usage.items()):
                cost = usage.cost(model)
                lines.append(
                    f"{model:<25} {usage.input_tokens:>10,} "
                    f"{usage.output_tokens:>10,} "
                    f"{usage.total_tokens:>10,} "
                    f"${cost:>9.6f}"
                )
                grand_input += usage.input_tokens
                grand_output += usage.output_tokens
                grand_cost += cost

            grand_total = grand_input + grand_output
            lines.append("-" * 60)
            lines.append(
                f"{'TOTAL':<25} {grand_input:>10,} "
                f"{grand_output:>10,} "
                f"{grand_total:>10,} "
                f"${grand_cost:>9.6f}"
            )
            lines.append("-" * 60)

            return "\n".join(lines)

    def log_summary(self) -> None:
        """Log the summary at DEBUG level."""
        summary = self.get_summary()
        logger.debug(summary)


# Global singleton instance
tracker = TokenTracker()
