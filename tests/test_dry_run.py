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
