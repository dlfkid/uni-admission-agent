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
