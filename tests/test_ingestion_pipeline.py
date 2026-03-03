from unittest.mock import MagicMock

import pytest

from src.models.ingestion import IngestionStage
from src.models.scraper_models import CrawlPageResult
from src.services.ingestion_pipeline import IngestionPipeline, StageExecutionError


def test_validate_rules_filters_invalid_records() -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    request_payload = {"year": 2026}
    context = {
        "program_candidates": [
            {
                "name_en": "MSc Finance",
                "academic_year": 2026,
                "study_options": None,
                "deadlines": None,
                "requirements": None,
            },
            {
                "name_en": "",
                "academic_year": 2026,
            },
        ]
    }

    result = pipeline._stage_validate_rules(request_payload, context)

    assert result["validated_count"] == 1
    assert len(result["validated_programs"]) == 1
    assert len(result["rejected_programs"]) == 1
    assert result["validated_programs"][0]["name_en"] == "MSc Finance"
    assert result["validated_programs"][0]["study_options"] == []


def test_persist_versioned_counts_create_and_update() -> None:
    mock_db = MagicMock()
    mock_db.upsert_program.side_effect = [
        (MagicMock(), True),
        (MagicMock(), False),
    ]
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            {"name_en": "MSc A", "academic_year": 2026},
            {"name_en": "MSc B", "academic_year": 2026},
        ],
        "validated_hash": "abc123",
    }

    result = pipeline._stage_persist_versioned(request_payload, context)

    assert result["persisted_count"] == 2
    assert result["created_count"] == 1
    assert result["updated_count"] == 1
    assert mock_db.upsert_program.call_count == 2


def test_persist_versioned_raises_when_any_record_fails() -> None:
    mock_db = MagicMock()
    mock_db.upsert_program.side_effect = RuntimeError("db fail")
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            {"name_en": "MSc A", "academic_year": 2026},
        ]
    }

    with pytest.raises(StageExecutionError):
        pipeline._stage_persist_versioned(request_payload, context)


def test_idempotency_key_is_deterministic() -> None:
    payload = {
        "univ_slug": "hku",
        "year": 2026,
        "url": "https://example.com",
    }
    stage_input = {
        "validated_count": 2,
        "validated_hash": "xyz",
    }

    key_a = IngestionPipeline._build_idempotency_key(
        stage=IngestionStage.PERSIST_VERSIONED,
        request_payload=payload,
        stage_input=stage_input,
    )
    key_b = IngestionPipeline._build_idempotency_key(
        stage=IngestionStage.PERSIST_VERSIONED,
        request_payload=payload,
        stage_input=stage_input,
    )

    assert key_a == key_b


def test_stage_trace_sequence_increments() -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    context = {}

    pipeline._append_stage_trace(context, IngestionStage.FETCH_RAW, "SUCCEEDED", 1)
    pipeline._append_stage_trace(context, IngestionStage.EXTRACT_STRUCTURED, "SUCCEEDED", 1)

    assert context["trace_seq"] == 2
    assert context["stage_trace"][0]["seq"] == 1
    assert context["stage_trace"][1]["seq"] == 2


def test_prune_context_from_stage_removes_downstream_keys() -> None:
    pipeline = IngestionPipeline(db_manager=MagicMock())
    context = {
        "raw_pages": [{"url": "https://example.com"}],
        "program_candidates": [{"name_en": "A"}],
        "validated_programs": [{"name_en": "A"}],
        "persisted_count": 1,
    }

    pipeline._prune_context_from_stage(context, IngestionStage.VALIDATE_RULES)

    assert "raw_pages" in context
    assert "program_candidates" in context
    assert "validated_programs" not in context
    assert "persisted_count" not in context


@pytest.mark.asyncio
async def test_fetch_raw_uses_scout_when_continue_depth_enabled(monkeypatch) -> None:
    class FakeScoutLink:
        url = "https://example.com/deep"
        reason = "More admission details"
        confidence = "high"

    class FakeScraper:
        def __init__(self) -> None:
            self.router = MagicMock()
            self._export_md = False
            self._export_path = None

        def _reset_session_state(self) -> None:
            return

        async def _crawl_urls(self, urls):
            if urls == ["https://example.com/detail"]:
                return [
                    CrawlPageResult(
                        url="https://example.com/detail",
                        markdown="# detail",
                        char_count=8,
                        links=["https://example.com/deep"],
                    )
                ]
            if urls == ["https://example.com/deep"]:
                return [
                    CrawlPageResult(
                        url="https://example.com/deep",
                        markdown="# deep",
                        char_count=6,
                        links=[],
                    )
                ]
            return []

    def fake_run_scout(router, candidates, visited_urls, scout_call_count, all_scouted_links):
        _ = router, candidates, visited_urls
        return ["https://example.com/deep"], scout_call_count + 1, all_scouted_links + [FakeScoutLink()]

    monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", FakeScraper)
    monkeypatch.setattr("src.services.ingestion_pipeline.run_scout", fake_run_scout)

    pipeline = IngestionPipeline(db_manager=MagicMock())
    result = await pipeline._stage_fetch_raw(
        {
            "url": "https://example.com",
            "selected_urls": ["https://example.com/detail"],
            "continue_depth": 1,
            "page_type_hint": "auto",
        }
    )

    assert result["raw_page_count"] == 2
    assert result["scout_call_count"] == 1
    assert len(result["scouted_links"]) == 1
    depths = [row["crawl_depth"] for row in result["raw_pages"]]
    assert depths == [0, 1]
