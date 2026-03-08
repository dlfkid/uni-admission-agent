from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.api import server


@dataclass
class _FakeSummary:
    id: int
    faculty: str | None = None
    tuition_amount: float | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "faculty": self.faculty,
            "tuition_amount": self.tuition_amount,
        }


def test_program_patch_updates_single_record(monkeypatch) -> None:
    def _fake_patch_program_snapshot(program_id: int, patch_payload: dict[str, Any]):
        assert program_id == 101
        assert patch_payload == {"faculty": "Engineering"}
        return _FakeSummary(id=101, faculty="Engineering")

    monkeypatch.setattr("src.api.server.patch_program_snapshot", _fake_patch_program_snapshot)

    result = server.mcp_program_patch(
        program_id=101,
        patch={"faculty": "Engineering"},
    )

    assert result["updated"] is True
    assert result["program"]["id"] == 101
    assert result["program"]["faculty"] == "Engineering"


def test_program_patch_batch_updates_multiple_records(monkeypatch) -> None:
    def _fake_patch_program_snapshot(program_id: int, patch_payload: dict[str, Any]):
        return _FakeSummary(
            id=program_id,
            tuition_amount=float(patch_payload.get("tuition_amount") or 0.0),
        )

    monkeypatch.setattr("src.api.server.patch_program_snapshot", _fake_patch_program_snapshot)

    result = server.mcp_program_patch_batch(
        items=[
            {"program_id": 201, "patch": {"tuition_amount": 100000}},
            {"program_id": 202, "patch": {"tuition_amount": 110000}},
        ]
    )

    assert result["updated_count"] == 2
    assert result["failed_items"] == []
    assert "2" in result["summary"]


def test_program_patch_batch_returns_failed_items_without_abort(monkeypatch) -> None:
    def _fake_patch_program_snapshot(program_id: int, patch_payload: dict[str, Any]):
        _ = patch_payload
        if program_id == 302:
            raise ValueError("invalid field")
        if program_id == 303:
            return None
        return _FakeSummary(id=program_id, faculty="Science")

    monkeypatch.setattr("src.api.server.patch_program_snapshot", _fake_patch_program_snapshot)

    result = server.mcp_program_patch_batch(
        items=[
            {"program_id": 301, "patch": {"faculty": "Science"}},
            {"program_id": 302, "patch": {"faculty": "Unknown"}},
            {"program_id": 303, "patch": {"faculty": "Business"}},
        ]
    )

    assert result["updated_count"] == 1
    assert len(result["failed_items"]) == 2
    assert {row["program_id"] for row in result["failed_items"]} == {302, 303}
