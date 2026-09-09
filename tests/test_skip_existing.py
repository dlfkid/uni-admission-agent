"""`--skip-existing`: continue a crawl without re-fetching what is already stored.

There was no way to pick up where a crawl left off. A canary `--limit 20`
followed by `--all` re-fetched the twenty; a full run that was interrupted
before the persist stage had to start over. Discovery still finds every
detail URL, but with this flag the ones whose canonical source_url already
exists for the same university and year are dropped before any page is
fetched or any LLM call made.
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import src.services.crawler as crawler_mod
from src.models.admission import Program, ProgramCatalog, University
from src.services.crawl_strategy.discovery import DiscoveryResult
from src.services.crawler import partition_existing_urls
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas

A = "https://x.edu/programmes/a"
B = "https://x.edu/programmes/b"
C = "https://x.edu/programmes/c"


class _DB:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _seed(self, slug: str, year: int, *source_urls: str) -> None:
        with Session(self.engine) as s:
            uni = University(name=slug.upper(), slug=slug)
            s.add(uni); s.flush()
            for i, url in enumerate(source_urls):
                cat = ProgramCatalog(university_id=uni.id, canonical_name_en=f"P{i}", catalog_key=f"url:{url}")
                s.add(cat); s.flush()
                s.add(Program(name_en=f"P{i}", academic_year=year, university_id=uni.id,
                              program_catalog_id=cat.id, source_url=url))
            s.commit()


# ── the partition ─────────────────────────────────────────────────────


class TestPartition(_DB):
    def test_urls_already_stored_for_the_same_university_and_year_are_skipped(self) -> None:
        self._seed("x", 2027, A, B)
        fresh, skipped = partition_existing_urls([A, B, C], "x", 2027)
        assert fresh == [C]
        assert skipped == [A, B]

    def test_matching_is_canonical_not_byte_for_byte(self) -> None:
        """Stored source_url is the page URL as crawled; discovery may hand back
        a trailing slash, different host case or a query string."""
        self._seed("x", 2027, A + "/")
        fresh, skipped = partition_existing_urls(["HTTPS://X.EDU/programmes/a?utm=1", B], "x", 2027)
        assert skipped == ["HTTPS://X.EDU/programmes/a?utm=1"]
        assert fresh == [B]

    def test_another_year_does_not_count(self) -> None:
        self._seed("x", 2026, A)
        fresh, skipped = partition_existing_urls([A], "x", 2027)
        assert fresh == [A] and skipped == []

    def test_another_university_does_not_count(self) -> None:
        self._seed("y", 2027, A)
        fresh, skipped = partition_existing_urls([A], "x", 2027)
        assert fresh == [A] and skipped == []

    def test_unknown_university_keeps_everything(self) -> None:
        fresh, skipped = partition_existing_urls([A, B], "nobody", 2027)
        assert fresh == [A, B] and skipped == []

    def test_order_is_preserved(self) -> None:
        self._seed("x", 2027, B)
        fresh, _ = partition_existing_urls([C, B, A], "x", 2027)
        assert fresh == [C, A]


# ── crawl_url wiring ──────────────────────────────────────────────────


def _matched(*urls: str) -> DiscoveryResult:
    return DiscoveryResult(matched=True, link_texts={u: f"Name {u[-1]}" for u in urls},
                           names_total=len(urls), strategy_used="server×inline_degree",
                           stopped_reason="exhausted", pages_fetched=1)


@pytest.fixture(name="run_new_job_spy")
def _run_new_job_spy(monkeypatch):
    spy = AsyncMock(return_value={"imported_count": 0, "persisted_program_ids": []})
    monkeypatch.setattr(crawler_mod.IngestionPipeline, "run_new_job", spy, raising=True)
    monkeypatch.setattr(crawler_mod.browser_provider_service, "resolve_browser_inputs",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(crawler_mod, "_build_review_items", lambda *a, **k: [])
    return spy


class TestCrawlUrl(_DB):
    @pytest.mark.asyncio
    async def test_skip_existing_drops_stored_urls_before_the_pipeline(self, run_new_job_spy, monkeypatch) -> None:
        self._seed("x", 2027, A)
        monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", lambda url, rng: _matched(A, B, C))
        events: list = []

        result = await crawler_mod.crawl_url(
            "https://x.edu/p", "x", 2027, page_type_hint="index", crawl_all=True,
            skip_existing=True, progress_callback=lambda kind, payload: events.append((kind, payload)),
        )

        kwargs = run_new_job_spy.call_args.kwargs
        assert kwargs["selected_urls"] == [B, C]
        assert set(kwargs["selected_link_texts"]) == {B, C}
        assert result.skipped_existing == 1
        assert ("skip_existing", {"skipped": 1, "remaining": 2}) in events

    @pytest.mark.asyncio
    async def test_without_the_flag_nothing_is_dropped(self, run_new_job_spy, monkeypatch) -> None:
        self._seed("x", 2027, A)
        monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", lambda url, rng: _matched(A, B))
        result = await crawler_mod.crawl_url("https://x.edu/p", "x", 2027, page_type_hint="index", crawl_all=True)
        assert run_new_job_spy.call_args.kwargs["selected_urls"] == [A, B]
        assert result.skipped_existing == 0

    @pytest.mark.asyncio
    async def test_when_everything_is_stored_no_job_is_started(self, run_new_job_spy, monkeypatch) -> None:
        """An empty selected_urls would fall into the LLM index-analysis branch
        and re-fetch the index; the right answer is 'nothing left to do'."""
        self._seed("x", 2027, A, B)
        monkeypatch.setattr(crawler_mod, "discover_with_default_adapters", lambda url, rng: _matched(A, B))
        result = await crawler_mod.crawl_url("https://x.edu/p", "x", 2027, page_type_hint="index",
                                             crawl_all=True, skip_existing=True)
        run_new_job_spy.assert_not_called()
        assert result.imported_count == 0
        assert result.skipped_existing == 2
        assert result.ingestion_job_id is None

    @pytest.mark.asyncio
    async def test_caller_supplied_selected_urls_are_filtered_too(self, run_new_job_spy, monkeypatch) -> None:
        self._seed("x", 2027, A)
        monkeypatch.setattr(crawler_mod, "discover_with_default_adapters",
                            lambda url, rng: (_ for _ in ()).throw(AssertionError("no discovery")))
        await crawler_mod.crawl_url("https://x.edu/p", "x", 2027, page_type_hint="index",
                                    selected_urls=[A, B], skip_existing=True)
        assert run_new_job_spy.call_args.kwargs["selected_urls"] == [B]


def test_crawl_url_default_page_type_is_index_not_auto() -> None:
    import inspect
    assert inspect.signature(crawler_mod.crawl_url).parameters["page_type_hint"].default == "index"


# ── CLI flag ──────────────────────────────────────────────────────────


def test_cli_forwards_skip_existing(monkeypatch) -> None:
    from typer.testing import CliRunner
    from src.cmd import cli

    captured: dict = {}

    async def fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return crawler_mod.CrawlResult(imported_count=3, univ_slug="x", year=2027, skipped_existing=20)

    monkeypatch.setattr(cli, "_init_db", lambda verbose=False: None)
    monkeypatch.setattr(cli, "crawl_url", fake_crawl_url)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["crawl", "--name", "x", "--year", "2027", "--url", "https://x.edu/p",
                                  "--all", "--skip-existing"])
    assert res.exit_code == 0, res.output
    assert captured["skip_existing"] is True
    assert "20" in res.output and "already" in res.output.lower()

    captured.clear()
    res = runner.invoke(cli.app, ["crawl", "--name", "x", "--year", "2027", "--url", "https://x.edu/p", "--all"])
    assert res.exit_code == 0, res.output
    assert captured["skip_existing"] is False
