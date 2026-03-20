import pytest

from src.agent_runtime.base import AgentRequest
from src.agent_runtime.legacy_runtime import LegacyRuntime
from src.agent_runtime.loop import AgentPageTimeout
from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime


@pytest.mark.asyncio
async def test_pydanticai_runtime_executes_skill_plan(monkeypatch):
    del monkeypatch

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
