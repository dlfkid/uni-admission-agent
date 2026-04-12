import pytest
from pydantic import ValidationError

from src.agent_runtime.skills.registry import build_skill_registry


def test_required_skills_registered():
    registry = build_skill_registry()
    assert "analyze_page_skill" in registry
    assert "legacy_crawl_batch_skill" in registry


def test_skill_input_validation_errors_on_bad_payload():
    registry = build_skill_registry()

    with pytest.raises(ValidationError):
        registry.execute("analyze_page_skill", {"url": ""})


def test_paginated_crawl_skill_registered():
    registry = build_skill_registry()
    assert "paginated_crawl_skill" in registry
