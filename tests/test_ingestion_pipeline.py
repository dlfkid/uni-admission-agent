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
            {"name_en": "MSc Finance", "academic_year": 2026, "tuition_amount": 100},
            {"name_en": "MSc Economics", "academic_year": 2026, "tuition_amount": 100},
        ],
        "validated_hash": "abc123",
    }

    result = pipeline._stage_persist_versioned(request_payload, context)

    assert result["persisted_count"] == 2
    assert result["created_count"] == 1
    assert result["updated_count"] == 1
    assert mock_db.upsert_program.call_count == 2


def test_persist_versioned_learns_taxonomy_from_persisted_names() -> None:
    mock_db = MagicMock()
    created_program = MagicMock()
    created_program.id = 101
    created_program.name_en = "Master of Science in Finance"
    created_program.source_url = "https://example.edu/finance"
    created_program.extra_metadata = {
        "taxonomy_match": {"best_score": 0.96}
    }
    mock_db.upsert_program.return_value = (created_program, True)

    pipeline = IngestionPipeline(db_manager=mock_db)
    pipeline.taxonomy_service = MagicMock()

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            {
                "name_en": "Master of Science in Finance",
                "academic_year": 2026,
                "source_url": "https://example.edu/finance",
                "tuition_amount": 100,
                "extra_metadata": {"taxonomy_match": {"best_score": 0.96}},
            }
        ],
        "validated_hash": "abc123",
    }

    pipeline._stage_persist_versioned(request_payload, context)

    pipeline.taxonomy_service.learn_persisted_names.assert_called_once()


def test_persist_versioned_raises_when_any_record_fails() -> None:
    mock_db = MagicMock()
    mock_db.upsert_program.side_effect = RuntimeError("db fail")
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            {"name_en": "MSc Finance", "academic_year": 2026, "tuition_amount": 100},
        ]
    }

    with pytest.raises(StageExecutionError):
        pipeline._stage_persist_versioned(request_payload, context)


def test_persist_versioned_routes_empty_shells_to_quarantine() -> None:
    """Quality-gate failures must skip upsert_program and call upsert_quarantine."""
    mock_db = MagicMock()
    mock_db.upsert_program.return_value = (MagicMock(id=1, name_en="OK", source_url="", extra_metadata=None), True)
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            # Good record — should persist.
            {"name_en": "MSc Finance", "academic_year": 2026, "tuition_amount": 100},
            # Empty shell — should be quarantined.
            {"name_en": "MSc Economics", "academic_year": 2026},
            # Noise name — should be quarantined.
            {"name_en": "Course Search", "academic_year": 2026, "tuition_amount": 100},
        ],
        "validated_hash": "h",
    }

    result = pipeline._stage_persist_versioned(request_payload, context)

    assert result["persisted_count"] == 1
    assert result["quarantined_count"] == 2
    assert mock_db.upsert_program.call_count == 1
    assert mock_db.upsert_quarantine.call_count == 2
    reasons = {c.kwargs["reason"].value for c in mock_db.upsert_quarantine.call_args_list}
    assert reasons == {"empty_shell", "noise_name"}


def test_persist_versioned_writes_extraction_audit() -> None:
    """When fetch_raw recorded a funnel, persist_versioned must finalize
    an extraction_audit row combining funnel + final counts."""
    mock_db = MagicMock()
    mock_db.upsert_program.return_value = (
        MagicMock(id=1, name_en="MSc Finance", source_url="https://e.edu/fin", extra_metadata=None),
        True,
    )
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku", "year": 2026}
    context = {
        "validated_programs": [
            {"name_en": "MSc Finance", "academic_year": 2026, "tuition_amount": 100},
            {"name_en": "MSc Empty", "academic_year": 2026},  # empty shell → quarantine
        ],
        "validated_hash": "h",
        "audit_funnel": {
            "index_url": "https://www.hku.hk/programs",
            "raw_link_count": 87,
            "llm_filtered_count": 23,
            "candidate_count": 22,
        },
        "job_uid": "job-xyz",
    }

    pipeline._stage_persist_versioned(request_payload, context)

    mock_db.record_extraction_audit.assert_called_once()
    kwargs = mock_db.record_extraction_audit.call_args.kwargs
    assert kwargs["university_slug"] == "hku"
    assert kwargs["academic_year"] == 2026
    assert kwargs["index_url"] == "https://www.hku.hk/programs"
    assert kwargs["raw_link_count"] == 87
    assert kwargs["llm_filtered_count"] == 23
    assert kwargs["candidate_count"] == 22
    assert kwargs["extracted_count"] == 1
    assert kwargs["quarantined_count"] == 1
    assert kwargs["job_uid"] == "job-xyz"


