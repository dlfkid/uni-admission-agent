"""`--page-delay N`: a minimum interval between detail-page fetches.

CUHK's Graduate School host refuses connections after ~18 requests at the
default pace (one page every ~8 s) and blocked the address for hours after a
full run. The default pace stays exactly as it was; the flag lets a run be
slowed to a rhythm the host tolerates. The interval is measured between the
starts of consecutive fetches, so a slow page does not add to the wait.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.crawler as crawler_mod
from src.models.scraper_models import CrawlPageResult
from src.services.ingestion_pipeline import IngestionPipeline


def _page(url: str) -> CrawlPageResult:
    return CrawlPageResult(url=url, markdown="# P", char_count=3, links=[])


class _RecordingScraper:
    def __init__(self, fetch_seconds: float = 0.0) -> None:
        self.starts: list[float] = []
        self.fetch_seconds = fetch_seconds
        self.router = MagicMock(); self._export_md = False; self._export_path = None

    def _reset_session_state(self) -> None: return

    async def _crawl_urls(self, urls):
        import asyncio
        self.starts.append(time.monotonic())
        if self.fetch_seconds:
            await asyncio.sleep(self.fetch_seconds)
        return [_page(u) for u in urls]


URLS = [f"https://x.edu/{i}" for i in range(4)]


@pytest.mark.asyncio
async def test_consecutive_fetch_starts_are_at_least_page_delay_apart() -> None:
    scraper = _RecordingScraper()
    pipeline = IngestionPipeline(db_manager=MagicMock())
    await pipeline._crawl_urls_with_failures(scraper, URLS, page_delay=0.3)
    gaps = [b - a for a, b in zip(scraper.starts, scraper.starts[1:])]
    assert len(gaps) == 3 and all(g >= 0.29 for g in gaps), gaps


@pytest.mark.asyncio
async def test_a_slow_fetch_counts_toward_the_interval() -> None:
    """Interval is start-to-start: a 0.25 s fetch under a 0.3 s delay waits ~0.05 s more, not 0.3 s."""
    scraper = _RecordingScraper(fetch_seconds=0.25)
    pipeline = IngestionPipeline(db_manager=MagicMock())
    await pipeline._crawl_urls_with_failures(scraper, URLS[:2], page_delay=0.3)
    gap = scraper.starts[1] - scraper.starts[0]
    assert 0.29 <= gap < 0.5, gap


@pytest.mark.asyncio
async def test_without_page_delay_nothing_is_added() -> None:
    scraper = _RecordingScraper()
    pipeline = IngestionPipeline(db_manager=MagicMock())
    t0 = time.monotonic()
    await pipeline._crawl_urls_with_failures(scraper, URLS)
    assert time.monotonic() - t0 < 0.2
    await pipeline._crawl_urls_with_failures(scraper, URLS, page_delay=None)
    assert time.monotonic() - t0 < 0.4


@pytest.mark.asyncio
async def test_fetch_raw_passes_the_request_payload_delay_to_every_fetch(monkeypatch) -> None:
    monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", _RecordingScraper)
    pipeline = IngestionPipeline(db_manager=MagicMock())
    seen: list = []
    real = pipeline._crawl_urls_with_failures

    async def spy(scraper, urls, **kw):
        seen.append(kw.get("page_delay"))
        return await real(scraper, urls, **kw)
    monkeypatch.setattr(pipeline, "_crawl_urls_with_failures", spy)

    await pipeline._stage_fetch_raw({"url": "https://x.edu/i", "page_type_hint": "index",
                                     "selected_urls": URLS[:2], "page_delay": 0.01})
    assert seen == [0.01]


@pytest.mark.asyncio
async def test_crawl_url_threads_page_delay_into_the_job(monkeypatch) -> None:
    spy = AsyncMock(return_value={"imported_count": 0, "persisted_program_ids": []})
    monkeypatch.setattr(crawler_mod.IngestionPipeline, "run_new_job", spy, raising=True)
    monkeypatch.setattr(crawler_mod.browser_provider_service, "resolve_browser_inputs", AsyncMock(return_value={}))
    monkeypatch.setattr(crawler_mod, "_build_review_items", lambda *a, **k: [])
    await crawler_mod.crawl_url("https://x.edu/p", "x", 2027, page_type_hint="index",
                                selected_urls=URLS[:1], page_delay=10)
    assert spy.call_args.kwargs["page_delay"] == 10
    await crawler_mod.crawl_url("https://x.edu/p", "x", 2027, page_type_hint="index", selected_urls=URLS[:1])
    assert spy.call_args.kwargs["page_delay"] is None


def test_run_new_job_records_page_delay_in_the_request_payload() -> None:
    """ingestion-resume replays request_payload, so the pace must travel with the job."""
    captured: dict = {}
    pipeline = IngestionPipeline(db_manager=MagicMock())
    pipeline._create_job = lambda payload: captured.update(payload) or "uid"

    async def fake_run(**kw): return {}
    pipeline._run_job = fake_run
    import asyncio
    asyncio.run(pipeline.run_new_job(url="https://x.edu/i", univ_slug="x", year=2027, page_delay=7.5))
    assert captured["page_delay"] == 7.5


def test_cli_forwards_page_delay_and_defaults_to_none(monkeypatch) -> None:
    from typer.testing import CliRunner
    from src.cmd import cli
    captured: dict = {}

    async def fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return crawler_mod.CrawlResult(imported_count=0, univ_slug="x", year=2027)
    monkeypatch.setattr(cli, "_init_db", lambda verbose=False: None)
    monkeypatch.setattr(cli, "crawl_url", fake_crawl_url)
    runner = CliRunner()
    base = ["crawl", "--name", "x", "--year", "2027", "--url", "https://x.edu/p", "--all"]
    assert runner.invoke(cli.app, base + ["--page-delay", "10"]).exit_code == 0
    assert captured["page_delay"] == 10.0
    captured.clear()
    assert runner.invoke(cli.app, base).exit_code == 0
    assert captured["page_delay"] is None
    res = runner.invoke(cli.app, base + ["--page-delay", "-1"])
    assert res.exit_code != 0
