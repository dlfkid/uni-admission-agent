"""A detail page's navigation is whatever also appears, verbatim and in a
block, on the same site's index page.

HKBU's detail pages convert to ~117k chars of markdown of which ~90% is the
site's mega-menu; the programme itself is ~5k chars in the last of 8 chunks,
so 7 of every 8 extraction calls parsed navigation. Pattern lists ("Skip to
content", "MyEd login") are one university's furniture; dropping pure-link
lines deletes CityU's tuition and Lingnan's requirements, which those sites
publish inside links. The evidence that a line is boilerplate is the site's
own index page carrying the identical line.
"""

from pathlib import Path

import pytest

from src.scrapers.helpers import strip_shared_boilerplate

MENU = "\n".join(
    [
        "* [About](https://x/about)",
        "* [Admissions](https://x/adm)",
        "* [Programmes](https://x/prog)",
        "* [Scholarships & Fees](https://x/fees)",
        "Open main menu",
    ]
)
CONTENT = "\n".join(
    [
        "# Master of Arts in Communication",
        "Tuition Fee HK$180,000/programme",
        "Applicants should possess a bachelor's degree.",
    ]
)


def test_without_a_reference_the_page_is_returned_unchanged() -> None:
    page = MENU + "\n" + CONTENT
    assert strip_shared_boilerplate(page, "") == page
    assert strip_shared_boilerplate(page, None) == page


def test_a_menu_block_shared_with_the_index_is_removed() -> None:
    index = MENU + "\n# All programmes\n* [MA Communication](https://x/p/1)"
    out = strip_shared_boilerplate(MENU + "\n" + CONTENT, index)
    assert "Open main menu" not in out
    assert "Scholarships & Fees" not in out
    assert "Tuition Fee HK$180,000/programme" in out
    assert "Applicants should possess" in out


def test_an_isolated_shared_line_is_kept() -> None:
    """Edinburgh's index lists 'Full-time' and '4 years' for every degree; the
    same short facts on a detail page are content, not navigation."""
    index = MENU + "\nMA (Hons)\n4 years\nFull-time\n"
    page = "\n".join(
        ["# Accounting and Business", "MA (Hons)", "Duration:", "4 years", "Mode:", "Full-time"]
    )
    assert strip_shared_boilerplate(page, index) == page


def test_a_run_shorter_than_min_run_is_kept_and_at_min_run_is_dropped() -> None:
    index = "a\nb\nc\n"
    two = "unique-1\na\nb\nunique-2"
    three = "unique-1\na\nb\nc\nunique-2"
    assert strip_shared_boilerplate(two, index) == two
    assert strip_shared_boilerplate(three, index) == "unique-1\nunique-2"
    assert strip_shared_boilerplate(three, index, min_run=4) == three


def test_blank_lines_inside_a_shared_block_do_not_split_it() -> None:
    index = "a\nb\nc\n"
    page = "content\na\n\nb\n\n\nc\nmore content"
    assert strip_shared_boilerplate(page, index) == "content\nmore content"


def test_matching_ignores_surrounding_whitespace_only() -> None:
    index = "  * [A](u)\n* [B](u)\n* [C](u)"
    page = "* [A](u)\n  * [B](u)\n* [C](u)\nreal"
    assert strip_shared_boilerplate(page, index) == "real"


# ── every golden pair: nothing the LLM needs may disappear ────────────

_CASES = Path(__file__).parent.parent / "golden_samples" / "cases"
_GOLDEN = sorted(
    d.name for d in _CASES.iterdir() if (d / "index.md").exists() and (d / "detail.md").exists()
)
_KEYS = (
    "tuition", "fee", "ielts", "toefl", "requirement", "deadline",
    "duration", "full-time", "part-time", "bachelor",
)


@pytest.mark.parametrize("case", _GOLDEN)
def test_golden_detail_pages_keep_every_admission_signal(case: str) -> None:
    index = (_CASES / case / "index.md").read_text(encoding="utf-8")
    detail = (_CASES / case / "detail.md").read_text(encoding="utf-8")
    out = strip_shared_boilerplate(detail, index)
    assert len(out) <= len(detail)
    lost = [k for k in _KEYS if k in detail.lower() and k not in out.lower()]
    assert not lost, f"{case}: filter removed {lost}"


def test_hkbu_detail_page_fits_in_a_single_chunk() -> None:
    from src.agents.cleaner_agent import MAX_DETAIL_CHARS

    case = _CASES / "hkbu_masters_communication"
    index = (case / "index.md").read_text(encoding="utf-8")
    detail = (case / "detail.md").read_text(encoding="utf-8")
    assert len(detail) > 5 * MAX_DETAIL_CHARS  # the problem this fixes
    out = strip_shared_boilerplate(detail, index)
    assert len(out) <= MAX_DETAIL_CHARS
    assert "Tuition Fee" in out
    assert "Applicants should possess" in out
