"""Tests for streaming acceptance checks in the E2E smoke script."""

from scripts.e2e_agent_smoke import evaluate_streaming_acceptance


def test_e2e_agent_smoke_checks_streaming_acceptance():
    verdict = evaluate_streaming_acceptance(
        events=[
            {"type": "agent_started"},
            {"type": "llm_call_started"},
            {"type": "tool_call_finished"},
            {"type": "agent_done"},
            {"type": "summary_started"},
            {"type": "summary_finished", "streamed": False},
        ]
    )

    assert verdict["lifecycle_ok"] is True
    assert verdict["summary_ok"] is True
