import json

import pytest

from src.client.config import ClientConfig
from src.client.runtime import ClientRuntime, build_ws_url, render_fetch_command


def test_build_ws_url() -> None:
    config = ClientConfig(
        server_url="http://127.0.0.1:8910",
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    assert build_ws_url(config) == "ws://127.0.0.1:8910/clients/ws"


def test_render_fetch_command_injects_url_and_page_type() -> None:
    rendered = render_fetch_command(
        template='client fetch --url "{url}" --page-type "{page_type_hint}"',
        url="https://example.edu/programmes",
        page_type_hint="index",
    )
    assert "https://example.edu/programmes" in rendered
    assert "index" in rendered


@pytest.mark.asyncio
async def test_runtime_uses_native_browser_when_fetch_command_missing(monkeypatch) -> None:
    monkeypatch.delenv("ADM_AGENT_CLIENT_FETCH_CMD", raising=False)
    config = ClientConfig(
        server_url="http://127.0.0.1:8910",
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    runtime = ClientRuntime(config)

    def _fake_native_fetch(**kwargs):
        return {
            "html_content": "<html></html>",
            "detail_pages_batch": [],
            "selected_urls": [],
        }

    monkeypatch.setattr("src.client.runtime.fetch_browser_payload", _fake_native_fetch)

    payload = await runtime._fetch_browser_payload(
        url="https://example.edu/programmes",
        page_type_hint="index",
    )
    assert payload["html_content"] == "<html></html>"


@pytest.mark.asyncio
async def test_fetch_browser_payload_uses_caller_detail_limit_over_local_default(monkeypatch) -> None:
    """Regression: a crawl requesting 5 programmes via client mode
    silently got at most 4 (this client's own ADM_AGENT_CLIENT_DETAIL_LIMIT
    default) with no error or warning — the caller's actual request never
    reached native_browser.fetch_browser_payload's own detail_limit param
    at all. The caller's limit, when given, must win."""
    monkeypatch.delenv("ADM_AGENT_CLIENT_FETCH_CMD", raising=False)
    monkeypatch.delenv("ADM_AGENT_CLIENT_DETAIL_LIMIT", raising=False)
    config = ClientConfig(
        server_url="http://127.0.0.1:8910",
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    runtime = ClientRuntime(config)
    assert runtime.native_detail_limit == 4  # the documented default

    captured: dict = {}

    def _fake_native_fetch(**kwargs):
        captured.update(kwargs)
        return {"html_content": "<html></html>", "detail_pages_batch": [], "selected_urls": []}

    monkeypatch.setattr("src.client.runtime.fetch_browser_payload", _fake_native_fetch)

    await runtime._fetch_browser_payload(
        url="https://example.edu/programmes",
        page_type_hint="index",
        detail_limit=5,
    )
    assert captured["detail_limit"] == 5

    captured.clear()
    await runtime._fetch_browser_payload(
        url="https://example.edu/programmes",
        page_type_hint="index",
    )
    assert captured["detail_limit"] == 4  # no caller limit -> local default unchanged


@pytest.mark.asyncio
async def test_handle_rpc_request_forwards_detail_limit_from_payload(monkeypatch) -> None:
    """The server-side RPC dispatch now includes detail_limit in the wire
    payload when the caller specified one — this locks in that
    _handle_rpc_request actually reads and forwards it, not just that
    _fetch_browser_payload honours it in isolation (previous test)."""
    config = ClientConfig(
        server_url="http://127.0.0.1:8910",
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    runtime = ClientRuntime(config)

    captured: dict = {}

    async def _fake_fetch_browser_payload(*, url: str, page_type_hint: str, detail_limit=None):
        captured["url"] = url
        captured["page_type_hint"] = page_type_hint
        captured["detail_limit"] = detail_limit
        return {"html_content": "<html></html>", "detail_pages_batch": [], "selected_urls": []}

    monkeypatch.setattr(runtime, "_fetch_browser_payload", _fake_fetch_browser_payload)

    class DummyWebSocket:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, payload: str) -> None:
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
                "detail_limit": 5,
            },
        },
    )

    assert captured["detail_limit"] == 5
    sent = [json.loads(msg) for msg in websocket.messages]
    assert any(item.get("type") == "rpc_result" for item in sent)


@pytest.mark.asyncio
async def test_handle_rpc_request_tolerates_missing_or_malformed_detail_limit(monkeypatch) -> None:
    config = ClientConfig(
        server_url="http://127.0.0.1:8910",
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    runtime = ClientRuntime(config)

    captured: dict = {}

    async def _fake_fetch_browser_payload(*, url: str, page_type_hint: str, detail_limit=None):
        del url, page_type_hint
        captured["detail_limit"] = detail_limit
        return {"html_content": "<html></html>", "detail_pages_batch": [], "selected_urls": []}

    monkeypatch.setattr(runtime, "_fetch_browser_payload", _fake_fetch_browser_payload)

    class DummyWebSocket:
        async def send(self, payload: str) -> None:
            del payload

    # No detail_limit key at all (older server, or a detail-page fetch).
    await runtime._handle_rpc_request(
        DummyWebSocket(),
        {
            "request_id": "req-1",
            "action": "fetch_browser_payload",
            "payload": {"url": "https://example.edu/list", "page_type_hint": "index"},
        },
    )
    assert captured["detail_limit"] is None

    # Malformed value must not crash the whole request.
    await runtime._handle_rpc_request(
        DummyWebSocket(),
        {
            "request_id": "req-2",
            "action": "fetch_browser_payload",
            "payload": {
                "url": "https://example.edu/list",
                "page_type_hint": "index",
                "detail_limit": "not-a-number",
            },
        },
    )
    assert captured["detail_limit"] is None
