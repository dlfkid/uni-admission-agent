"""Turn ingestion jobs whose process died into a truthful terminal status.

The pipeline writes RUNNING at each stage start and SUCCEEDED / FAILED /
POISONED / CANCELLED only from inside the process. Kill that process —
Ctrl-C after the pipeline's own handler, ``pkill``, a crash, a laptop going
to sleep — and no code runs: the row stays RUNNING with ``updated_at`` frozen.
``GET /ingestion/jobs`` then shows a user crawls "still running" days after
they died, and nothing distinguishes them from a crawl alive in another
process.

The pipeline now refreshes ``updated_at`` on every processed item (a
heartbeat), so ``updated_at`` means "alive as of". This module runs at every
process start (CLI ``_init_db``, server lifespan) and marks RUNNING/PENDING
jobs whose heartbeat is older than :data:`STALE_AFTER` as FAILED with a
message naming the stage and the last heartbeat. FAILED rather than a new
status: the job did not finish, and adding an enum label would need a
Postgres type migration for no gain in meaning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import inspect
from sqlmodel import select

from src.models.ingestion import (
    IngestionJob,
    IngestionJobStatus,
    IngestionTask,
    IngestionTaskState,
)
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# The pipeline heartbeats once per fetched page / extracted programme /
# persisted programme. The slowest single step observed is one LLM extraction
# call at ~2.5 minutes, so ten minutes without a beat means the process is gone.
STALE_AFTER = timedelta(minutes=10)

_LIVE = (IngestionJobStatus.RUNNING, IngestionJobStatus.PENDING)
_LIVE_TASK = (IngestionTaskState.RUNNING, IngestionTaskState.PENDING, IngestionTaskState.RETRY_SCHEDULED)


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC, which is how they were written."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def reap_stale_jobs(
    db_manager: Optional[DatabaseManager] = None,
    *,
    now: Optional[datetime] = None,
    stale_after: timedelta = STALE_AFTER,
) -> list[str]:
    """Mark live-looking jobs with no heartbeat for *stale_after* as FAILED.

    Returns the ``job_uid`` of every job reaped, oldest first. Terminal jobs
    and jobs with a fresh heartbeat are never touched, so calling this at
    every process start is safe even while another process is mid-crawl.
    """
    db = db_manager or DatabaseManager()
    if not inspect(db.engine).has_table(IngestionJob.__tablename__):
        return []  # fresh install / uninitialised file: nothing to reap
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - stale_after
    reaped: list[str] = []

    with db.get_session() as session:
        jobs = session.exec(
            select(IngestionJob)
            .where(IngestionJob.status.in_(_LIVE))  # type: ignore[attr-defined]
            .order_by(IngestionJob.updated_at)
        ).all()
        for job in jobs:
            last_beat = _as_utc(job.updated_at or job.started_at or moment)
            if last_beat > cutoff:
                continue
            stage = job.current_stage.value if job.current_stage else "unknown"
            job.status = IngestionJobStatus.FAILED
            job.error_message = (
                f"aborted: the process ended during stage {stage} without finishing "
                f"(last heartbeat {last_beat.isoformat(timespec='seconds')}, "
                f"none for over {int(stale_after.total_seconds() // 60)} minutes). "
                "Resume it with: adm-agent ingestion-resume <job_uid>"
            )
            job.updated_at = moment
            job.finished_at = moment
            session.add(job)
            for task in session.exec(
                select(IngestionTask).where(IngestionTask.job_id == job.id)
            ).all():
                if task.state in _LIVE_TASK:
                    task.state = IngestionTaskState.FAILED
                    task.error_message = "aborted with its job: process ended without finishing"
                    session.add(task)
            reaped.append(job.job_uid)
        if reaped:
            session.commit()

    if reaped:
        logger.warning(
            "Reaped %d ingestion job(s) whose process died: %s", len(reaped), ", ".join(reaped)
        )
    return reaped
