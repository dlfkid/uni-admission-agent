import pytest

from src.agent_runtime.base import AgentRequest
from src.agent_runtime.pydanticai_runtime import PydanticAIRuntime
from src.services.crawler import CrawlResult


@pytest.mark.asyncio
async def test_runtime_returns_wait_user_selection_with_onhold_items(monkeypatch):
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [
                {"url": "https://x/p1", "text": "P1"},
                {"url": "https://x/p2", "text": "P2"},
                {"url": "https://x/p3", "text": "P3"},
            ],
        }

    class _DummyPipeline:
        def rank_index_candidates(
            self,
            links,
            *,
            keep_threshold,
            auto_run_threshold,
            top_k,
        ):
            del links, keep_threshold, auto_run_threshold, top_k
            return [
                {
                    "url": "https://x/p1",
                    "text": "P1",
                    "taxonomy_score": 0.97,
                    "program_name_inferred": "Program One",
                    "auto_run_eligible": True,
                },
                {
                    "url": "https://x/p2",
                    "text": "P2",
                    "taxonomy_score": 0.81,
                    "program_name_inferred": "Program Two",
                    "auto_run_eligible": False,
                },
                {
                    "url": "https://x/p3",
                    "text": "P3",
                    "taxonomy_score": 0.63,
                    "program_name_inferred": "Program Three",
                    "auto_run_eligible": False,
                },
            ]

    async def _fake_crawl_url(**_kwargs):
        del _kwargs
        return CrawlResult(
            imported_count=1,
            univ_slug="uom",
            year=2026,
            review_items=[{"program_id": 1, "name_en": "Program One"}],
            review_token="token-1",
        )

    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.IngestionPipeline", _DummyPipeline)
    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.crawl_url", _fake_crawl_url)

    runtime = PydanticAIRuntime()
    result = await runtime.run(
        AgentRequest(
            task="crawl",
            payload={
                "url": "https://x/index",
                "univ_slug": "uom",
                "year": 2026,
                "page_type_hint": "index",
                "policy_profile": {
                    "taxonomy_keep_threshold": 0.6,
                    "taxonomy_auto_threshold": 0.9,
                },
            },
            context={"autonomous": True},
        )
    )

    assert result.status == "wait_user_selection"
    assert result.output["auto_processed_count"] == 1
    assert result.output["onhold_count"] == 2
    assert result.output["onhold_items"][0]["confidence"] >= result.output["onhold_items"][1]["confidence"]
    assert [item["index"] for item in result.output["onhold_items"]] == [1, 2]


@pytest.mark.asyncio
async def test_runtime_done_when_no_onhold_candidates(monkeypatch):
    async def _fake_analyze_url_candidates(**_kwargs):
        return {
            "page_type": "index",
            "links": [{"url": "https://x/p1", "text": "P1"}],
        }

    class _DummyPipeline:
        def rank_index_candidates(self, links, *, keep_threshold, auto_run_threshold, top_k):
            del links, keep_threshold, auto_run_threshold, top_k
            return [
                {
                    "url": "https://x/p1",
                    "text": "P1",
                    "taxonomy_score": 0.97,
                    "auto_run_eligible": True,
                }
            ]

    async def _fake_crawl_url(**_kwargs):
        del _kwargs
        return CrawlResult(imported_count=1, univ_slug="uom", year=2026)

    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.analyze_url_candidates", _fake_analyze_url_candidates)
    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.IngestionPipeline", _DummyPipeline)
    monkeypatch.setattr("src.agent_runtime.pydanticai_runtime.crawl_url", _fake_crawl_url)

    runtime = PydanticAIRuntime()
    result = await runtime.run(
        AgentRequest(
            task="crawl",
            payload={"url": "https://x/index", "univ_slug": "uom", "year": 2026},
            context={"autonomous": True},
        )
    )

    assert result.status == "done"
    assert result.output["onhold_count"] == 0
