from pathlib import Path

from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy.types import ExtractKind, FetchMode, Strategy


def _leeds_md():
    return "".join(
        f"##  [Programme {i} MSc](https://courses.leeds.ac.uk/c{i}/programme-{i}-msc) Duration\n"
        for i in range(15))


def test_known_university_uses_pinned_strategy(tmp_path):
    def server(url):
        return ("<html>", _leeds_md())

    def client(url, **kw):
        raise AssertionError("known Leeds is server-pinned; client must not be called")

    out = crawl_index(
        "https://courses.leeds.ac.uk/course-search/masters-courses",
        server_fetch=server, client_fetch=client,
        report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 15
    assert out.strategy_used == "server×heading_link"
    assert out.report_zip is None


def test_unknown_known_structure_classifies(tmp_path):
    md = "".join(
        f"[Programme {i} BSc](https://example.edu/degrees/programme-{i}-bsc)\n"
        for i in range(9))

    def server(url):
        return ("<html>", md)

    def client(url, **kw):
        return ("", "")

    out = crawl_index("https://example.edu/degrees",
                      server_fetch=server, client_fetch=client,
                      report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 9
    assert out.strategy_used.endswith("inline_degree")


def test_unsupported_page_exports_report(tmp_path):
    def server(url):
        return ("<html>nav</html>", "[Home](https://x/)\n[Apply](https://x/a)\n")

    def client(url, **kw):
        return ("<html>nav</html>", "[Home](https://x/)\n[Apply](https://x/a)\n")

    out = crawl_index("https://newuni.edu/programmes",
                      server_fetch=server, client_fetch=client,
                      report_out=tmp_path, timestamp="20260609-120000")
    assert out.status == "unsupported"
    assert out.report_zip is not None
    assert Path(out.report_zip).exists()
    assert out.message_for_user


def test_pinned_client_wait_forwards_wait_true(tmp_path, monkeypatch):
    """Fix 1: CLIENT_WAIT pinned strategy must call client_fetch with wait=True."""
    import src.services.crawl_strategy.orchestrator as orch_mod

    pinned_strategy = Strategy(
        FetchMode.CLIENT_WAIT,
        ExtractKind.TEXT_HEADING,
        params={"wait_selector": ".card"},
    )
    monkeypatch.setattr(orch_mod.registry_mod, "lookup", lambda url: pinned_strategy)

    received_kwargs: dict = {}

    def client(url, **kw):
        received_kwargs.update(kw)
        # Return thin content so we fall through to the report path (no real
        # extraction needed — we only care that the kwargs were forwarded).
        return ("<html>thin</html>", "[Nav](https://x/)\n")

    crawl_index(
        "https://nus.edu.sg/programmes",
        server_fetch=lambda url: ("<html>", ""),
        client_fetch=client,
        report_out=tmp_path,
        timestamp="t",
    )

    assert received_kwargs.get("wait") is True, (
        f"Expected wait=True to be forwarded; got kwargs={received_kwargs}"
    )
    assert received_kwargs.get("wait_selector") == ".card", (
        f"Expected wait_selector='.card' to be forwarded; got kwargs={received_kwargs}"
    )


def _thin_nav_md():
    """Return nav-only markdown that fails the content_is_usable gate."""
    return "[Home](https://courses.leeds.ac.uk/)\n[Apply](https://courses.leeds.ac.uk/apply)\n"


def test_pinned_strategy_failed_message_mentions_known_strategy(tmp_path, monkeypatch):
    """Fix 4: when a pinned university's fetch yields unusable content, the
    message should mention '已知策略' and NOT say '暂不支持'."""
    import src.services.crawl_strategy.orchestrator as orch_mod

    pinned_strategy = Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK)
    monkeypatch.setattr(orch_mod.registry_mod, "lookup", lambda url: pinned_strategy)

    def server(url):
        # Thin nav-only markdown — passes through server path but fails content gate
        return ("<html>nav</html>", _thin_nav_md())

    def client(url, **kw):
        raise AssertionError("pinned server strategy must not call client")

    out = crawl_index(
        "https://courses.leeds.ac.uk/course-search/masters-courses",
        server_fetch=server,
        client_fetch=client,
        report_out=tmp_path,
        timestamp="20260609-120000",
    )

    assert out.status == "unsupported"
    assert out.report_zip is not None
    assert Path(out.report_zip).exists()
    assert "已知策略" in out.message_for_user, (
        f"Expected '已知策略' in message; got: {out.message_for_user!r}"
    )
    assert "暂不支持" not in out.message_for_user, (
        f"Unexpected '暂不支持' in known-strategy-failure message: {out.message_for_user!r}"
    )
