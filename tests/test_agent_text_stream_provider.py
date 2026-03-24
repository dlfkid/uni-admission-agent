"""Tests for provider-side text streaming adapter support."""

from src.agent_runtime.model_provider import ModelProviderAdapter


def test_model_provider_adapter_exposes_stream_text_when_supported():
    class _StreamingClient:
        async def stream_text(self, prompt: str):
            yield prompt

        async def generate_text(self, prompt: str) -> str:
            return prompt

    adapter = ModelProviderAdapter(
        allow_internal=True,
        allow_external=False,
        internal_factory=lambda: _StreamingClient(),
    )

    client = adapter.resolve(mode="internal")

    assert hasattr(client, "stream_text")
    assert callable(client.stream_text)
