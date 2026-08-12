"""Tests for the 'db-export'/'db-import' CLI commands."""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd import cli
from src.storage.db_portability import DatabaseNotEmptyError
from src.services.migrations import MigrationError

runner = CliRunner()


class TestDbExportCli:
    def test_export_writes_to_output_and_reports_counts(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch(
                "src.cmd.cli.export_database",
                return_value={"university": 3, "program": 40},
            ) as mock_export,
        ):
            result = runner.invoke(cli.app, ["db-export", "--output", "out.zip"])

        assert result.exit_code == 0
        assert "43" in result.stdout  # total rows
        mock_export.assert_called_once_with("out.zip")

    def test_export_failure_exits_nonzero(self) -> None:
        with (
            patch("src.cmd.cli._init_db"),
            patch("src.cmd.cli.export_database", side_effect=RuntimeError("disk full")),
        ):
            result = runner.invoke(cli.app, ["db-export", "--output", "out.zip"])

        assert result.exit_code != 0


class TestDbImportCli:
    """db_import no longer calls _init_db() (see Fix 2, C2): it calls
    DatabaseManager().init_db() directly, skipping _init_db()'s taxonomy
    auto-seed which would falsify the "target is empty" check. Every test
    here patches src.cmd.cli.DatabaseManager so it never touches a real
    (dev/CI) database via that direct call."""

    def test_import_cancelled_without_yes_exits_zero_and_does_not_import(self) -> None:
        with (
            patch("src.cmd.cli.DatabaseManager") as mock_dm,
            patch("src.cmd.cli.typer.confirm", return_value=False) as mock_confirm,
            patch("src.cmd.cli.import_database") as mock_import,
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip"])

        assert result.exit_code == 0
        assert "cancelled" in result.stdout.lower()
        mock_dm.return_value.init_db.assert_called_once_with()
        mock_confirm.assert_called_once()
        mock_import.assert_not_called()

    def test_import_yes_skips_prompt_and_imports(self) -> None:
        with (
            patch("src.cmd.cli.DatabaseManager") as mock_dm,
            patch("src.cmd.cli.typer.confirm") as mock_confirm,
            patch(
                "src.cmd.cli.import_database",
                return_value={"university": 3, "program": 40},
            ) as mock_import,
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code == 0
        assert "43" in result.stdout
        mock_dm.return_value.init_db.assert_called_once_with()
        mock_confirm.assert_not_called()
        mock_import.assert_called_once_with("in.zip", force=False)

    def test_import_passes_force_flag_through(self) -> None:
        with (
            patch("src.cmd.cli.DatabaseManager"),
            patch(
                "src.cmd.cli.import_database", return_value={"university": 0}
            ) as mock_import,
        ):
            result = runner.invoke(
                cli.app, ["db-import", "--file", "in.zip", "--yes", "--force"]
            )

        assert result.exit_code == 0
        mock_import.assert_called_once_with("in.zip", force=True)

    def test_import_nonempty_target_without_force_reports_error(self) -> None:
        with (
            patch("src.cmd.cli.DatabaseManager"),
            patch(
                "src.cmd.cli.import_database",
                side_effect=DatabaseNotEmptyError("Target database already has data."),
            ),
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code != 0
        assert "already has data" in result.output

    def test_import_migration_failure_reports_error(self) -> None:
        with (
            patch("src.cmd.cli.DatabaseManager"),
            patch(
                "src.cmd.cli.import_database",
                side_effect=MigrationError("schema not at head"),
            ),
        ):
            result = runner.invoke(cli.app, ["db-import", "--file", "in.zip", "--yes"])

        assert result.exit_code != 0
        assert "migration" in result.output.lower()
