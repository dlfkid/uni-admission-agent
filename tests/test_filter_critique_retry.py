"""Tests for LLM filter critique retry — automatic recovery of dropped links.

When the first-pass LLM filter retains a suspiciously small fraction of
links on an index page, this retry re-prompts the LLM with the dropped
list and asks it to reconsider. The goal is to automatically recover
real program detail pages that the filter mistakenly rejected, without
requiring any user inspection.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.scrapers.link_parser import filter_links_critique_retry


def _link_pairs(n: int) -> list[tuple[str, str]]:
    return [
        (f"https://uni.edu/path{i}", f"Anchor {i}")
        for i in range(n)
    ]


def _mock_router(rescued_urls: list[str]) -> MagicMock:
    """Make a router whose generate() returns FilteredLinks with given URLs."""
    from src.scrapers.link_parser import FilteredLinks

    router = MagicMock()
    response = MagicMock()
    response.text = FilteredLinks(urls=rescued_urls).model_dump_json()
    router.generate.return_value = response
    return router


class TestFilterLinksCritiqueRetry:
    def test_returns_only_urls_originally_dropped(self) -> None:
        """The retry must never invent URLs that weren't in the original
        link set — only ones the first pass had rejected."""
        link_pairs = _link_pairs(10)
        kept = [link_pairs[0][0], link_pairs[1][0]]
        # LLM claims it found a totally new URL + one of the dropped ones.
        router = _mock_router([
            "https://invented.example.com/fake",  # never in link_pairs
            link_pairs[5][0],  # legitimate rescue
        ])

        recovered = filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        # Invented URL must be filtered out; only the genuine rescue stays.
        assert recovered == [link_pairs[5][0]]

    def test_returns_empty_when_no_dropped(self) -> None:
        """If everything was kept, there's nothing to rescue. No LLM call
        is needed."""
        link_pairs = _link_pairs(5)
        kept = [u for u, _ in link_pairs]
        router = MagicMock()

        recovered = filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        assert recovered == []
        router.generate.assert_not_called()

    def test_returns_empty_when_llm_rescues_nothing(self) -> None:
        link_pairs = _link_pairs(10)
        kept = [link_pairs[0][0]]
        router = _mock_router([])  # LLM looked again, found nothing

        recovered = filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        assert recovered == []

    def test_dedupes_rescued_urls(self) -> None:
        link_pairs = _link_pairs(10)
        kept = [link_pairs[0][0]]
        # LLM accidentally returns the same URL twice.
        router = _mock_router([link_pairs[5][0], link_pairs[5][0], link_pairs[7][0]])

        recovered = filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        # Dedupe — each unique rescued URL appears once.
        assert sorted(recovered) == sorted([link_pairs[5][0], link_pairs[7][0]])

    def test_caps_dropped_sample_size(self) -> None:
        """Prompt size protection: when many dropped URLs exist, the
        critique only samples up to the cap. The LLM sees a manageable
        subset, not all 500."""
        link_pairs = _link_pairs(200)
        kept = [link_pairs[0][0]]
        router = _mock_router([])

        filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
            max_sample=50,
        )

        prompt = router.generate.call_args[0][0]
        # The prompt should contain at most max_sample URLs from link_pairs.
        url_appearances = sum(
            1 for url, _ in link_pairs if url in prompt
        )
        assert url_appearances <= 50

    def test_critique_prompt_includes_anchor_text_and_retention(self) -> None:
        link_pairs = _link_pairs(10)
        kept = [link_pairs[0][0], link_pairs[1][0]]
        router = _mock_router([])

        filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        prompt = router.generate.call_args[0][0]
        # Prompt names the situation: retention rate is low.
        assert "20%" in prompt or "0.20" in prompt or "2 / 10" in prompt or "2/10" in prompt
        # Anchor text of a dropped link should appear so the LLM has context.
        assert "Anchor 5" in prompt
        # Explicit "quality over recall" instruction to mitigate over-claiming.
        assert "unsure" in prompt.lower() or "not sure" in prompt.lower()

    def test_llm_failure_returns_empty(self) -> None:
        """If the LLM call itself raises, recovery degrades to no-op rather
        than crashing the crawl."""
        link_pairs = _link_pairs(10)
        kept = [link_pairs[0][0]]
        router = MagicMock()
        router.generate.side_effect = RuntimeError("provider down")

        recovered = filter_links_critique_retry(
            router,
            link_pairs=link_pairs,
            kept_urls=kept,
            source_url="https://uni.edu/index",
        )

        assert recovered == []
