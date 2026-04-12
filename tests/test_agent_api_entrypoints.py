import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app
from src.api.task_manager import TaskManager


def test_agent_run_endpoint_explicitly_disabled_returns_409(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "false")
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


def test_agent_run_endpoint_enabled_by_default_returns_task_id(monkeypatch) -> None:
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
                "runtime": "legacy",
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


def test_agent_run_task_records_events(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    async def _fake_run_agent_crawl(**kwargs):
        event_sink = kwargs.get("event_sink")
        if callable(event_sink):
            event_sink({"type": "agent_started", "seq": 1})
            event_sink({"type": "llm_call_started", "seq": 2})
        return {
            "status": "done",
            "runtime_used": "pydanticai",
            "trace": [],
            "output": {"agent_response": "done", "parsed_programs": []},
        }

    monkeypatch.setattr("src.api.server.run_agent_crawl", _fake_run_agent_crawl)

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
                "runtime": "pydanticai",
            },
        )

        assert response.status_code == 200
        task_id = response.json()["task_id"]

        final_status = {}
        for _ in range(60):
            final_status = client.get(f"/tasks/{task_id}").json()
            if final_status.get("state") in {"DONE", "FAILED"}:
                break
            time.sleep(0.02)

    assert final_status["state"] == "DONE"
    assert final_status["progress_meta"]["event"] in {
        "agent_task_started",
        "agent_task_succeeded",
    }
    assert final_status["events"]
