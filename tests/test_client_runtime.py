from src.client.config import ClientConfig
from src.client.runtime import build_ws_url, render_fetch_command


def test_build_ws_url() -> None:
    config = ClientConfig(
        server_host="127.0.0.1",
        server_port=8910,
        client_name="Rayne-Mac",
        client_id="client-1",
        workdir="/Users/rayne",
    )
    assert build_ws_url(config) == "ws://127.0.0.1:8910/clients/ws"


def test_render_fetch_command_injects_url_and_page_type() -> None:
    rendered = render_fetch_command(
        template='cliten fetch --url "{url}" --page-type "{page_type_hint}"',
        url="https://example.edu/programmes",
        page_type_hint="index",
    )
    assert "https://example.edu/programmes" in rendered
    assert "index" in rendered

