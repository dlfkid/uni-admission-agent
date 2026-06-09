# Pagination & Range Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a caller-supplied crawl range plus a Paginate axis (none/scroll/url_pages) so the crawl-strategy system fetches exactly as many programme names as asked, by whichever paging mechanism the site uses, and auto-stops on five anomaly signals.

**Architecture:** A new pure `paginator.py` owns the paging loop, stop signals, and unknown-site mechanism auto-detection; `types.py` gains `CrawlRange`/`PaginateMode`; the registry pins a mechanism per known site; `fetch_adapters.py`'s `client_wait` becomes range-aware (scroll to a target); the orchestrator threads a `crawl_range` through and dispatches via `paginate()`.

**Tech Stack:** Python 3.12, pytest, typer (CLI), Playwright (scroll fetch, mocked in unit tests).

**Spec:** `docs/superpowers/specs/2026-06-09-pagination-range-strategy-design.md`

**Branch:** `feat/pagination-range-strategy` (already created, spec already committed).

---

### Task 1: Types — PaginateMode, CrawlRange, Strategy.paginate, CrawlOutcome fields

**Files:**
- Modify: `src/services/crawl_strategy/types.py`
- Test: `tests/test_crawl_strategy/test_types_pagination.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_strategy/test_types_pagination.py`:

```python
from src.services.crawl_strategy.types import (
    CrawlRange, CrawlOutcome, ExtractKind, FetchMode, PaginateMode, Strategy,
)


def test_paginate_mode_values():
    assert PaginateMode.NONE.value == "none"
    assert PaginateMode.SCROLL.value == "scroll"
    assert PaginateMode.URL_PAGES.value == "url_pages"


def test_crawl_range_default_is_first_batch_capped_30():
    r = CrawlRange.default()
    assert r.limit == 30
    assert r.paginate is False


def test_crawl_range_of_n_paginates():
    r = CrawlRange.of(200)
    assert r.limit == 200
    assert r.paginate is True


def test_crawl_range_all_is_unbounded_paginating():
    r = CrawlRange.all_()
    assert r.limit is None
    assert r.paginate is True


def test_strategy_defaults_to_no_pagination():
    s = Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK)
    assert s.paginate is PaginateMode.NONE


def test_strategy_can_pin_a_mechanism():
    s = Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                 paginate=PaginateMode.SCROLL)
    assert s.paginate is PaginateMode.SCROLL


def test_crawl_outcome_has_pagination_fields():
    o = CrawlOutcome(status="ok", university="x")
    assert o.pages_fetched == 0
    assert o.stopped_reason == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_types_pagination.py -v`
Expected: FAIL with `ImportError`/`AttributeError` (PaginateMode / CrawlRange not defined).

- [ ] **Step 3: Add the enum, the dataclass, and the new fields**

In `src/services/crawl_strategy/types.py`, add `PaginateMode` after the `ExtractKind` enum:

```python
class PaginateMode(str, Enum):
    """How to obtain more programmes than the first screen holds."""

    NONE = "none"
    SCROLL = "scroll"
    URL_PAGES = "url_pages"
```

Add `paginate` to `Strategy` (after the `params` field). `PaginateMode` is
defined just above `Strategy`, so use it directly as the default — no
`__post_init__` needed:

```python
@dataclass(frozen=True)
class Strategy:
    """Immutable pairing of a fetch mode and an extract kind, plus free-form params."""

    fetch: FetchMode
    extract: ExtractKind
    params: Dict[str, Any] = field(default_factory=dict)
    paginate: PaginateMode = PaginateMode.NONE

    def label(self) -> str:
        """Return a short human-readable identifier for this strategy."""
        return f"{self.fetch.value}×{self.extract.value}"
```

Add `CrawlRange` after `Strategy`:

```python
@dataclass(frozen=True)
class CrawlRange:
    """How much of an index to crawl. ``limit=None`` means all (safety-capped)."""

    limit: Optional[int] = 30
    paginate: bool = False

    @classmethod
    def default(cls) -> "CrawlRange":
        """First batch only, capped at 30 — the no-argument default."""
        return cls(limit=30, paginate=False)

    @classmethod
    def of(cls, n: int) -> "CrawlRange":
        """Crawl the first ``n`` programmes, paginating as needed."""
        return cls(limit=n, paginate=True)

    @classmethod
    def all_(cls) -> "CrawlRange":
        """Crawl everything, paginating to exhaustion (safety-capped)."""
        return cls(limit=None, paginate=True)
```

Add the two fields to `CrawlOutcome` (after `report_zip`):

```python
    pages_fetched: int = 0
    stopped_reason: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_types_pagination.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the existing suite to confirm no regression**

Run: `uv run pytest tests/test_crawl_strategy/ -q`
Expected: all pass (the `Strategy.paginate` default keeps existing rows valid).

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/types.py tests/test_crawl_strategy/test_types_pagination.py
git commit -m "feat(types): add PaginateMode, CrawlRange, Strategy.paginate, outcome fields"
```