def test_persist_versioned_skips_audit_when_no_funnel() -> None:
    """Direct detail-mode crawls (no index page) don't have a funnel —
    audit must not be written in that case."""
    mock_db = MagicMock()
    mock_db.upsert_program.return_value = (
        MagicMock(id=1, name_en="X", source_url="", extra_metadata=None),
        True,
    )
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku", "year": 2026}
    context = {
        "validated_programs": [
            {"name_en": "MSc Finance", "academic_year": 2026, "tuition_amount": 100},
        ],
        "validated_hash": "h",
    }

    pipeline._stage_persist_versioned(request_payload, context)

    mock_db.record_extraction_audit.assert_not_called()


def test_persist_versioned_graduates_prior_quarantine_on_success() -> None:
    """Successful upsert must clear any prior quarantine entry for the
    same source_url, so the table reflects current state, not history."""
    mock_db = MagicMock()
    mock_db.upsert_program.return_value = (
        MagicMock(id=1, name_en="MSc Finance", source_url="https://e.edu/fin", extra_metadata=None),
        True,
    )
    pipeline = IngestionPipeline(db_manager=mock_db)

    request_payload = {"univ_slug": "hku"}
    context = {
        "validated_programs": [
            {
                "name_en": "MSc Finance",
                "academic_year": 2026,
                "tuition_amount": 100,
                "source_url": "https://e.edu/fin",
            },
        ],
        "validated_hash": "h",
    }

    pipeline._stage_persist_versioned(request_payload, context)

    mock_db.upsert_program.assert_called_once()
    mock_db.clear_quarantine.assert_called_once_with(
        university_slug="hku", source_url="https://e.edu/fin"
    )


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


@pytest.mark.asyncio
async def test_fetch_raw_uses_detail_pages_batch_without_network_fetch(monkeypatch) -> None:
    created = {}
    events = []

    class FakeScraper:
        def __init__(self) -> None:
            self.router = MagicMock()
            self._export_md = False
            self._export_path = None
            self.crawl_page_called = False
            self.crawl_urls_called = False
            created["instance"] = self

        def _reset_session_state(self) -> None:
            return

        def _create_result_from_browser_html(self, url: str, html_content: str) -> CrawlPageResult:
            return CrawlPageResult(
                url=url,
                markdown=html_content,
                char_count=len(html_content),
                links=[],
                status_code=200,
                html=html_content,
            )

        async def _crawl_urls(self, _urls):
            self.crawl_urls_called = True
            return []

        async def crawl_page(self, url: str):
            self.crawl_page_called = True
            return CrawlPageResult(
                url=url,
                markdown="# seed",
                char_count=6,
                links=[],
                status_code=200,
                html="<html>seed</html>",
            )

    monkeypatch.setattr("src.services.ingestion_pipeline.AdmissionScraper", FakeScraper)

    pipeline = IngestionPipeline(db_manager=MagicMock())
    def capture_event(event_type, payload):
        events.append((event_type, payload))

    result = await pipeline._stage_fetch_raw(
        {
            "url": "https://index.example",
            "page_type_hint": "index",
            "selected_urls": [],
            "batch_index": 1,
            "batch_total": 2,
            "detail_pages_batch": [
                {
                    "url": "https://detail.example/1",
                    "html_content": "<html>one</html>",
                    "selected_anchor_text": "Program One",
                },
                {
                    "url": "https://detail.example/2",
                    "html_content": "<html>two</html>",
                },
            ],
        },
        event_callback=capture_event,
    )

    scraper = created["instance"]
    assert scraper.crawl_page_called is False
    assert scraper.crawl_urls_called is False
    assert result["raw_page_count"] == 2
    assert [row["url"] for row in result["raw_pages"]] == [
        "https://detail.example/1",
        "https://detail.example/2",
    ]
    assert all(bool(row["from_browser"]) for row in result["raw_pages"])
    assert result["raw_pages"][0]["selected_anchor_text"] == "Program One"
    assert events
    fetch_events = [payload for event, payload in events if event.startswith("fetch_")]
    assert fetch_events
    assert all(payload.get("batch_index") == 1 for payload in fetch_events)
    assert all(payload.get("batch_total") == 2 for payload in fetch_events)
    assert any(payload.get("source") == "browser_automation" for payload in fetch_events)


