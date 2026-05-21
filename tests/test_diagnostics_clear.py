"""Tests for the unified `diagnostics clear` CLI + REST endpoint.

These surfaces wipe both quarantine and audit (+ audit_link) records for
one university in one call — the typical "give me a clean slate for hku"
use case.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from src.api.server import app as fastapi_app
from src.cmd.cli import app as cli_app


class TestDiagnosticsClearCli:
    def test_clear_by_university(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_diagnostics.return_value = {
            "quarantine_deleted": 5,
            "audits_deleted": 3,
            "links_deleted": 27,
        }
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["diagnostics", "clear", "--university", "hku"]
        )

        assert result.exit_code == 0
        # Counts surface in human-readable form.
        assert "5" in result.stdout  # quarantine
        assert "3" in result.stdout  # audits
        assert "27" in result.stdout  # links
        fake_db.clear_diagnostics.assert_called_once_with(
            university_slug="hku", year=None
        )

    def test_clear_with_year(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_diagnostics.return_value = {
            "quarantine_deleted": 1, "audits_deleted": 1, "links_deleted": 4,
        }
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["diagnostics", "clear", "--university", "hku", "--year", "2026"],
        )
        assert result.exit_code == 0
        fake_db.clear_diagnostics.assert_called_once_with(
            university_slug="hku", year=2026
        )

    def test_clear_requires_university(self, monkeypatch) -> None:
        fake_db = MagicMock()
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["diagnostics", "clear"])
        # Typer should reject without --university.
        assert result.exit_code != 0
        fake_db.clear_diagnostics.assert_not_called()

    def test_clear_zero_counts_shows_friendly_message(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_diagnostics.return_value = {
            "quarantine_deleted": 0, "audits_deleted": 0, "links_deleted": 0,
        }
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["diagnostics", "clear", "--university", "ghost"]
        )
        assert result.exit_code == 0
        assert "0" in result.stdout  # at least one count shown


class TestDiagnosticsClearRestEndpoint:
    def test_delete_by_university(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_diagnostics.return_value = {
            "quarantine_deleted": 5, "audits_deleted": 3, "links_deleted": 27,
        }
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/diagnostics?university=hku")

        assert resp.status_code == 200
        body = resp.json()
        assert body["quarantine_deleted"] == 5
        assert body["audits_deleted"] == 3
        assert body["links_deleted"] == 27
        fake_db.clear_diagnostics.assert_called_once_with(
            university_slug="hku", year=None
        )

    def test_delete_with_year(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.clear_diagnostics.return_value = {
            "quarantine_deleted": 1, "audits_deleted": 1, "links_deleted": 4,
        }
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/diagnostics?university=hku&year=2026")
        assert resp.status_code == 200
        fake_db.clear_diagnostics.assert_called_once_with(
            university_slug="hku", year=2026
        )

    def test_delete_without_university_rejected(self, monkeypatch) -> None:
        fake_db = MagicMock()
        monkeypatch.setattr("src.api.server.get_db_manager", lambda: fake_db)

        client = TestClient(fastapi_app)
        resp = client.delete("/diagnostics")
        assert resp.status_code == 400
        fake_db.clear_diagnostics.assert_not_called()
