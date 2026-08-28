"""The install skill must route through `upgrade`, not re-download — spec §8."""

from pathlib import Path

import pytest

SKILL = Path("skills/uni-admission-install/SKILL.md")


@pytest.fixture(name="skill_text")
def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_upgrade_section_calls_the_upgrade_command(skill_text: str) -> None:
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "adm-agent upgrade" in upgrade_section


def test_upgrade_section_no_longer_reruns_fresh_install(skill_text: str) -> None:
    """The re-download path had no backup, atomicity or verification."""
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "Run §1 (Fresh install)" not in upgrade_section


def test_skill_documents_the_exit_codes_the_agent_routes_on(skill_text: str) -> None:
    for code in ("10", "12", "13", "15"):
        assert f"| `{code}` |" in skill_text


def test_skill_documents_rollback(skill_text: str) -> None:
    assert "--rollback" in skill_text


def test_fresh_install_creates_the_versioned_layout(skill_text: str) -> None:
    assert "versions/" in skill_text
    assert "--strip-components=1 -C ~/.uni-agent/bin" not in skill_text
