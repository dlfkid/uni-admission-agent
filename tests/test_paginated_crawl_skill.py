"""Tests for paginated crawl skill handler."""

from unittest.mock import MagicMock, patch

import pytest

from src.agent_runtime.skills.contracts import PaginatedCrawlSkillInput


class TestPaginatedCrawlSkillHandler:
    def test_single_page_no_pagination(self):
        """When no pagination detected, processes page 1 only."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        inp = PaginatedCrawlSkillInput(
            url="https://example.com/courses",
            univ_slug="example",
            year=2026,
        )

        fake_html = "<html><body><a href='/course/1'>Course 1</a></body></html>"
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content=fake_html
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            from src.agent_runtime.skills.contracts import PaginationInfo
            mock_detect.return_value = PaginationInfo(
                pagination_type="single_page", total_pages=1
            )
            mock_process.return_value = [
                {"name_en": "MSc Data Science", "faculty": "Computing"}
            ]

            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "done"
        assert result["pagination_type"] == "single_page"
        assert result["pages_processed"] == 1
        assert len(result["programs"]) == 1

    def test_spa_button_returns_not_supported(self):
        """When SPA pagination detected, returns pagination_not_supported."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        inp = PaginatedCrawlSkillInput(
            url="https://study.nus.edu.sg/programme",
            univ_slug="nus",
            year=2026,
        )

        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<button data-page='1'>1</button><button data-page='2'>2</button><button data-page='25'>25</button>"
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            from src.agent_runtime.skills.contracts import PaginationInfo
            mock_detect.return_value = PaginationInfo(
                pagination_type="spa_button", total_pages=25, confidence=0.7
            )
            mock_process.return_value = [
                {"name_en": "Doctor of Engineering", "faculty": "CDE"}
            ]

            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "pagination_not_supported"
        assert "SPA" in (result.get("warning") or "")
        # Should still process page 1
        assert result["pages_processed"] == 1
