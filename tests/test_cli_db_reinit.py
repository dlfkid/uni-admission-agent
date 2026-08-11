from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd import cli
from src.storage.db_manager import DatabaseManager


runner = CliRunner()


def test_db_reinit_cancelled_exits_zero() -> None:
    db_url = "postgresql://user:pass@localhost:5432/adm_agent"
    with (
        patch.dict("os.environ", {"DATABASE_URL": db_url}, clear=False),
        patch("src.cmd.cli.typer.confirm", return_value=False) as mock_confirm,
        patch("src.cmd.cli.database_exists", create=True) as mock_database_exists,
        patch("src.cmd.cli.drop_database", create=True) as mock_drop_database,
        patch("src.cmd.cli.create_database", create=True) as mock_create_database,
        patch("src.cmd.cli.run_db_migrations", create=True) as mock_run_db_migrations,
    ):
        result = runner.invoke(cli.app, ["db-reinit"])

    assert result.exit_code == 0
    assert "cancelled" in result.stdout.lower()
    mock_confirm.assert_called_once()
    mock_database_exists.assert_not_called()
    mock_drop_database.assert_not_called()
    mock_create_database.assert_not_called()
    mock_run_db_migrations.assert_not_called()


def test_db_reinit_yes_runs_drop_create_and_migrate() -> None:
    db_url = "postgresql://user:pass@localhost:5432/adm_agent"
    with (
        patch.dict("os.environ", {"DATABASE_URL": db_url}, clear=False),
        patch("src.cmd.cli.typer.confirm", return_value=True) as mock_confirm,
        patch("src.cmd.cli.database_exists", create=True, return_value=True) as mock_database_exists,
        patch("src.cmd.cli.drop_database", create=True) as mock_drop_database,
        patch("src.cmd.cli.create_database", create=True) as mock_create_database,
        patch(
            "src.cmd.cli.run_db_migrations",
            create=True,
            return_value={
                "before_revision": None,
                "after_revision": "head",
                "pending": False,
                "legacy_bootstrap": False,
            },
        ) as mock_run_db_migrations,
    ):
        result = runner.invoke(cli.app, ["db-reinit", "--yes"])

    assert result.exit_code == 0
    mock_confirm.assert_not_called()
    mock_database_exists.assert_called_once_with(db_url)
    mock_drop_database.assert_called_once_with(db_url)
    mock_create_database.assert_called_once_with(db_url)
    mock_run_db_migrations.assert_called_once_with(
        db_url=db_url,
        revision="head",
        verbose=False,
    )


def test_db_reinit_falls_back_to_default_sqlite_url_when_unset(monkeypatch, tmp_path) -> None:
    """Regression: this command used to hard-fail with "DATABASE_URL is not
    configured" whenever the env var was unset — which is the documented,
    zero-config default setup (uni-admission-install SKILL.md §1.6 tells
    users to leave DATABASE_URL commented out for SQLite). Every other
    command falls back to DatabaseManager._default_sqlite_url() in exactly
    this situation; db-reinit must do the same instead of refusing to run
    for the most common install."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sqlite_url = f"sqlite:///{tmp_path}/admission.db"
    with (
        patch.object(
            DatabaseManager, "_default_sqlite_url", return_value=sqlite_url
        ) as mock_default_url,
        patch("src.cmd.cli.typer.confirm", return_value=True) as mock_confirm,
        patch("src.cmd.cli.database_exists", create=True, return_value=False) as mock_database_exists,
        patch("src.cmd.cli.drop_database", create=True) as mock_drop_database,
        patch("src.cmd.cli.create_database", create=True) as mock_create_database,
        patch(
            "src.cmd.cli.run_db_migrations",
            create=True,
            return_value={
                "before_revision": None,
                "after_revision": "head",
                "pending": False,
                "legacy_bootstrap": False,
            },
        ) as mock_run_db_migrations,
    ):
        result = runner.invoke(cli.app, ["db-reinit", "--yes"])

    assert result.exit_code == 0
    assert "DATABASE_URL is not configured" not in result.stdout
    mock_confirm.assert_not_called()
    mock_default_url.assert_called_once()
    mock_database_exists.assert_called_once_with(sqlite_url)
    mock_drop_database.assert_not_called()  # database_exists=False -> skip drop
    mock_create_database.assert_called_once_with(sqlite_url)
    mock_run_db_migrations.assert_called_once_with(
        db_url=sqlite_url,
        revision="head",
        verbose=False,
    )
