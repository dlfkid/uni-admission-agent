"""Tests for the index→detail extraction audit table.

The audit table answers the question: "Did we find every program on the
index page, or did our filter drop legitimate links?" It records the
funnel — raw links found vs. kept after LLM filter vs. successfully
extracted vs. quarantined — per index crawl.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from src.models.extraction_audit import ExtractionAudit
from src.storage.audit_repo import ExtractionAuditRepo


@pytest.fixture(name="session")
def fixture_session():
    """In-memory SQLite session with extraction_audit table created."""
    import src.models.admission  # noqa: F401
    import src.models.requirement  # noqa: F401
    import src.models.extraction_audit  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine, tables=[SQLModel.metadata.tables["extraction_audit"]]
    )
    with Session(engine) as session:
        yield session


def _sample_funnel(**overrides) -> dict:
    base = {
        "university_slug": "hku",
        "academic_year": 2026,
        "index_url": "https://www.hku.hk/programs",
        "raw_link_count": 87,
        "llm_filtered_count": 23,
        "candidate_count": 22,
        "extracted_count": 11,
        "quarantined_count": 6,
        "job_uid": "job-abc-123",
    }
    base.update(overrides)
    return base


class TestExtractionAuditModel:
    def test_model_persists(self, session: Session) -> None:
        entry = ExtractionAudit(**_sample_funnel())
        session.add(entry)
        session.commit()

        loaded = session.exec(select(ExtractionAudit)).one()
        assert loaded.university_slug == "hku"
        assert loaded.raw_link_count == 87
        assert loaded.extracted_count == 11
        assert loaded.id is not None


class TestExtractionAuditRepo:
    def test_record_inserts_new(self, session: Session) -> None:
        repo = ExtractionAuditRepo(session)
        repo.record(**_sample_funnel())
        rows = session.exec(select(ExtractionAudit)).all()
        assert len(rows) == 1
        assert rows[0].raw_link_count == 87

    def test_record_appends_history_per_call(self, session: Session) -> None:
        """Each crawl is an event; we keep all of them so the user can see
        whether things are getting better or worse over time."""
        repo = ExtractionAuditRepo(session)
        repo.record(**_sample_funnel(extracted_count=11))
        repo.record(**_sample_funnel(extracted_count=15))
        repo.record(**_sample_funnel(extracted_count=18))
        rows = session.exec(select(ExtractionAudit)).all()
        assert len(rows) == 3
        assert [r.extracted_count for r in rows] == [11, 15, 18]

    def test_list_for_filters_by_university(self, session: Session) -> None:
        repo = ExtractionAuditRepo(session)
        repo.record(**_sample_funnel(university_slug="hku"))
        repo.record(**_sample_funnel(university_slug="ust"))
        repo.record(**_sample_funnel(university_slug="hku", academic_year=2027))

        hku_rows = repo.list_for(university_slug="hku")
        assert len(hku_rows) == 2

        hku_2026 = repo.list_for(university_slug="hku", year=2026)
        assert len(hku_2026) == 1

        all_rows = repo.list_for()
        assert len(all_rows) == 3

    def test_list_for_orders_newest_first(self, session: Session) -> None:
        """Recent crawls are usually what the user cares about — surface them
        first."""
        repo = ExtractionAuditRepo(session)
        repo.record(**_sample_funnel(extracted_count=1))
        repo.record(**_sample_funnel(extracted_count=2))
        repo.record(**_sample_funnel(extracted_count=3))
        rows = repo.list_for(university_slug="hku")
        assert [r.extracted_count for r in rows] == [3, 2, 1]

    def test_list_for_respects_limit(self, session: Session) -> None:
        repo = ExtractionAuditRepo(session)
        for i in range(10):
            repo.record(**_sample_funnel(extracted_count=i))
        rows = repo.list_for(university_slug="hku", limit=3)
        assert len(rows) == 3


@pytest.fixture(name="full_session")
def fixture_full_session():
    """In-memory SQLite session with both audit + audit_link tables."""
    import src.models.admission  # noqa: F401
    import src.models.requirement  # noqa: F401
    import src.models.extraction_audit  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SQLModel.metadata.tables["extraction_audit"],
            SQLModel.metadata.tables["extraction_audit_link"],
        ],
    )
    with Session(engine) as session:
        yield session


class TestExtractionAuditLinkModel:
    def test_model_persists(self, full_session: Session) -> None:
        from src.models.extraction_audit import ExtractionAuditLink

        # Need a parent audit row first.
        repo = ExtractionAuditRepo(full_session)
        parent = repo.record(**_sample_funnel())

        link = ExtractionAuditLink(
            audit_id=parent.id,
            url="https://www.hku.hk/about",
            anchor_text="About HKU",
            stage_dropped="llm_filter",
        )
        full_session.add(link)
        full_session.commit()

        rows = full_session.exec(select(ExtractionAuditLink)).all()
        assert len(rows) == 1
        assert rows[0].url == "https://www.hku.hk/about"
        assert rows[0].stage_dropped == "llm_filter"


class TestAuditRepoDroppedLinks:
    def test_record_with_dropped_links_inserts_link_rows(
        self, full_session: Session
    ) -> None:
        """When recording an audit, any dropped_links payload should be
        stored as separate audit_link rows."""
        from src.models.extraction_audit import ExtractionAuditLink

        repo = ExtractionAuditRepo(full_session)
        dropped = [
            {"url": "https://hku.hk/about", "anchor_text": "About",
             "stage_dropped": "llm_filter"},
            {"url": "https://hku.hk/contact", "anchor_text": "Contact",
             "stage_dropped": "llm_filter"},
            {"url": "https://hku.hk/news", "anchor_text": "News",
             "stage_dropped": "taxonomy_filter"},
        ]
        repo.record(**_sample_funnel(), dropped_links=dropped)

        link_rows = full_session.exec(select(ExtractionAuditLink)).all()
        assert len(link_rows) == 3
        assert {r.stage_dropped for r in link_rows} == {"llm_filter", "taxonomy_filter"}

    def test_list_dropped_links_returns_grouped(
        self, full_session: Session
    ) -> None:
        repo = ExtractionAuditRepo(full_session)
        audit = repo.record(
            **_sample_funnel(),
            dropped_links=[
                {"url": "https://a", "anchor_text": "A",
                 "stage_dropped": "llm_filter"},
                {"url": "https://b", "anchor_text": "B",
                 "stage_dropped": "taxonomy_filter"},
            ],
        )

        links = repo.list_dropped_links(audit_id=audit.id)
        assert len(links) == 2
        assert {l.url for l in links} == {"https://a", "https://b"}

    def test_record_with_empty_dropped_links_writes_no_link_rows(
        self, full_session: Session
    ) -> None:
        from src.models.extraction_audit import ExtractionAuditLink

        repo = ExtractionAuditRepo(full_session)
        repo.record(**_sample_funnel(), dropped_links=[])

        rows = full_session.exec(select(ExtractionAuditLink)).all()
        assert rows == []
