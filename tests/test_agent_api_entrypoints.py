import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app
from src.api.task_manager import TaskManager


def test_agent_run_endpoint_disabled_returns_409(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ENABLED", raising=False)
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.post(
            "/agent/run",
            json={
                "url": "https://example.edu/list",
                "univ_slug": "uom",
                "year": 2026,
            },
        )

    assert response.status_code == 409


def test_agent_run_endpoint_enabled_returns_task_id(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.post(
            "/agent/run",
            json={
                "url": "https://example.edu/list",
                "univ_slug": "uom",
                "year": 2026,
            },
        )

        assert response.status_code == 200
        task_id = response.json().get("task_id")
        assert task_id

        final_state = ""
        for _ in range(60):
            task = client.get(f"/tasks/{task_id}").json()
            final_state = task.get("state")
            if final_state in {"DONE", "FAILED"}:
                break
            time.sleep(0.02)

    assert final_state == "DONE"
