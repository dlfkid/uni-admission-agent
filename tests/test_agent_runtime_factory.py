from types import SimpleNamespace

from src.agent_runtime.runtime_factory import build_agent_runtime


def _config(runtime=None):
    return SimpleNamespace(runtime=runtime)


def test_factory_returns_pydanticai_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)

    runtime = build_agent_runtime(_config(), bridge=None, model_adapter=None)

    assert runtime.name == "pydanticai"


def test_factory_returns_pydanticai_when_configured(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "pydanticai")

    runtime = build_agent_runtime(_config(), bridge=None, model_adapter=None)

    assert runtime.name == "pydanticai"
