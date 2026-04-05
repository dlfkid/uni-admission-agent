"""Tests for POST /agent/chat endpoint and AgentChatRequest schema."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.schemas import AgentChatRequest, AgentChatResponse
from src.api.server import app
from src.api.task_manager import TaskManager


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_agent_chat_request_requires_message():
    req = AgentChatRequest(message="Hello agent")
    assert req.message == "Hello agent"
    assert req.context is None


def test_agent_chat_request_accepts_context():
    req = AgentChatRequest(message="Hi", context={"foo": "bar"})
    assert req.context == {"foo": "bar"}


def test_agent_chat_response_has_task_id():
    resp = AgentChatResponse(task_id="abc-123")
    assert resp.task_id == "abc-123"
    assert resp.message == "Chat task submitted"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_chat_endpoint_disabled_returns_409(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ENABLED", raising=False)
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        resp = client.post("/agent/chat", json={"message": "Hello"})

    assert resp.status_code == 409


def test_chat_endpoint_returns_task_id(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    async def _fake_chat(**_kwargs):
        sink = _kwargs.get("event_sink")
        if callable(sink):
            sink({"type": "agent_started"})
            sink({"type": "summary_started"})
            sink({"type": "summary_delta", "delta": "Hello!"})
            sink({"type": "summary_finished"})
        return {
            "status": "done",
            "runtime_used": "pydanticai",
            "trace": [],
            "output": {"task": "chat", "agent_response": "Hello!"},
        }

    monkeypatch.setattr("src.api.server.run_agent_chat", _fake_chat)

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        resp = client.post("/agent/chat", json={"message": "Hello agent"})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        task_id = data["task_id"]
        assert task_id

        # Poll until done
        final = {}
        for _ in range(60):
            final = client.get(f"/tasks/{task_id}").json()
            if final.get("state") in {"DONE", "FAILED"}:
                break
            time.sleep(0.02)

    assert final.get("state") == "DONE"
    # Events should have been captured
    assert any(e.get("type") == "agent_started" for e in final.get("events", []))
    assert any(e.get("type") == "summary_delta" for e in final.get("events", []))


def test_chat_endpoint_missing_message_returns_422(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())

    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        resp = client.post("/agent/chat", json={})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# pydanticai_runtime chat task message building
# ---------------------------------------------------------------------------


def test_build_user_message_chat_returns_message_directly():
    from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
    from src.agent_runtime.base import AgentRequest

    runtime = PydanticAIRuntime()
    request = AgentRequest(
        task="chat",
        payload={"message": "What universities are in Hong Kong?"},
    )
    msg = runtime._build_user_message(request)
    assert msg == "What universities are in Hong Kong?"


def test_build_user_message_chat_empty_message():
    from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
    from src.agent_runtime.base import AgentRequest

    runtime = PydanticAIRuntime()
    request = AgentRequest(task="chat", payload={})
    msg = runtime._build_user_message(request)
    assert msg == ""