def test_extract_structured_skips_antibot_pages_in_index_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        MagicMock,
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    extract_mock = MagicMock(return_value=({"name_en": "ShouldNotBeUsed"}, None))
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        extract_mock,
    )

    request_payload = {
        "univ_slug": "polyu",
        "year": 2026,
        "page_type_hint": "index",
        "selected_urls": ["https://www.polyu.edu.hk/study/pg/tpg/2026/62027-dfm-dpm"],
    }
    context = {
        "raw_pages": [
            {
                "url": "https://www.polyu.edu.hk/study/pg/tpg/2026/62027-dfm-dpm",
                "markdown": "\n",
                "char_count": 1,
                "links": [],
                "status_code": 200,
                "html": "<html>" + ("x" * 15000) + "</html>",
                "crawl_depth": 0,
                "from_browser": False,
                "selected_anchor_text": "Doctor of Financial Management",
            }
        ]
    }

    result = pipeline._stage_extract_structured(request_payload, context)

    extract_mock.assert_not_called()
    assert result["extracted_count"] == 0
    assert len(result["extract_errors"]) == 1
    assert "Anti-crawl suspected" in result["extract_errors"][0]["error"]


def test_extract_structured_allows_browser_html_in_detail_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        MagicMock,
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    extract_mock = MagicMock(return_value=({"name_en": "Doctor of Financial Management"}, None))
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        extract_mock,
    )

    request_payload = {
        "univ_slug": "polyu",
        "year": 2026,
        "page_type_hint": "detail",
        "selected_urls": [],
    }
    context = {
        "raw_pages": [
            {
                "url": "https://www.polyu.edu.hk/study/pg/tpg/2026/62027-dfm-dpm",
                "markdown": "\n",
                "char_count": 1,
                "links": [],
                "status_code": 200,
                "html": "<html>" + ("x" * 15000) + "</html>",
                "crawl_depth": 0,
                "from_browser": True,
                "selected_anchor_text": None,
            }
        ]
    }

    result = pipeline._stage_extract_structured(request_payload, context)

    extract_mock.assert_called_once()
    assert result["extracted_count"] == 1
    assert len(result["extract_errors"]) == 0


def test_extract_structured_skips_unresolved_program_name(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        MagicMock,
    )
    pipeline = IngestionPipeline(db_manager=MagicMock())
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.resolve_program_name",
        lambda **_kwargs: type(
            "Resolution",
            (),
            {
                "status": "unresolved",
                "name": "",
                "confidence": 0.0,
                "source": "none",
                "reason": "llm_low_confidence",
                "top_candidates": [],
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        lambda **_kwargs: ({"name_en": "Study with us"}, None),
    )

    result = pipeline._stage_extract_structured(
        {"univ_slug": "leeds", "year": 2026, "page_type_hint": "index", "selected_urls": ["https://x"]},
        {
            "raw_pages": [
                {
                    "url": "https://courses.leeds.ac.uk/k198/ai-for-business-msc",
                    "markdown": "# Study with us",
                    "char_count": 15,
                    "links": [],
                    "status_code": 200,
                    "html": "<html></html>",
                    "crawl_depth": 0,
                    "from_browser": True,
                    "selected_anchor_text": "AI for Business MSc",
                }
            ]
        },
    )

    assert result["extracted_count"] == 0
    assert len(result["unresolved_urls"]) == 1
    assert result["unresolved_urls"][0]["reason"] == "llm_low_confidence"


def test_persist_versioned_not_called_for_unresolved(monkeypatch) -> None:
    mock_db = MagicMock()
    pipeline = IngestionPipeline(db_manager=mock_db)
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        MagicMock,
    )
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.resolve_program_name",
        lambda **_kwargs: type(
            "Resolution",
            (),
            {
                "status": "unresolved",
                "name": "",
                "confidence": 0.0,
                "source": "none",
                "reason": "llm_low_confidence",
                "top_candidates": [],
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        lambda **_kwargs: ({"name_en": "Study with us"}, None),
    )

    extract_result = pipeline._stage_extract_structured(
        {"univ_slug": "leeds", "year": 2026, "page_type_hint": "index", "selected_urls": ["https://x"]},
        {
            "raw_pages": [
                {
                    "url": "https://courses.leeds.ac.uk/k198/ai-for-business-msc",
                    "markdown": "# Study with us",
                    "char_count": 15,
                    "links": [],
                    "status_code": 200,
                    "html": "<html></html>",
                    "crawl_depth": 0,
                    "from_browser": True,
                    "selected_anchor_text": "AI for Business MSc",
                }
            ]
        },
    )
    validate_result = pipeline._stage_validate_rules({"year": 2026}, extract_result)
    pipeline._stage_persist_versioned({"univ_slug": "leeds"}, validate_result)

    assert validate_result["validated_count"] == 0
    mock_db.upsert_program.assert_not_called()


@pytest.mark.asyncio
async def test_select_detail_urls_applies_candidate_taxonomy_filter(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_links_with_text",
        lambda _markdown, _base: [
            ("https://example.edu/programmes/msc-finance", "MSc Finance"),
            ("https://example.edu/programmes/msc-data-analytics", "MSc Data Analytics"),
            ("https://example.edu/apply", "Apply Now"),
        ],
    )
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.filter_links_by_llm",
        lambda *_args, **_kwargs: [
            "https://example.edu/programmes/msc-finance",
            "https://example.edu/programmes/msc-data-analytics",
            "https://example.edu/apply",
        ],
    )

    pipeline = IngestionPipeline(db_manager=MagicMock())
    taxonomy_service = MagicMock()

    def _match_signals(signals, top_k=1):
        _ = top_k
        joined = " ".join(signals).lower()
        if "finance" in joined:
            return [{"name_en": "Finance", "score": 0.93}]
        if "analytics" in joined:
            return [{"name_en": "Data Analytics", "score": 0.88}]
        return [{"name_en": "Noise", "score": 0.35}]

    taxonomy_service.match_signals.side_effect = _match_signals
    pipeline.taxonomy_service = taxonomy_service

    page = CrawlPageResult(
        url="https://example.edu/programmes",
        markdown="# Programmes",
        char_count=12,
        links=[],
    )
    scraper = MagicMock()
    scraper.router = MagicMock()

    urls, text_map = await pipeline._select_detail_urls(
        scraper,
        page,
        candidate_taxonomy_filter_enabled=True,
        candidate_taxonomy_filter_threshold=0.8,
        candidate_taxonomy_filter_top_k=2,
    )

    assert urls == [
        "https://example.edu/programmes/msc-finance",
        "https://example.edu/programmes/msc-data-analytics",
    ]
    assert set(text_map.keys()) == set(urls)


