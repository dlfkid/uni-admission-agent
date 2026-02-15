"""Tests for src.core.token_tracker — token usage tracking and cost estimation."""

import threading
from unittest.mock import patch

import pytest

from src.core.token_tracker import TokenTracker, _ModelUsage, COST_TABLE, _DEFAULT_COST
from typing import Generator


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """Ensure each test gets a fresh TokenTracker singleton."""
    TokenTracker._instance = None
    TokenTracker._lock = threading.Lock()
    yield
    TokenTracker._instance = None
    TokenTracker._lock = threading.Lock()


# ── _ModelUsage ───────────────────────────────────────────────────────


def test_model_usage_defaults() -> None:
    u = _ModelUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.total_tokens == 0


def test_model_usage_total() -> None:
    u = _ModelUsage()
    u.input_tokens = 100
    u.output_tokens = 50
    assert u.total_tokens == 150


def test_model_usage_cost_known_model() -> None:
    u = _ModelUsage()
    u.input_tokens = 1_000_000
    u.output_tokens = 1_000_000
    cost = u.cost("gemini-2.0-flash")
    # input: 1M * $0.10/1M = $0.10, output: 1M * $0.40/1M = $0.40
    assert abs(cost - 0.50) < 1e-6


def test_model_usage_cost_unknown_model() -> None:
    u = _ModelUsage()
    u.input_tokens = 1_000_000
    u.output_tokens = 1_000_000
    cost = u.cost("some-unknown-model")
    expected = _DEFAULT_COST["input"] + _DEFAULT_COST["output"]
    assert abs(cost - expected) < 1e-6


# ── TokenTracker ──────────────────────────────────────────────────────


def test_track_usage() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model="gemini-2.0-flash")
    assert "gemini-2.0-flash" in tracker._usage
    assert tracker._usage["gemini-2.0-flash"].input_tokens == 100
    assert tracker._usage["gemini-2.0-flash"].output_tokens == 50


def test_track_usage_accumulates() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model="test-model")
    tracker.track_usage(200, 100, model="test-model")
    assert tracker._usage["test-model"].input_tokens == 300
    assert tracker._usage["test-model"].output_tokens == 150


def test_track_usage_multiple_models() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model="model-a")
    tracker.track_usage(200, 100, model="model-b")
    assert len(tracker._usage) == 2
    assert tracker._usage["model-a"].total_tokens == 150
    assert tracker._usage["model-b"].total_tokens == 300


def test_track_usage_none_model() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model=None)
    assert "unknown" in tracker._usage


def test_get_summary_empty() -> None:
    tracker = TokenTracker()
    summary = tracker.get_summary()
    assert "No LLM calls recorded" in summary


def test_get_summary_with_data() -> None:
    tracker = TokenTracker()
    tracker.track_usage(1000, 500, model="gemini-2.0-flash")
    summary = tracker.get_summary()
    assert "gemini-2.0-flash" in summary
    assert "TOTAL" in summary
    assert "$" in summary


def test_reset() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model="test")
    assert len(tracker._usage) == 1
    tracker.reset()
    assert len(tracker._usage) == 0
    assert tracker.get_summary().endswith("No LLM calls recorded.")


def test_reset_then_track_again() -> None:
    tracker = TokenTracker()
    tracker.track_usage(100, 50, model="a")
    tracker.reset()
    tracker.track_usage(200, 100, model="b")
    assert "a" not in tracker._usage
    assert "b" in tracker._usage
    assert tracker._usage["b"].input_tokens == 200


# ── Thread safety ─────────────────────────────────────────────────────


def test_thread_safety() -> None:
    tracker = TokenTracker()
    errors: list[Exception] = []

    def worker(model: str) -> None:
        try:
            for _ in range(1000):
                tracker.track_usage(1, 1, model=model)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"m{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for i in range(5):
        assert tracker._usage[f"m{i}"].total_tokens == 2000
