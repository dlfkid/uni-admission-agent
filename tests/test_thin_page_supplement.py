"""Tests for the thin-page supplement mechanism (hub/stub detail layout).

Locks in the Lingnan battle-test finding: official "detail pages" that are
routing stubs (name + application period only), with real content on an
index-row sibling link's site, one more hop down its navigation.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.thin_page_supplement import (
    MAX_TOTAL_SUPPLEMENT_CHARS,
    build_sibling_link_map,
    dedupe_same_row_candidates,
    expand_thin_page,
    is_thin_program_result,
    rank_admission_sub_links,
)


# The exact row shape from Lingnan's live index page (programme link +
# Apply Now portal + Visit Website department link on one markdown line).
_LN_ROW = (
    "Programme Name: [ Master of Arts in Artificial Intelligence and the Future ]"
    "(https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-artificial-intelligence-and-the-future)"
    " |  Medium of Instruction: English  |  Mode of Study: Full-time / Part-time"
    " |  [ Apply Now ](https://apply.ln.edu.hk/)"
    " [ Visit Website ](https://www.ln.edu.hk/philoso/hkcrc/maaif)"
)
_LN_STUB_URL = (
    "https://www.ln.edu.hk/sgs/programmes-on-offer/"
    "master-of-arts-in-artificial-intelligence-and-the-future"
)


# ── is_thin_program_result ───────────────────────────────────────────

def test_thin_when_only_name_and_deadline() -> None:
    """The routing-stub fingerprint: name + deadline, nothing substantive.
    This shape PASSES the persist-stage quality gate (deadline present),
    which is exactly why thin detection must be stricter than the gate."""
    assert is_thin_program_result(
        {"name_en": "MA in X", "deadlines": [{"round": 1}]}
    )


def test_not_thin_with_tuition() -> None:
    assert not is_thin_program_result({"name_en": "X", "tuition_amount": 158400.0})


def test_thin_when_tuition_missing_even_with_other_fields_present() -> None:
    """Regression for a real Lingnan full-crawl finding: requiring ALL of
    tuition/requirements/study_options to be missing under-triggered
    badly — a stub commonly picks up requirements on the first pass (once
    even from an incidental generic sitewide admission-policy nav link),
    which cleared the old bar and left tuition permanently unrecovered
    even though expansion would very likely have found it. Tuition alone
    is the trigger now."""
    assert is_thin_program_result(
        {
            "name_en": "X",
            "requirements": [{"requirement_text": "IELTS 6.5"}],
            "study_options": [{"mode": "FullTime", "duration_months": 12}],
        }
    )


def test_none_is_not_thin() -> None:
    """None means extraction failed entirely — that's the quarantine path,
    not the supplement path."""
    assert not is_thin_program_result(None)


# ── build_sibling_link_map ───────────────────────────────────────────

def test_sibling_map_keeps_department_link_drops_apply_portal() -> None:
    result = build_sibling_link_map(_LN_ROW, [_LN_STUB_URL])
    assert result == {
        _LN_STUB_URL: ["https://www.ln.edu.hk/philoso/hkcrc/maaif"]
    }


def test_sibling_map_ignores_rows_without_selected_url() -> None:
    md = "[ About Us ](https://x.edu/about) [ Contact ](https://x.edu/contact)"
    assert build_sibling_link_map(md, [_LN_STUB_URL]) == {}


def test_sibling_map_ignores_single_link_lines() -> None:
    md = f"[ Programme ]({_LN_STUB_URL})"
    assert build_sibling_link_map(md, [_LN_STUB_URL]) == {}


def test_sibling_map_excludes_social_and_language_toggles() -> None:
    md = (
        f"[ P ]({_LN_STUB_URL}) "
        "[ fb ](https://www.facebook.com/LingnanUniversity) "
        "[ 繁 ](https://www.ln.edu.hk/cht/sgs/programmes-on-offer) "
        "[ pdf ](https://www.ln.edu.hk/doc/guide.pdf)"
    )
    assert build_sibling_link_map(md, [_LN_STUB_URL]) == {}


def test_sibling_map_still_registers_when_sibling_is_also_selected() -> None:
    """Regression for the actual Lingnan full-catalogue crawl: index-page
    candidate discovery selected BOTH the stub link and its own "Visit
    Website" sibling as separate top-level candidates. An earlier version
    excluded a sibling whenever it was ALSO independently selected — that
    made the stub and its own department page mutually block each other,
    so enrichment failed almost everywhere at full scale. The stub must
    still resolve its generic-anchored dept link as a sibling regardless
    of the dept URL's own selection status.

    The reverse direction (dept -> stub) is deliberately NOT registered:
    the stub's anchor is the programme NAME, and name-anchored same-row
    links are other candidates, not escape hatches — cross-registering
    them lets a thin page merge a sibling programme's content (see
    test_sibling_map_never_links_name_anchored_rows). The dept page's own
    enrichment path is own_page_hop2, not its (useless) stub."""
    dept_url = "https://www.ln.edu.hk/philoso/hkcrc/maaif"
    result = build_sibling_link_map(_LN_ROW, [_LN_STUB_URL, dept_url])
    assert result[_LN_STUB_URL] == [dept_url]
    assert dept_url not in result


def test_sibling_map_never_links_name_anchored_rows() -> None:
    """A CUHK-style table row listing a subject's MA/MPhil/PhD variants
    side by side must not cross-register the variants as each other's
    siblings — a thin MA page merging the MPhil page's content would
    extract the wrong programme's tuition. Wrong data is worse than
    missing data: name-anchored same-row links are candidates, never
    supplement sources."""
    row = (
        "[MA in Anthropology](https://x.edu/ma-anthropology) | "
        "[MPhil in Anthropology](https://x.edu/mphil-anthropology) | "
        "[PhD in Anthropology](https://x.edu/phd-anthropology)"
    )
    result = build_sibling_link_map(
        row,
        ["https://x.edu/ma-anthropology", "https://x.edu/mphil-anthropology"],
    )
    assert result == {}


def test_sibling_map_matches_canonicalized_urls() -> None:
    """Trailing-slash / case differences between selection and markdown
    must not break row association."""
    md = f"[ P ]({_LN_STUB_URL}/) [ Visit Website ](https://www.ln.edu.hk/philoso/hkcrc/maaif)"
    result = build_sibling_link_map(md, [_LN_STUB_URL])
    assert result == {
        _LN_STUB_URL: ["https://www.ln.edu.hk/philoso/hkcrc/maaif"]
    }


# ── dedupe_same_row_candidates ───────────────────────────────────────

_LN_DEPT_URL = "https://www.ln.edu.hk/philoso/hkcrc/maaif"


def test_row_dedupe_keeps_name_link_drops_visit_website() -> None:
    """The Lingnan full-catalogue shape: LLM filter selected BOTH the
    programme-name stub link and its "Visit Website" sibling as separate
    candidates (94 candidates for ~47 programmes -> duplicate DB rows).
    The name-bearing link wins; the action link is demoted (and gets
    re-attached as the keeper's sibling by build_sibling_link_map)."""
    kept, dropped = dedupe_same_row_candidates(
        _LN_ROW,
        [_LN_STUB_URL, _LN_DEPT_URL],
        {_LN_STUB_URL: "Master of Arts in Artificial Intelligence and the Future",
         _LN_DEPT_URL: "Visit Website"},
    )
    assert kept == [_LN_STUB_URL]
    assert len(dropped) == 1
    assert dropped[0]["url"] == _LN_DEPT_URL
    assert dropped[0]["duplicate_of"] == _LN_STUB_URL
    # And the demoted URL still becomes the keeper's sibling afterwards:
    sibling_map = build_sibling_link_map(_LN_ROW, kept)
    assert sibling_map[_LN_STUB_URL] == [_LN_DEPT_URL]


def test_row_dedupe_keeps_both_when_both_anchors_are_name_like() -> None:
    """A layout that genuinely lists two programmes on one markdown line
    must not lose one — demotion requires a generic-action anchor."""
    row = (
        "[ MSc in Finance ](https://x.edu/msc-finance) | "
        "[ MSc in Economics ](https://x.edu/msc-economics)"
    )
    kept, dropped = dedupe_same_row_candidates(
        row,
        ["https://x.edu/msc-finance", "https://x.edu/msc-economics"],
        {"https://x.edu/msc-finance": "MSc in Finance",
         "https://x.edu/msc-economics": "MSc in Economics"},
    )
    assert kept == ["https://x.edu/msc-finance", "https://x.edu/msc-economics"]
    assert dropped == []


def test_row_dedupe_keeps_all_when_no_anchor_is_name_like() -> None:
    """With no name-bearing anchor on the row there is no safe keeper —
    don't guess, keep everything."""
    row = "[ Visit Website ](https://x.edu/a) [ Learn More ](https://x.edu/b)"
    kept, dropped = dedupe_same_row_candidates(
        row,
        ["https://x.edu/a", "https://x.edu/b"],
        {"https://x.edu/a": "Visit Website", "https://x.edu/b": "Learn More"},
    )
    assert kept == ["https://x.edu/a", "https://x.edu/b"]
    assert dropped == []


def test_row_dedupe_ignores_candidates_on_different_rows() -> None:
    md = (
        f"[ MA in X ]({_LN_STUB_URL})\n"
        f"[ Visit Website ]({_LN_DEPT_URL})\n"
    )
    kept, dropped = dedupe_same_row_candidates(
        md,
        [_LN_STUB_URL, _LN_DEPT_URL],
        {_LN_STUB_URL: "MA in X", _LN_DEPT_URL: "Visit Website"},
    )
    assert kept == [_LN_STUB_URL, _LN_DEPT_URL]
    assert dropped == []


# ── rank_admission_sub_links ─────────────────────────────────────────

def test_rank_prefers_requirements_and_fees_drops_unmatched() -> None:
    ranked = rank_admission_sub_links([
        "https://x.edu/dept/programme-information/overview",
        "https://x.edu/dept/admissions-and-application/admission-requirements",
        "https://x.edu/dept/admissions-and-application/fees-and-scholarships",
        "https://x.edu/dept/news",
        "https://x.edu/dept/staff",
        "https://www.facebook.com/x",
    ])
    # requirements + fees outrank overview; news/staff/social dropped entirely
    assert ranked[0].endswith(("fees-and-scholarships", "admission-requirements"))
    assert ranked[1].endswith(("fees-and-scholarships", "admission-requirements"))
    assert ranked[2].endswith("overview")
    assert len(ranked) == 3


# ── expand_thin_page ─────────────────────────────────────────────────

class _FakeScraper:
    """Serves canned pages; records every fetched URL."""

    def __init__(self, pages: dict) -> None:
        self._pages = pages
        self.fetched: list = []

    async def _crawl_urls(self, urls):
        out = []
        for url in urls:
            self.fetched.append(url)
            md = self._pages.get(url)
            if md is not None:
                out.append(SimpleNamespace(url=url, markdown=md))
        return out


_DEPT_HOME = "https://www.ln.edu.hk/philoso/hkcrc/maaif"
_DEPT_REQS = f"{_DEPT_HOME}/admissions-and-application/admission-requirements"
_DEPT_FEES = f"{_DEPT_HOME}/admissions-and-application/fees-and-scholarships"


def _dept_home_markdown() -> str:
    return (
        f"[Admission Requirements]({_DEPT_REQS})\n"
        f"[Fees and Scholarships]({_DEPT_FEES})\n"
        f"[News]({_DEPT_HOME}/news)\n"
    )


def test_expand_follows_sibling_then_admission_sub_links() -> None:
    """The full Lingnan shape: stub → sibling dept homepage (hop 1) →
    keyword-ranked admission sub-pages (hop 2), all markdown merged."""
    scraper = _FakeScraper({
        _DEPT_HOME: _dept_home_markdown(),
        _DEPT_REQS: "Applicants shall hold a Bachelor's degree. IELTS 6.5.",
        _DEPT_FEES: "Tuition Fee: HK$180,000 per programme.",
    })
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# MA in AI\nApplication Period: 1 Oct\n")

    merged, fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )

    assert _DEPT_HOME in fetched
    assert _DEPT_REQS in fetched and _DEPT_FEES in fetched
    assert "IELTS 6.5" in merged
    assert "HK$180,000" in merged
    # hub itself is included too (some dept homepages carry content directly)
    assert "Admission Requirements" in merged
    # news page never fetched — zero keyword score
    assert f"{_DEPT_HOME}/news" not in scraper.fetched


