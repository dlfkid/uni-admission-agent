"""Helpers for streaming the final user-visible agent summary."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from src.agent_runtime.base import AgentEvent, EventSink

logger = logging.getLogger(__name__)


def _emit_summary_event(event_sink: EventSink | None, event: AgentEvent) -> None:
    """Safely emit one summary-stream event."""
    if event_sink is not None:
        event_sink(dict(event))


async def _generate_one_shot(provider: Any, prompt: str) -> str | None:
    """Generate one-shot summary text when the provider supports it."""
    generate_text = getattr(provider, "generate_text", None)
    if not callable(generate_text):
        return None

    result = generate_text(prompt)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


async def generate_summary_with_stream(
    *,
    prompt: str,
    provider: Any = None,
    fallback_text: str = "",
    event_sink: EventSink | None = None,
) -> str:
    """Stream final summary tokens when possible, else fall back gracefully."""
    _emit_summary_event(
        event_sink,
        {
            "type": "summary_started",
            "streaming_supported": callable(getattr(provider, "stream_text", None)),
        },
    )

    stream_text = getattr(provider, "stream_text", None)
    if callable(stream_text):
        chunks: list[str] = []
        try:
            async for chunk in stream_text(prompt):
                text = str(chunk or "")
                if not text:
                    continue
                chunks.append(text)
                _emit_summary_event(
                    event_sink,
                    {
                        "type": "summary_delta",
                        "delta": text,
                    },
                )
        except Exception as exc:  # pylint: disable=broad-except
            logger.info("Summary streaming unavailable, falling back: %s", exc)
        else:
            summary = "".join(chunks)
            _emit_summary_event(
                event_sink,
                {
                    "type": "summary_finished",
                    "text": summary,
                    "streamed": True,
                },
            )
            return summary

    one_shot = await _generate_one_shot(provider, prompt) if provider is not None else None
    summary = one_shot if one_shot is not None else str(fallback_text or prompt or "")
    _emit_summary_event(
        event_sink,
        {
            "type": "summary_finished",
            "text": summary,
            "streamed": False,
        },
    )
    return summary
