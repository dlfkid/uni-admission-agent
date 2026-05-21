"""Tests for CLI `quarantine list` and REST `GET /quarantine` surfaces."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from src.api.server import app as fastapi_app
from src.cmd.cli import app as cli_app
from src.models.quarantine import ProgramQuarantine


def _fake_entry(
    *,
    id_: int = 1,
    university_slug: str = "hku",
    year: int = 2026,
    name: str = "MSc Finance",
    reason: str = "empty_shell",
) -> ProgramQuarantine:
    return ProgramQuarantine(
        id=id_,
        university_slug=university_slug,
        academic_year=year,
        source_url=f"https://example.edu/p{id_}",
        extracted_name=name,
        payload="{}",
        quarantine_reason=reason,
        quarantine_signals='{"deadline_count":0}',
        created_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
    )


# ── CLI ──────────────────────────────────────────────────────────────


class TestQuarantineListCli:
    def test_list_renders_entries(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_quarantine.return_value = [
            _fake_entry(id_=1, name="MSc A", reason="empty_shell"),
            _fake_entry(id_=2, name="Course Search", reason="noise_name"),
        ]
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["quarantine", "list", "--university", "hku"])

        assert result.exit_code == 0
        assert "MSc A" in result.stdout
        assert "Course Search" in result.stdout
        assert "empty_shell" in result.stdout
        assert "noise_name" in result.stdout
        fake_db.list_quarantine.assert_called_once_with(
            university_slug="hku", year=None
        )

    def test_list_with_year_filter(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["quarantine", "list", "--university", "hku", "--year", "2026"]
        )

        assert result.exit_code == 0
        fake_db.list_quarantine.assert_called_once_with(
            university_slug="hku", year=2026
        )

    def test_list_empty_message(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["quarantine", "list"])
        assert result.exit_code == 0
        assert "no quarantine entries" in result.stdout.lower()


# ── REST ─────────────────────────────────────────────────────────────


class TestQuarantineRestEndpoint:
    def test_get_returns_entries_as_json(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_quarantine.return_value = [
            _fake_entry(id_=1, name="MSc A", reason="empty_shell"),
        ]
        monkeypatch.setattr(
            "src.api.server.get_db_manager", lambda: fake_db
        )

        client = TestClient(fastapi_app)
        resp = client.get("/quarantine?university=hku&year=2026")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["extracted_name"] == "MSc A"
        assert body[0]["quarantine_reason"] == "empty_shell"
        assert body[0]["university_slug"] == "hku"
        fake_db.list_quarantine.assert_called_once_with(
            university_slug="hku", year=2026
        )

    def test_get_without_filters(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr(
            "src.api.server.get_db_manager", lambda: fake_db
        )

        client = TestClient(fastapi_app)
        resp = client.get("/quarantine")
        assert resp.status_code == 200
        fake_db.list_quarantine.assert_called_once_with(
            university_slug=None, year=None
        )


class TestQuarantineClearCli:
    def test_clear_by_university(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_quarantine.return_value = 5
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["quarantine", "clear", "--university", "hku"]
        )

        assert result.exit_code == 0
        assert "5" in result.stdout
        fake_db.clear_quarantine.assert_called_once_with(
            university_slug="hku", reason=None
        )

    def test_clear_with_reason(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_quarantine.return_value = 2
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["quarantine", "clear", "--university", "hku", "--reason", "empty_shell"],
        )
        assert result.exit_code == 0
        kwargs = fake_db.clear_quarantine.call_args.kwargs
        assert kwargs["university_slug"] == "hku"
        assert kwargs["reason"].value == "empty_shell"

    def test_clear_requires_university(self, monkeypatch) -> None:
        """No --university → refuse; we don't want users nuking the whole
        table by accident."""
        fake_db = MagicMock()
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["quarantine", "clear"])
        # typer should reject the call without --university.
        assert result.exit_code != 0
        fake_db.clear_quarantine.assert_not_called()


class TestQuarantineRestDelete:
    def test_delete_by_university(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_quarantine.return_value = 3
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/quarantine?university=hku")

        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 3
        fake_db.clear_quarantine.assert_called_once_with(
            university_slug="hku", reason=None
        )

    def test_delete_with_reason(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_quarantine.return_value = 1
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/quarantine?university=hku&reason=empty_shell")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_delete_without_university_is_rejected(self, monkeypatch) -> None:
        fake_db = MagicMock()
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/quarantine")
        assert resp.status_code == 400
        fake_db.clear_quarantine.assert_not_called()

    def test_delete_unknown_reason_is_rejected(self, monkeypatch) -> None:
        fake_db = MagicMock()
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/quarantine?university=hku&reason=not_a_real_reason")
        assert resp.status_code == 400