def test_expand_checks_own_links_when_candidate_is_the_department_page() -> None:
    """Regression for the full-catalogue Lingnan crawl: when the
    department homepage itself is selected as a top-level candidate (not
    reached via a stub's sibling link), its OWN sibling is the (useless)
    stub — fetched as hop 1 but with nothing to offer — while the REAL
    admission-requirements/fees sub-links are among the department page's
    OWN links, which must be checked directly (own_page_hop2), not only
    among whatever hop-1 pages happened to be fetched."""
    stub_markdown = "# MA in AI\nApplication Period: 1 Oct\n"
    scraper = _FakeScraper({
        _LN_STUB_URL: stub_markdown,
        _DEPT_REQS: "Applicants shall hold a Bachelor's degree. IELTS 6.5.",
        _DEPT_FEES: "Tuition Fee: HK$180,000 per programme.",
    })
    dept_page = SimpleNamespace(url=_DEPT_HOME, markdown=_dept_home_markdown())

    merged, fetched = asyncio.run(
        expand_thin_page(
            scraper, router=None, page=dept_page, sibling_urls=[_LN_STUB_URL]
        )
    )
    assert _DEPT_REQS in fetched and _DEPT_FEES in fetched
    assert "IELTS 6.5" in merged
    assert "HK$180,000" in merged


