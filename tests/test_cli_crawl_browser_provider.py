from typer.testing import CliRunner

from src.cmd import cli
from src.services.crawler import CrawlResult


runner = CliRunner()


def test_cli_crawl_passes_browser_provider(monkeypatch) -> None:
    captured: dict = {}

    async def fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return CrawlResult(
            imported_count=1,
            univ_slug="uom",
            year=2026,
            ingestion_job_id="job-cli",
        )

    monkeypatch.setattr(cli, "_setup_logging", lambda _verbose: None)
    monkeypatch.setattr(cli, "_init_db", lambda _verbose: None)
    monkeypatch.setattr(cli, "crawl_url", fake_crawl_url)

    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "--name",
            "uom",
            "--year",
            "2026",
            "--url",
            "https://example.edu/programmes",
            "--browser-provider",
            "client",
            "--client-id",
            "c1",
            "--strict-client",
            "--candidate-taxonomy-filter-enabled",
            "--candidate-taxonomy-filter-threshold",
            "0.84",
            "--candidate-taxonomy-filter-top-k",
            "11",
        ],
    )

    assert result.exit_code == 0
    assert captured["browser_provider"] == "client"
    assert captured["client_id"] == "c1"
    assert captured["strict_client"] is True
    assert captured["candidate_taxonomy_filter_enabled"] is True
    assert captured["candidate_taxonomy_filter_threshold"] == 0.84
    assert captured["candidate_taxonomy_filter_top_k"] == 11
