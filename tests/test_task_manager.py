"""Tests for src.api.task_manager — in-memory task registry."""

import time
from unittest.mock import patch

import pytest

from src.api.task_manager import (
    EVICTION_TTL_SECONDS,
    MAX_LOGS_PER_TASK,
    MAX_TASKS,
    TaskInfo,
    TaskManager,
    TaskState,
)
from typing import Generator


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """Ensure each test gets a fresh TaskManager singleton."""
    TaskManager._instance = None
    yield
    TaskManager._instance = None


# ── TaskInfo ──────────────────────────────────────────────────────────


def test_task_info_defaults() -> None:
    info = TaskInfo("abc")
    assert info.task_id == "abc"
    assert info.state == TaskState.PENDING
    assert info.logs == []
    assert info.tokens_used == 0
    assert info.completed_at is None
    assert info.created_at > 0


def test_task_info_to_dict() -> None:
    info = TaskInfo("x1")
    info.state = TaskState.DONE
    info.progress = "Complete"
    d = info.to_dict()
    assert d["task_id"] == "x1"
    assert d["state"] == "DONE"
    assert d["progress"] == "Complete"
    assert "logs" in d
    assert "tokens_used" in d


# ── create_task ───────────────────────────────────────────────────────


def test_create_task() -> None:
    mgr = TaskManager()
    tid = mgr.create_task(params={"url": "http://example.com"})
    assert tid is not None
    assert len(tid) == 12
    info = mgr.get_task(tid)
    assert info is not None
    assert info.state == TaskState.PENDING
    assert info.params == {"url": "http://example.com"}


def test_create_task_conflict() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    mgr.update_task(tid, state=TaskState.RUNNING)
    with pytest.raises(RuntimeError, match="already running"):
        mgr.create_task()


def test_create_task_after_completion() -> None:
    mgr = TaskManager()
    tid1 = mgr.create_task()
    mgr.update_task(tid1, state=TaskState.DONE)
    # Should succeed after previous task is done
    tid2 = mgr.create_task()
    assert tid2 != tid1


# ── update_task ───────────────────────────────────────────────────────


def test_update_task_state_transitions() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()

    mgr.update_task(tid, state=TaskState.RUNNING, progress="Working")
    info = mgr.get_task(tid)
    assert info is not None
    assert info.state == TaskState.RUNNING
    assert info.progress == "Working"

    mgr.update_task(tid, state=TaskState.DONE, result={"ok": True}, tokens_used=100)
    assert info.state == TaskState.DONE
    assert info.result == {"ok": True}
    assert info.tokens_used == 100
    assert info.completed_at is not None


def test_update_task_unknown_id() -> None:
    mgr = TaskManager()
    # Should not raise, just log warning
    mgr.update_task("nonexistent", state=TaskState.DONE)


def test_update_task_clears_active_on_failure() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    mgr.update_task(tid, state=TaskState.FAILED, error="boom")
    assert mgr.get_active_task() is None


# ── cancel_task ───────────────────────────────────────────────────────


def test_cancel_task_no_task_object() -> None:
    mgr = TaskManager()
    # No task object registered → returns False
    assert mgr.cancel_task("nonexistent") is False


# ── get_active_task ───────────────────────────────────────────────────


def test_get_active_task() -> None:
    mgr = TaskManager()
    assert mgr.get_active_task() is None

    tid = mgr.create_task()
    mgr.update_task(tid, state=TaskState.RUNNING)
    active = mgr.get_active_task()
    assert active is not None
    assert active.task_id == tid


def test_get_active_task_returns_none_after_done() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    mgr.update_task(tid, state=TaskState.DONE)
    assert mgr.get_active_task() is None


# ── add_log / log capping ────────────────────────────────────────────


def test_add_log() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    mgr.add_log(tid, "line1")
    mgr.add_log(tid, "line2")
    info = mgr.get_task(tid)
    assert info is not None
    assert info.logs == ["line1", "line2"]


def test_add_log_cap() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    for i in range(MAX_LOGS_PER_TASK + 50):
        mgr.add_log(tid, f"msg-{i}")
    info = mgr.get_task(tid)
    assert info is not None
    assert len(info.logs) == MAX_LOGS_PER_TASK
    # Oldest messages should have been dropped (FIFO)
    assert info.logs[0] == "msg-50"
    assert info.logs[-1] == f"msg-{MAX_LOGS_PER_TASK + 49}"


def test_add_log_nonexistent_task() -> None:
    mgr = TaskManager()
    mgr.add_log("no-such-id", "ignored")  # should not raise


# ── Eviction ──────────────────────────────────────────────────────────


def test_eviction_by_ttl() -> None:
    mgr = TaskManager()
    tid = mgr.create_task()
    mgr.update_task(tid, state=TaskState.DONE)

    info = mgr.get_task(tid)
    assert info is not None
    # Simulate task completed long ago
    info.completed_at = time.monotonic() - EVICTION_TTL_SECONDS - 10

    # Creating a new task triggers eviction
    tid2 = mgr.create_task()
    assert mgr.get_task(tid) is None  # evicted
    assert mgr.get_task(tid2) is not None  # new task still exists


def test_eviction_by_max_size() -> None:
    mgr = TaskManager()

    # Fill up the store beyond MAX_TASKS
    task_ids = []
    for _ in range(MAX_TASKS + 5):
        # Reset active to allow creation
        mgr._active_task_id = None
        tid = mgr.create_task()
        mgr.update_task(tid, state=TaskState.DONE)
        task_ids.append(tid)

    # Trigger eviction via new task creation
    mgr._active_task_id = None
    new_tid = mgr.create_task()

    # The new PENDING task cannot be evicted, so total ≤ MAX_TASKS + 1
    # But completed ones should have been pruned down to MAX_TASKS
    assert len(mgr._task_store) <= MAX_TASKS + 1
    assert mgr.get_task(new_tid) is not None


def test_eviction_preserves_running_tasks() -> None:
    mgr = TaskManager()

    # Create many completed tasks
    completed_ids = []
    for _ in range(MAX_TASKS + 5):
        mgr._active_task_id = None
        tid = mgr.create_task()
        mgr.update_task(tid, state=TaskState.DONE)
        completed_ids.append(tid)

    # Mark one as RUNNING (shouldn't be evicted)
    mgr._active_task_id = None
    running_tid = mgr.create_task()
    mgr.update_task(running_tid, state=TaskState.RUNNING)

    # Trigger eviction
    mgr._active_task_id = None
    mgr._evict_stale()

    # Running task must survive
    assert mgr.get_task(running_tid) is not None


# ── get_task ──────────────────────────────────────────────────────────


def test_get_task_not_found() -> None:
    mgr = TaskManager()
    assert mgr.get_task("does-not-exist") is None
