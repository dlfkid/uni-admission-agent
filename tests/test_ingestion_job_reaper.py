"""A job's status must reflect whether anything is still working on it.

The pipeline writes RUNNING at each stage start and a terminal status when the
process itself finishes or raises. Kill the process — Ctrl-C, pkill, a crash,
a machine going to sleep — and nothing writes anything: the row sits at
RUNNING forever with updated_at frozen at the last stage switch. Three such
rows accumulated in a week, and GET /ingestion/jobs showed a user crawls that
"were still running" days after their processes had died.

Three pieces fix that: a heartbeat on every processed item so updated_at
means "alive as of"; a reaper that turns RUNNING/PENDING jobs whose heartbeat
is stale into FAILED with a message naming the stage; and CANCELLED written
when the process is interrupted cleanly.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.models.ingestion import (
    IngestionJob, IngestionJobStatus, IngestionStage, IngestionTask, IngestionTaskState,
)
from src.models.scraper_models import CrawlPageResult
from src.services.ingestion_pipeline import IngestionPipeline
from src.services.job_reaper import STALE_AFTER, reap_stale_jobs
from src.storage.db_manager import DatabaseManager, _attach_sqlite_pragmas

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


class _Base:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        _attach_sqlite_pragmas(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine
        self.pipeline = IngestionPipeline(db_manager=self.dm)

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _job(self, uid: str, status: IngestionJobStatus, updated_at: datetime,
             stage: IngestionStage = IngestionStage.FETCH_RAW, with_task: bool = True) -> None:
        with Session(self.engine) as s:
            job = IngestionJob(job_uid=uid, univ_slug="x", academic_year=2027, status=status,
                               current_stage=stage, started_at=updated_at, updated_at=updated_at)
            s.add(job); s.flush()
            if with_task:
                s.add(IngestionTask(job_id=job.id, stage=stage,
                                    state=IngestionTaskState.RUNNING if status is IngestionJobStatus.RUNNING
                                    else IngestionTaskState.PENDING))
            s.commit()

    def _get(self, uid: str) -> IngestionJob:
        with Session(self.engine) as s:
            return s.exec(select(IngestionJob).where(IngestionJob.job_uid == uid)).one()

    def _task_states(self, uid: str) -> list[str]:
        with Session(self.engine) as s:
            job = s.exec(select(IngestionJob).where(IngestionJob.job_uid == uid)).one()
            return [t.state.value for t in s.exec(select(IngestionTask).where(IngestionTask.job_id == job.id))]


# ── the reaper ────────────────────────────────────────────────────────


class TestReaper(_Base):
    def test_a_stale_running_job_becomes_failed_and_says_why(self) -> None:
        self._job("stale", IngestionJobStatus.RUNNING, NOW - timedelta(hours=3))
        reaped = reap_stale_jobs(self.dm, now=NOW)
        job = self._get("stale")
        assert reaped == ["stale"]
        assert job.status is IngestionJobStatus.FAILED
        assert job.finished_at is not None
        assert "fetch_raw" in job.error_message
        assert "heartbeat" in job.error_message.lower()
        assert self._task_states("stale") == ["FAILED"]

    def test_a_fresh_running_job_is_left_alone(self) -> None:
        self._job("fresh", IngestionJobStatus.RUNNING, NOW - timedelta(minutes=2))
        assert reap_stale_jobs(self.dm, now=NOW) == []
        assert self._get("fresh").status is IngestionJobStatus.RUNNING
        assert self._task_states("fresh") == ["RUNNING"]

    def test_the_threshold_is_the_boundary(self) -> None:
        self._job("edge", IngestionJobStatus.RUNNING, NOW - STALE_AFTER - timedelta(seconds=1))
        assert reap_stale_jobs(self.dm, now=NOW) == ["edge"]

    def test_terminal_jobs_are_never_touched(self) -> None:
        old = NOW - timedelta(days=30)
        for uid, status in (("ok", IngestionJobStatus.SUCCEEDED), ("bad", IngestionJobStatus.FAILED),
                            ("poison", IngestionJobStatus.POISONED), ("cx", IngestionJobStatus.CANCELLED)):
            self._job(uid, status, old, with_task=False)
        assert reap_stale_jobs(self.dm, now=NOW) == []
        assert self._get("ok").status is IngestionJobStatus.SUCCEEDED

    def test_a_stale_pending_job_is_reaped_too(self) -> None:
        """PENDING is written by _create_job before the first stage starts; a
        process that died between the two leaves it there just the same."""
        self._job("pend", IngestionJobStatus.PENDING, NOW - timedelta(hours=1), with_task=False)
        assert reap_stale_jobs(self.dm, now=NOW) == ["pend"]
        assert self._get("pend").status is IngestionJobStatus.FAILED

    def test_reaping_is_idempotent(self) -> None:
        self._job("stale", IngestionJobStatus.RUNNING, NOW - timedelta(hours=3))
        reap_stale_jobs(self.dm, now=NOW)
        assert reap_stale_jobs(self.dm, now=NOW) == []


# ── the heartbeat ─────────────────────────────────────────────────────


def _fake_scraper_class(pages_by_url: dict):
    class FakeScraper:
        def __init__(self) -> None:
            self.router = MagicMock(); self._export_md = False; self._export_path = None
        def _reset_session_state(self) -> None: return
        async def _crawl_urls(self, urls):
            return [pages_by_url[u] for u in urls if u in pages_by_url]
    return FakeScraper


def _page(url: str) -> CrawlPageResult:
    return CrawlPageResult(url=url, markdown="# A MSc\nTuition Fee HK$1", char_count=20, links=[])


class TestHeartbeat(_Base):
    @pytest.mark.asyncio
    async def test_each_fetched_page_refreshes_the_job_heartbeat(self, monkeypatch) -> None:
        urls = [f"https://x.edu/{i}" for i in range(3)]
        monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper",
                            _fake_scraper_class({u: _page(u) for u in urls}))
        # Real clock here: the heartbeat writes datetime.now(), not a test clock.
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        self._job("beat", IngestionJobStatus.RUNNING, stale, with_task=False)
        self.pipeline._active_job_uid = "beat"
        beats: list = []
        real_touch = self.pipeline._touch_job
        monkeypatch.setattr(self.pipeline, "_touch_job", lambda: (beats.append(1), real_touch())[1])

        await self.pipeline._stage_fetch_raw(
            {"url": "https://x.edu/i", "page_type_hint": "index", "selected_urls": urls}
        )

        assert len(beats) == 3
        refreshed = self._get("beat").updated_at
        if refreshed.tzinfo is None:  # SQLite returns naive UTC
            refreshed = refreshed.replace(tzinfo=timezone.utc)
        assert refreshed > stale

    def test_extract_and_persist_beat_once_per_programme(self, monkeypatch) -> None:
        monkeypatch.setattr("src.services.ingestion_pipeline.LLMCleanerAgent", MagicMock)
        monkeypatch.setattr("src.services.ingestion_pipeline.extract_program_data_from_page",
                            MagicMock(return_value=({"name_en": "A MSc", "tuition_amount": 1}, None)))
        beats: list = []
        monkeypatch.setattr(self.pipeline, "_touch_job", lambda: beats.append(1))
        raw_pages = [
            {"url": f"https://x.edu/{i}", "markdown": "# A MSc\n" + "body " * 400, "char_count": 2000,
             "links": [], "status_code": 200, "html": "", "crawl_depth": 1, "from_browser": False,
             "selected_anchor_text": f"A{i} MSc"}
            for i in range(2)
        ]
        self.pipeline._stage_extract_structured(
            {"univ_slug": "x", "year": 2027, "page_type_hint": "index",
             "selected_urls": [p["url"] for p in raw_pages]},
            {"raw_pages": raw_pages},
        )
        assert len(beats) == 2

    def test_touch_without_an_active_job_is_a_no_op(self) -> None:
        self.pipeline._active_job_uid = None
        self.pipeline._touch_job()  # must not raise


# ── cancellation ──────────────────────────────────────────────────────


class TestCancellation(_Base):
    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [asyncio.CancelledError, KeyboardInterrupt])
    async def test_an_interrupted_run_leaves_the_job_cancelled(self, monkeypatch, exc) -> None:
        class Interrupting(_fake_scraper_class({})):
            async def _crawl_urls(self, urls):
                raise exc()
        monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", Interrupting)

        with pytest.raises(exc):
            await self.pipeline.run_new_job(
                url="https://x.edu/i", univ_slug="x", year=2027, page_type_hint="index",
                selected_urls=["https://x.edu/a"],
            )

        with Session(self.engine) as s:
            job = s.exec(select(IngestionJob)).one()
        assert job.status is IngestionJobStatus.CANCELLED
        assert job.finished_at is not None
        assert "cancel" in job.error_message.lower()
        assert "fetch_raw" in job.error_message
        # The stage's task must not be left RUNNING under a terminal job.
        assert self._task_states(job.job_uid) == ["FAILED"]


# ── wiring: every process start reaps ─────────────────────────────────


def test_init_db_reaps_before_any_command_touches_the_database() -> None:
    from src.cmd import cli
    with patch.object(cli, "DatabaseManager"), patch.object(cli, "bootstrap_subject_taxonomy"), \
         patch.object(cli, "get_migration_status", return_value={"pending": False, "current_revision": "b", "head_revision": "b"}), \
         patch.object(cli, "reap_stale_jobs", return_value=["dead1"]) as reaper:
        cli._init_db(verbose=False)
    reaper.assert_called_once()


def test_a_reap_failure_does_not_block_the_command(capsys) -> None:
    from src.cmd import cli
    with patch.object(cli, "DatabaseManager"), patch.object(cli, "bootstrap_subject_taxonomy"), \
         patch.object(cli, "get_migration_status", return_value={"pending": False, "current_revision": "b", "head_revision": "b"}), \
         patch.object(cli, "reap_stale_jobs", side_effect=RuntimeError("locked")):
        cli._init_db(verbose=False)  # does not raise
    assert "locked" in "".join(capsys.readouterr())


def test_server_startup_reaps() -> None:
    from fastapi.testclient import TestClient
    from src.api import server as server_mod
    with patch.object(server_mod, "DatabaseManager"), patch.object(server_mod, "bootstrap_subject_taxonomy"), \
         patch.object(server_mod, "reap_stale_jobs", return_value=[]) as reaper:
        with TestClient(server_mod.app):
            pass
    reaper.assert_called_once()


# ── SIGTERM behaves like Ctrl-C so the pipeline can write CANCELLED ───


def test_sigterm_is_turned_into_the_interrupt_asyncio_handles() -> None:
    """asyncio.run only cancels the running task on SIGINT. A plain SIGTERM
    (what `pkill`/`kill` send) would tear the process down with the job still
    RUNNING, so the CLI re-raises it as SIGINT before starting a crawl."""
    import signal
    from src.cmd import cli

    previous = signal.getsignal(signal.SIGTERM)
    try:
        cli._install_sigterm_as_interrupt()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


class TestSigtermEndsTheJobAsCancelled(_Base):
    """Process-level: a real SIGTERM to this process, delivered while the
    pipeline is inside a worker thread, must leave the job CANCELLED — even
    when SIGINT is ignored, which is how `cmd &` from a non-interactive shell
    starts a process. The first live kill test ran to completion because the
    handler re-raised SIGINT into that ignore."""

    @pytest.mark.parametrize("sigint_ignored", [False, True])
    def test_sigterm_during_a_worker_thread_stage(self, monkeypatch, sigint_ignored) -> None:
        import os
        import signal
        import threading
        import time
        from src.cmd import cli

        class SlowScraper(_fake_scraper_class({})):
            async def _crawl_urls(self, urls):
                await asyncio.to_thread(time.sleep, 5)   # like an LLM call in extract
                return []
        monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", SlowScraper)

        prev_term, prev_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
        try:
            if sigint_ignored:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
            cli._install_sigterm_as_interrupt()
            threading.Thread(target=lambda: (time.sleep(0.5), os.kill(os.getpid(), signal.SIGTERM)),
                             daemon=True).start()
            started = time.time()
            with pytest.raises(KeyboardInterrupt):
                asyncio.run(self.pipeline.run_new_job(
                    url="https://x.edu/i", univ_slug="x", year=2027, page_type_hint="index",
                    selected_urls=["https://x.edu/a"],
                ))
        finally:
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)

        with Session(self.engine) as s:
            job = s.exec(select(IngestionJob)).one()
        assert job.status is IngestionJobStatus.CANCELLED, job.status
        assert "cancel" in job.error_message.lower()
        assert time.time() - started < 15


def test_crawl_and_resume_commands_install_the_handler() -> None:
    """Both entry points that run the pipeline must arm it before asyncio.run."""
    import inspect
    from src.cmd import cli

    for cmd in (cli.crawl, cli.ingestion_resume_cmd):
        src = inspect.getsource(cmd)
        assert "_install_sigterm_as_interrupt()" in src, cmd.__name__
        assert src.index("_install_sigterm_as_interrupt()") < src.index("asyncio.run("), cmd.__name__


def test_reaping_an_uninitialised_database_is_a_no_op() -> None:
    """A fresh install (or a process pointed at an empty file) has no
    ingestion_job table yet; reaping must return [] rather than raise."""
    DatabaseManager._instance = None
    try:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        dm = DatabaseManager(); dm.engine = engine   # no create_all: no tables
        assert reap_stale_jobs(dm, now=NOW) == []
    finally:
        DatabaseManager._instance = None
