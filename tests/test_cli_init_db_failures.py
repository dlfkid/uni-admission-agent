"""A command must not run against a database it failed to prepare.

`_init_db` runs before every DB-touching command. When it swallowed a failed
migration the command carried on against a schema that was not at head: a
crawl then imported nothing, wrote no audit record, and still printed
``✅ Crawl complete: 0 programs imported`` — indistinguishable, to the person
reading it, from "this university has no programmes".
"""

import os
from unittest.mock import patch

import pytest
import typer

import src.cmd.cli as cli
from src.services.migrations import MigrationError

_PENDING = {"pending": True, "current_revision": "aaa", "head_revision": "bbb"}
_UP_TO_DATE = {"pending": False, "current_revision": "bbb", "head_revision": "bbb"}


# ── a failed migration aborts ─────────────────────────────────────────


@pytest.mark.parametrize("verbose", [False, True])
def test_a_failed_migration_aborts_the_command(verbose: bool, capsys) -> None:
    """Not a warning, and not conditional on --verbose: the command stops."""
    with patch.object(cli, "get_migration_status", return_value=_PENDING), patch.object(
        cli, "run_db_migrations", side_effect=MigrationError("column already exists")
    ), patch.object(cli, "DatabaseManager"), patch.object(
        cli, "bootstrap_subject_taxonomy"
    ):
        with pytest.raises(typer.Exit) as exc:
            cli._init_db(verbose=verbose)

    assert exc.value.exit_code != 0
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "column already exists" in combined
    # Names the gap so the user can see what state they are in...
    assert "aaa" in combined and "bbb" in combined
    # ...and how to get out of it.
    assert "repair --auto" in combined


@pytest.mark.parametrize("verbose", [False, True])
def test_an_unexpected_migration_error_also_aborts(verbose: bool, capsys) -> None:
    """Anything raised while migrating leaves the schema unknown."""
    with patch.object(cli, "get_migration_status", return_value=_PENDING), patch.object(
        cli, "run_db_migrations", side_effect=RuntimeError("connection reset")
    ), patch.object(cli, "DatabaseManager"), patch.object(
        cli, "bootstrap_subject_taxonomy"
    ):
        with pytest.raises(typer.Exit):
            cli._init_db(verbose=verbose)
    assert "connection reset" in "".join(capsys.readouterr())


def test_a_failed_init_aborts_too(capsys) -> None:
    """Same class: a database that could not be opened must not be used."""
    with patch.object(
        cli, "DatabaseManager", side_effect=RuntimeError("could not connect")
    ), patch.object(cli, "bootstrap_subject_taxonomy"):
        with pytest.raises(typer.Exit):
            cli._init_db(verbose=False)
    assert "could not connect" in "".join(capsys.readouterr())


# ── what must NOT abort ───────────────────────────────────────────────


def test_a_failed_taxonomy_bootstrap_only_warns(capsys) -> None:
    """The taxonomy is a matching aid, not a schema requirement — a crawl
    without it still produces correct data, so this one is genuinely
    non-fatal. It is still reported, unconditionally."""
    with patch.object(cli, "get_migration_status", return_value=_UP_TO_DATE), patch.object(
        cli, "DatabaseManager"
    ), patch.object(
        cli, "bootstrap_subject_taxonomy", side_effect=RuntimeError("seed file missing")
    ):
        cli._init_db(verbose=False)  # does not raise
    assert "seed file missing" in "".join(capsys.readouterr())


def test_an_up_to_date_schema_is_silent(capsys) -> None:
    with patch.object(cli, "get_migration_status", return_value=_UP_TO_DATE), patch.object(
        cli, "DatabaseManager"
    ), patch.object(cli, "bootstrap_subject_taxonomy"):
        cli._init_db(verbose=False)
    assert capsys.readouterr().out.strip() == ""


def test_a_successful_migration_reports_completion(capsys) -> None:
    with patch.object(cli, "get_migration_status", return_value=_PENDING), patch.object(
        cli, "run_db_migrations"
    ), patch.object(cli, "DatabaseManager"), patch.object(
        cli, "bootstrap_subject_taxonomy"
    ):
        cli._init_db(verbose=False)
    out = capsys.readouterr().out
    assert "Applying database migration" in out
    assert "up to date" in out


# ── an explicit DATABASE_URL must beat the .env file ──────────────────


def test_an_explicit_database_url_is_not_clobbered_by_dotenv(
    tmp_path, monkeypatch
) -> None:
    """`DATABASE_URL=... adm-agent crawl` has to work.

    The loader used override=True, so a .env on disk replaced whatever the
    caller had set — the README tells users to switch backends with this
    variable and it silently did nothing. It also disagreed with
    src/agents/factory.py, which loads the same file with override=False.
    """
    from src.storage.db_helpers import _load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://from-dotenv/db\n", encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit.db")
    assert _load_env_file(str(env_file)) is True
    assert os.environ["DATABASE_URL"] == "sqlite:///explicit.db"


def test_dotenv_still_supplies_the_value_when_unset(tmp_path, monkeypatch) -> None:
    from src.storage.db_helpers import _load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://from-dotenv/db\n", encoding="utf-8")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _load_env_file(str(env_file)) is True
    assert os.environ["DATABASE_URL"] == "postgresql://from-dotenv/db"
