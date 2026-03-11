import json

import pytest

from src.client.config import (
    ClientConfig,
    ClientPolicyProfile,
    load_client_config,
    save_client_config,
)
from src.client.runtime import ClientRuntime


def test_client_policy_profile_saved_and_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    config = ClientConfig(
        server_host="127.0.0.1",
        server_port=8910,
        client_name="Rayne-Mac",
        client_id="client-001",
        workdir="/Users/rayne",
        policy_profile=ClientPolicyProfile(batch_size=7, taxonomy_auto_threshold=0.88),
    )

    save_client_config(config)
    loaded = load_client_config()

    assert loaded is not None
    assert loaded.policy_profile is not None
    assert loaded.policy_profile.batch_size == 7
    assert loaded.policy_profile.taxonomy_auto_threshold == 0.88


@pytest.mark.asyncio
async def test_client_runtime_embeds_policy_in_rpc_payload(monkeypatch):
    config = ClientConfig(
        server_host="127.0.0.1",
        server_port=8910,
        client_name="Rayne-Mac",
        client_id="client-001",
        workdir="/Users/rayne",
        policy_profile=ClientPolicyProfile(batch_size=6),
    )
    runtime = ClientRuntime(config)

    async def _fake_fetch_browser_payload(*, url: str, page_type_hint: str):
        del url, page_type_hint
        return {
            "html_content": "<html></html>",
            "detail_pages_batch": [],
            "selected_urls": [],
        }

    monkeypatch.setattr(runtime, "_fetch_browser_payload", _fake_fetch_browser_payload)

    class DummyWebSocket:
        def __init__(self):
            self.messages = []

        async def send(self, payload: str):
            self.messages.append(payload)

    websocket = DummyWebSocket()

    await runtime._handle_rpc_request(
        websocket,
        {
            "request_id": "req-1",
            "action": "fetch_browser_payload",
            "payload": {
                "url": "https://example.edu/list",
                "page_type_hint": "index",
            },
        },
    )

    sent = [json.loads(msg) for msg in websocket.messages]
    result_payload = next(item for item in sent if item.get("type") == "rpc_result")

    assert result_payload["payload"]["policy_profile"]["batch_size"] == 6
