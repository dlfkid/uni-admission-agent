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
