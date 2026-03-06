from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app


@dataclass
class _FakeProgram:
    id: int
    name_en: str
    name_zh: str | None
    academic_year: int
    faculty: str | None
    program_group_code: str | None
    tuition_amount: float | None
    currency: str | None
    study_options: list[dict[str, Any]]
    deadlines: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    requirement_version: dict[str, Any] | None
    source_url: str | None
    program_catalog_id: int
    university_id: int

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name_en": self.name_en,
            "name_zh": self.name_zh,
            "academic_year": self.academic_year,
            "faculty": self.faculty,
            "program_group_code": self.program_group_code,
            "tuition_amount": self.tuition_amount,
            "currency": self.currency,
            "study_options": self.study_options,
            "deadlines": self.deadlines,
            "requirements": self.requirements,
            "requirement_version": self.requirement_version,
            "source_url": self.source_url,
        }


class _FakeDbManager:
    def __init__(self) -> None:
        self._programs: dict[int, _FakeProgram] = {
            1001: _FakeProgram(
                id=1001,
                name_en="Computer Science",
                name_zh="计算机科学",
                academic_year=2026,
                faculty="Science",
                program_group_code="cs-bsc",
                tuition_amount=120000.0,
                currency="HKD",
                study_options=[{"mode": "FullTime", "duration_months": 48}],
                deadlines=[{"round": 1, "description": "Main", "cutoff_date": "2026-01-15"}],
                requirements=[{"category": "language", "requirement_text": "IELTS 6.5"}],
                requirement_version={"version_no": 1},
                source_url="https://example.edu/cs",
                program_catalog_id=501,
                university_id=301,
            ),
            1002: _FakeProgram(
                id=1002,
                name_en="Computer Science",
                name_zh="计算机科学",
                academic_year=2025,
                faculty="Science",
                program_group_code="cs-bsc",
                tuition_amount=115000.0,
                currency="HKD",
                study_options=[{"mode": "FullTime", "duration_months": 48}],
                deadlines=[{"round": 1, "description": "Main", "cutoff_date": "2025-01-15"}],
                requirements=[{"category": "language", "requirement_text": "IELTS 6.5"}],
                requirement_version={"version_no": 1},
                source_url="https://example.edu/cs",
                program_catalog_id=501,
                university_id=301,
            ),
        }

    def init_db(self) -> None:
        return

    def delete_program_snapshot(self, program_id: int) -> bool:
        removed = self._programs.pop(program_id, None)
        return removed is not None

    def patch_program_snapshot(self, program_id: int, patch_payload: dict[str, Any]) -> _FakeProgram | None:
        program = self._programs.get(program_id)
        if program is None:
            return None
        for key, value in patch_payload.items():
            setattr(program, key, value)
        return program


def test_delete_program_removes_single_snapshot() -> None:
    fake_db = _FakeDbManager()
    with (
        patch("src.api.server.DatabaseManager", return_value=fake_db),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.delete("/programs/1001")

    assert response.status_code == 200
    assert 1001 not in fake_db._programs
    assert 1002 in fake_db._programs


def test_patch_program_updates_only_changed_fields() -> None:
    fake_db = _FakeDbManager()
    with (
        patch("src.api.server.DatabaseManager", return_value=fake_db),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        response = client.patch("/programs/1001", json={"faculty": "Engineering"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == 1001
    assert payload["faculty"] == "Engineering"
    assert payload["academic_year"] == 2026
    assert payload["name_en"] == "Computer Science"
    assert payload["source_url"] == "https://example.edu/cs"
