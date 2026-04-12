"""Tests for pagination detection contracts and detector."""

import pytest
from pydantic import ValidationError

from src.agent_runtime.skills.contracts import (
    PaginationInfo,
    QualityCheckResult,
    PaginatedCrawlSkillInput,
    PaginatedCrawlSkillOutput,
)


def test_pagination_info_defaults():
    info = PaginationInfo(pagination_type="single_page")
    assert info.pagination_type == "single_page"
    assert info.page_urls == []
    assert info.total_pages is None
    assert info.current_page == 1
    assert info.confidence == 0.0


def test_pagination_info_rejects_invalid_type():
    with pytest.raises(ValidationError):
        PaginationInfo(pagination_type="invalid_type")


def test_quality_check_result_defaults():
    result = QualityCheckResult(
        verdict="pass", heuristic_score=0.8, llm_used=False, reason="ok"
    )
    assert result.verdict == "pass"
    assert result.failed_at_page is None


def test_paginated_crawl_input_defaults():
    inp = PaginatedCrawlSkillInput(
        url="https://example.com", univ_slug="test", year=2026
    )
    assert inp.max_pages == 50
    assert inp.batch_quality_size == 10
    assert inp.client_id is None


def test_paginated_crawl_input_rejects_empty_url():
    with pytest.raises(ValidationError):
        PaginatedCrawlSkillInput(url="", univ_slug="test", year=2026)


def test_paginated_crawl_output_defaults():
    out = PaginatedCrawlSkillOutput(status="done", pagination_type="single_page")
    assert out.pages_processed == 0
    assert out.programs == []
    assert out.warning is None
