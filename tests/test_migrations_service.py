from unittest.mock import patch

from src.services.migrations import (
    _bootstrap_legacy_schema,
    get_migration_status,
    run_db_migrations,
)


def test_get_migration_status_pending_true() -> None:
    with (
        patch("src.services.migrations._build_config", return_value=object()),
        patch("src.services.migrations._resolve_db_url", return_value="postgresql://db"),
        patch("src.services.migrations._get_head_revision", return_value="rev_head"),
        patch("src.services.migrations._get_current_revision", return_value="rev_old"),
    ):
        status = get_migration_status()

    assert status["current_revision"] == "rev_old"
    assert status["head_revision"] == "rev_head"
    assert status["pending"] is True


def test_bootstrap_legacy_schema_stamps_once() -> None:
    cfg = object()
    mock_script = type("MockScript", (), {"get_revision": lambda self, _: object()})()
    with (
        patch("src.services.migrations._get_current_revision", return_value=None),
        patch("src.services.migrations._legacy_tables_exist", return_value=True),
        patch("src.services.migrations.ScriptDirectory.from_config", return_value=mock_script),
        patch("src.services.migrations.command.stamp") as mock_stamp,
    ):
        stamped = _bootstrap_legacy_schema(cfg, "postgresql://db", "rev_head")

    assert stamped is True
    mock_stamp.assert_called_once_with(cfg, "20260302_0001")


def test_run_db_migrations_happy_path() -> None:
    cfg = object()
    with (
        patch("src.services.migrations._build_config", return_value=cfg),
        patch("src.services.migrations._resolve_db_url", return_value="postgresql://db"),
        patch("src.services.migrations._get_head_revision", return_value="rev_head"),
        patch("src.services.migrations._bootstrap_legacy_schema", return_value=False),
        patch(
            "src.services.migrations._get_current_revision",
            side_effect=["rev_old", "rev_head"],
        ),
        patch("src.services.migrations.command.upgrade") as mock_upgrade,
    ):
        result = run_db_migrations()

    mock_upgrade.assert_called_once_with(cfg, "head")
    assert result["before_revision"] == "rev_old"
    assert result["after_revision"] == "rev_head"
    assert result["pending"] is False
