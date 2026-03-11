import pytest

from src.agent_runtime.model_provider import AgentConfigError, ModelProviderAdapter


def test_model_provider_uses_internal_router_when_enabled(monkeypatch):
    del monkeypatch

    adapter = ModelProviderAdapter(
        allow_internal=True,
        allow_external=False,
        internal_factory=lambda: object(),
    )
    client = adapter.resolve(mode="internal")

    assert client.mode == "internal"


def test_model_provider_uses_external_client_context(monkeypatch):
    del monkeypatch

    adapter = ModelProviderAdapter(allow_internal=False, allow_external=True)
    client = adapter.resolve(mode="external", external_context={"session_id": "abc"})

    assert client.mode == "external"


def test_model_provider_errors_when_internal_disabled():
    adapter = ModelProviderAdapter(allow_internal=False, allow_external=True)

    with pytest.raises(AgentConfigError, match="internal model disabled"):
        adapter.resolve(mode="internal")
