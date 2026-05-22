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


class TestPaginationStopSignals:
    """Pre-extraction stop signals: URL drift and decreasing yield."""

    def _make_pagination(self, page_urls: list[str]):
        from src.agent_runtime.skills.contracts import PaginationInfo
        return PaginationInfo(
            pagination_type="url_param",
            total_pages=len(page_urls),
            page_urls=page_urls,
        )

    def test_stops_when_next_page_url_diverges_from_index(self) -> None:
        """If a discovered page URL no longer matches the index pattern
        (e.g. AI followed a sibling section link), stop BEFORE extracting
        so no LLM tokens are wasted on irrelevant pages."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        # Page 1 fetch succeeds.
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>page1</html>"
        )

        # Pagination yields 3 URLs but #2 is from a totally different section.
        page_urls = [
            "https://e.edu/programs?page=1",
            "https://e.edu/about-us",  # drifted
            "https://e.edu/programs?page=3",
        ]

        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="e",
            year=2026,
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            mock_detect.return_value = self._make_pagination(page_urls)
            mock_process.return_value = [
                {"name_en": "MSc Finance", "faculty": "Business"}
            ]
            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "url_drift"
        assert result["stop_reason"] == "url_drift"
        assert result["pages_processed"] == 1  # only page 1 processed
        # Critically: _process_single_index_page was NOT called for the drifted page.
        assert mock_process.call_count == 1

    def test_stops_on_decreasing_yield(self) -> None:
        """After several pages of stable yield, a sharp drop triggers
        early stop — page count caps are too coarse on their own."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>page</html>"
        )
        page_urls = [
            f"https://e.edu/programs?page={i}" for i in range(1, 11)
        ]

        # Page yields: 10, 10, 10, 1 → ratio 0.1 < 0.2 → STOP after page 4.
        yields_per_page = [10, 10, 10, 1, 10, 10, 10, 10, 10, 10]

        def fake_process(*args, **kwargs):
            idx = fake_process.calls
            fake_process.calls += 1
            count = yields_per_page[idx]
            return [
                {"name_en": f"Program {idx}-{i}", "faculty": "F"}
                for i in range(count)
            ]
        fake_process.calls = 0

        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="e",
            year=2026,
            batch_quality_size=50,  # disable mid-loop quality checks
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page",
            side_effect=fake_process,
        ):
            mock_detect.return_value = self._make_pagination(page_urls)
            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "decreasing_yield"
        assert result["stop_reason"] == "decreasing_yield"
        assert result["pages_processed"] == 4
        # Programs from pages 1-4 are kept (10+10+10+1 = 31).
        assert result["total_programs"] == 31

    def test_normal_completion_sets_done_stop_reason(self) -> None:
        """Successful full completion still reports a stop_reason for audit."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>p</html>"
        )
        page_urls = [f"https://e.edu/programs?page={i}" for i in range(1, 4)]

        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="e", year=2026, batch_quality_size=50,
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process:
            mock_detect.return_value = self._make_pagination(page_urls)
            mock_process.return_value = [{"name_en": "P", "faculty": "F"}]
            result = paginated_crawl_skill_handler(inp, mock_bridge)

        assert result["status"] == "done"
        assert result["stop_reason"] in {"exhausted", "max_pages"}
        assert result["pages_processed"] == 3


class TestPaginationAuditWrite:
    """Pagination skill should record its stop_reason to extraction_audit
    so users can see WHY the crawl stopped after the fact (not just in
    realtime events that may be missed)."""

    def _make_pagination(self, page_urls):
        from src.agent_runtime.skills.contracts import PaginationInfo
        return PaginationInfo(
            pagination_type="url_param",
            total_pages=len(page_urls),
            page_urls=page_urls,
        )

    def test_skill_writes_audit_row_with_stop_reason(self) -> None:
        """When pagination completes (or stops), an audit row is written
        with the stop_reason for later inspection via `adm-agent audit list`."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>p</html>"
        )
        fake_db = MagicMock()

        page_urls = [f"https://e.edu/programs?page={i}" for i in range(1, 4)]
        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="hku", year=2026, batch_quality_size=50,
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page"
        ) as mock_process, patch(
            "src.agent_runtime.skills.impl.paginated_crawl.DatabaseManager",
            return_value=fake_db,
        ):
            mock_detect.return_value = self._make_pagination(page_urls)
            mock_process.return_value = [{"name_en": "MSc Finance", "faculty": "B"}]
            paginated_crawl_skill_handler(inp, mock_bridge)

        fake_db.record_extraction_audit.assert_called_once()
        kwargs = fake_db.record_extraction_audit.call_args.kwargs
        assert kwargs["university_slug"] == "hku"
        assert kwargs["academic_year"] == 2026
        assert kwargs["pagination_stop_reason"] in {"exhausted", "max_pages"}
        # Counts reflect actual processing.
        assert kwargs["extracted_count"] == 3  # 3 pages × 1 program

    def test_audit_records_url_drift_stop(self) -> None:
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>p</html>"
        )
        fake_db = MagicMock()

        page_urls = [
            "https://e.edu/programs?page=1",
            "https://e.edu/about",  # drift
        ]
        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="hku", year=2026,
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page",
            return_value=[{"name_en": "P", "faculty": "F"}],
        ), patch(
            "src.agent_runtime.skills.impl.paginated_crawl.DatabaseManager",
            return_value=fake_db,
        ):
            mock_detect.return_value = self._make_pagination(page_urls)
            paginated_crawl_skill_handler(inp, mock_bridge)

        fake_db.record_extraction_audit.assert_called_once()
        assert fake_db.record_extraction_audit.call_args.kwargs[
            "pagination_stop_reason"
        ] == "url_drift"

    def test_audit_write_failure_does_not_break_skill(self) -> None:
        """If audit write fails (e.g. DB down), the skill still returns
        its result — diagnostic logging should not block the user's data."""
        from src.agent_runtime.skills.impl.paginated_crawl import (
            paginated_crawl_skill_handler,
        )

        mock_bridge = MagicMock()
        mock_bridge.fetch_browser_payload.return_value = MagicMock(
            html_content="<html>p</html>"
        )
        fake_db = MagicMock()
        fake_db.record_extraction_audit.side_effect = RuntimeError("db down")

        inp = PaginatedCrawlSkillInput(
            url="https://e.edu/programs?page=1",
            univ_slug="hku", year=2026, batch_quality_size=50,
        )

        with patch(
            "src.agent_runtime.skills.impl.paginated_crawl.detect_pagination"
        ) as mock_detect, patch(
            "src.agent_runtime.skills.impl.paginated_crawl._process_single_index_page",
            return_value=[{"name_en": "P", "faculty": "F"}],
        ), patch(
            "src.agent_runtime.skills.impl.paginated_crawl.DatabaseManager",
            return_value=fake_db,
        ):
            mock_detect.return_value = self._make_pagination(
                ["https://e.edu/programs?page=1"]
            )
            result = paginated_crawl_skill_handler(inp, mock_bridge)

        # Result still produced despite audit failure.
        assert result["status"] in {"done", "url_drift", "decreasing_yield"}
        assert "programs" in result
