from unittest.mock import patch

import pytest

from src.services.repair import RepairError, run_auto_repair


def test_run_auto_repair_success() -> None:
    with (
        patch(
            "src.services.repair.check_database_health",
            side_effect=[
                {"ok": True, "missing_tables": []},
                {"ok": True, "missing_tables": []},
            ],
        ),
        patch(
            "src.services.repair.get_migration_status",
            return_value={
                "current_revision": "rev_old",
                "head_revision": "rev_head",
                "pending": True,
            },
        ),
        patch(
            "src.services.repair.create_backup_snapshot",
            return_value={"university": "_ua_backup_20260302120000__university"},
        ),
        patch(
            "src.services.repair.run_db_migrations",
            return_value={
                "before_revision": "rev_old",
                "after_revision": "rev_head",
                "head_revision": "rev_head",
                "pending": False,
                "legacy_bootstrap": False,
            },
        ),
        patch("src.services.repair.cleanup_old_backups") as mock_cleanup,
    ):
        result = run_auto_repair("postgresql://example")

    assert result["snapshot_id"] == "20260302120000"
    assert result["migration_result"]["pending"] is False
    mock_cleanup.assert_called_once_with("postgresql://example")


def test_run_auto_repair_migration_fail_but_rollback_success() -> None:
    with (
        patch("src.services.repair.check_database_health", return_value={"ok": True, "missing_tables": []}),
        patch(
            "src.services.repair.get_migration_status",
            return_value={"current_revision": None, "head_revision": "rev_head", "pending": True},
        ),
        patch(
            "src.services.repair.create_backup_snapshot",
            return_value={"program": "_ua_backup_20260302121000__program"},
        ),
        patch("src.services.repair.run_db_migrations", side_effect=RuntimeError("boom")),
        patch("src.services.repair.restore_from_snapshot") as mock_restore,
    ):
        with pytest.raises(RepairError, match="rollback succeeded"):
            run_auto_repair("postgresql://example")

    mock_restore.assert_called_once_with("postgresql://example", "20260302121000")


def test_run_auto_repair_migration_fail_and_rollback_fail() -> None:
    with (
        patch("src.services.repair.check_database_health", return_value={"ok": True, "missing_tables": []}),
        patch(
            "src.services.repair.get_migration_status",
            return_value={"current_revision": None, "head_revision": "rev_head", "pending": True},
        ),
        patch(
            "src.services.repair.create_backup_snapshot",
            return_value={"program": "_ua_backup_20260302121500__program"},
        ),
        patch("src.services.repair.run_db_migrations", side_effect=RuntimeError("boom")),
        patch("src.services.repair.restore_from_snapshot", side_effect=RuntimeError("restore failed")),
    ):
        with pytest.raises(RepairError, match="rollback failed"):
            run_auto_repair("postgresql://example")
