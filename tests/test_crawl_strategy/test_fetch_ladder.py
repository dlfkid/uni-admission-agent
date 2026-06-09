from src.services.crawl_strategy.fetch_ladder import content_is_usable, fetch_with_escalation
from src.services.crawl_strategy.types import FetchMode


def test_cloudflare_challenge_not_usable():
    md = "# Just a moment...\nVerifying you are human. cloudflare"
    assert content_is_usable(md) is False


def test_empty_page_not_usable():
    assert content_is_usable("\n\n  ") is False


def test_real_listing_is_usable():
    md = "".join(f"## [Programme {i} MSc](https://x/p{i}-msc)\n" for i in range(10))
    assert content_is_usable(md) is True


def test_escalation_stops_at_first_usable():
    calls = []

    def server(url):
        calls.append("server")
        return ("", "")

    def client(url, **kw):
        calls.append("client")
        md = "".join(f"## [P{i} MSc](https://x/p{i}-msc)\n" for i in range(10))
        return ("<html>", md)

    res = fetch_with_escalation(
        "https://x/programmes", server_fetch=server, client_fetch=client)
    assert res.level_used == FetchMode.CLIENT.value
    assert calls == ["server", "client"]
    assert "P0 MSc" in res.markdown


def test_escalation_exhausted_returns_last_empty():
    def empty_server(url):
        return ("", "")

    def empty_client(url, **kw):
        return ("", "")

    res = fetch_with_escalation(
        "https://x/programmes", server_fetch=empty_server, client_fetch=empty_client)
    assert res.level_used == FetchMode.CLIENT_WAIT.value
    assert res.levels_tried == ["server", "client", "client_wait"]
    assert content_is_usable(res.markdown) is False