def test_expand_ignores_stubs_own_links_without_a_sibling() -> None:
    """Regression: a routing stub's own link list is site-wide chrome, not
    a path to that specific programme's page. Confirmed on a live Lingnan
    crawl: without a sibling, hop-1's old LLM-filter-on-the-stub's-own-
    links fallback picked FIVE generic SGS/RPg-wide navigation pages
    (including the bare site homepage) as if they were programme detail
    candidates, and re-extraction on that noise pulled a policy-page
    heading in as the programme's "name". No sibling now means no hop 1
    at all — a real absence-of-escape-hatch stays a real absence."""
    scraper = _FakeScraper({
        "https://www.ln.edu.hk/": "site homepage content",
        f"{_DEPT_HOME.rsplit('/', 1)[0]}-generic/admission-information": "generic sgs-wide policy",
    })
    stub_markdown = (
        "# MA in X\nApplication Period: 1 Oct\n"
        "[Admission Information](https://www.ln.edu.hk/sgs/for-prospective-students/"
        "taught-postgraduate-programmes/admission-information)\n"
        "[List of Programmes](https://www.ln.edu.hk/rpg/admission/list-of-programmes)\n"
        "[Home](https://www.ln.edu.hk/)\n"
    )
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown=stub_markdown)

    merged, fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[])
    )
    assert merged == ""
    assert fetched == []
    assert scraper.fetched == []


