"""Tests for streamed final summary generation with graceful fallback."""

import pytest

from src.agent_runtime.summary_stream import generate_summary_with_stream


@pytest.mark.asyncio
async def test_summary_stream_emits_deltas_when_supported():
    chunks = ["Hello", " world"]
    emitted: list[dict[str, object]] = []

    class _StreamingProvider:
        async def stream_text(self, prompt: str):
            assert prompt == "Summarize the crawl"
            for chunk in chunks:
                yield chunk

    result = await generate_summary_with_stream(
        prompt="Summarize the crawl",
        provider=_StreamingProvider(),
        fallback_text="Hello world",
        event_sink=emitted.append,
    )

    assert any(event["type"] == "summary_delta" for event in emitted)
    assert result == "Hello world"


@pytest.mark.asyncio
async def test_summary_stream_falls_back_to_one_shot_when_unsupported():
    emitted: list[dict[str, object]] = []

    class _FallbackProvider:
        async def generate_text(self, prompt: str) -> str:
            assert prompt == "Summarize the crawl"
            return "Fallback summary"

    result = await generate_summary_with_stream(
        prompt="Summarize the crawl",
        provider=_FallbackProvider(),
        fallback_text="Original summary",
        event_sink=emitted.append,
    )

    assert result == "Fallback summary"
    assert emitted[0]["type"] == "summary_started"
    assert emitted[-1]["type"] == "summary_finished"
