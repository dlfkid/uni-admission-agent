"""Tests for the name self-critique flow.

Components:
- `_name_looks_suspect`: detects when an extracted program name is
  obviously wrong (matches noise filter OR doesn't share enough tokens
  with any high-confidence taxonomy match).
- `refine_name_with_critique`: re-prompts the LLM specifically to find
  the real program name, given the bad first guess and taxonomy hints.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.scrapers.name_critique import (
    _name_looks_suspect,
    refine_name_with_critique,
)


# ---------------------------------------------------------------------------
# _name_looks_suspect
# ---------------------------------------------------------------------------


class TestNameLooksSuspect:
    def test_noise_name_is_suspect_regardless_of_taxonomy(self) -> None:
        # No taxonomy context — noise regex alone is enough.
        assert _name_looks_suspect("Course Search", []) is True
        assert _name_looks_suspect("Faculty of Business", []) is True
        assert _name_looks_suspect("Apply Now", []) is True

    def test_short_name_is_suspect(self) -> None:
        assert _name_looks_suspect("", []) is True
        assert _name_looks_suspect("X", []) is True
        assert _name_looks_suspect("AB", []) is True

    def test_normal_name_with_no_taxonomy_is_not_suspect(self) -> None:
        """Without taxonomy context, only noise-regex filtering applies."""
        assert _name_looks_suspect("MSc Finance", []) is False
        assert _name_looks_suspect("Some Random Program Name", []) is False

    def test_name_aligned_with_taxonomy_is_not_suspect(self) -> None:
        """When taxonomy has a high-confidence match and the LLM name
        shares tokens with it, the name is accepted even as a variant."""
        matches = [
            {"name_en": "MSc Finance", "score": 0.92},
            {"name_en": "MSc Accounting", "score": 0.50},
        ]
        # Exact match
        assert _name_looks_suspect("MSc Finance", matches) is False
        # Reasonable variant — extra words OK
        assert _name_looks_suspect("MSc Finance with AI Specialization", matches) is False
        # Different casing
        assert _name_looks_suspect("msc finance", matches) is False

    def test_name_far_from_high_confidence_taxonomy_is_suspect(self) -> None:
        """When taxonomy strongly suggests this page is 'MSc Finance' but
        the LLM extracted 'Faculty of Business' (zero token overlap with
        the high-confidence match), the name is suspect."""
        matches = [
            {"name_en": "MSc Finance", "score": 0.92},
            {"name_en": "MSc Accounting", "score": 0.50},
        ]
        # Note: "Faculty of Business" would be caught by noise regex first,
        # but the taxonomy-alignment check is what catches non-noise names
        # that are nonetheless far from what taxonomy says the page is about.
        # Use a non-noise example: a different real-sounding program name.
        assert _name_looks_suspect("Bachelor of Mechanical Engineering", matches) is True

    def test_taxonomy_below_threshold_does_not_trigger_alignment_check(self) -> None:
        """When all taxonomy scores are low (no confident match), the
        alignment check is skipped — we don't know what the page should
        be about, so we can't claim LLM picked wrong."""
        matches = [
            {"name_en": "MSc Finance", "score": 0.20},
            {"name_en": "MSc Accounting", "score": 0.15},
        ]
        # Even a totally unrelated name passes — taxonomy is too weak to judge.
        assert _name_looks_suspect("MSc Marine Biology", matches) is False


# ---------------------------------------------------------------------------
# refine_name_with_critique
# ---------------------------------------------------------------------------


def _mock_router_returning(name: str | None):
    """Make a router whose generate() returns a RefinedName JSON with the given name."""
    from src.scrapers.name_critique import RefinedName

    router = MagicMock()
    response = MagicMock()
    response.text = RefinedName(name=name).model_dump_json()
    router.generate.return_value = response
    return router


class TestRefineNameWithCritique:
    def test_returns_refined_name_from_llm(self) -> None:
        router = _mock_router_returning("MSc Finance with AI")
        result = refine_name_with_critique(
            router=router,
            markdown="# MSc Finance with AI\n\nProgram details...",
            bad_name="Faculty of Business",
            taxonomy_hints=["MSc Finance", "MSc Accounting"],
            source_url="https://e.edu/finance-ai",
        )
        assert result == "MSc Finance with AI"

    def test_returns_none_when_llm_returns_null(self) -> None:
        router = _mock_router_returning(None)
        result = refine_name_with_critique(
            router=router,
            markdown="page body with no program",
            bad_name="Course Search",
            taxonomy_hints=["MSc Finance"],
            source_url="https://e.edu",
        )
        assert result is None

    def test_returns_none_when_llm_raises(self) -> None:
        router = MagicMock()
        router.generate.side_effect = RuntimeError("provider down")
        result = refine_name_with_critique(
            router=router,
            markdown="x",
            bad_name="bad",
            taxonomy_hints=["MSc Finance"],
            source_url="u",
        )
        assert result is None

    def test_returns_none_when_refined_name_is_also_noise(self) -> None:
        """If the LLM stubbornly returns another navigation label, refuse —
        don't let critique degenerate into 'any non-null answer wins'."""
        router = _mock_router_returning("Apply Now")
        result = refine_name_with_critique(
            router=router,
            markdown="x",
            bad_name="Course Search",
            taxonomy_hints=["MSc Finance"],
            source_url="u",
        )
        assert result is None

    def test_prompt_includes_bad_name_and_taxonomy_hints(self) -> None:
        """The critique prompt must include the failed name (so LLM knows
        what to NOT do) and the taxonomy hints (so LLM knows what kind
        of name to look for)."""
        router = _mock_router_returning("MSc Finance")
        refine_name_with_critique(
            router=router,
            markdown="page body",
            bad_name="Faculty of Business",
            taxonomy_hints=["MSc Finance", "MSc Accounting"],
            source_url="https://e.edu/p",
        )
        prompt = router.generate.call_args[0][0]
        # The bad name must appear (so LLM sees what failed).
        assert "Faculty of Business" in prompt
        # The taxonomy hints must appear (so LLM knows what kind of program this is).
        assert "MSc Finance" in prompt
        assert "MSc Accounting" in prompt
        # Explicit null escape valve to prevent hallucination.
        assert "null" in prompt.lower()
        # Page content must be present (so LLM can re-extract).
        assert "page body" in prompt

    def test_no_taxonomy_hints_still_works(self) -> None:
        """When the noise regex triggered without taxonomy guidance,
        refine should still try — using only the bad-name signal."""
        router = _mock_router_returning("MSc Real Program")
        result = refine_name_with_critique(
            router=router,
            markdown="page",
            bad_name="Apply Now",
            taxonomy_hints=[],
            source_url="u",
        )
        assert result == "MSc Real Program"