def test_expand_returns_empty_without_candidates() -> None:
    """No siblings and a stub with no usable links → nothing to do, and
    the scraper factory must never be invoked."""
    factory = MagicMock(side_effect=AssertionError("factory must not be called"))
    # spec-less MagicMock has _crawl_urls; use a plain function as factory
    def _factory():
        raise AssertionError("factory must not be called")

    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="just text, no links")
    merged, fetched = asyncio.run(
        expand_thin_page(_factory, router=None, page=stub, sibling_urls=[])
    )
    assert merged == ""
    assert fetched == []
    factory.assert_not_called()


def test_expand_invokes_factory_lazily() -> None:
    scraper = _FakeScraper({_DEPT_HOME: "some department blurb, no links"})

    def _factory():
        return scraper

    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")
    merged, fetched = asyncio.run(
        expand_thin_page(_factory, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    assert fetched == [_DEPT_HOME]
    assert "department blurb" in merged


def test_expand_tolerates_fetch_failure() -> None:
    class _BoomScraper:
        async def _crawl_urls(self, urls):
            raise RuntimeError("network down")

    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")
    merged, fetched = asyncio.run(
        expand_thin_page(_BoomScraper(), router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    assert merged == ""
    assert fetched == []


def test_expand_does_not_truncate_content_late_in_a_long_page() -> None:
    """Regression: some department homepages put admission requirements
    and tuition fees after a long nav/hero/overview section — confirmed
    live on Lingnan's eng/mades page, "Tuition Fee" past char 30000.
    A naive per-page truncation would silently drop it; the merge must
    preserve the full page and let downstream chunking handle length."""
    long_page = ("x" * 30_000) + "\n## Tuition Fee\nHKD178,000 per year.\n"
    scraper = _FakeScraper({_DEPT_HOME: long_page})
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")

    merged, _fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    assert "HKD178,000" in merged


def test_expand_never_refetches_visited_urls() -> None:
    """A dept homepage linking back to itself / the stub must not loop."""
    scraper = _FakeScraper({
        _DEPT_HOME: (
            f"[Self]({_DEPT_HOME})\n"
            f"[Stub]({_LN_STUB_URL})\n"
            f"[Admission Requirements]({_DEPT_REQS})\n"
        ),
        _DEPT_REQS: "Bachelor's degree required.",
    })
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")
    asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    assert scraper.fetched.count(_DEPT_HOME) == 1
    assert _LN_STUB_URL not in scraper.fetched


def test_expand_own_links_respect_same_section_guard() -> None:
    """A stub whose sitewide nav carries a keyword-matching fee link
    ("/admissions/tuition-fees") must NOT fetch it — merging a sitewide
    (e.g. undergraduate) fee page would hand the LLM an unrelated tuition
    figure to extract as this programme's fee. Off-section links are the
    old "stub links are chrome" problem in keyword-matching disguise."""
    scraper = _FakeScraper({
        "https://www.ln.edu.hk/admissions/tuition-fees": "UG fees HK$42,100",
    })
    stub = SimpleNamespace(
        url=_LN_STUB_URL,
        markdown=(
            "# MA in X\n"
            "[Tuition Fees](https://www.ln.edu.hk/admissions/tuition-fees)\n"
        ),
    )
    merged, fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[])
    )
    assert merged == ""
    assert fetched == []
    assert scraper.fetched == []


def test_expand_hop2_respects_same_section_guard() -> None:
    """Hop-2 links from a fetched hub must stay inside that hub's own
    section too — a dept page linking out to the university-wide fees
    page must not drag it into this programme's extraction."""
    scraper = _FakeScraper({
        _DEPT_HOME: (
            f"[Admission Requirements]({_DEPT_REQS})\n"
            "[University Fees](https://www.ln.edu.hk/admissions/tuition-fees)\n"
        ),
        _DEPT_REQS: "Bachelor's degree required.",
        "https://www.ln.edu.hk/admissions/tuition-fees": "UG fees HK$42,100",
    })
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")
    merged, fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    assert _DEPT_REQS in fetched
    assert "https://www.ln.edu.hk/admissions/tuition-fees" not in scraper.fetched
    assert "UG fees" not in merged


def test_expand_total_budget_caps_merged_size() -> None:
    """One thin page must never merge into an unbounded blob — the total
    cap bounds the chunked re-extraction cost (the EdUHK 43-chunk
    incident, reborn as a supplement bill)."""
    big = "x" * 70_000  # above per-page cap too
    scraper = _FakeScraper({
        _DEPT_HOME: big,
        _DEPT_REQS: big,
        _DEPT_FEES: big,
        f"{_DEPT_HOME}/admissions-and-application/scholarship": big,
    })
    dept_md = (
        f"[Admission Requirements]({_DEPT_REQS})\n"
        f"[Fees and Scholarships]({_DEPT_FEES})\n"
        f"[Scholarship]({_DEPT_HOME}/admissions-and-application/scholarship)\n"
    )
    scraper._pages[_DEPT_HOME] = dept_md + big
    stub = SimpleNamespace(url=_LN_STUB_URL, markdown="# stub\n")
    merged, _fetched = asyncio.run(
        expand_thin_page(scraper, router=None, page=stub, sibling_urls=[_DEPT_HOME])
    )
    # headers add a little, but the page-content total must respect the cap
    assert len(merged) <= MAX_TOTAL_SUPPLEMENT_CHARS + 1_000


# ── pipeline integration (_stage_extract_structured hook) ────────────

def _thin_row() -> dict:
    return {
        "url": _LN_STUB_URL,
        "markdown": "# MA in AI\nApplication Period: 1 Oct 2026\n",
        "char_count": 50,
        "links": [],
        "status_code": 200,
        "html": None,
        "crawl_depth": 1,
        "from_browser": False,
        "selected_anchor_text": "Master of Arts in Artificial Intelligence and the Future",
        "sibling_urls": [_DEPT_HOME],
    }


def _pipeline_with_mocks(monkeypatch, extract_side_effect, expand_result):
    from unittest.mock import MagicMock as MM

    from src.services.ingestion_pipeline import IngestionPipeline

    monkeypatch.setattr(
        "src.services.ingestion_pipeline.LLMCleanerAgent", MM
    )
    extract_mock = MM(side_effect=extract_side_effect)
    monkeypatch.setattr(
        "src.services.ingestion_pipeline.extract_program_data_from_page",
        extract_mock,
    )

    expand_calls: list = []

    async def fake_expand(scraper, router, page, sibling_urls=None):
        expand_calls.append({"url": page.url, "siblings": list(sibling_urls or [])})
        return expand_result

    monkeypatch.setattr(
        "src.services.ingestion_pipeline.expand_thin_page", fake_expand
    )
    pattern_calls: list = []
    monkeypatch.setattr(
        "src.services.crawl_strategy.learned_cache.record_detail_pattern",
        lambda url, pattern: pattern_calls.append((url, pattern)),
    )
    return IngestionPipeline(db_manager=MM()), extract_mock, expand_calls, pattern_calls


def test_pipeline_enriches_thin_result_and_records_pattern(monkeypatch) -> None:
    thin = {"name_en": "MA in AI", "deadlines": [{"round": 1}]}
    rich = {
        "name_en": "MA in AI",
        "tuition_amount": 180000.0,
        "currency": "HKD",
        "requirements": [{"requirement_text": "IELTS 6.5"}],
    }
    pipeline, extract_mock, expand_calls, pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(thin), None), (dict(rich), None)],
        expand_result=("\n\n## Supplemental Detail\nTuition HK$180,000", [_DEPT_FEES]),
    )

    result = pipeline._stage_extract_structured(
        {"univ_slug": "ln", "year": 2026, "page_type_hint": "detail"},
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 2  # thin first pass + enriched re-extract
    assert expand_calls == [{"url": _LN_STUB_URL, "siblings": [_DEPT_HOME]}]
    # the enriched second page must contain the merged supplement markdown
    enriched_page = extract_mock.call_args_list[1].kwargs["page"]
    assert "Supplemental Detail" in enriched_page.markdown
    candidate = result["program_candidates"][0]
    assert candidate["tuition_amount"] == 180000.0
    assert candidate["extra_metadata"]["thin_page_supplement"]["fetched_urls"] == [_DEPT_FEES]
    assert result["thin_supplement_urls"] == [_LN_STUB_URL]
    assert pattern_calls == [(_LN_STUB_URL, "thin_page_supplement")]


def test_pipeline_preserves_original_name_over_supplement_heading(monkeypatch) -> None:
    """Regression: the merged markdown contains the supplement pages' own
    headings, and the enriched re-extraction's heading ladder can crown
    one of THOSE as the programme name (a record literally named
    "Tuition Fees, Scholarships & Financial Assistance" on a real crawl).
    The first pass saw only the page itself — its legitimate name wins,
    and the catalog group code must follow the restored name (stale-code
    dedup breakage is the CUHK battle-test-3 bug shape)."""
    thin = {"name_en": "Master of Accountancy", "deadlines": [{"round": 1}]}
    rich_wrong_name = {
        "name_en": "Tuition Fees, Scholarships & Financial Assistance",
        "program_group_code": "ln#tuitionfeesscholarshipsfinancialassistance",
        "tuition_amount": 158620.0,
        "currency": "HKD",
    }
    pipeline, extract_mock, _expand_calls, _pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(thin), None), (dict(rich_wrong_name), None)],
        expand_result=("\n\n## Supplemental Detail\nTuition HK$158,620", [_DEPT_FEES]),
    )

    result = pipeline._stage_extract_structured(
        # taxonomy disabled: the shared taxonomy-service singleton may be
        # seeded by earlier tests and its high-confidence override would
        # rewrite name_en AFTER the preservation under test (test-order
        # dependency, not the behavior being locked in here).
        {
            "univ_slug": "ln",
            "year": 2026,
            "page_type_hint": "detail",
            "taxonomy_enabled": False,
        },
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 2
    candidate = result["program_candidates"][0]
    assert candidate["name_en"] == "Master of Accountancy"
    assert candidate["tuition_amount"] == 158620.0  # enriched fields kept
    assert candidate["program_group_code"] == "ln#masteraccountancy"


def test_pipeline_keeps_thin_result_when_supplement_finds_nothing(monkeypatch) -> None:
    thin = {"name_en": "MA in Chinese", "deadlines": [{"round": 1}]}
    pipeline, extract_mock, expand_calls, pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(thin), None)],
        expand_result=("", []),  # e.g. no Visit Website sibling on this row
    )

    result = pipeline._stage_extract_structured(
        {"univ_slug": "ln", "year": 2026, "page_type_hint": "detail"},
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 1  # no re-extract without supplement
    assert len(expand_calls) == 1
    # Thin result kept as-is: no enrichment fields, no supplement trace.
    # (Deliberately NOT asserting on name_en — the shared taxonomy-override
    # singleton may legitimately rewrite it depending on test order.)
    candidate = result["program_candidates"][0]
    assert candidate.get("tuition_amount") is None
    assert "thin_page_supplement" not in (candidate.get("extra_metadata") or {})
    assert result["thin_supplement_urls"] == []
    assert pattern_calls == []


def test_pipeline_keeps_original_when_enriched_still_thin(monkeypatch) -> None:
    """Supplement fetched something, but re-extraction stayed thin — keep
    the first result and do NOT record the pattern (it didn't help)."""
    thin = {"name_en": "MA in X", "deadlines": [{"round": 1}]}
    pipeline, extract_mock, _expand_calls, pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(thin), None), (dict(thin), None)],
        expand_result=("\n\n## Supplemental Detail\nnothing useful", ["https://x.edu/sub"]),
    )

    result = pipeline._stage_extract_structured(
        {"univ_slug": "ln", "year": 2026, "page_type_hint": "detail"},
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 2
    candidate = result["program_candidates"][0]
    assert "thin_page_supplement" not in (candidate.get("extra_metadata") or {})
    assert result["thin_supplement_urls"] == []
    assert pattern_calls == []


def test_pipeline_supplement_disabled_flag(monkeypatch) -> None:
    thin = {"name_en": "MA in AI", "deadlines": [{"round": 1}]}
    pipeline, extract_mock, expand_calls, _pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(thin), None)],
        expand_result=("should never be used", ["https://x.edu"]),
    )

    pipeline._stage_extract_structured(
        {
            "univ_slug": "ln",
            "year": 2026,
            "page_type_hint": "detail",
            "thin_page_supplement_enabled": False,
        },
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 1
    assert expand_calls == []


def test_pipeline_rich_result_never_triggers_supplement(monkeypatch) -> None:
    rich = {"name_en": "MSc Finance", "tuition_amount": 300000.0}
    pipeline, extract_mock, expand_calls, _pattern_calls = _pipeline_with_mocks(
        monkeypatch,
        extract_side_effect=[(dict(rich), None)],
        expand_result=("should never be used", []),
    )

    pipeline._stage_extract_structured(
        {"univ_slug": "ln", "year": 2026, "page_type_hint": "detail"},
        {"raw_pages": [_thin_row()]},
    )

    assert extract_mock.call_count == 1
    assert expand_calls == []


def test_select_detail_urls_collapses_same_row_duplicates(monkeypatch) -> None:
    """End-to-end through _select_detail_urls: when the LLM filter keeps
    both the stub and its Visit Website sibling, the returned candidate
    list contains only the stub, and the drop is recorded in the funnel
    with its keeper for auditability."""
    from unittest.mock import MagicMock as MM

    from src.models.scraper_models import CrawlPageResult
    from src.services.ingestion_pipeline import IngestionPipeline

    monkeypatch.setattr(
        "src.services.ingestion_pipeline.filter_links_by_llm",
        lambda router, pairs, base_url: [_LN_STUB_URL, _LN_DEPT_URL],
    )
    pipeline = IngestionPipeline(db_manager=MM())
    page = CrawlPageResult(
        url="https://www.ln.edu.hk/sgs/programmes-on-offer",
        markdown=_LN_ROW, char_count=len(_LN_ROW), links=[],
        status_code=200, html=None,
    )
    funnel: dict = {}
    urls, text_map = asyncio.run(
        pipeline._select_detail_urls(MM(), page, funnel_out=funnel)
    )
    assert urls == [_LN_STUB_URL]
    assert _LN_DEPT_URL not in text_map
    same_row_drops = [
        d for d in funnel.get("dropped_links", [])
        if d.get("stage_dropped") == "same_row_duplicate"
    ]
    assert len(same_row_drops) == 1
    assert same_row_drops[0]["url"] == _LN_DEPT_URL
    assert same_row_drops[0]["duplicate_of"] == _LN_STUB_URL
    assert funnel["candidate_count"] == 1


# ── sibling plumbing through _serialize_pages ────────────────────────

def test_serialize_pages_attaches_sibling_urls() -> None:
    from src.models.scraper_models import CrawlPageResult
    from src.services.ingestion_pipeline import IngestionPipeline

    page = CrawlPageResult(
        url=_LN_STUB_URL + "/",  # redirected variant: trailing slash
        markdown="# stub", char_count=6, links=[], status_code=200, html=None,
    )
    rows = IngestionPipeline._serialize_pages(
        pages=[page],
        depth=1,
        from_browser=False,
        selected_link_texts={_LN_STUB_URL: "MA in AI"},
        sibling_urls={_LN_STUB_URL: [_DEPT_HOME]},
    )
    assert rows[0]["selected_anchor_text"] == "MA in AI"
    assert rows[0]["sibling_urls"] == [_DEPT_HOME]


# ── learned cache pattern recording ──────────────────────────────────

def test_record_detail_pattern_merges_into_domain_entry(monkeypatch, tmp_path) -> None:
    import src.core.paths as paths

    monkeypatch.setattr(paths, "get_data_dir", lambda: tmp_path)

    from src.services.crawl_strategy.learned_cache import (
        load_cache,
        lookup,
        record_detail_pattern,
        record_success,
    )

    record_success("https://www.ln.edu.hk/sgs/x", fetch_mode="server")
    record_detail_pattern("https://www.ln.edu.hk/sgs/y", "thin_page_supplement")

    entry = lookup("https://www.ln.edu.hk/anything")
    assert entry["fetch_mode"] == "server"  # merged, not replaced
    assert entry["detail_pattern"] == "thin_page_supplement"
    assert "detail_pattern_recorded_at" in entry
    assert set(load_cache().keys()) == {"www.ln.edu.hk"}
