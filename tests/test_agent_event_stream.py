"""Tests for agent task event storage used by streaming progress."""

from typing import Generator

import pytest

from src.api.task_manager import TaskManager


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """Ensure each test gets a fresh TaskManager singleton."""
    TaskManager._instance = None
    yield
    TaskManager._instance = None


def test_task_manager_stores_agent_events_in_order() -> None:
    manager = TaskManager()
    task_id = manager.create_task(params={"mode": "agent"})

    manager.add_event(task_id, {"type": "agent_started", "seq": 1})
    manager.add_event(task_id, {"type": "llm_call_started", "seq": 2})

    task = manager.get_task(task_id)
    assert task is not None
    assert [event["type"] for event in task.events] == [
        "agent_started",
        "llm_call_started",
    ]
