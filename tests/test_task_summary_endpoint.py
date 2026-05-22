"""Tests for `GET /tasks/{task_id}/summary` — Chrome extension result summary.

The extension's existing logs-console appends one row per SSE event during
a crawl. When the crawl finishes (agent_done event), the extension calls
this endpoint to fetch a structured summary block that it appends to the
SAME console — no new popup or page.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.server import app as fastapi_app
from src.models.extraction_audit import ExtractionAudit
from src.models.quarantine import ProgramQuarantine


def _make_audit(**kw) -> ExtractionAudit:
    base = {
        "id": 42,
        "university_slug": "leeds",
        "academic_year": 2026,
        "index_url": "https://courses.leeds.ac.uk/course-search/masters-courses?page=4",
        "raw_link_count": 5,
        "llm_filtered_count": 5,
        "candidate_count": 5,
        "extracted_count": 75,
        "quarantined_count": 0,
        "recovered_count": 0,
        "pagination_stop_reason": "exhausted",
        "created_at": datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc),
    }
    base.update(kw)
    return ExtractionAudit(**base)


def _make_q(reason: str, name: str = "X") -> ProgramQuarantine:
    return ProgramQuarantine(
        id=hash(name) & 0xFFFF,
        university_slug="leeds",
        academic_year=2026,
        source_url=f"https://e.edu/{name}",
        extracted_name=name,
        payload="{}",
        quarantine_reason=reason,
        quarantine_signals="{}",
        created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )


def _fake_task(univ_slug: str | None, year: int | None) -> MagicMock:
    task = MagicMock()
    task.params = {}
    if univ_slug is not None:
        task.params["univ_slug"] = univ_slug
    if year is not None:
        task.params["year"] = year
    return task


class TestTaskSummaryEndpoint:
    def test_returns_summary_when_task_has_completed_crawl(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [_make_audit()]
        fake_db.list_quarantine.return_value = [
            _make_q("empty_shell", "a"),
            _make_q("empty_shell", "b"),
            _make_q("noise_name", "c"),
        ]
        fake_tm = MagicMock()
        fake_tm.get_task.return_value = _fake_task("leeds", 2026)

        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)
        monkeypatch.setattr("src.api.server.task_manager", fake_tm)

        client = TestClient(fastapi_app)
        resp = client.get("/tasks/abc123/summary")

        assert resp.status_code == 200
        body = resp.json()
        # Funnel data
        assert body["university_slug"] == "leeds"
        assert body["academic_year"] == 2026
        assert body["raw_link_count"] == 5
        assert body["extracted_count"] == 75
        assert body["quarantined_count"] == 0
        assert body["stop_reason"] == "exhausted"
        # Quarantine breakdown
        assert body["quarantine_breakdown"] == {
            "empty_shell": 2,
            "noise_name": 1,
        }
        # Anomalous flag
        assert body["stop_reason_anomalous"] is False
        fake_db.list_extraction_audit.assert_called_once_with(
            university_slug="leeds", year=2026, limit=1
        )

    def test_marks_stop_reason_anomalous_when_drift_or_quality_fail(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [
            _make_audit(pagination_stop_reason="url_drift")
        ]
        fake_db.list_quarantine.return_value = []
        fake_tm = MagicMock()
        fake_tm.get_task.return_value = _fake_task("leeds", 2026)
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)
        monkeypatch.setattr("src.api.server.task_manager", fake_tm)

        client = TestClient(fastapi_app)
        resp = client.get("/tasks/x/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["stop_reason"] == "url_drift"
        assert body["stop_reason_anomalous"] is True

    def test_returns_404_when_task_unknown(self, monkeypatch) -> None:
        fake_tm = MagicMock()
        fake_tm.get_task.return_value = None
        monkeypatch.setattr("src.api.server.task_manager", fake_tm)

        client = TestClient(fastapi_app)
        resp = client.get("/tasks/nonexistent/summary")
        assert resp.status_code == 404

    def test_returns_no_data_when_task_has_no_univ_slug(self, monkeypatch) -> None:
        """Some tasks don't have a univ_slug (e.g., agent chat). Return a
        well-formed empty-state response so the extension can show
        something instead of erroring out."""
        fake_tm = MagicMock()
        fake_tm.get_task.return_value = _fake_task(univ_slug=None, year=None)
        monkeypatch.setattr("src.api.server.task_manager", fake_tm)

        client = TestClient(fastapi_app)
        resp = client.get("/tasks/x/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("available") is False

    def test_returns_no_audit_when_crawl_left_no_record(self, monkeypatch) -> None:
        """If somehow the task ended without writing an audit row (e.g.,
        very early failure), still return 200 with an empty-state shape."""
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = []
        fake_db.list_quarantine.return_value = []
        fake_tm = MagicMock()
        fake_tm.get_task.return_value = _fake_task("hku", 2026)
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)
        monkeypatch.setattr("src.api.server.task_manager", fake_tm)

        client = TestClient(fastapi_app)
        resp = client.get("/tasks/x/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("available") is False
