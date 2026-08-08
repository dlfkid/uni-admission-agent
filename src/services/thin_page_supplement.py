"""Thin-page supplement: recover admission data hidden behind hub pages.

Some universities publish an official "detail page" per programme that is
really just a routing stub — programme name, an application period, and
site navigation — while the substantive content (tuition, entry
requirements, study modes) lives on a department-run site reachable only
through a sibling link on the index row (e.g. Lingnan's "Visit Website"),
and often one more hop down that site's own navigation
(".../admission-requirements", ".../tuition-fees").

This module makes the generic pipeline recognise and traverse that layout
pattern at runtime, for ANY university, without a registry entry:

1. Detection: an extraction result is "thin" when tuition is missing —
   the single field most consistently absent from a stub, and the one
   this mechanism exists to recover (requirements/study_options are NOT
   also required to be missing: a stub commonly picks up one of those
   from incidental navigation while tuition stays genuinely unreachable).
2. Expansion: bounded two-hop fetch. Hop 1 = index-row sibling links ONLY
   (e.g. Lingnan's "Visit Website") — a stub's own links are site-wide
   chrome, never a path to its own department page; no sibling means no
   hop 1. Hop 2 = admission-flavoured sub-links (deterministic URL-
   keyword ranking — the target pages name themselves: "admission-
   requirements", "fees-and-scholarships") of each hop-1 page, with an
   LLM-filter fallback when keywords match nothing.
3. Merge: fetched supplement markdown is appended to the stub page's
   markdown and extraction re-runs once on the combined content.

The mechanism deliberately mirrors ``_enrich_with_supplement`` (the
registry-pinned CityU tuition sub-page enrichment) but is trigger-detected
instead of regex-configured, so unseen universities with the same layout
work on first contact.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from src.scrapers.helpers import is_noise_program_name
from src.scrapers.link_parser import extract_links_with_text, filter_links_by_llm

logger = logging.getLogger(__name__)

# Markdown inline link: reuse the nested-bracket-tolerant label shape from
# link_parser so sibling scanning sees the same links extraction does.
_LINK_LABEL = r"(?:[^\[\]]|\[[^\[\]]*\])*"
_MD_LINK_RE = re.compile(rf"\[({_LINK_LABEL})\]\(([^)\s]+)[^)]*\)")

# Links that can never carry programme detail: application portals,
# language toggles, share/social widgets, media assets, anchors, the bare
# site root, and school/faculty-WIDE categorisation pages (as opposed to a
# specific department's own admission page) — a stub's mega-menu nav links
# to these under generic umbrella sections ("For Prospective Students",
# "For Current Students") rather than anything programme-specific; the URL
# keyword "admission" alone doesn't distinguish them from a genuine
# department page like ".../dais/application-admission/admission-
# requirements", so the path shape (nested under these umbrella sections)
# is the signal instead.
_NOISE_URL_RE = re.compile(
    r"(?:"
    r"^javascript:|^mailto:|^tel:|^#"
    r"|^https?://[^/]+/?$"
    r"|//apply\.|/apply\b|onlineappl|application-form"
    r"|/cht/|/chs/|/zh[-_/]"
    r"|/for-prospective-students/|/for-current-students/"
    r"|/admission/list-of-programmes\b|/indicative-timeframe"
    r"|facebook\.com|instagram\.com|youtube\.com|linkedin\.com|twitter\.com|x\.com|weibo\.com"
    r"|\.(?:pdf|jpe?g|png|gif|svg|mp4|zip)(?:$|\?)"
    r")",
    re.IGNORECASE,
)

# URL path keywords that mark a sub-page as admission-relevant, weighted:
# concrete data pages (requirements / fees) first, context pages last.
_SUB_LINK_KEYWORDS: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"requirement", re.IGNORECASE), 100),
    (re.compile(r"tuition|fees?[-/]|[-/]fees?\b", re.IGNORECASE), 100),
    (re.compile(r"admission", re.IGNORECASE), 80),
    (re.compile(r"scholarship", re.IGNORECASE), 40),
    (re.compile(r"deadline", re.IGNORECASE), 40),
    (re.compile(r"curriculum|course-detail|course-description", re.IGNORECASE), 30),
    (re.compile(r"programme-information|program-information|overview", re.IGNORECASE), 20),
]

# Generic action-label anchors ("Visit Website", "Apply Now") — never a
# programme name. Used to pick which of several same-row candidates is the
# name-bearing one; complements is_noise_program_name, which covers the
# broader navigation-label vocabulary but not the visit-website family.
_GENERIC_ANCHOR_RE = re.compile(
    r"^(?:"
    r"visit(?:\s+(?:the\s+)?(?:web\s?site|site|page|homepage))?"
    r"|(?:official|programme|program)\s+(?:web\s?site|site|page)"
    r"|web\s?site|homepage|home\s?page"
    r"|more(?:\s+(?:info(?:rmation)?|details?))?|details?"
    r"|click\s+here|here|link"
    r")$",
    re.IGNORECASE,
)

# Bounds: a thin page triggers at most one hop-1 batch and one hop-2 batch.
MAX_HOP1_PAGES = 2
MAX_HOP2_PAGES = 3
# Some Lingnan department homepages put admission requirements and tuition
# fees directly in their own body — after a long nav/hero/overview section
# that pushes the real content past 25-30K chars (confirmed on a live page:
# "Admission Requirements" at offset 25194, "Tuition Fee" at 30622). The
# per-page cap must comfortably exceed that shape (2× headroom over the
# worst observed offset); the TOTAL cap bounds the merged re-extraction
# cost — the cleaner chunks anything over its MAX_DETAIL_CHARS with
# rolling context, so an unbounded merge silently turns one thin page
# into a 15+-chunk LLM bill (the EdUHK 43-chunk incident, reborn).
MAX_SUPPLEMENT_CHARS_PER_PAGE = 60_000
MAX_TOTAL_SUPPLEMENT_CHARS = 200_000


def is_thin_program_result(program_data: Optional[Dict[str, Any]]) -> bool:
    """True when an extraction is missing tuition — the field this
    mechanism exists to recover.

    Originally required ALL of tuition/requirements/study_options to be
    missing (the strict fingerprint of a pure routing stub). Widened after
    a real Lingnan crawl showed that requires too much: a hub/stub page
    commonly picks up SOME field on the first pass — most often
    requirements, sometimes via a generic sitewide admission-policy nav
    link the LLM happens to surface — which cleared the old bar and left
    tuition (and study mode) permanently unrecovered even though
    expansion would very likely have found them, since fee/tuition sub-
    pages are the highest-weighted target in rank_admission_sub_links.
    Tuition alone is the trigger now: it is the single field most
    consistently missing and the one prospective students care about
    most, and unlike a generic requirements blurb it is never
    incidentally picked up from unrelated navigation.

    Deliberately does NOT require requirements/study_options to also be
    missing — that would under-trigger exactly as the old all-three bar
    did. The cost tradeoff (more triggers, more fetches) was an explicit,
    accepted choice for completeness over token spend.
    """
    if not program_data:
        return False  # nothing extracted at all — that's extraction_failed, not thin
    return program_data.get("tuition_amount") is None


def _canonical(url: str) -> str:
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return str(url or "").strip().rstrip("/")
    if not parts.scheme or not parts.netloc:
        return str(url or "").strip().rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def build_sibling_link_map(
    index_markdown: str,
    selected_urls: List[str],
) -> Dict[str, List[str]]:
    """Map each selected detail URL to other links on its index-page row.

    Index pages that use the hub layout put the escape hatch — the link to
    the department site that actually holds the content — on the SAME
    markdown line/row as the programme-name link ("Visit Website" next to
    "Master of Arts in ..."), and the routing stub itself never links out.
    Row-level association at selection time is therefore the only moment
    this information exists; once the stub is fetched it is gone.

    Deliberately does NOT exclude a sibling URL just because it is ALSO in
    ``selected_urls`` (i.e. independently picked as its own top-level
    candidate). An earlier version did exclude it, reasoning that another
    selected candidate shouldn't double as a "supplement" — but on a real
    Lingnan full-catalogue crawl, the index-page candidate discovery
    itself selects BOTH the stub link and its own "Visit Website" sibling
    on the same row as separate candidates (roughly doubling the true
    programme count). With the exclusion, that made the stub and its own
    department page mutually block each other from ever being usable as
    each other's sibling — enrichment failed almost everywhere at full
    scale, despite working perfectly on a small sample that happened not
    to include both members of a pair. A markdown line is one programme's
    row on every layout seen so far, so a same-row link is safe to use as
    a sibling regardless of whether it independently made the candidate
    list too.
    """
    selected_canon = {_canonical(u): u for u in selected_urls if str(u or "").strip()}
    if not selected_canon:
        return {}

    sibling_map: Dict[str, List[str]] = {}
    for line in (index_markdown or "").splitlines():
        links = [(m.group(1), m.group(2)) for m in _MD_LINK_RE.finditer(line)]
        if len(links) < 2:
            continue
        line_selected = [
            selected_canon[_canonical(u)]
            for _t, u in links
            if _canonical(u) in selected_canon
        ]
        if not line_selected:
            continue
        candidates = []
        for anchor_text, sib_url in links:
            if _NOISE_URL_RE.search(sib_url):
                continue
            if not sib_url.lower().startswith(("http://", "https://")):
                continue
            # Only generic-action anchors ("Visit Website") qualify as a
            # sibling escape hatch. A NAME-anchored link on the same row
            # is another candidate/programme (e.g. a CUHK-style table row
            # listing a subject's MA/MPhil/PhD variants side by side) —
            # cross-registering those as each other's siblings would let
            # a thin MA page merge the MPhil page's content and extract
            # the wrong programme's tuition. Wrong data is worse than
            # missing data; name-like same-row links stay out.
            if not _anchor_is_generic(anchor_text):
                continue
            candidates.append(sib_url)
        if not candidates:
            continue
        # Per-sel exclusion, not shared across the row: a link is only
        # excluded from being ITS OWN sibling (self-reference, e.g. the
        # same URL reappearing with a trailing slash) — never excluded
        # just because it is ANOTHER selected candidate's own link (see
        # docstring: that WAS the bug).
        for sel in line_selected:
            sel_canon = _canonical(sel)
            siblings = [s for s in candidates if _canonical(s) != sel_canon]
            if not siblings:
                continue
            existing = sibling_map.setdefault(sel, [])
            for sib in siblings:
                if sib not in existing:
                    existing.append(sib)
    return sibling_map


def _anchor_is_generic(anchor: Optional[str]) -> bool:
    """True when an anchor text is an action label, navigation noise, or
    empty — i.e. it cannot be the programme name for its row."""
    text = " ".join(str(anchor or "").split())
    if not text:
        return True
    return bool(_GENERIC_ANCHOR_RE.match(text)) or is_noise_program_name(text)


def dedupe_same_row_candidates(
    index_markdown: str,
    selected_urls: List[str],
    url_to_text: Dict[str, str],
) -> Tuple[List[str], List[Dict[str, Optional[str]]]]:
    """Collapse same-index-row candidates down to the name-bearing link.

    On hub-layout index pages, one programme's row carries both the
    programme-name link (the stub) and an action link to the department
    site ("Visit Website"). The LLM candidate filter routinely selects
    BOTH as separate top-level candidates — on a real Lingnan
    full-catalogue crawl this roughly doubled the candidate count (94 for
    ~47 programmes) and produced duplicate DB rows per programme, one
    named from the stub and one named from the department page's own
    title ("Master of Arts in X" next to "Lingnan X Programme Site").

    Demotion is deliberately conservative: a row must have BOTH at least
    one name-like anchor AND at least one generic-action anchor before
    anything is dropped — rows where every selected link carries a
    name-like anchor are left alone (a layout that genuinely lists two
    programmes on one markdown line must not lose one). The demoted URL
    is not discarded knowledge: build_sibling_link_map runs over the same
    rows afterwards and registers it as the keeper's sibling, so the
    department page still feeds the thin-page supplement for that row.

    Returns ``(kept_urls_in_original_order, dropped_records)``.
    """
    selected_canon = {_canonical(u): u for u in selected_urls if str(u or "").strip()}
    if not selected_canon:
        return list(selected_urls), []

    dropped: Dict[str, str] = {}  # dropped url -> keeper url
    for line in (index_markdown or "").splitlines():
        row_selected: List[str] = []
        seen_row: set = set()
        for match in _MD_LINK_RE.finditer(line):
            canon = _canonical(match.group(2))
            sel = selected_canon.get(canon)
            if sel is not None and canon not in seen_row:
                seen_row.add(canon)
                row_selected.append(sel)
        if len(row_selected) < 2:
            continue
        named = [
            u for u in row_selected
            if not _anchor_is_generic(url_to_text.get(u))
        ]
        if not named or len(named) == len(row_selected):
            continue
        keeper = named[0]
        for url in row_selected:
            if url not in named:
                dropped.setdefault(url, keeper)

    kept = [u for u in selected_urls if u not in dropped]
    records: List[Dict[str, Optional[str]]] = [
        {
            "url": url,
            "duplicate_of": keeper,
            "anchor_text": url_to_text.get(url) or None,
        }
        for url, keeper in dropped.items()
    ]
    return kept, records


def _same_section(base_url: str, candidate_url: str) -> bool:
    """True when *candidate_url* lives inside *base_url*'s own site section.

    The guard that keeps supplement fetches programme-specific: a
    department page's admission/fee sub-pages live under its own path
    (".../dais" -> ".../dais/application-admission/..."), while sitewide
    links that happen to carry admission-ish keywords live elsewhere
    ("/admissions/fees" on the same host, or another host entirely).
    Merging a SITEWIDE fee page into one programme's extraction is worse
    than fetching nothing — the LLM will happily extract some unrelated
    (e.g. undergraduate) tuition figure as this programme's fee.

    A dedicated-subdomain programme site (base path "/") owns its whole
    host, so everything on that host qualifies.
    """
    try:
        base, cand = urlsplit(base_url), urlsplit(candidate_url)
    except ValueError:
        return False
    if not base.netloc or base.netloc.lower() != cand.netloc.lower():
        return False
    base_path = base.path.rstrip("/")
    if not base_path:
        return True
    return cand.path == base_path or cand.path.startswith(base_path + "/")


def rank_admission_sub_links(urls: List[str]) -> List[str]:
    """Order candidate sub-links by admission-content likelihood.

    Deterministic and free: hub sites name their sub-pages after their
    content ("admission-requirements", "fees-and-scholarships"), so URL
    keywords alone separate data pages from news/gallery/staff noise.
    Unmatched URLs are dropped, not ranked last — a zero-score link on a
    department site is almost always navigation we should not fetch.
    """
    scored: List[Tuple[int, str]] = []
    for url in urls:
        if _NOISE_URL_RE.search(url):
            continue
        score = sum(w for rx, w in _SUB_LINK_KEYWORDS if rx.search(url))
        if score > 0:
            scored.append((score, url))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [u for _s, u in scored]


def _llm_pick_links(router: Any, markdown: str, base_url: str, cap: int) -> List[str]:
    """LLM-filter a page's links down to programme/admission candidates."""
    pairs = extract_links_with_text(markdown or "", base_url)
    if not pairs:
        return []
    try:
        picked = filter_links_by_llm(router, pairs, base_url)
    except Exception:
        logger.warning("thin-page supplement: LLM link filter failed for %s", base_url, exc_info=True)
        return []
    out: List[str] = []
    for url in picked:
        if _NOISE_URL_RE.search(url) or _canonical(url) == _canonical(base_url):
            continue
        out.append(url)
        if len(out) >= cap:
            break
    return out


