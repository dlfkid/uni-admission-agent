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


from src.agent_runtime.skills.impl.pagination_detector import detect_pagination


class TestStrategy1PaginationContainer:
    """Strategy 1: <nav> / <ul> pagination container."""

    def test_edinburgh_pagination(self):
        """Edinburgh: <nav aria-label="Pagination"> with ?page=0..71."""
        html = """
        <nav aria-label="Pagination">
          <ul class="pagination">
            <li class="page-item active">
              <a href="?page=0" class="page-link">1</a>
            </li>
            <li class="page-item">
              <a href="?page=1" class="page-link">2</a>
            </li>
            <li class="page-item">
              <a href="?page=2" class="page-link">3</a>
            </li>
            <li class="page-item">
              <a href="?page=71" aria-label="Last page" class="page-link">72</a>
            </li>
          </ul>
        </nav>
        """
        result = detect_pagination(html, "https://study.ed.ac.uk/programmes/undergraduate?page=0")
        assert result.pagination_type == "url_param"
        assert result.total_pages == 72
        assert len(result.page_urls) == 72
        assert result.page_urls[0] == "https://study.ed.ac.uk/programmes/undergraduate?page=0"
        assert result.page_urls[71] == "https://study.ed.ac.uk/programmes/undergraduate?page=71"
        assert result.confidence >= 0.8

    def test_leeds_pagination(self):
        """Leeds: <nav class="uol-pagination"> with ?page=1..19 and extra params."""
        html = """
        <nav class="uol-pagination" aria-label="pagination">
          <ol class="uol-pagination__list">
            <li class="uol-pagination__item uol-pagination__item--current">
              <a href="?page=1&amp;start_rank=1&amp;type=PGT&amp;term=202627"
                 class="uol-pagination__link">1</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=2&amp;start_rank=16&amp;type=PGT&amp;term=202627"
                 class="uol-pagination__link">2</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=3&amp;start_rank=31&amp;type=PGT&amp;term=202627"
                 class="uol-pagination__link">3</a>
            </li>
            <li class="uol-pagination__item">
              <a href="?page=19&amp;start_rank=271&amp;type=PGT&amp;term=202627"
                 class="uol-pagination__link">Last</a>
            </li>
          </ol>
        </nav>
        """
        result = detect_pagination(html, "https://courses.leeds.ac.uk/course-search/masters-courses")
        assert result.pagination_type == "url_param"
        assert result.total_pages == 19
        assert len(result.page_urls) == 19
        assert "page=1" in result.page_urls[0]
        assert "page=19" in result.page_urls[18]
        # Static params preserved
        assert "type=PGT" in result.page_urls[5]
        assert result.confidence >= 0.8


class TestStrategy3SpaButton:
    """Strategy 3: SPA button detection."""

    def test_nus_spa_buttons(self):
        """NUS: <button data-page="N"> pagination."""
        html = """
        <div class="pagination-container">
          <button class="arrow-button" disabled>Prev</button>
          <button data-page="1" class="active-page">1</button>
          <button data-page="2" class="page-button">2</button>
          <span class="ellipsis">...</span>
          <button data-page="25" class="page-button">25</button>
          <button class="arrow-button">Next</button>
        </div>
        """
        result = detect_pagination(html, "https://study.nus.edu.sg/programme")
        assert result.pagination_type == "spa_button"
        assert result.page_urls == []
        assert result.confidence >= 0.5


class TestStrategy4NoPagination:
    """Strategy 4: No pagination fallback."""

    def test_ucl_no_pagination(self):
        """UCL: large page with no pagination elements."""
        html = """
        <div class="degree-list">
          <a href="/degrees/anthropology-bsc">Anthropology BSc</a>
          <a href="/degrees/chemistry-bsc">Chemistry BSc</a>
        </div>
        """
        result = detect_pagination(html, "https://www.ucl.ac.uk/prospective-students/undergraduate/degrees")
        assert result.pagination_type == "single_page"
        assert result.total_pages == 1
        assert result.page_urls == []

    def test_polyu_swiper_not_pagination(self):
        """PolyU: swiper-pagination is NOT course pagination."""
        html = """
        <div class="swiper-pagination"></div>
        <div class="course-list">
          <a href="/study/pg/tpg/2026/xxx">Course A</a>
        </div>
        """
        result = detect_pagination(html, "https://www.polyu.edu.hk/study/pg/taught-postgraduate")
        assert result.pagination_type == "single_page"


from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden_samples" / "cases"


class TestGoldenSampleDetection:
    """Test pagination detection against actual golden sample HTML files."""

    def test_edinburgh_real_html(self):
        html_path = GOLDEN_DIR / "edinburgh_undergrad_accounting_business" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://study.ed.ac.uk/programmes/undergraduate?page=0"
        )
        assert result.pagination_type == "url_param"
        assert result.total_pages is not None and result.total_pages >= 70
        assert len(result.page_urls) >= 70
        assert result.confidence >= 0.8

    def test_leeds_real_html(self):
        html_path = GOLDEN_DIR / "leeds_masters_ai_business" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://courses.leeds.ac.uk/course-search/masters-courses"
        )
        assert result.pagination_type == "url_param"
        assert result.total_pages is not None and result.total_pages >= 15
        assert result.confidence >= 0.8

    def test_ucl_real_html(self):
        html_path = GOLDEN_DIR / "ucl_undergrad_anthropology" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.ucl.ac.uk/prospective-students/undergraduate/degrees"
        )
        assert result.pagination_type == "single_page"

    def test_polyu_real_html(self):
        html_path = GOLDEN_DIR / "polyu_masters_asset_wealth" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.polyu.edu.hk/study/pg/taught-postgraduate"
        )
        assert result.pagination_type == "single_page"

    def test_manchester_real_html(self):
        html_path = GOLDEN_DIR / "manchester_masters_business_psychology" / "index.html"
        if not html_path.exists():
            pytest.skip("Golden sample not available")
        html = html_path.read_text(encoding="utf-8")
        result = detect_pagination(
            html, "https://www.manchester.ac.uk/study/masters/courses/list/"
        )
        assert result.pagination_type == "single_page"
