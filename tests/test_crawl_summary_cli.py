"""Tests for the `adm-agent crawl-summary` convenience CLI.

This command exists primarily for LLM CLI consumption (Claude Code,
Codex, Gemini CLI via the uni-admission-crawl skill) — it consolidates
the latest audit row + quarantine breakdown for one university into a
single human-readable block that the model can quote back to the user
without parsing multiple commands.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from typer.testing import CliRunner

from src.cmd.cli import app as cli_app
from src.models.extraction_audit import ExtractionAudit
from src.models.quarantine import ProgramQuarantine


def _make_audit(**kw) -> ExtractionAudit:
    base = {
        "id": 42,
        "university_slug": "hku",
        "academic_year": 2026,
        "index_url": "https://www.hku.hk/programs",
        "raw_link_count": 87,
        "llm_filtered_count": 23,
        "candidate_count": 22,
        "extracted_count": 18,
        "quarantined_count": 4,
        "recovered_count": 2,
        "pagination_stop_reason": "exhausted",
        "created_at": datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc),
    }
    base.update(kw)
    return ExtractionAudit(**base)


def _make_q(reason: str, name: str = "X") -> ProgramQuarantine:
    return ProgramQuarantine(
        id=hash(name) & 0xFFFF,
        university_slug="hku",
        academic_year=2026,
        source_url=f"https://e.edu/{name}",
        extracted_name=name,
        payload="{}",
        quarantine_reason=reason,
        quarantine_signals="{}",
        created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )


class TestCrawlSummary:
    def test_renders_full_summary_when_data_present(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [_make_audit()]
        fake_db.list_quarantine.return_value = [
            _make_q("empty_shell", "a"),
            _make_q("empty_shell", "b"),
            _make_q("noise_name", "c"),
            _make_q("extraction_failed", "d"),
        ]
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["crawl-summary", "--university", "hku", "--year", "2026"],
        )

        assert result.exit_code == 0
        # Funnel numbers all visible.
        for token in ["87", "23", "22", "18", "4", "exhausted"]:
            assert token in result.stdout, f"missing {token!r} in output"
        # Quarantine breakdown shows reason counts.
        assert "empty_shell: 2" in result.stdout
        assert "noise_name: 1" in result.stdout
        assert "extraction_failed: 1" in result.stdout
        fake_db.list_extraction_audit.assert_called_once_with(
            university_slug="hku", year=2026, limit=1
        )
        fake_db.list_quarantine.assert_called_once_with(
            university_slug="hku", year=2026
        )

    def test_renders_recovered_count_when_nonzero(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [_make_audit(recovered_count=5)]
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["crawl-summary", "--university", "hku"]
        )

        assert result.exit_code == 0
        assert "recovered=5" in result.stdout or "rescued=5" in result.stdout

    def test_no_audit_reports_no_recent_crawl(self, monkeypatch) -> None:
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = []
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["crawl-summary", "--university", "ghost"]
        )

        assert result.exit_code == 0
        assert "no recent crawl" in result.stdout.lower()

    def test_quarantine_empty_shows_zero_explicitly(self, monkeypatch) -> None:
        """When there are zero quarantine entries, that's good news — say so
        explicitly rather than just omitting the section."""
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [
            _make_audit(quarantined_count=0)
        ]
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["crawl-summary", "--university", "hku"]
        )
        assert result.exit_code == 0
        # Some affirmative signal that nothing was quarantined.
        out = result.stdout.lower()
        assert "no quarantine" in out or "quarantine: 0" in out or "quarantined: 0" in out

    def test_university_required(self, monkeypatch) -> None:
        fake_db = MagicMock()
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(cli_app, ["crawl-summary"])
        assert result.exit_code != 0
        fake_db.list_extraction_audit.assert_not_called()

    def test_stop_reason_marked_with_warning_when_anomalous(self, monkeypatch) -> None:
        """Stop reasons other than 'exhausted' / 'max_pages' indicate a
        possible problem — surface with a visual cue so the LLM can
        flag it to the user."""
        fake_db = MagicMock()
        fake_db.list_extraction_audit.return_value = [
            _make_audit(pagination_stop_reason="url_drift")
        ]
        fake_db.list_quarantine.return_value = []
        monkeypatch.setattr("src.cmd.cli.DatabaseManager", lambda: fake_db)

        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["crawl-summary", "--university", "hku"]
        )
        assert result.exit_code == 0
        # The url_drift reason itself must appear.
        assert "url_drift" in result.stdout
        # Some warning indicator (⚠️ or "warning" or "anomalous") attached.
        out = result.stdout.lower()
        assert "⚠" in result.stdout or "warning" in out or "anomalous" in out
