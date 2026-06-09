"""The serve/crawl commands apply pending migrations on startup via
_init_db. A schema ALTER can block for tens of seconds; without a
user-visible notice it reads as a hang. These tests pin the stdout
notice behavior.
"""
from __future__ import annotations

from src.cmd import cli


def _stub_common(monkeypatch):
    """Stub DB init + taxonomy bootstrap so only migration logic runs."""
    class _FakeDB:
        def init_db(self):
            return None

    monkeypatch.setattr(cli, "DatabaseManager", _FakeDB)
    monkeypatch.setattr(cli, "bootstrap_subject_taxonomy", lambda: None)


def test_pending_migration_prints_user_notice(monkeypatch, capsys):
    _stub_common(monkeypatch)
    monkeypatch.setattr(
        cli,
        "get_migration_status",
        lambda: {"pending": True, "current_revision": "0008", "head_revision": "0009"},
    )
    applied = {"called": False}

    def _fake_run(*_a, **_k):
        applied["called"] = True
        return {"after_revision": "0009"}

    monkeypatch.setattr(cli, "run_db_migrations", _fake_run)

    cli._init_db(verbose=False)

    out = capsys.readouterr().out
    assert applied["called"] is True
    assert "Applying database migration" in out
    assert "0008" in out and "0009" in out
    assert "up to date" in out


def test_no_pending_migration_is_silent(monkeypatch, capsys):
    _stub_common(monkeypatch)
    monkeypatch.setattr(
        cli,
        "get_migration_status",
        lambda: {"pending": False, "current_revision": "0009", "head_revision": "0009"},
    )

    def _fail_run(*_a, **_k):
        raise AssertionError("run_db_migrations must not be called when not pending")

    monkeypatch.setattr(cli, "run_db_migrations", _fail_run)

    cli._init_db(verbose=False)

    out = capsys.readouterr().out
    assert "Applying database migration" not in out
