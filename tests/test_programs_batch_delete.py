"""Tests for batch-deleting programs by university/year (CLI + REST).

CLI coverage is added in a follow-up task in the same plan — this file
starts with the REST surface.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from src.api.server import app as fastapi_app
from src.cmd.cli import app as cli_app
from src.storage.db_manager import ProgramDeleteScope


class TestDeleteProgramsRestEndpoint:
    def test_preview_without_confirm_performs_no_delete(self) -> None:
        scope = ProgramDeleteScope(
            university_slug="leeds", count=42, years=[2025, 2026], deleted_names=[]
        )
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete("/programs", params={"univ_slug": "leeds"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] is False
        assert payload["count"] == 42
        assert payload["years"] == [2025, 2026]
        mock_count.assert_called_once_with("leeds", None)
        mock_delete.assert_not_called()

    def test_confirm_true_executes_delete(self) -> None:
        scope = ProgramDeleteScope(
            university_slug="leeds", count=42, years=[2025, 2026], deleted_names=[]
        )
        with (
            patch("src.api.server.count_programs_by_scope") as mock_count,
            patch("src.api.server.delete_programs_by_scope", return_value=scope) as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete(
                "/programs", params={"univ_slug": "leeds", "confirm": "true"}
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] is True
        assert payload["count"] == 42
        mock_count.assert_not_called()
        mock_delete.assert_called_once_with("leeds", None)

    def test_year_filter_passed_through(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=0)
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete(
                "/programs", params={"univ_slug": "leeds", "year": "2099"}
            )

        assert response.status_code == 200
        assert response.json()["count"] == 0
        mock_count.assert_called_once_with("leeds", 2099)
        mock_delete.assert_not_called()

    def test_zero_match_preview_message_mentions_university(self) -> None:
        scope = ProgramDeleteScope(university_slug="ghost", count=0)
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope),
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete("/programs", params={"univ_slug": "ghost"})

        assert response.status_code == 200
        assert "ghost" in response.json()["message"]
        mock_delete.assert_not_called()


class TestProgramsDeleteCli:
    def test_preview_without_yes_performs_no_delete(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=42, years=[2025, 2026])
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "leeds"]
            )

        assert result.exit_code == 0
        assert "42" in result.stdout
        assert "2025" in result.stdout and "2026" in result.stdout
        mock_count.assert_called_once_with("leeds", None)
        mock_delete.assert_not_called()

    def test_yes_executes_delete(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=42, years=[2025, 2026])
        with (
            patch("src.cmd.cli.count_programs_by_scope") as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope", return_value=scope) as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "leeds", "--yes"]
            )

        assert result.exit_code == 0
        assert "42" in result.stdout
        mock_count.assert_not_called()
        mock_delete.assert_called_once_with("leeds", None)

    def test_year_filter_passed_through(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=10, years=[2025])
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app,
                ["programs", "delete", "--university", "leeds", "--year", "2025"],
            )

        assert result.exit_code == 0
        mock_count.assert_called_once_with("leeds", 2025)
        mock_delete.assert_not_called()

    def test_requires_university(self) -> None:
        with (
            patch("src.cmd.cli.count_programs_by_scope") as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(cli_app, ["programs", "delete"])

        assert result.exit_code != 0
        mock_count.assert_not_called()
        mock_delete.assert_not_called()

    def test_zero_match_preview_shows_friendly_message(self) -> None:
        scope = ProgramDeleteScope(university_slug="ghost", count=0)
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope),
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "ghost"]
            )

        assert result.exit_code == 0
        assert "No programs found" in result.stdout
        mock_delete.assert_not_called()
