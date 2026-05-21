"""Tests for `adm-agent audit list` CLI and `GET /audit` REST surfaces."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from src.api.server import app as fastapi_app
from src.cmd.cli import app as cli_app
from src.models.extraction_audit import ExtractionAudit


def _entry(
    *,
    id_: int = 1,
    university_slug: str = "hku",
    year: int = 2026,
    index_url: str = "https://www.hku.hk/programs",
    raw: int = 87,
    filtered: int = 23,
    candidates: int = 22,
    extracted: int = 11,
    quarantined: int = 6,
) -> ExtractionAudit:
    return ExtractionAudit(
        id=id_,
        university_slug=university_slug,
        academic_year=year,
        index_url=index_url,
        raw_link_count=raw,
        llm_filtered_count=filtered,
        candidate_count=candidates,
        extracted_count=extracted,
        quarantined_count=quarantined,
        created_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
    )


class TestAuditListCli:
    def test_list_renders_funnel(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [
            _entry(id_=1, extracted=11, quarantined=6),
            _entry(id_=2, extracted=15, quarantined=2),
        ]
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["audit", "list", "--university", "hku"])

        assert result.exit_code == 0
        # Funnel numbers must all appear in the output.
        assert "87" in result.stdout  # raw
        assert "23" in result.stdout  # filtered
        assert "22" in result.stdout  # candidates
        assert "11" in result.stdout  # extracted
        assert "6" in result.stdout   # quarantined
        fake_db.list_extraction_audit.assert_called_once_with(
            university_slug="hku", year=None, limit=20
        )

    def test_list_with_year_and_limit(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["audit", "list", "--university", "hku", "--year", "2026", "--limit", "5"]
        )

        assert result.exit_code == 0
        fake_db.list_extraction_audit.assert_called_once_with(
            university_slug="hku", year=2026, limit=5
        )

    def test_list_empty(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["audit", "list"])
        assert result.exit_code == 0
        assert "no audit" in result.stdout.lower()


class TestAuditRestEndpoint:
    def test_get_returns_funnel_json(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [_entry()]
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.get("/audit?university=hku&year=2026")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["raw_link_count"] == 87
        assert body[0]["llm_filtered_count"] == 23
        assert body[0]["candidate_count"] == 22
        assert body[0]["extracted_count"] == 11
        assert body[0]["quarantined_count"] == 6

    def test_get_with_limit(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = []
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.get("/audit?limit=5")
        assert resp.status_code == 200
        fake_db.list_extraction_audit.assert_called_once_with(
            university_slug=None, year=None, limit=5
        )


def _fake_link(*, audit_id: int, url: str, anchor: str, stage: str):
    from src.models.extraction_audit import ExtractionAuditLink
    return ExtractionAuditLink(
        id=hash(url) & 0xFFFFFF,
        audit_id=audit_id,
        url=url,
        anchor_text=anchor,
        stage_dropped=stage,
    )


class TestAuditDrillCli:
    def test_drill_shows_dropped_links_grouped_by_stage(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_audit_dropped_links.return_value = [
            _fake_link(audit_id=1, url="https://hku.hk/about", anchor="About",
                       stage="llm_filter"),
            _fake_link(audit_id=1, url="https://hku.hk/news", anchor="News",
                       stage="llm_filter"),
            _fake_link(audit_id=1, url="https://hku.hk/events", anchor="Events",
                       stage="taxonomy_filter"),
        ]
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["audit", "drill", "1"])

        assert result.exit_code == 0
        assert "llm_filter" in result.stdout
        assert "taxonomy_filter" in result.stdout
        assert "https://hku.hk/about" in result.stdout
        assert "https://hku.hk/news" in result.stdout
        assert "https://hku.hk/events" in result.stdout
        fake_db.list_audit_dropped_links.assert_called_once_with(audit_id=1)

    def test_drill_empty_message(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_audit_dropped_links.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["audit", "drill", "1"])
        assert result.exit_code == 0
        assert "no dropped" in result.stdout.lower()


class TestAuditDrillRestEndpoint:
    def test_get_returns_dropped_grouped(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_audit_dropped_links.return_value = [
            _fake_link(audit_id=1, url="https://hku.hk/about", anchor="About",
                       stage="llm_filter"),
            _fake_link(audit_id=1, url="https://hku.hk/events", anchor="Events",
                       stage="taxonomy_filter"),
        ]
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.get("/audit/1/dropped")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert "llm_filter" in body
        assert "taxonomy_filter" in body
        assert len(body["llm_filter"]) == 1
        assert body["llm_filter"][0]["url"] == "https://hku.hk/about"
        assert body["llm_filter"][0]["anchor_text"] == "About"
        assert len(body["taxonomy_filter"]) == 1
        fake_db.list_audit_dropped_links.assert_called_once_with(audit_id=1)
