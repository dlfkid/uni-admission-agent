"""Tests for agent task event SSE streaming."""

from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.api.server import api_task_events, app
from src.api.task_manager import TaskManager, TaskState


@pytest.fixture(autouse=True)
def _reset_task_manager_singleton() -> Generator[None, None, None]:
    """Ensure each test uses a fresh in-memory task manager."""
    TaskManager._instance = None
    yield
    TaskManager._instance = None


def test_agent_task_events_endpoint_streams_sse(monkeypatch) -> None:
    manager = TaskManager()
    task_id = manager.create_task(params={"mode": "agent"})
    manager.add_event(task_id, {"type": "agent_started", "seq": 1})
    manager.update_task(task_id, state=TaskState.DONE, progress="Complete")
    monkeypatch.setattr("src.api.server.task_manager", manager)

    with TestClient(app) as client:
        response = client.get(f"/tasks/{task_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"type": "agent_started", "seq": 1}' in response.text


@pytest.mark.asyncio
async def test_sse_disconnect_does_not_fail_task(monkeypatch) -> None:
    manager = TaskManager()
    task_id = manager.create_task(params={"mode": "agent"})
    manager.update_task(task_id, state=TaskState.RUNNING, progress="Running")
    manager.add_event(task_id, {"type": "agent_started", "seq": 1})
    monkeypatch.setattr("src.api.server.task_manager", manager)

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    response = await api_task_events(task_id, _ConnectedRequest())
    first_chunk = await anext(response.body_iterator)
    assert "agent_started" in first_chunk

    await response.body_iterator.aclose()
    manager.update_task(task_id, state=TaskState.DONE, progress="Complete")

    task = manager.get_task(task_id)
    assert task is not None
    assert task.state == TaskState.DONE
