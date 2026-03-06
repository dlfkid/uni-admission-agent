from unittest.mock import MagicMock

import pytest

from src.services.ingestion_pipeline import IngestionPipeline


def _raw_page(
    *,
    url: str,
    markdown: str,
    selected_anchor_text: str | None = None,
) -> dict:
    return {
        "url": url,
        "markdown": markdown,
        "char_count": len(markdown),
        "links": [],
        "status_code": 200,
        "html": None,
        "crawl_depth": 0,
        "from_browser": False,
        "selected_anchor_text": selected_anchor_text,
    }


@pytest.fixture(autouse=True)
def _stub_llm_cleaner_agent(monkeypatch) -> None:
    class _StubCleaner:
        pass

    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        _StubCleaner,
    )


def test_signal_priority_anchor_then_url_then_heading(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    captured_signals: list[str] = []

    class FakeTaxonomyService:
        def match_signals(self, signals, top_k=3):
            captured_signals.extend(signals)
            return []

    def fake_extract_program_data_from_page(**_kwargs):
        return (
            {
                "academic_year": 2026,
                "name_en": "What's New",
                "extra_metadata": {},
            },
            None,
        )

    pipeline.taxonomy_service = FakeTaxonomyService()
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        fake_extract_program_data_from_page,
    )

    pipeline._stage_extract_structured(
        {
            "univ_slug": "polyu",
            "year": 2026,
            "taxonomy_enabled": True,
            "taxonomy_low_threshold": 0.8,
            "taxonomy_high_threshold": 0.92,
            "taxonomy_hint_top_k": 3,
            "taxonomy_override_enabled": True,
        },
        {
            "raw_pages": [
                _raw_page(
                    url="https://www.polyu.edu.hk/programmes/asset-wealth-management",
                    markdown="# What's New\n\nProgram details",
                    selected_anchor_text="Master of Science in Asset and Wealth Management",
                )
            ]
        },
    )

    assert captured_signals[0] == "Master of Science in Asset and Wealth Management"
    assert "asset wealth management" in captured_signals[1].lower()
    assert captured_signals[-1] == "What's New"


def test_inject_hint_only_when_low_threshold_met(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    cleaner_hints: list[list[str]] = []

    class FakeTaxonomyService:
        def __init__(self) -> None:
            self._calls = 0

        def match_signals(self, _signals, top_k=3):
            self._calls += 1
            if self._calls == 1:
                return [{"name_en": "Master of Science in Finance", "score": 0.79}]
            return [{"name_en": "Master of Science in Finance", "score": 0.81}]

    def fake_extract_program_data_from_page(**kwargs):
        cleaner_hints.append(list(kwargs.get("name_hints") or []))
        return (
            {
                "academic_year": 2026,
                "name_en": "Master of Science in Finance",
                "extra_metadata": {},
            },
            None,
        )

    pipeline.taxonomy_service = FakeTaxonomyService()
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        fake_extract_program_data_from_page,
    )

    pipeline._stage_extract_structured(
        {
            "univ_slug": "polyu",
            "year": 2026,
            "taxonomy_enabled": True,
            "taxonomy_low_threshold": 0.8,
            "taxonomy_high_threshold": 0.92,
            "taxonomy_hint_top_k": 3,
            "taxonomy_override_enabled": False,
        },
        {
            "raw_pages": [
                _raw_page(
                    url="https://example.com/finance-a",
                    markdown="# Finance A",
                    selected_anchor_text="Finance A",
                ),
                _raw_page(
                    url="https://example.com/finance-b",
                    markdown="# Finance B",
                    selected_anchor_text="Finance B",
                ),
            ]
        },
    )

    assert cleaner_hints[0] == []
    assert cleaner_hints[1] == ["Master of Science in Finance|0.81"]


def test_high_confidence_override_replaces_whats_new(monkeypatch) -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())

    class FakeTaxonomyService:
        def match_signals(self, _signals, top_k=3):
            return [
                {
                    "name_en": "Master of Science in Asset and Wealth Management",
                    "score": 0.95,
                    "normalized_name": "masterofscienceinassetandwealthmanagement",
                }
            ]

    def fake_extract_program_data_from_page(**_kwargs):
        return (
            {
                "academic_year": 2026,
                "name_en": "What's New",
                "program_group_code": "polyu#whatsnew",
                "extra_metadata": {},
            },
            None,
        )

    pipeline.taxonomy_service = FakeTaxonomyService()
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        fake_extract_program_data_from_page,
    )

    result = pipeline._stage_extract_structured(
        {
            "univ_slug": "polyu",
            "year": 2026,
            "taxonomy_enabled": True,
            "taxonomy_low_threshold": 0.8,
            "taxonomy_high_threshold": 0.92,
            "taxonomy_hint_top_k": 3,
            "taxonomy_override_enabled": True,
        },
        {
            "raw_pages": [
                _raw_page(
                    url="https://www.polyu.edu.hk/programmes/asset-wealth-management",
                    markdown="# What's New\n\nProgram details",
                    selected_anchor_text="Master of Science in Asset and Wealth Management",
                )
            ]
        },
    )

    candidate = result["program_candidates"][0]
    assert candidate["name_en"] == "Master of Science in Asset and Wealth Management"
    trace = candidate["extra_metadata"]["taxonomy_match"]
    assert trace["override_applied"] is True
