from types import SimpleNamespace

import pytest

from src.agent_runtime.base import AgentRequest
from src.agent_runtime.loop import agent_loop
from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
from src.agent_runtime.skills.registry import SkillRegistry


@pytest.mark.asyncio
async def test_runtime_delegates_to_agent_loop(monkeypatch):
    """The runtime now delegates to the LLM-driven agent loop."""

    async def _fake_agent_loop(*, user_message, registry, **_kwargs):
        del registry
        return {
            "response": f"Processed: {user_message[:30]}",
            "trace": [{"stage": "agent_done", "iteration": 2}],
            "iterations": 2,
        }

    monkeypatch.setattr(
        "src.agent_runtime.pydanticai_runtime.agent_loop", _fake_agent_loop
    )

    runtime = PydanticAIRuntime()
    result = await runtime.run(
        AgentRequest(
            task="crawl",
            payload={
                "url": "https://x/index",
                "univ_slug": "uom",
                "year": 2026,
                "page_type_hint": "index",
            },
            context={"autonomous": True},
        )
    )

    assert result.status == "done"
    assert result.runtime_used == "pydanticai"
    assert result.output["iterations"] == 2
    assert "Processed:" in result.output["agent_response"]


@pytest.mark.asyncio
async def test_runtime_builds_correct_user_message(monkeypatch):
    """Verify the user message includes URL, slug, year, and hint."""
    captured_messages: list[str] = []

    async def _capture_loop(*, user_message, registry, **_kwargs):
        del registry
        captured_messages.append(user_message)
        return {"response": "ok", "trace": [], "iterations": 1}

    monkeypatch.setattr(
        "src.agent_runtime.pydanticai_runtime.agent_loop", _capture_loop
    )

    runtime = PydanticAIRuntime()
    await runtime.run(
        AgentRequest(
            task="crawl",
            payload={
                "url": "https://example.com/programs",
                "univ_slug": "ucl",
                "year": 2026,
                "page_type_hint": "auto",
            },
        )
    )

    assert len(captured_messages) == 1
    msg = captured_messages[0]
    assert "https://example.com/programs" in msg
    assert "ucl" in msg
    assert "2026" in msg
    assert "auto" in msg


@pytest.mark.asyncio
async def test_agent_loop_emits_tool_call_started_and_finished(monkeypatch, tmp_path):
    emitted: list[dict[str, object]] = []
    monkeypatch.chdir(tmp_path)

    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="todo",
                                    arguments=(
                                        '{"items":[{"content":"inspect page","status":"completed"}]}'
                                    ),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="done", tool_calls=[]),
                    finish_reason="stop",
                )
            ]
        ),
    ]

    class FakeCompletions:
        async def create(self, **_kwargs):
            return responses.pop(0)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    monkeypatch.setattr(
        "src.agent_runtime.loop.resolve_openai_client",
        lambda: (fake_client, "fake-model"),
    )

    result = await agent_loop(
        user_message="crawl this page",
        registry=SkillRegistry([]),
        max_iterations=2,
        event_sink=emitted.append,
    )

    event_types = [event["type"] for event in emitted]
    assert "tool_call_started" in event_types
    assert "tool_call_finished" in event_types
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_agent_loop_emits_agent_done_when_max_iterations_hit(monkeypatch, tmp_path):
    emitted: list[dict[str, object]] = []
    monkeypatch.chdir(tmp_path)

    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="todo",
                                    arguments=(
                                        '{"items":[{"content":"inspect page","status":"completed"}]}'
                                    ),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ]
        )
    ]

    class FakeCompletions:
        async def create(self, **_kwargs):
            return responses.pop(0)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    monkeypatch.setattr(
        "src.agent_runtime.loop.resolve_openai_client",
        lambda: (fake_client, "fake-model"),
    )

    await agent_loop(
        user_message="crawl this page",
        registry=SkillRegistry([]),
        max_iterations=1,
        event_sink=emitted.append,
    )

    event_types = [event["type"] for event in emitted]
    assert "agent_done" in event_types
