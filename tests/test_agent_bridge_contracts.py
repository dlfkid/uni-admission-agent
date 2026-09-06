from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.contracts import (
    AnalyzeInput,
    AnalyzeOutput,
    BrowserFetchInput,
)
from src.agent_bridge.serve_tool_bridge import ServeToolBridge


def test_serve_tool_bridge_analyze_contract(monkeypatch):
    del monkeypatch

    def fake_analyze(url: str, html_content: str, page_type_hint: str):
        del url, html_content, page_type_hint
        return {
            "page_type": "index",
            "links": [{"url": "https://x/detail", "text": "Detail"}],
            "total_found": 1,
        }

    bridge = ServeToolBridge(analyze_fn=fake_analyze)
    output = bridge.analyze_page(
        AnalyzeInput(
            url="https://x",
            page_type_hint="index",
            html_content="<html></html>",
        )
    )

    assert isinstance(output, AnalyzeOutput)


def test_client_automation_bridge_fetch_contract(monkeypatch):
    del monkeypatch

    async def fake_fetch(*, url: str, page_type_hint: str, client_id=None):
        del url, page_type_hint, client_id
        return {
            "html_content": "<html>ok</html>",
            "detail_pages_batch": [],
            "selected_urls": [],
            "selected_link_texts": {},
        }

    bridge = ClientAutomationBridge(fetch_fn=fake_fetch)
    output = bridge.fetch_browser_payload(
        BrowserFetchInput(url="https://x", page_type_hint="index")
    )

    assert output.html_content is not None
