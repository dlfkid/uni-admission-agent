from unittest.mock import patch

from src.services.crawl_strategy import fetch_adapters
import src.services.crawl_strategy.fetch_adapters as fa


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


def test_client_fetch_wait_forwards_target_count(monkeypatch):
    seen = {}

    def fake_wait(url, *, target_count=None, max_rounds=40):
        seen["target_count"] = target_count
        return "<html>Doctor of X</html>"

    monkeypatch.setattr(fa, "_run_client_wait_fetch", fake_wait)
    monkeypatch.setattr(fa, "_html_to_markdown", lambda html, url: "## Doctor of X")

    html, md = fa.client_fetch("https://x.edu/p", wait=True, target_count=17)
    assert seen["target_count"] == 17
    assert md == "## Doctor of X"


def test_enough_matches_gates_the_scroll_stop():
    # The scroll loop stops once this helper says enough programme names show.
    assert fa._enough_matches("Doctor of A Doctor of B", 2) is True
    assert fa._enough_matches("Doctor of A", 2) is False
    assert fa._enough_matches("anything", None) is False   # target=None never "enough"
