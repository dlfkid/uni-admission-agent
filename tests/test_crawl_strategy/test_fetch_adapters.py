from unittest.mock import patch

from src.services.crawl_strategy import fetch_adapters


def test_server_adapter_returns_html_and_markdown():
    class _Page:
        html = "<html>x</html>"
        markdown = "# md"

    with patch.object(fetch_adapters, "_run_server_crawl", return_value=_Page()):
        html, md = fetch_adapters.server_fetch("https://x/")
    assert html == "<html>x</html>"
    assert md == "# md"


def test_client_adapter_converts_payload_to_markdown():
    with patch.object(fetch_adapters, "_run_client_fetch",
                      return_value={"html_content": "<html>c</html>"}), \
         patch.object(fetch_adapters, "_html_to_markdown", return_value="# client md"):
        html, md = fetch_adapters.client_fetch("https://x/")
    assert html == "<html>c</html>"
    assert md == "# client md"


def test_client_fetch_wait_uses_render_path():
    with patch.object(fetch_adapters, "_run_client_wait_fetch", return_value="<html>nus</html>"), \
         patch.object(fetch_adapters, "_html_to_markdown", return_value="### Doctor of X"):
        html, md = fetch_adapters.client_fetch("https://study.nus.edu.sg/programme", wait=True)
    assert html == "<html>nus</html>"
    assert "Doctor of X" in md
