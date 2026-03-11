from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app
from src.api.task_manager import TaskManager, TaskState


def _fresh_task_manager() -> TaskManager:
    manager = TaskManager()
    manager._task_store = {}  # pylint: disable=protected-access
    manager._active_task_id = None  # pylint: disable=protected-access
    manager._task_objects = {}  # pylint: disable=protected-access
    return manager


def _seed_agent_task(manager: TaskManager) -> str:
    task_id = manager.create_task(params={"mode": "agent"})
    manager.update_task(
        task_id,
        state=TaskState.DONE,
        result={
            "status": "wait_user_selection",
            "runtime_used": "pydanticai",
            "trace": [],
            "output": {
                "request_payload": {
                    "url": "https://x/index",
                    "univ_slug": "uom",
                    "year": 2026,
                },
                "onhold_items": [
                    {
                        "index": 1,
                        "item_id": "hold-1",
                        "source_url": "https://x/p1",
                        "program_name_candidate": "Program One",
                        "confidence": 0.82,
                        "hold_reason": "low_confidence",
                    },
                    {
                        "index": 2,
                        "item_id": "hold-2",
                        "source_url": "https://x/p2",
                        "program_name_candidate": "Program Two",
                        "confidence": 0.77,
                        "hold_reason": "low_confidence",
                    },
                    {
                        "index": 3,
                        "item_id": "hold-3",
                        "source_url": "https://x/p3",
                        "program_name_candidate": "Program Three",
                        "confidence": 0.66,
                        "hold_reason": "low_confidence",
                    },
                ],
            },
        },
    )
    return task_id


def test_agent_review_confirm_rejects_invalid_indices(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    manager = _fresh_task_manager()
    monkeypatch.setattr("src.api.server.task_manager", manager)

    task_id = _seed_agent_task(manager)

    async def _unexpected_call(**_kwargs):
        raise AssertionError("run_agent_review_confirmation should not be called")

    monkeypatch.setattr("src.api.server.run_agent_review_confirmation", _unexpected_call)

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.post(
            "/agent/review/confirm",
            json={"task_id": task_id, "selected_indices": [6]},
        )

    assert response.status_code == 400
    payload = response.json().get("detail")
    assert payload["error"] == "invalid_indices"
    assert payload["invalid_indices"] == [6]


def test_agent_review_confirm_applies_selected_and_discards_rest(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    manager = _fresh_task_manager()
    monkeypatch.setattr("src.api.server.task_manager", manager)

    task_id = _seed_agent_task(manager)
    captured: dict = {}

    async def _fake_confirm(**kwargs):
        captured.update(kwargs)
        return {
            "total_onhold": 3,
            "selected_count": 2,
            "discarded_count": 1,
            "invalid_indices": [],
            "applied_items": [
                {"index": 1, "source_url": "https://x/p1"},
                {"index": 3, "source_url": "https://x/p3"},
            ],
            "discarded_items": [{"index": 2, "source_url": "https://x/p2"}],
            "applied_result": {
                "imported_count": 2,
                "review_token": "review-token-confirmed",
                "review_items": [{"program_id": 1001}, {"program_id": 1002}],
            },
        }

    monkeypatch.setattr("src.api.server.run_agent_review_confirmation", _fake_confirm)

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.post(
            "/agent/review/confirm",
            json={"task_id": task_id, "selection_text": "continue 3,1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["selected_indices"] == [1, 3]
    assert payload["invalid_indices"] == []
    assert payload["invalid_tokens"] == []
    assert payload["selected_count"] == 2
    assert payload["discarded_count"] == 1

    assert captured["selected_indices"] == [1, 3]
    assert captured["task_payload"] == {
        "url": "https://x/index",
        "univ_slug": "uom",
        "year": 2026,
    }

    updated = manager.get_task(task_id)
    assert updated is not None
    assert updated.result is not None
    assert updated.result["status"] == "done"
    assert updated.result["output"]["onhold_confirmation"]["selected_count"] == 2
    assert updated.result["output"]["review_token"] == "review-token-confirmed"
    assert len(updated.result["output"]["review_items"]) == 2
