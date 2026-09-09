"""A host that refuses connections ends the run at once, at every layer.

When gs.cuhk.edu.hk started refusing us, tenacity retried each page three
times, the fetch ladder escalated server → client and retried again, the
pipeline moved on to the next URL and did it all over, and a re-run began by
hammering the index page twelve times in a minute. Refusal is not a flaky
page; it is the host saying stop — and every extra knock lengthens the ban.
So a refused connection raises HostRefusedError, nothing retries it, nothing
swallows it, the job is FAILED with a message that says what to do, and the
CLI prints that message instead of a traceback.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

import src.services.crawl_strategy.discovery as disc
from src.models.ingestion import IngestionJob, IngestionJobStatus, IngestionTask, IngestionTaskState
from src.models.scraper_models import CrawlPageResult
from src.scrapers.errors import HostRefusedError, is_connection_refused
from src.services.crawl_strategy.fetch_ladder import fetch_with_escalation
from src.services.crawl_strategy.types import CrawlRange
from src.services.ingestion_pipeline import IngestionPipeline
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas

REFUSED_MSG = ("Unexpected error in _crawl_web at line 718 ... Error: Failed on navigating ACS-GOTO:\n"
               "Page.goto: net::ERR_CONNECTION_REFUSED at https://www.gs.cuhk.edu.hk/programmes/arts/ma-translation")


# ── recognising a refusal ─────────────────────────────────────────────


@pytest.mark.parametrize("text", [REFUSED_MSG, "ConnectError: [Errno 61] ECONNREFUSED", "connection refused by peer"])
def test_refusal_wording_is_recognised(text: str) -> None:
    assert is_connection_refused(RuntimeError(text))


@pytest.mark.parametrize("text", ["Page.goto: Timeout 30000ms exceeded", "HTTP 503", "net::ERR_NAME_NOT_RESOLVED"])
def test_other_failures_are_not_refusals(text: str) -> None:
    assert not is_connection_refused(RuntimeError(text))


def test_a_refusal_buried_in_the_cause_chain_counts() -> None:
    try:
        try:
            raise OSError("ECONNREFUSED")
        except OSError as inner:
            raise RuntimeError("fetch failed") from inner
    except RuntimeError as exc:
        assert is_connection_refused(exc)


def test_the_error_names_the_host_and_tells_the_user_to_wait() -> None:
    err = HostRefusedError("https://www.gs.cuhk.edu.hk/programmes/arts/ma-translation")
    assert err.host == "www.gs.cuhk.edu.hk"
    msg = str(err).lower()
    assert "www.gs.cuhk.edu.hk" in msg and "refus" in msg and "later" in msg


# ── engine: one attempt, no retry, not swallowed ──────────────────────


class _FakeCrawler:
    calls = 0

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def arun(self, url, config=None):
        _FakeCrawler.calls += 1
        return [SimpleNamespace(success=False, error_message=REFUSED_MSG)]  # crawl4ai returns a container


@pytest.mark.asyncio
async def test_crawl_page_raises_host_refused_after_a_single_attempt(monkeypatch) -> None:
    from src.scrapers import engine
    monkeypatch.setattr(engine, "AsyncWebCrawler", _FakeCrawler)
    _FakeCrawler.calls = 0
    scraper = engine.AdmissionScraper.__new__(engine.AdmissionScraper)
    scraper.browser_config = None; scraper.crawler_config = None
    scraper._export_md = False; scraper._export_path = None
    with pytest.raises(HostRefusedError) as exc:
        await scraper.crawl_page("https://www.gs.cuhk.edu.hk/programmes/arts/ma-translation")
    assert _FakeCrawler.calls == 1          # tenacity would have made it 3
    assert exc.value.host == "www.gs.cuhk.edu.hk"


@pytest.mark.asyncio
async def test_crawl_urls_stops_at_the_refusal_instead_of_skipping(monkeypatch) -> None:
    from src.scrapers import engine
    scraper = engine.AdmissionScraper.__new__(engine.AdmissionScraper)
    scraper._failed_urls = []
    attempted: list = []

    async def fake_crawl_page(url):
        attempted.append(url)
        raise HostRefusedError(url)
    monkeypatch.setattr(scraper, "crawl_page", fake_crawl_page)
    with pytest.raises(HostRefusedError):
        await scraper._crawl_urls(["https://x.edu/a", "https://x.edu/b"])
    assert attempted == ["https://x.edu/a"]


# ── pipeline: the run ends, the job says why, no stage retry ──────────


class _RefusingScraper:
    def __init__(self) -> None:
        self.router = MagicMock(); self._export_md = False; self._export_path = None
        self.attempted: list = []
    def _reset_session_state(self) -> None: return
    async def _crawl_urls(self, urls):
        self.attempted.extend(urls)
        if urls[0].endswith("/b"):
            raise HostRefusedError(urls[0])
        return [CrawlPageResult(url=u, markdown="# P", char_count=3, links=[]) for u in urls]


class TestPipeline:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine); SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager(); self.dm.engine = self.engine
        self.pipeline = IngestionPipeline(db_manager=self.dm)

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    @pytest.mark.asyncio
    async def test_fetch_loop_stops_at_the_refused_page(self) -> None:
        scraper = _RefusingScraper()
        with pytest.raises(HostRefusedError):
            await self.pipeline._crawl_urls_with_failures(
                scraper, ["https://x.edu/a", "https://x.edu/b", "https://x.edu/c"])
        assert scraper.attempted == ["https://x.edu/a", "https://x.edu/b"]

    @pytest.mark.asyncio
    async def test_the_job_fails_once_with_a_message_and_no_stage_retry(self, monkeypatch) -> None:
        created: list = []
        def make():
            s = _RefusingScraper(); created.append(s); return s
        monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", make)
        executions: list = []
        real = self.pipeline._execute_stage
        async def counting(stage, request_payload, context, event_callback=None):
            executions.append(stage)
            return await real(stage, request_payload, context, event_callback=event_callback)
        monkeypatch.setattr(self.pipeline, "_execute_stage", counting)

        with pytest.raises(HostRefusedError):
            await self.pipeline.run_new_job(url="https://x.edu/i", univ_slug="x", year=2027,
                                            page_type_hint="index",
                                            selected_urls=["https://x.edu/a", "https://x.edu/b", "https://x.edu/c"])

        assert len(executions) == 1, executions          # fetch_raw ran once; not retried
        with Session(self.engine) as s:
            job = s.exec(select(IngestionJob)).one()
            tasks = s.exec(select(IngestionTask).where(IngestionTask.job_id == job.id)).all()
        assert job.status is IngestionJobStatus.FAILED
        assert "x.edu" in job.error_message and "refus" in job.error_message.lower()
        assert "later" in job.error_message.lower()
        assert [t.state for t in tasks] == [IngestionTaskState.FAILED]


# ── discovery: ladder and fallback do not knock again ─────────────────


def test_fetch_ladder_does_not_escalate_past_a_refusal() -> None:
    def server(url): raise HostRefusedError(url)
    def client(url, **kw): raise AssertionError("client fetch must not run after a refusal")
    with pytest.raises(HostRefusedError):
        fetch_with_escalation("https://x.edu/i", server_fetch=server, client_fetch=client)


def test_discovery_propagates_a_refusal_instead_of_falling_back_to_scout(monkeypatch, tmp_path) -> None:
    def refusing(*a, **k): raise HostRefusedError("https://x.edu/i")
    monkeypatch.setattr(disc, "crawl_index", refusing)
    with pytest.raises(HostRefusedError):
        disc.discover_candidates("https://x.edu/i", CrawlRange.default(),
                                 server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
                                 api_fetch=lambda e, **k: "", report_out=tmp_path, timestamp="t")


# ── CLI: a sentence, not a traceback ──────────────────────────────────


def test_cli_prints_the_refusal_and_exits_nonzero(monkeypatch) -> None:
    from typer.testing import CliRunner
    from src.cmd import cli

    async def refusing(**kwargs):
        raise HostRefusedError("https://www.gs.cuhk.edu.hk/programme-filter")
    monkeypatch.setattr(cli, "_init_db", lambda verbose=False: None)
    monkeypatch.setattr(cli, "crawl_url", refusing)
    res = CliRunner().invoke(cli.app, ["crawl", "--name", "cuhk", "--year", "2027",
                                       "--url", "https://www.gs.cuhk.edu.hk/programme-filter", "--all"])
    assert res.exit_code == 1
    out = res.output.lower()
    assert "www.gs.cuhk.edu.hk" in out and "refus" in out and "later" in out
    assert "traceback" not in out
