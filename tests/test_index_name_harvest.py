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


def test_trailing_duration_suffix_is_stripped_manchester_style():
    """Manchester merges name/degree/duration columns into the link text
    ([Accounting MSc (1 year)](...)). The trailing duration is not part of
    the program name and must be stripped — but a real parenthetical like
    (Paediatrics) must survive."""
    md = (
        "[Accounting MSc (1 year)](https://www.manchester.ac.uk/study/masters/courses/list/10867/msc-accounting/)\n"
        "[Adult Nursing MSc (2 years)](https://www.manchester.ac.uk/study/masters/courses/list/18749/msc-adult-nursing/)\n"
        "[Advanced Clinical Practice (Paediatrics) MSc (3 years)](https://www.manchester.ac.uk/study/masters/courses/list/12526/x/)\n"
        "[Advanced Clinical Optometric Practice MSc](https://www.manchester.ac.uk/study/masters/courses/list/18940/y/)\n"
        "[Linguistics MA (1 or 2 years)](https://www.manchester.ac.uk/study/masters/courses/list/18941/z/)\n"
        "[Human Rights (Standard Route) MA (1 or 2 years)](https://www.manchester.ac.uk/study/masters/courses/list/18942/w/)\n"
    )
    names = [it["name_en"] for it in harvest_index_program_names(md, base_url="https://www.manchester.ac.uk/")]
    assert names == [
        "Accounting MSc",
        "Adult Nursing MSc",
        "Advanced Clinical Practice (Paediatrics) MSc",
        "Advanced Clinical Optometric Practice MSc",
        "Linguistics MA",
        "Human Rights (Standard Route) MA",
    ]


def test_inline_degree_suffix_links_are_harvested_ucl_style():
    """UCL lists degrees as INLINE links (not headings) whose anchor ends
    with a degree token: [Anthropology BSc](.../degrees/anthropology-bsc).
    These must be harvested; nav links (Search, Browse by subject) must not."""
    md = (
        "[Skip to main content](https://www.ucl.ac.uk/x#main)\n"
        "[Search](https://www.ucl.ac.uk/x#tab1)\n"
        "[Browse by subject](https://www.ucl.ac.uk/x#tab2)\n"
        "[Ancient History BA](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/ancient-history-ba)\n"
        "[Anthropology BSc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/anthropology-bsc)\n"
        "[Architecture MSci](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/architecture-msci)\n"
        "[Arts and Sciences BASc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/arts-sciences-basc)\n"
    )
    names = [it["name_en"] for it in harvest_index_program_names(md, base_url="https://www.ucl.ac.uk/")]
    assert names == [
        "Ancient History BA",
        "Anthropology BSc",
        "Architecture MSci",
        "Arts and Sciences BASc",
    ]


def test_leeds_heading_harvest_unaffected_by_inline_support():
    """Enabling inline degree-suffix capture must not change the Leeds
    heading-based result — still exactly the 15 course cards."""
    names = [item["name_en"] for item in _harvest()]
    assert names == EXPECTED_LEEDS


def test_polyu_blob_anchor_name_is_extracted():
    """PolyU merges code | entry | mode-duration | NAME - DEGREE - Master… |
    Chinese | deadlines into one link text. The English program name sits
    between the duration and the ' - <Degree> - Master' marker; extract it
    (with the degree token) and drop the rest."""
    md = (
        "[ 02022 | Sept 2026 Entry  Full-time - 1 year  Business Management - MSc - Master of Science  "
        "商業管理理學碩士學位 Local Application Deadline: 30 Apr 2026 ]"
        "(https://www.polyu.edu.hk/study/pg/tpg/2026/02022)\n"
        "[ 02029 | Sept 2026 Entry  Mixed Mode - 1 year (Full-time)2 years (Part-time)  "
        "Asset and Wealth Management - MSc - Master of Science  資產和財富管理理學碩士學位 "
        "Local Application Deadline: 30 Apr 2026 ]"
        "(https://www.polyu.edu.hk/study/pg/tpg/2026/02029-dfm)\n"
        "[ 02021 | Sept 2026 Entry  Full-time - 1 year including summer term  "
        "Business Administration - Master - Master (of)  工商管理碩士學位 "
        "Local Application Deadline: 30 Apr 2026 ]"
        "(https://www.polyu.edu.hk/study/pg/tpg/2026/02021)\n"
    )
    names = [it["name_en"] for it in harvest_index_program_names(md, base_url="https://www.polyu.edu.hk/")]
    assert names == [
        "Business Management MSc",
        "Asset and Wealth Management MSc",
        "Business Administration Master",
    ]


def test_same_name_different_url_dedupes_to_one():
    """Edinburgh lists each course twice under URLs that differ only by a
    /2026/ year segment. For a names-only set the anchor name is reliable,
    so identical names collapse to one entry."""
    md = (
        "### [Accounting and Business MA (Hons)]"
        "(https://study.ed.ac.uk/programmes/undergraduate/2026/189-accounting-and-business)\n"
        "### [Accounting and Business MA (Hons)]"
        "(https://study.ed.ac.uk/programmes/undergraduate/189-accounting-and-business)\n"
        "### [Acoustics and Music Technology BSc (Hons)]"
        "(https://study.ed.ac.uk/programmes/undergraduate/2026/077-acoustics)\n"
    )
    items = harvest_index_program_names(md, base_url="https://study.ed.ac.uk/")
    names = [it["name_en"] for it in items]
    assert names == [
        "Accounting and Business MA (Hons)",
        "Acoustics and Music Technology BSc (Hons)",
    ]
