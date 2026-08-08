import pytest

from src.agent_runtime.base import AgentRequest
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.loop import AgentPageTimeout
from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime


@pytest.mark.asyncio
async def test_pydanticai_runtime_executes_skill_plan(monkeypatch):
    """Happy-path wiring: run() -> _run_agent() -> agent_loop() produces a
    "done" AgentResponse carrying the loop's trace through.

    Previously called the REAL agent_loop with no mocking — unlike every
    other test in this file — against the bogus URL "https://x". That
    isn't a fast failure: PAGE_TIMEOUT (src/agent_runtime/loop.py) is
    3600s, so a real run only ever *resolves* (success or
    AgentPageTimeout) after up to an hour of the live agent actually
    retrying the unreachable fetch, not the near-instant unit test this
    file's name implies. Mocking agent_loop — the same seam
    test_pydanticai_runtime_emits_lifecycle_events already uses — verifies
    the same wiring deterministically in milliseconds.
    """
    async def fake_loop(**_kwargs):
        return {
            "response": "done",
            "trace": ["step-1"],
            "iterations": 1,
            "collected_programs": [],
        }

    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.agent_loop", fake_loop)
    runtime = PydanticAIRuntime()
    result = await runtime.run(AgentRequest(task="crawl", payload={"url": "https://x"}))

    assert result.status == "done"
    assert result.trace


@pytest.mark.asyncio
async def test_runtime_falls_back_to_legacy_when_pydanticai_errors(monkeypatch):
    fallback_runtime = LegacyRuntime()
    runtime = PydanticAIRuntime(fallback_runtime=fallback_runtime)

    async def failing(_request):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "_run_agent", failing)

    result = await runtime.run(AgentRequest(task="crawl", payload={"url": "https://x"}))

    assert result.runtime_used == "legacy"


@pytest.mark.asyncio
async def test_page_timeout_is_not_caught_by_fallback(monkeypatch):
    """AgentPageTimeout must propagate — it should NOT trigger LegacyRuntime fallback."""
    runtime = PydanticAIRuntime()

    async def timeout_agent(_request):
        raise AgentPageTimeout("agent_loop exceeded 900s")

    monkeypatch.setattr(runtime, "_run_agent", timeout_agent)

    with pytest.raises(AgentPageTimeout):
        await runtime.run(AgentRequest(task="crawl", payload={"url": "https://x"}))


@pytest.mark.asyncio
async def test_pydanticai_runtime_emits_lifecycle_events(monkeypatch):
    emitted: list[dict[str, object]] = []

    async def fake_loop(**kwargs):
        event_sink = kwargs.get("event_sink")
        if event_sink is not None:
            event_sink({"type": "llm_call_started", "iteration": 1})
        return {
            "response": "done",
            "trace": [],
            "iterations": 1,
            "collected_programs": [],
        }

    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.agent_loop", fake_loop)
    runtime = PydanticAIRuntime()

    await runtime.run(
        AgentRequest(
            task="crawl",
            payload={"url": "https://example.com", "univ_slug": "x", "year": 2026},
            context={"event_sink": emitted.append},
        )
    )

    assert [event["type"] for event in emitted][:2] == [
        "agent_started",
        "llm_call_started",
    ]
