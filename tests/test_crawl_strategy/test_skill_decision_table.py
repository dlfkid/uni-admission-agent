"""Smoke test verifying the crawl skill documents the strategy decision table."""
from pathlib import Path

SKILL = Path("skills/uni-admission-crawl/SKILL.md")


def test_skill_documents_crawl_index_and_three_statuses():
    text = SKILL.read_text(encoding="utf-8")
    assert "crawl-index" in text
    for status in ("ok", "llm_fallback", "unsupported"):
        assert status in text
    assert "message_for_user" in text


def test_skill_documents_range_and_stop_reason():
    text = SKILL.read_text(encoding="utf-8")
    assert "--limit" in text
    assert "--all" in text
    assert "stopped_reason" in text
