from __future__ import annotations

import importlib

import pytest


def _reload_server_with_agent_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_ENABLED", "true")

    def _raise_unavailable():
        raise RuntimeError("internal llm unavailable")

    monkeypatch.setattr("src.agents.factory.create_router", _raise_unavailable)

    import src.api.server as server_module

    return importlib.reload(server_module)


def _fresh_task_manager(server_module):
    manager = server_module.TaskManager()
    manager._task_store = {}  # pylint: disable=protected-access
    manager._active_task_id = None  # pylint: disable=protected-access
    manager._task_objects = {}  # pylint: disable=protected-access
    return manager


def _seed_agent_task(server_module, manager) -> str:
    task_id = manager.create_task(params={"mode": "agent"})
    manager.update_task(
        task_id,
        state=server_module.TaskState.DONE,
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


@pytest.mark.asyncio
async def test_mcp_agent_review_confirm_applies_selected_indices(monkeypatch) -> None:
    server_module = _reload_server_with_agent_enabled(monkeypatch)

    manager = _fresh_task_manager(server_module)
    monkeypatch.setattr(server_module, "task_manager", manager)
    task_id = _seed_agent_task(server_module, manager)

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

    monkeypatch.setattr(server_module, "run_agent_review_confirmation", _fake_confirm)

    result = await server_module.mcp_agent_review_confirm(
        task_id=task_id,
        selection_text="continue 3,1",
    )

    assert result["task_id"] == task_id
    assert result["selected_indices"] == [1, 3]
    assert result["selected_count"] == 2
    assert result["discarded_count"] == 1
    assert result["invalid_indices"] == []

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
