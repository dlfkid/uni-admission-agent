"""Names-only harvest from an index page.

Course names live on the index page as heading-level markdown links
(## [Name MSc](url)); navigation lives as inline links. Harvesting names
from the index anchors directly means ZERO detail-page crawls and ZERO
per-page LLM calls — and the names are exactly the course titles (with
degree suffix), not detail-page body text. This is both the correctness
fix and the token-cost win.
"""
from __future__ import annotations

from pathlib import Path

from src.services.index_name_harvest import harvest_index_program_names

_INDEX_MD = (
    Path(__file__).resolve().parent.parent
    / "golden_samples" / "cases" / "leeds_masters_ai_business" / "index.md"
)

# The 15 real course cards on the Leeds masters index snapshot, in order.
EXPECTED_LEEDS = [
    "Accounting and Finance MSc",
    "Advanced Chemical Engineering MSc",
    "Advanced Clinical Practice MSc",
    "Advanced Clinical Practice (Apprenticeship) MSc",
    "Advanced Computer Science MSc",
    "Advanced Computer Science (Artificial Intelligence) MSc",
    "Advanced Computer Science (Cloud Computing) MSc",
    "Advanced Computer Science (Data Analytics) MSc",
    "Advanced Manufacturing and Automation MSc",
    "Advanced Mechanical Engineering MSc (Eng)",
    "Advertising and Design MA",
    "Aerospace Engineering MSc",
    "AI Ethics and Society MSc",
    "AI for Business MSc",
    "Applied and Professional Ethics PGDip",
]


def _harvest():
    md = _INDEX_MD.read_text(encoding="utf-8")
    return harvest_index_program_names(
        md, base_url="https://courses.leeds.ac.uk/course-search/masters-courses"
    )


def test_harvests_exactly_the_15_course_names_with_suffix():
    names = [item["name_en"] for item in _harvest()]
    assert names == EXPECTED_LEEDS


def test_no_navigation_or_faculty_links_leak():
    names = [item["name_en"] for item in _harvest()]
    for junk in ("Services A-Z", "Faculty of Business", "Minerva",
                 "University of Leeds homepage", "Skip to main content"):
        assert junk not in names


def test_each_item_has_a_source_url():
    items = _harvest()
    assert items, "expected at least one course"
    for item in items:
        assert item["source_url"].startswith("https://courses.leeds.ac.uk/")


def test_no_duplicate_source_urls():
    items = _harvest()
    urls = [it["source_url"] for it in items]
    assert len(urls) == len(set(urls))
