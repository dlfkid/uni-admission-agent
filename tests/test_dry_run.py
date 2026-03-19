"""Tests for dry-run propagation through skill contracts and persistence."""

from src.agent_runtime.skills.contracts import (
    PersistProgramsSkillInput,
    PersistProgramsSkillOutput,
)


def test_persist_input_accepts_dry_run_flag():
    payload = PersistProgramsSkillInput(
        univ_slug="ucl", year=2026, programs=[], dry_run=True
    )
    assert payload.dry_run is True


def test_persist_input_defaults_dry_run_false():
    payload = PersistProgramsSkillInput(
        univ_slug="ucl", year=2026, programs=[]
    )
    assert payload.dry_run is False


def test_persist_output_includes_dry_run_and_parsed_programs():
    output = PersistProgramsSkillOutput(
        imported_count=0,
        dry_run=True,
        parsed_programs=[{"name_en": "Test Program"}],
    )
    assert output.dry_run is True
    assert len(output.parsed_programs) == 1


from unittest.mock import patch


def test_ingest_dry_run_skips_db_upsert():
    """When dry_run=True, no DatabaseManager.upsert_program calls are made."""
    with patch("src.services.crawler.DatabaseManager") as mock_db_cls:
        from src.services.crawler import ingest_program_records_external

        result = ingest_program_records_external(
            univ_slug="ucl",
            year=2026,
            programs=[{"name_en": "Test Program", "faculty": "Arts"}],
            dry_run=True,
        )
        mock_db_cls.return_value.upsert_program.assert_not_called()
        assert result["dry_run"] is True
        assert result["imported_count"] == 0
        assert len(result["parsed_programs"]) == 1
        assert result["parsed_programs"][0]["name_en"] == "Test Program"


def test_ingest_dry_run_still_validates():
    """dry_run=True still rejects items without name_en."""
    with patch("src.services.crawler.DatabaseManager"):
        from src.services.crawler import ingest_program_records_external

        result = ingest_program_records_external(
            univ_slug="ucl",
            year=2026,
            programs=[{"faculty": "Arts"}],  # missing name_en
            dry_run=True,
        )
        assert len(result["failed_items"]) == 1
        assert result["failed_items"][0]["error_code"] == "missing_name_en"
        assert len(result["parsed_programs"]) == 0
