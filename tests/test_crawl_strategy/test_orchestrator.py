from pathlib import Path

from src.services.crawl_strategy.orchestrator import crawl_index


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
