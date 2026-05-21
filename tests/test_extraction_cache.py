"""Tests for src/agents/extraction_cache.py.

Unit tests for the persistent extraction cache used by LLMCleanerAgent
to avoid redundant LLM calls on identical markdown inputs.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from src.agents.cleaner_agent import ParsedProgramData
from src.agents.extraction_cache import (
    ExtractionCacheRepo,
    compute_cache_key,
)


class TestComputeCacheKey:
    def test_same_inputs_same_key(self) -> None:
        a = compute_cache_key(
            markdown="hello world",
            name_hints=["MSc Finance"],
            academic_year=2026,
            version="v1",
        )
        b = compute_cache_key(
            markdown="hello world",
            name_hints=["MSc Finance"],
            academic_year=2026,
            version="v1",
        )
        assert a == b

    def test_trailing_whitespace_normalized(self) -> None:
        a = compute_cache_key(
            markdown="hello world",
            name_hints=None,
            academic_year=0,
            version="v1",
        )
        b = compute_cache_key(
            markdown="hello world   \n\n  ",
            name_hints=None,
            academic_year=0,
            version="v1",
        )
        assert a == b

    def test_crlf_normalized_to_lf(self) -> None:
        a = compute_cache_key(
            markdown="line1\nline2",
            name_hints=None,
            academic_year=0,
            version="v1",
        )
        b = compute_cache_key(
            markdown="line1\r\nline2",
            name_hints=None,
            academic_year=0,
            version="v1",
        )
        assert a == b

    def test_different_markdown_different_key(self) -> None:
        a = compute_cache_key(markdown="x", name_hints=None, academic_year=0, version="v1")
        b = compute_cache_key(markdown="y", name_hints=None, academic_year=0, version="v1")
        assert a != b

    def test_different_name_hints_different_key(self) -> None:
        a = compute_cache_key(
            markdown="x", name_hints=["A"], academic_year=0, version="v1"
        )
        b = compute_cache_key(
            markdown="x", name_hints=["B"], academic_year=0, version="v1"
        )
        assert a != b

    def test_name_hints_order_insensitive(self) -> None:
        a = compute_cache_key(
            markdown="x", name_hints=["A", "B"], academic_year=0, version="v1"
        )
        b = compute_cache_key(
            markdown="x", name_hints=["B", "A"], academic_year=0, version="v1"
        )
        assert a == b

    def test_none_hints_equals_empty_hints(self) -> None:
        a = compute_cache_key(markdown="x", name_hints=None, academic_year=0, version="v1")
        b = compute_cache_key(markdown="x", name_hints=[], academic_year=0, version="v1")
        assert a == b

    def test_different_year_different_key(self) -> None:
        a = compute_cache_key(markdown="x", name_hints=None, academic_year=2026, version="v1")
        b = compute_cache_key(markdown="x", name_hints=None, academic_year=2027, version="v1")
        assert a != b

    def test_different_version_different_key(self) -> None:
        a = compute_cache_key(markdown="x", name_hints=None, academic_year=0, version="v1")
        b = compute_cache_key(markdown="x", name_hints=None, academic_year=0, version="v2")
        assert a != b

    def test_key_is_hex_string(self) -> None:
        key = compute_cache_key(
            markdown="x", name_hints=None, academic_year=0, version="v1"
        )
        assert isinstance(key, str)
        assert len(key) == 64  # sha256 hex digest
        int(key, 16)  # must be valid hex


@pytest.fixture(name="session")
def fixture_session():
    """In-memory SQLite session with extraction_cache table created.

    Imports admission/requirement modules so SQLAlchemy can resolve every
    relationship in the global mapper registry before we touch any table.
    """
    import src.models.admission  # noqa: F401
    import src.models.requirement  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[
        SQLModel.metadata.tables["extraction_cache"]
    ])
    with Session(engine) as session:
        yield session


def _sample_parsed_data(faculty: str = "Faculty of Engineering") -> ParsedProgramData:
    return ParsedProgramData(faculty=faculty)


class TestExtractionCacheRepo:
    def test_get_returns_none_when_missing(self, session: Session) -> None:
        repo = ExtractionCacheRepo(session)
        assert repo.get("nonexistent-key") is None

    def test_put_then_get_roundtrip(self, session: Session) -> None:
        repo = ExtractionCacheRepo(session)
        data = _sample_parsed_data()
        repo.put("k1", data)
        loaded = repo.get("k1")
        assert loaded is not None
        assert loaded.model_dump() == data.model_dump()

    def test_put_is_idempotent(self, session: Session) -> None:
        repo = ExtractionCacheRepo(session)
        repo.put("k1", _sample_parsed_data())
        # Second put with same key must not raise (idempotent upsert).
        repo.put("k1", _sample_parsed_data())
        assert repo.get("k1") is not None

    def test_put_updates_payload_on_same_key(self, session: Session) -> None:
        repo = ExtractionCacheRepo(session)
        repo.put("k1", _sample_parsed_data(faculty="Faculty of Engineering"))
        repo.put("k1", _sample_parsed_data(faculty="School of Business"))
        loaded = repo.get("k1")
        assert loaded is not None
        assert loaded.faculty == "School of Business"

    def test_corrupted_payload_returns_none(self, session: Session) -> None:
        """If a row exists but its JSON cannot be parsed, get() must
        return None rather than crashing — caller will treat as miss."""
        from src.agents.extraction_cache import ExtractionCacheEntry
        session.add(ExtractionCacheEntry(cache_key="bad", payload="not-json"))
        session.commit()
        repo = ExtractionCacheRepo(session)
        assert repo.get("bad") is None


class TestCleanerCacheIntegration:
    """LLMCleanerAgent uses the cache when one is wired in."""

    def _make_agent_with_cache(self, session: Session, parse_return):
        from unittest.mock import MagicMock

        from src.agents.cleaner_agent import LLMCleanerAgent

        agent = LLMCleanerAgent(
            router=MagicMock(),
            cache_repo=ExtractionCacheRepo(session),
        )
        # Replace the LLM-touching internals with a spy.
        spy = MagicMock(return_value=parse_return)
        agent._parse_single_pass = spy  # type: ignore[method-assign]
        agent._parse_rolling_chunks = spy  # type: ignore[method-assign]
        return agent, spy

    def test_cache_miss_invokes_parser_and_stores_result(self, session: Session) -> None:
        expected = _sample_parsed_data(faculty="Faculty of Law")
        agent, spy = self._make_agent_with_cache(session, expected)

        result = agent.clean_markdown(
            markdown="page body",
            source_url="https://e.edu/law",
            name_hints=["LLM"],
            academic_year=2026,
        )

        assert result is not None
        assert result.faculty == "Faculty of Law"
        assert spy.call_count == 1, "cache miss must call the parser exactly once"

        # And the result was persisted: a second cleaner with the same cache
        # but a parser that would crash if called should still return the
        # cached value.
        from unittest.mock import MagicMock

        from src.agents.cleaner_agent import LLMCleanerAgent

        agent2 = LLMCleanerAgent(
            router=MagicMock(),
            cache_repo=ExtractionCacheRepo(session),
        )
        crash_spy = MagicMock(
            side_effect=AssertionError("parser must not be called on cache hit")
        )
        agent2._parse_single_pass = crash_spy  # type: ignore[method-assign]
        agent2._parse_rolling_chunks = crash_spy  # type: ignore[method-assign]

        cached = agent2.clean_markdown(
            markdown="page body",
            source_url="https://e.edu/law",
            name_hints=["LLM"],
            academic_year=2026,
        )
        assert cached is not None
        assert cached.faculty == "Faculty of Law"
        assert crash_spy.call_count == 0, "cache hit must skip the parser"

    def test_no_cache_means_no_lookup_no_store(self, session: Session) -> None:
        """When cache_repo is not provided, behavior is identical to today."""
        from unittest.mock import MagicMock

        from src.agents.cleaner_agent import LLMCleanerAgent

        expected = _sample_parsed_data()
        agent = LLMCleanerAgent(router=MagicMock())  # no cache_repo
        agent._parse_single_pass = MagicMock(return_value=expected)  # type: ignore[method-assign]

        result = agent.clean_markdown(markdown="x", source_url="u", academic_year=2026)
        assert result is not None

        # Cache must remain empty since no repo was wired.
        from sqlmodel import select as sm_select

        from src.agents.extraction_cache import ExtractionCacheEntry

        rows = session.exec(sm_select(ExtractionCacheEntry)).all()
        assert rows == []

    def test_parser_returns_none_is_not_cached(self, session: Session) -> None:
        """Negative results are not poisoned into the cache."""
        agent, spy = self._make_agent_with_cache(session, None)

        result = agent.clean_markdown(markdown="x", source_url="u", academic_year=2026)
        assert result is None
        assert spy.call_count == 1

        # No cache entry should have been written for a None result.
        from sqlmodel import select as sm_select

        from src.agents.extraction_cache import ExtractionCacheEntry

        rows = session.exec(sm_select(ExtractionCacheEntry)).all()
        assert rows == []