async def expand_thin_page(
    scraper: Any,
    router: Any,
    page: Any,
    sibling_urls: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    """Fetch up to two hops of supplement pages for a thin detail page.

    Returns ``(supplement_markdown, fetched_urls)``; empty string when
    nothing useful was reachable. ``scraper`` needs ``_crawl_urls``, or may
    be a zero-arg factory returning one — the factory is only invoked once
    hop-1 candidates exist, so callers avoid constructing a real scraper
    for pages with nothing to expand. ``page`` needs ``.url``/``.markdown``
    (duck-typed for testability).
    """
    visited = {_canonical(page.url)}
    fetched_urls: List[str] = []
    supplement_parts: List[str] = []

    # Hop 1: index-row siblings ONLY — never an LLM pick over the page's
    # own links. A routing stub's own link list is site-wide chrome
    # (mega-menu items like "Admission Information", "List of
    # Programmes", the site root itself), and on a live Lingnan crawl the
    # LLM filter, run against exactly that link list, picked FIVE generic
    # SGS/RPg-wide navigation pages as "programme detail pages"; the
    # re-extraction then pulled a random policy-page heading in as the
    # programme's "name". The page's own links ARE still consulted — but
    # via the deterministic keyword ranking below (own_page_hop2), gated
    # by the _same_section guard, which is what actually separates "this
    # programme's own fee page" from "sitewide admission chrome". A bare
    # stub with no sibling therefore still nets nothing — matches the
    # "Master of Arts in Chinese" case, a genuine structural ceiling.
    hop1: List[str] = []
    for url in list(sibling_urls or []):
        if _canonical(url) not in visited and not _NOISE_URL_RE.search(url):
            hop1.append(url)
        if len(hop1) >= MAX_HOP1_PAGES:
            break

    # The candidate itself may already BE a department homepage rather
    # than a bare stub — this happens whenever the index page's own
    # candidate discovery selects both a stub and its "Visit Website"
    # sibling as separate top-level candidates (confirmed on a live
    # Lingnan full-catalogue crawl: with the stub-only-links exclusion
    # this used to carry, the two selections blocked each other from
    # ever being usable as each other's sibling). Rank the CURRENT page's
    # own links the same way hop 2 ranks a hop-1 page's links — free
    # (URL-keyword based) and a no-op for a genuine bare stub, since
    # a stub's nav never carries admission/fee-shaped URLs.
    # The _same_section guard doubles as the safety property the old
    # "stub's own links are chrome" rule provided: a stub's sitewide nav
    # links (even keyword-matching ones like "/admissions/tuition-fees")
    # never live under the stub's own path, so a genuine bare stub still
    # nets zero own-page candidates.
    own_page_hop2 = [
        u for u in rank_admission_sub_links(
            [u for u, _t in extract_links_with_text(page.markdown or "", page.url)]
        )
        if _canonical(u) not in visited and _same_section(page.url, u)
    ][:MAX_HOP2_PAGES]

    if not hop1 and not own_page_hop2:
        return "", []

    if callable(scraper) and not hasattr(scraper, "_crawl_urls"):
        scraper = scraper()

    hop1_pages = await _fetch_safe(scraper, hop1, visited) if hop1 else []
    fetched_urls.extend(p.url for p in hop1_pages)

    # Hop 2: admission-flavoured sub-links of each hop-1 page, PLUS the
    # current page's own such links (own_page_hop2, computed above — covers
    # the case where the candidate itself is already a department
    # homepage). Keyword ranking first (free, and the target pages name
    # themselves); LLM filter only as fallback when a site's URLs carry no
    # keywords.
    hop2: List[str] = list(own_page_hop2)
    for h1 in hop1_pages:
        candidate_urls = [
            u for u, _t in extract_links_with_text(h1.markdown or "", h1.url)
        ]
        ranked = rank_admission_sub_links(candidate_urls)
        if not ranked:
            ranked = _llm_pick_links(router, h1.markdown, h1.url, MAX_HOP2_PAGES)
        for url in ranked:
            canon = _canonical(url)
            if canon in visited or url in hop2:
                continue
            # Same-section guard relative to the page the link came FROM:
            # keeps a hop-1 hub from dragging in sitewide (e.g.
            # undergraduate) fee pages whose numbers would be extracted as
            # this programme's tuition.
            if not _same_section(h1.url, url):
                continue
            hop2.append(url)
            if len(hop2) >= MAX_HOP2_PAGES:
                break
        if len(hop2) >= MAX_HOP2_PAGES:
            break

    hop2_pages = await _fetch_safe(scraper, hop2, visited)
    fetched_urls.extend(p.url for p in hop2_pages)

    # Total-budget guard: without it, one thin page can merge into a
    # 300K+ blob whose chunked re-extraction costs more than the rest of
    # the crawl combined. Pages are appended in fetch order (hop 1 hubs
    # first — they may carry the content inline) until the budget runs
    # out; anything cut is logged rather than silently vanishing.
    remaining = MAX_TOTAL_SUPPLEMENT_CHARS
    for sup in hop1_pages + hop2_pages:
        if remaining <= 0:
            logger.info(
                "thin-page supplement: total budget reached, skipping %s", sup.url
            )
            continue
        md = (sup.markdown or "")[:MAX_SUPPLEMENT_CHARS_PER_PAGE][:remaining]
        if md.strip():
            supplement_parts.append(f"\n\n## Supplemental Detail ({sup.url})\n{md}")
            remaining -= len(md)

    return "".join(supplement_parts), fetched_urls


async def _fetch_safe(scraper: Any, urls: List[str], visited: set) -> List[Any]:
    """Fetch URLs one batch, tolerating individual failures."""
    to_fetch = []
    for url in urls:
        canon = _canonical(url)
        if canon in visited:
            continue
        visited.add(canon)
        to_fetch.append(url)
    if not to_fetch:
        return []
    try:
        pages = await scraper._crawl_urls(to_fetch)
    except Exception:
        logger.warning("thin-page supplement: fetch batch failed (%s)", to_fetch, exc_info=True)
        return []
    return [p for p in (pages or []) if p is not None and (p.markdown or "").strip()]