---

### Task 2: Registry — pin a Paginate mechanism per known university

**Files:**
- Modify: `src/services/crawl_strategy/registry.py`
- Test: `tests/test_crawl_strategy/test_registry.py:1-35` (append cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_registry.py`:

```python
def test_known_sites_pin_paginate_mechanism():
    from src.services.crawl_strategy.types import PaginateMode
    assert lookup("https://study.nus.edu.sg/programme").paginate is PaginateMode.SCROLL
    assert lookup("https://courses.leeds.ac.uk/x").paginate is PaginateMode.URL_PAGES
    assert lookup("https://www.ucl.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.manchester.ac.uk/x").paginate is PaginateMode.NONE
    assert lookup("https://www.polyu.edu.hk/x").paginate is PaginateMode.NONE


def test_leeds_carries_url_page_param():
    s = lookup("https://courses.leeds.ac.uk/x")
    assert s.params.get("page_param") == "page"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v -k paginate`
Expected: FAIL (paginate is NONE for all, no page_param).

- [ ] **Step 3: Add the paginate field to each registry row**

Replace the `REGISTRY` dict in `src/services/crawl_strategy/registry.py` (and update the import line to include `PaginateMode`):

```python
from src.services.crawl_strategy.types import (
    ExtractKind, FetchMode, PaginateMode, Strategy,
)

REGISTRY: Dict[str, Strategy] = {
    "courses.leeds.ac.uk": Strategy(
        FetchMode.SERVER, ExtractKind.HEADING_LINK,
        params={"page_param": "page", "page_start": 1},
        paginate=PaginateMode.URL_PAGES),
    "www.ucl.ac.uk": Strategy(
        FetchMode.CLIENT, ExtractKind.INLINE_DEGREE,
        paginate=PaginateMode.NONE),
    "www.manchester.ac.uk": Strategy(
        FetchMode.CLIENT, ExtractKind.MERGED_COLUMNS,
        paginate=PaginateMode.NONE),
    "www.polyu.edu.hk": Strategy(
        FetchMode.CLIENT, ExtractKind.BLOB,
        paginate=PaginateMode.NONE),
    "study.nus.edu.sg": Strategy(
        FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
        paginate=PaginateMode.SCROLL),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v`
Expected: PASS (original cases + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/registry.py tests/test_crawl_strategy/test_registry.py
git commit -m "feat(registry): pin paginate mechanism per known university"
```

---

### Task 3: Paginator core — `none` + `url_pages` loop + stop signals

**Files:**
- Create: `src/services/crawl_strategy/paginator.py`
- Test: `tests/test_crawl_strategy/test_paginator.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_strategy/test_paginator.py`:

```python
from src.services.crawl_strategy.paginator import paginate, PaginateResult
from src.services.crawl_strategy.types import (
    CrawlRange, ExtractItem, ExtractKind, FetchMode, PaginateMode, Strategy,
)

_STRAT = Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK,
                  params={"page_param": "page", "page_start": 1},
                  paginate=PaginateMode.URL_PAGES)


def _items(prefix, n):
    return [ExtractItem(f"{prefix} {i} MSc", f"https://x.edu/{prefix}{i}") for i in range(n)]


def _extract_from_marker(md, base_url):
    # Test fake: markdown is "P:<prefix>:<n>"; emit n items named by prefix.
    _, prefix, n = md.split(":")
    return _items(prefix, int(n))


def test_none_truncates_first_page_to_limit():
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:25",
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 10
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 1


def test_none_returns_all_when_under_limit():
    r = paginate(
        mechanism=PaginateMode.NONE, crawl_range=CrawlRange.of(50),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:9",
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 9
    assert r.stopped_reason == "exhausted"


def test_url_pages_accumulates_across_pages_and_truncates():
    # page 1 has prefix a (5), page 2 prefix b (5), page 3 prefix c (5)
    pages = {1: "P:a:5", 2: "P:b:5", 3: "P:c:5"}

    def server(url):
        # page number is the ?page=N value; page 1 = no query (first_md)
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        return ("<html>", pages[int(q.get("page", 2))])

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.of(12),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md=pages[1],
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 12         # 5 + 5 + 2 (truncated)
    assert r.stopped_reason == "reached_limit"
    assert r.pages_fetched == 3


def test_url_pages_stops_exhausted_on_zero_new():
    # every page after 1 repeats prefix a → 0 new names
    def server(url):
        return ("<html>", "P:a:5")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:5",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 5
    assert r.stopped_reason == "exhausted"


def test_url_pages_stops_unusable():
    def server(url):
        return ("<html>", "")   # empty → not usable

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:5",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 5
    assert r.stopped_reason == "unusable"


def test_url_pages_safety_cap():
    # each page yields fresh names forever; all_ must stop at the page ceiling
    def server(url):
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        return ("<html>", f"P:p{q.get('page', '0')}:3")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:first:3",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert r.stopped_reason == "safety_cap"
    assert r.pages_fetched == 50


def test_url_pages_paginate_false_stays_on_first_page():
    def server(url):
        raise AssertionError("paginate=False must not fetch page 2")

    r = paginate(
        mechanism=PaginateMode.URL_PAGES, crawl_range=CrawlRange.default(),
        index_url="https://x.edu/p", strategy=_STRAT,
        first_html="<html>", first_md="P:a:9",
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        extract=_extract_from_marker)
    assert len(r.items) == 9
    assert r.pages_fetched == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v`
Expected: FAIL with `ModuleNotFoundError: paginator`.

- [ ] **Step 3: Create the paginator with `none` + `url_pages` + stop signals**

Create `src/services/crawl_strategy/paginator.py`:

```python
"""Pagination loop, stop signals, and unknown-site mechanism detection.

Pure and browser-free: all I/O is via injected ``server_fetch`` /
``client_fetch`` / ``extract`` callables, so the whole module is unit-testable
with fakes.  The ``scroll`` mechanism's range-awareness lives inside the
``client_wait`` fetch adapter (it scrolls to a target); ``url_pages`` loops at
this level; ``none`` is a single page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from src.services.crawl_strategy.fetch_ladder import content_is_usable
from src.services.crawl_strategy.types import (
    CrawlRange, ExtractItem, FetchMode, PaginateMode, Strategy,
)

Fetch = Callable[..., Tuple[str, str]]
Extract = Callable[[str, str], List[ExtractItem]]

_MAX_URL_PAGES = 50
_MAX_SCROLL_ROUNDS = 40

_NEXT_LINK_RE = re.compile(
    r"\[[^\]]*(?:next|下一页|›|»)[^\]]*\]\(\s*([^)\s]+)", re.IGNORECASE)
_PAGE_PARAM_RE = re.compile(r"[?&](page|pg|p)=(\d+)", re.IGNORECASE)


@dataclass
class PaginateResult:
    """Outcome of a paging run: collected items, pages touched, why it stopped."""

    items: List[ExtractItem]
    pages_fetched: int
    stopped_reason: str  # reached_limit|exhausted|unusable|no_growth|safety_cap


def _key(item: ExtractItem) -> str:
    return item.name_en.casefold()


def _absorb(acc: List[ExtractItem], seen: set, new_items: List[ExtractItem]) -> None:
    for it in new_items:
        k = _key(it)
        if k in seen:
            continue
        seen.add(k)
        acc.append(it)


def _truncate(items: List[ExtractItem], limit: Optional[int]) -> List[ExtractItem]:
    return items if limit is None else items[:limit]


def _reached(acc: List[ExtractItem], limit: Optional[int]) -> bool:
    return limit is not None and len(acc) >= limit


def _set_query(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != param.lower()]
    q.append((param, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(q), parts.fragment))


def _derive_next_url(current_url: str, page_idx: int, markdown: str,
                     params: dict) -> Optional[str]:
    """Return the next page's URL, or None if one cannot be derived.

    Priority: registry-pinned page param → an existing ?page=N in the URL →
    a 'next' link found in the markdown.
    """
    page_param = params.get("page_param")
    if page_param:
        start = int(params.get("page_start", 1))
        return _set_query(current_url, page_param, start + page_idx)
    m = _PAGE_PARAM_RE.search(current_url)
    if m:
        return _set_query(current_url, m.group(1), int(m.group(2)) + 1)
    m = _NEXT_LINK_RE.search(markdown or "")
    if m:
        return urljoin(current_url, m.group(1))
    return None


def _fetch_for(strategy: Strategy, server_fetch: Fetch, client_fetch: Fetch) -> Fetch:
    if strategy.fetch is FetchMode.SERVER:
        return lambda u: server_fetch(u)
    return lambda u: client_fetch(u)


def _single(first_md: str, index_url: str, crawl_range: CrawlRange,
            extract: Extract) -> PaginateResult:
    items = extract(first_md, index_url)
    reason = "reached_limit" if _over(items, crawl_range.limit) else "exhausted"
    return PaginateResult(_truncate(items, crawl_range.limit), 1, reason)


def _over(items: List[ExtractItem], limit: Optional[int]) -> bool:
    return limit is not None and len(items) > limit


def _paginate_url(*, index_url: str, strategy: Strategy, first_md: str,
                  crawl_range: CrawlRange, server_fetch: Fetch,
                  client_fetch: Fetch, extract: Extract) -> PaginateResult:
    fetch = _fetch_for(strategy, server_fetch, client_fetch)
    acc: List[ExtractItem] = []
    seen: set = set()
    _absorb(acc, seen, extract(first_md, index_url))
    pages = 1
    cur_url, cur_md = index_url, first_md
    while True:
        if _reached(acc, crawl_range.limit):
            return PaginateResult(_truncate(acc, crawl_range.limit), pages, "reached_limit")
        if pages >= _MAX_URL_PAGES:
            return PaginateResult(_truncate(acc, crawl_range.limit), pages, "safety_cap")
        nxt = _derive_next_url(cur_url, pages, cur_md, strategy.params)
        if not nxt:
            return PaginateResult(acc, pages, "exhausted")
        _html, md = fetch(nxt)
        pages += 1
        if not content_is_usable(md):
            return PaginateResult(acc, pages, "unusable")
        before = len(acc)
        _absorb(acc, seen, extract(md, nxt))
        cur_url, cur_md = nxt, md
        if len(acc) == before:
            return PaginateResult(acc, pages, "exhausted")


def paginate(*, mechanism: PaginateMode, crawl_range: CrawlRange, index_url: str,
             strategy: Strategy, first_html: str, first_md: str,
             server_fetch: Fetch, client_fetch: Fetch,
             extract: Extract) -> PaginateResult:
    """Collect programme items per *mechanism*, honouring *crawl_range*.

    ``paginate=False`` (the default range) keeps url_pages on page 1; scroll is
    always driven by ``crawl_range.limit`` as its target; none is one page.
    """
    del first_html  # kept in the signature for symmetry with the scroll branch
    if mechanism is PaginateMode.SCROLL:
        return _paginate_scroll(
            index_url=index_url, crawl_range=crawl_range,
            client_fetch=client_fetch, extract=extract, first_md=first_md)
    if mechanism is PaginateMode.URL_PAGES and crawl_range.paginate:
        return _paginate_url(
            index_url=index_url, strategy=strategy, first_md=first_md,
            crawl_range=crawl_range, server_fetch=server_fetch,
            client_fetch=client_fetch, extract=extract)
    return _single(first_md, index_url, crawl_range, extract)
```

Add a temporary stub for `_paginate_scroll` at the end (Task 5 fleshes it out) so the module imports cleanly:

```python
def _paginate_scroll(*, index_url, crawl_range, client_fetch, extract,
                     first_md) -> PaginateResult:
    items = extract(first_md, index_url)
    reason = "reached_limit" if _over(items, crawl_range.limit) else "exhausted"
    return PaginateResult(_truncate(items, crawl_range.limit), 1, reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/paginator.py tests/test_crawl_strategy/test_paginator.py
git commit -m "feat(paginator): none + url_pages loop with stop signals"
```

---

### Task 4: Mechanism auto-detection for unknown sites

**Files:**
- Modify: `src/services/crawl_strategy/paginator.py`
- Test: `tests/test_crawl_strategy/test_paginator.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_paginator.py`:

```python
from src.services.crawl_strategy.paginator import detect_mechanism


def test_detect_url_pages_from_page_query_link():
    md = "[Next](https://x.edu/p?page=2)\n[Course MSc](https://x.edu/c1)\n"
    assert detect_mechanism("<html>", md, "https://x.edu/p", "server") is PaginateMode.URL_PAGES


def test_detect_url_pages_from_existing_page_param_in_url():
    assert detect_mechanism(
        "<html>", "[c](u)\n", "https://x.edu/p?page=1", "server"
    ) is PaginateMode.URL_PAGES


def test_detect_scroll_for_client_wait_app():
    assert detect_mechanism(
        "<html>", "## Doctor of X\n", "https://x.edu/p", "client_wait"
    ) is PaginateMode.SCROLL


def test_detect_none_for_static_single_page():
    assert detect_mechanism(
        "<html>", "[Course MSc](https://x.edu/c1)\n", "https://x.edu/p", "server"
    ) is PaginateMode.NONE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v -k detect`
Expected: FAIL with `ImportError` (detect_mechanism undefined).

- [ ] **Step 3: Implement `detect_mechanism`**

Add to `src/services/crawl_strategy/paginator.py` (after `paginate`):

```python
def detect_mechanism(first_html: str, first_md: str, index_url: str,
                     fetch_level: str) -> PaginateMode:
    """Infer how an unknown site paginates, from its first page.

    Conservative: only return URL_PAGES when a concrete next-page URL can be
    derived; only return SCROLL when the page is a JS app (client_wait); else
    NONE.  Never guesses url_pages blindly (would burn tokens — requirement 4).
    """
    del first_html
    if _derive_next_url(index_url, 1, first_md, {}) is not None:
        return PaginateMode.URL_PAGES
    if fetch_level == FetchMode.CLIENT_WAIT.value:
        return PaginateMode.SCROLL
    return PaginateMode.NONE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v`
Expected: PASS (11 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/paginator.py tests/test_crawl_strategy/test_paginator.py
git commit -m "feat(paginator): auto-detect paginate mechanism for unknown sites"
```

---

### Task 5: Range-aware scroll fetch + paginator scroll branch

**Files:**
- Modify: `src/services/crawl_strategy/fetch_adapters.py:55-95` (the `_run_client_wait_fetch` + `client_fetch`)
- Modify: `src/services/crawl_strategy/paginator.py` (replace the `_paginate_scroll` stub)
- Test: `tests/test_crawl_strategy/test_paginator.py` (append) and `tests/test_crawl_strategy/test_fetch_adapters.py` (append)

- [ ] **Step 1: Write the failing test for the scroll branch**

Append to `tests/test_crawl_strategy/test_paginator.py`:

```python
def test_scroll_passes_target_count_and_truncates():
    received = {}

    def client(url, **kw):
        received.update(kw)
        return ("<html>", "P:a:25")   # browser rendered 25 after scrolling

    r = paginate(
        mechanism=PaginateMode.SCROLL, crawl_range=CrawlRange.of(10),
        index_url="https://x.edu/p",
        strategy=Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                          paginate=PaginateMode.SCROLL),
        first_html="<html>", first_md="P:a:5",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        extract=_extract_from_marker)
    assert received.get("wait") is True
    assert received.get("target_count") == 10
    assert len(r.items) == 10
    assert r.stopped_reason == "reached_limit"


def test_scroll_all_passes_none_target():
    received = {}

    def client(url, **kw):
        received.update(kw)
        return ("<html>", "P:a:8")

    r = paginate(
        mechanism=PaginateMode.SCROLL, crawl_range=CrawlRange.all_(),
        index_url="https://x.edu/p",
        strategy=Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
                          paginate=PaginateMode.SCROLL),
        first_html="<html>", first_md="",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        extract=_extract_from_marker)
    assert received.get("target_count") is None
    assert len(r.items) == 8
    assert r.stopped_reason == "exhausted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v -k scroll`
Expected: FAIL (the stub ignores client_fetch; received stays empty, items come from first_md).

- [ ] **Step 3: Replace the `_paginate_scroll` stub**

In `src/services/crawl_strategy/paginator.py`, replace the stub `_paginate_scroll` with:

```python
def _paginate_scroll(*, index_url: str, crawl_range: CrawlRange,
                     client_fetch: Fetch, extract: Extract,
                     first_md: str) -> PaginateResult:
    del first_md  # scroll always re-fetches with the proper target_count
    _html, md = client_fetch(index_url, wait=True, target_count=crawl_range.limit)
    items = extract(md, index_url)
    reason = "reached_limit" if _over(items, crawl_range.limit) else "exhausted"
    return PaginateResult(_truncate(items, crawl_range.limit), 1, reason)
```

(For scroll, the adapter's internal no-growth / max-rounds ceilings surface as
`exhausted` — the round-level detail lives in the run log, not the outcome.)

- [ ] **Step 4: Run the scroll branch test**

Run: `uv run pytest tests/test_crawl_strategy/test_paginator.py -v -k scroll`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing test for the range-aware adapter**

Append to `tests/test_crawl_strategy/test_fetch_adapters.py` (create the file if it does not exist, with the import line below at the top):

```python
import src.services.crawl_strategy.fetch_adapters as fa


def test_client_fetch_wait_forwards_target_count(monkeypatch):
    seen = {}

    def fake_wait(url, *, target_count=None, max_rounds=40):
        seen["target_count"] = target_count
        return "<html>Doctor of X</html>"

    monkeypatch.setattr(fa, "_run_client_wait_fetch", fake_wait)
    monkeypatch.setattr(fa, "_html_to_markdown", lambda html, url: "## Doctor of X")

    html, md = fa.client_fetch("https://x.edu/p", wait=True, target_count=17)
    assert seen["target_count"] == 17
    assert md == "## Doctor of X"


def test_enough_matches_gates_the_scroll_stop():
    # The scroll loop stops once this helper says enough programme names show.
    from src.services.crawl_strategy.fetch_adapters import _enough_matches
    assert _enough_matches("Doctor of A Doctor of B", 2) is True
    assert _enough_matches("Doctor of A", 2) is False
    assert _enough_matches("anything", None) is False   # target=None never "enough"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v`
Expected: FAIL (`client_fetch` has no `target_count`; `_enough_matches` undefined).

- [ ] **Step 7: Make the adapter range-aware**

In `src/services/crawl_strategy/fetch_adapters.py`, add the helper and rewrite the scroll loop + `client_fetch`. Replace `_run_client_wait_fetch` and `client_fetch`:

```python
def _enough_matches(html: str, target_count: Optional[int]) -> bool:
    """True when *html* already shows >= target_count programme names."""
    if target_count is None:
        return False
    return len(_PROGRAM_RENDER_RE.findall(html or "")) >= target_count


def _run_client_wait_fetch(url: str, *, target_count: Optional[int] = None,
                           max_rounds: int = 40) -> str:  # noqa: C901
    """Fetch *url* via Playwright headless Chromium, scrolling to a target.

    Scrolls until the rendered HTML shows >= ``target_count`` programme names,
    OR its byte length stops growing for two consecutive rounds, OR
    ``max_rounds`` is reached.  ``target_count=None`` (the 'all' case) scrolls
    to the no-growth / max_rounds ceiling.  Returns the final HTML.
    """
    from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            html = ""
            prev_len = 0
            stale = 0
            for _ in range(max_rounds):
                page.mouse.wheel(0, _CLIENT_WAIT_SCROLL_PIXELS)
                page.wait_for_timeout(_CLIENT_WAIT_TICK_MS)
                html = page.content()
                if _enough_matches(html, target_count):
                    break
                if len(html) <= prev_len:
                    stale += 1
                    if stale >= 2:
                        break
                else:
                    stale = 0
                prev_len = len(html)
            if not html:
                html = page.content()
            return html
    except Exception:  # pylint: disable=broad-except
        return ""
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # pylint: disable=broad-except
                pass


def client_fetch(url: str, *, wait: bool = False,
                 wait_selector: Optional[str] = None,
                 target_count: Optional[int] = None, **_: Any) -> Tuple[str, str]:
    """Fetch *url* via native Chrome CDP or Playwright range-aware scroll.

    Args:
        url:           Target URL.
        wait:          When True, use the Playwright scroll-and-wait render
                       (``_run_client_wait_fetch``), scrolling toward
                       ``target_count`` programme names.  When False, use the
                       native Chrome CDP path (``_run_client_fetch``).
        wait_selector: Accepted for interface compatibility; not forwarded.
        target_count:  Scroll target (programme-name count); None scrolls to the
                       no-growth / max-rounds ceiling.

    Returns:
        ``(html, markdown)`` tuple; either field is an empty string on failure.
    """
    if wait:
        html = _run_client_wait_fetch(url, target_count=target_count)
        return (html, _html_to_markdown(html, url) if html else "")
    payload = _run_client_fetch(url, wait_selector=wait_selector)
    html = str(payload.get("html_content") or "")
    return (html, _html_to_markdown(html, url) if html else "")
```

Delete the now-unused module constant `_CLIENT_WAIT_SCROLL_ROUNDS` and
`_CLIENT_WAIT_MIN_MATCHES` (their behaviour moved into the parameters). Keep
`_CLIENT_WAIT_SCROLL_PIXELS` and `_CLIENT_WAIT_TICK_MS`.

- [ ] **Step 8: Run the adapter tests**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/services/crawl_strategy/paginator.py src/services/crawl_strategy/fetch_adapters.py tests/test_crawl_strategy/test_paginator.py tests/test_crawl_strategy/test_fetch_adapters.py
git commit -m "feat(scroll): range-aware client_wait fetch + paginator scroll branch"
```

---

### Task 6: Orchestrator — thread crawl_range, dispatch via paginate()

**Files:**
- Modify: `src/services/crawl_strategy/orchestrator.py`
- Test: `tests/test_crawl_strategy/test_orchestrator.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_orchestrator.py`:

```python
from src.services.crawl_strategy.types import CrawlRange, PaginateMode


def test_default_range_caps_known_single_page_at_30(tmp_path):
    # UCL-like: one page with 60 inline-degree links; default caps at 30.
    md = "".join(
        f"[Programme {i} BSc](https://www.ucl.ac.uk/p{i})\n" for i in range(60))

    def client(url, **kw):
        return ("<html>", md)

    out = crawl_index(
        "https://www.ucl.ac.uk/degrees",
        server_fetch=lambda u: ("", ""), client_fetch=client,
        report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 30
    assert out.stopped_reason == "reached_limit"
    assert out.pages_fetched == 1


def test_explicit_all_returns_everything(tmp_path):
    md = "".join(
        f"[Programme {i} BSc](https://www.ucl.ac.uk/p{i})\n" for i in range(60))

    def client(url, **kw):
        return ("<html>", md)

    out = crawl_index(
        "https://www.ucl.ac.uk/degrees", crawl_range=CrawlRange.all_(),
        server_fetch=lambda u: ("", ""), client_fetch=client,
        report_out=tmp_path, timestamp="t")
    assert out.names_count == 60
    assert out.stopped_reason == "exhausted"


def test_leeds_url_pages_paginates_when_limit_given(tmp_path):
    def make_md(tag, n):
        return "".join(
            f"##  [{tag} {i} MSc](https://courses.leeds.ac.uk/{tag}{i}) D\n"
            for i in range(n))

    def server(url):
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlsplit(url).query))
        page = int(q.get("page", 1))
        return ("<html>", make_md(f"p{page}", 15))

    out = crawl_index(
        "https://courses.leeds.ac.uk/search", crawl_range=CrawlRange.of(40),
        server_fetch=server, client_fetch=lambda u, **k: ("", ""),
        report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 40        # 15 + 15 + 10 (truncated)
    assert out.pages_fetched == 3
    assert out.stopped_reason == "reached_limit"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v -k "range or all or url_pages"`
Expected: FAIL (`crawl_index` has no `crawl_range`; no `stopped_reason`/`pages_fetched`).

- [ ] **Step 3: Wire the orchestrator to the paginator**

In `src/services/crawl_strategy/orchestrator.py`, update the imports and the `crawl_index` body. Add imports:

```python
from src.services.crawl_strategy.paginator import detect_mechanism, paginate
from src.services.crawl_strategy.types import (
    CrawlOutcome, CrawlRange, FetchMode, PaginateMode, Strategy,
)
```

Add the signature parameter (after `index_url`):

```python
def crawl_index(
    index_url: str,
    *,
    crawl_range: CrawlRange = None,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    report_out: "Path | str",
    timestamp: str,
) -> CrawlOutcome:
```

At the top of the body (after the docstring), normalise the default and resolve
strategy + mechanism:

```python
    if crawl_range is None:
        crawl_range = CrawlRange.default()
    uni = _university_slug(index_url)
    pinned: Optional[Strategy] = registry_mod.lookup(index_url)

    html, md, fetch_level, levels_tried = _do_fetch(
        index_url, pinned, server_fetch, client_fetch
    )

    if pinned:
        kind, confident = pinned.extract, True
        cr = None
        strategy, mechanism = pinned, pinned.paginate
    else:
        cr = classify(md, index_url)
        kind, confident = cr.kind, cr.confident
        mechanism = detect_mechanism(html, md, index_url, fetch_level)
        strategy = (
            Strategy(FetchMode(fetch_level), kind, paginate=mechanism)
            if kind is not None else None)
```

Replace the single-extract block:

```python
    items = []
    pages_fetched = 0
    stopped_reason = ""
    if confident and kind is not None and content_is_usable(md):
        pr = paginate(
            mechanism=mechanism, crawl_range=crawl_range, index_url=index_url,
            strategy=strategy, first_html=html, first_md=md,
            server_fetch=server_fetch, client_fetch=client_fetch,
            extract=get_extractor(kind))
        items = pr.items
        pages_fetched = pr.pages_fetched
        stopped_reason = pr.stopped_reason
```

Update the success return to carry the new fields and reason text:

```python
    if items:
        names = [it.name_en for it in items]
        strat = f"{fetch_level}×{kind.value}"
        reason_zh = _REASON_ZH.get(stopped_reason, "")
        return CrawlOutcome(
            status="ok", university=uni, names=names, items=items,
            names_count=len(names), strategy_used=strat,
            pages_fetched=pages_fetched, stopped_reason=stopped_reason,
            message_for_user=(
                f"成功抓取 {len(names)} 门课程名字"
                f"（策略 {strat}，翻页 {mechanism.value}，{reason_zh}）。"),
        )
```

Add the reason map near the top of the module (after the imports):

```python
_REASON_ZH = {
    "reached_limit": "因达到上限停止",
    "exhausted": "已抓完全部",
    "unusable": "因翻页中遇到无法解析的页面停止",
    "no_growth": "因内容不再增长停止",
    "safety_cap": "因达到安全翻页上限停止",
}
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v`
Expected: PASS (existing + 3 new). The existing tests pass unchanged because
`crawl_range` defaults to `None` → `CrawlRange.default()`, and the Leeds fixture
(15 items) is below the cap of 30 with `paginate=False`.

- [ ] **Step 5: Run the whole strategy suite**

Run: `uv run pytest tests/test_crawl_strategy/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/orchestrator.py tests/test_crawl_strategy/test_orchestrator.py
git commit -m "feat(orchestrator): thread CrawlRange and dispatch via paginate()"
```

---

### Task 7: CLI — `--limit` / `--all`

**Files:**
- Modify: `src/cmd/cli.py:515-548` (the `crawl-index` command)
- Test: `tests/test_crawl_strategy/test_cli_range.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_strategy/test_cli_range.py`:

```python
import pytest

from src.cmd.cli import _resolve_crawl_range
from src.services.crawl_strategy.types import CrawlRange


def test_resolve_default_when_neither_given():
    r = _resolve_crawl_range(limit=None, all_=False)
    assert r.limit == 30 and r.paginate is False


def test_resolve_limit():
    r = _resolve_crawl_range(limit=200, all_=False)
    assert r.limit == 200 and r.paginate is True


def test_resolve_all():
    r = _resolve_crawl_range(limit=None, all_=True)
    assert r.limit is None and r.paginate is True


def test_resolve_rejects_both():
    with pytest.raises(ValueError):
        _resolve_crawl_range(limit=10, all_=True)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_cli_range.py -v`
Expected: FAIL (`_resolve_crawl_range` undefined).

- [ ] **Step 3: Add the resolver and CLI options**

In `src/cmd/cli.py`, add the helper above `crawl_index_cmd` (and import `CrawlRange`
near the top with the other crawl_strategy imports):

```python
from src.services.crawl_strategy.types import CrawlRange


def _resolve_crawl_range(*, limit: Optional[int], all_: bool) -> CrawlRange:
    """Map CLI --limit/--all to a CrawlRange. Mutually exclusive."""
    if all_ and limit is not None:
        raise ValueError("--limit and --all are mutually exclusive")
    if all_:
        return CrawlRange.all_()
    if limit is not None:
        return CrawlRange.of(limit)
    return CrawlRange.default()
```

Add the two options to `crawl_index_cmd` and pass the resolved range. Insert the
options in the signature:

```python
    limit: Optional[int] = typer.Option(
        None, "--limit", help="抓取前 N 门课程名字（翻页直到 N）。"),
    all_: bool = typer.Option(
        False, "--all", help="抓取全部（翻页到底，有安全上限）。"),
```

In the body, build the range and pass it (replace the `crawl_index(...)` call):

```python
    crawl_range = _resolve_crawl_range(limit=limit, all_=all_)
    outcome = crawl_index(
        index_url,
        crawl_range=crawl_range,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        report_out=out_dir, timestamp=timestamp,
    )
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_crawl_strategy/test_cli_range.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Smoke the CLI help**

Run: `uv run python -m src.cmd.cli crawl-index --help`
Expected: help text shows `--limit` and `--all`.

- [ ] **Step 6: Commit**

```bash
git add src/cmd/cli.py tests/test_crawl_strategy/test_cli_range.py
git commit -m "feat(cli): add --limit/--all crawl range to crawl-index"
```

---

### Task 8: Skill decision table — document the range parameter

**Files:**
- Modify: `skills/uni-admission-crawl/SKILL.md`
- Test: `tests/test_crawl_strategy/test_skill_decision_table.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_skill_decision_table.py`:

```python
def test_skill_documents_range_and_stop_reason():
    text = SKILL.read_text(encoding="utf-8")
    assert "--limit" in text
    assert "--all" in text
    assert "stopped_reason" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v -k range`
Expected: FAIL (terms absent).

- [ ] **Step 3: Add a range section to the skill**

Append to `skills/uni-admission-crawl/SKILL.md` (under the strategy-based crawl section):

```markdown
### Crawl range (how many to fetch)

The caller chooses how much to crawl; the tool paginates and auto-stops.

| Want | Command |
|---|---|
| Default (first batch, ≤30) | `adm-agent crawl-index <url>` |
| First N | `adm-agent crawl-index <url> --limit N` |
| Everything (safety-capped) | `adm-agent crawl-index <url> --all` |

The result JSON carries `pages_fetched` and `stopped_reason`
(`reached_limit` / `exhausted` / `unusable` / `no_growth` / `safety_cap`).
Relay `message_for_user` verbatim — it already explains why crawling stopped.
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/uni-admission-crawl/SKILL.md tests/test_crawl_strategy/test_skill_decision_table.py
git commit -m "docs(skill): document --limit/--all crawl range + stop reasons"
```

---

### Task 9: Full-suite + lint gate, then live acceptance

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `uv run pytest -q`
Expected: all pass (the prior ~877 + the new pagination tests), no failures.

- [ ] **Step 2: Run the lint gate (the CI command)**

Run: `uv run pylint $(git ls-files '*.py')`
Expected: rated 10.00/10, exit code 0 (no messages). Fix any new lint message
in the file it points to, then re-run until clean.

- [ ] **Step 3: Live NUS scroll acceptance (manual, needs network + browser)**

Run: `uv run python -m src.cmd.cli crawl-index 'https://study.nus.edu.sg/programme' --all --json`
Expected: `names_count` substantially greater than 10 (more than the first
lazy-load batch), `stopped_reason` one of `exhausted`/`safety_cap`. Record the
count. If it still returns ~10, the scroll loop is stopping too early — inspect
the `max_rounds` / no-growth logic in `_run_client_wait_fetch`.

- [ ] **Step 4: Live Leeds url_pages acceptance (manual)**

Run: `uv run python -m src.cmd.cli crawl-index 'https://courses.leeds.ac.uk/course-search/masters-courses' --limit 60 --json`
Expected: `pages_fetched > 1`, `names_count == 60`, `stopped_reason == reached_limit`.
If page 2 comes back `unusable`, the Leeds `page_param` in the registry is wrong —
inspect the live pagination URL and correct `params={"page_param": ...}` in
`registry.py`, then re-run.

- [ ] **Step 5: Commit any fixes from acceptance**

```bash
git add -A
git commit -m "fix(pagination): corrections from live NUS/Leeds acceptance"
```

(Skip this commit if acceptance passed with no changes.)

---

## Notes for the implementer

- **TDD discipline:** every task is test-first. Run the failing test before
  implementing.
- **pylint is strict:** the CI gate (`pylint $(git ls-files '*.py')`) fails on
  ANY message even at 10.0/10. Keep functions small, add docstrings to public
  functions, and avoid unused imports/arguments (use `del arg` where a param
  exists only for interface symmetry, as the existing code does).
- **Don't break the index-name-authoritative rule:** pagination only changes
  *how many* names are collected, never *how* a name is derived.
- **Scroll re-fetches:** for a known scroll site, `paginate()` re-fetches with
  the proper `target_count` (one extra browser session, NUS-only). This is
  intentional and keeps scroll's range logic in one place; do not try to thread
  `target_count` through the orchestrator's first fetch.