@pytest.mark.asyncio
async def test_select_detail_urls_taxonomy_filter_falls_back_when_all_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_links_with_text",
        lambda _markdown, _base: [
            ("https://example.edu/programmes/msc-finance", "MSc Finance"),
            ("https://example.edu/programmes/msc-data-analytics", "MSc Data Analytics"),
        ],
    )
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.filter_links_by_llm",
        lambda *_args, **_kwargs: [
            "https://example.edu/programmes/msc-finance",
            "https://example.edu/programmes/msc-data-analytics",
        ],
    )

    pipeline = IngestionPipeline(db_manager=MagicMock())
    taxonomy_service = MagicMock()
    taxonomy_service.match_signals.return_value = [{"name_en": "Noise", "score": 0.2}]
    pipeline.taxonomy_service = taxonomy_service

    page = CrawlPageResult(
        url="https://example.edu/programmes",
        markdown="# Programmes",
        char_count=12,
        links=[],
    )
    scraper = MagicMock()
    scraper.router = MagicMock()

    urls, text_map = await pipeline._select_detail_urls(
        scraper,
        page,
        candidate_taxonomy_filter_enabled=True,
        candidate_taxonomy_filter_threshold=0.95,
        candidate_taxonomy_filter_top_k=1,
    )

    assert urls == [
        "https://example.edu/programmes/msc-finance",
        "https://example.edu/programmes/msc-data-analytics",
    ]
    assert set(text_map.keys()) == set(urls)


def test_extract_structured_quarantines_silent_failures(monkeypatch) -> None:
    """When extract_program_data_from_page returns None for a URL (cleaner
    found nothing), the URL must be recorded in quarantine — not silently
    dropped to extract_errors with no DB trace.

    This was the bug surfaced by smoke-testing the Edinburgh accounting
    page: LLM returned nothing → 0 programs imported → quarantine empty.
    """
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent",
        MagicMock,
    )
    mock_db = MagicMock()
    pipeline = IngestionPipeline(db_manager=mock_db)

    # Simulate LLM returning nothing usable for this URL.
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        MagicMock(return_value=(None, "No structured data extracted")),
    )

    request_payload = {"univ_slug": "edinburgh", "year": 2026}
    context = {
        "raw_pages": [
            {
                "url": "https://study.ed.ac.uk/programmes/undergraduate/189",
                "markdown": "# Accounting and Business",
                "char_count": 100,
                "links": [],
                "status_code": 200,
                "html": "<html><body>page</body></html>",
                "crawl_depth": 0,
                "from_browser": False,
            }
        ]
    }

    pipeline._stage_extract_structured(request_payload, context)

    mock_db.upsert_quarantine.assert_called_once()
    kwargs = mock_db.upsert_quarantine.call_args.kwargs
    assert kwargs["university_slug"] == "edinburgh"
    assert kwargs["reason"].value == "extraction_failed"
    assert (
        kwargs["program_data"]["source_url"]
        == "https://study.ed.ac.uk/programmes/undergraduate/189"
    )
    assert kwargs["program_data"]["academic_year"] == 2026
