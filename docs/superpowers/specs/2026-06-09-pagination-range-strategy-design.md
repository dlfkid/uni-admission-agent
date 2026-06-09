# Pagination & Range Strategy — Design Spec

**Date:** 2026-06-09
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/pagination-range-strategy` (to be created)
**Builds on:** `2026-06-09-crawl-strategy-system-design.md` (the Fetch × Extract backbone, now merged)

## Problem

The crawl-strategy system fetches a programme-index page **once** and extracts
one screen's worth of names. It has no notion of *how many* programmes the
caller wants, and no way to keep going past the first batch. Concretely:

- UCL returns its full ~433 on one page (no pagination needed, but no way to
  cap it either).
- NUS lazy-loads on scroll; we stop after the first render batch (~10), far
  short of the full catalog.
- Leeds-style listings paginate by URL (`?page=N`); we only ever see page 1.

This spec adds a **third axis — Paginate** — plus a caller-supplied **range**,
so the system can crawl exactly as much as asked, by whichever paging mechanism
the site uses, and stop safely the moment it stops making progress.

### Requirements (verbatim intent)

1. The caller (agent, before invoking the plugin) can pass a **crawl range** as
   a parameter.
2. Range examples: first 10, first 200, all.
3. Both **scroll-to-load** and **URL-pagination** sites honour the range and
   auto-stop accordingly.
4. Pagination **auto-stops on anomaly** — clearly can't get data, or hits a
   page form with no strategy — so it never wastes tokens.

## Core decisions (from brainstorming)

- **Known sites pinned, unknown sites auto-detect.** Pagination mechanism is a
  registry field for the 5 known universities; for unknown universities it is
  auto-detected at runtime. Rationale: a user's URL will rarely match a golden
  sample, so pagination that only worked for registered sites would be useless
  until a developer added a golden sample. Auto-detect is made token-safe by
  the requirement-4 stop signals.
- **Range = item count, truncate to exactly N.** Crossing N mid-batch returns
  exactly N (predictable). `all` = unbounded (with a hard safety ceiling).
- **Default = first batch ∧ ≤30, whichever comes first.** Not passing a range
  fetches only the first batch and caps at 30 items (so a casual call never
  burns tokens on a UCL-style 400-item page). Full coverage requires an
  explicit `all`.

## Architecture: Paginate as a third axis

Two axes existed (Fetch × Extract). This adds a third:

```
PaginateMode  (how to obtain MORE than the first screen)
  none        single page holds everything; never paginate     UCL
  scroll      infinite scroll; loop lives inside the browser    NUS
  url_pages   ?page=N iteration; loop lives in the orchestrator  Leeds-style
```

### Key insight: scroll and url_pages are two different loop shapes

They cannot share one loop:

- **scroll** — the page is one continuous session; you cannot
  "fetch a page → extract → decide next page". Range-awareness must live
  **inside the `client_wait` fetch**: scroll until the rendered HTML contains
  ≥ `limit` programme-name matches, **or** content stops growing, **or** the
  round ceiling is hit. Then extract once.
- **url_pages** — each page is an independent URL; the loop lives in the
  **orchestrator**: `for page in 1..max: fetch page → extract → dedup-accumulate
  → stop on signal → truncate`.

So the `paginate()` dispatcher branches by mechanism; the two branches have
genuinely different internals.

---

## Module 1 — Types (`types.py`, extended)

```python
class PaginateMode(str, Enum):
    NONE = "none"
    SCROLL = "scroll"
    URL_PAGES = "url_pages"

@dataclass(frozen=True)
class CrawlRange:
    limit: Optional[int] = 30      # None = all (unbounded, safety-capped)
    paginate: bool = False         # whether to go past the first batch

    @classmethod
    def default(cls):  return cls(limit=30,   paginate=False)  # first batch ∧ ≤30
    @classmethod
    def of(cls, n):    return cls(limit=n,     paginate=True)   # first N
    @classmethod
    def all_(cls):     return cls(limit=None,  paginate=True)   # everything
```

- `Strategy` gains `paginate: PaginateMode = PaginateMode.NONE` (pinned per
  known site). Pagination params (e.g. URL page-query name) live in the
  existing `Strategy.params` dict.
- `CrawlOutcome` gains `pages_fetched: int = 0` and `stopped_reason: str = ""`
  so the weak agent can relay *why* crawling stopped.

## Module 2 — Paginator (`paginator.py`, new)

Pure, browser-free, fully unit-testable with injected fetch/extract callables.

```python
@dataclass
class PaginateResult:
    items: List[ExtractItem]
    pages_fetched: int
    stopped_reason: str  # reached_limit|exhausted|unusable|no_growth|safety_cap

def paginate(
    *, mechanism: PaginateMode, crawl_range: CrawlRange, index_url: str,
    strategy: Strategy, first_html: str, first_md: str,
    server_fetch, client_fetch, extract,
) -> PaginateResult: ...

def detect_mechanism(
    first_html: str, first_md: str, index_url: str, fetch_level: str,
) -> PaginateMode: ...
```

`paginate()` branches:

- **none** — extract the first page, truncate to `limit`.
- **scroll** — re-fetch via `client_wait` with `target_count=limit` (or the
  scroll ceiling when `limit is None`), extract once, truncate.
- **url_pages** — loop `page=2,3,…` using the same fetch mode and extractor as
  page 1, dedup-accumulate (by name + canonical URL, reusing the extractor's
  existing dedup), apply stop signals, truncate.

**Role of the `paginate` flag per mechanism** (resolves the default-vs-mechanism
interaction): `paginate=False` (the default range) means *do not fetch beyond
the first page*. It only gates `url_pages` — when `False`, page 1 is fetched and
truncated, pages 2+ are never requested. For `scroll`, `target_count=limit`
always drives the scroll (a scroll site needs some scrolling just to render its
first batch); `paginate` does not gate it — `limit` does. For `none`, there is
nothing to paginate. Thus the default range (`limit=30, paginate=False`) yields:
url_pages → page 1 truncated to 30; scroll → scroll until 30 names; none →
first page truncated to 30.

### Stop signals (constants in `paginator.py`; both shapes)

| Signal | Threshold | `stopped_reason` | Meets req |
|---|---|---|---|
| Reached | accumulated ≥ `limit` | `reached_limit` | normal (then truncate) |
| Exhausted | a page/round yields 0 **new** names | `exhausted` | hit the end |
| Unusable | a fetched page fails `content_is_usable` | `unusable` | req 4 (can't get data) |
| No growth | accumulated count unchanged 2 pages/rounds running | `no_growth` | URL drift / repeat content |
| Safety cap | url_pages ≤ **50 pages**; scroll ≤ **40 rounds** | `safety_cap` | req 4 (even `all` can't run away) |

Notes:
- `url_pages` exhaustion fires on **one** zero-new-names page (over-paging a URL
  listing usually yields an empty/repeat page immediately).
- `scroll` no-growth tolerates one extra round (render latency): stop after
  **2** consecutive rounds where the HTML byte length does not grow.
- Any signal stops the loop immediately; no signal is skipped to "try once more".

### Auto-detect (`detect_mechanism`, unknown sites, after first fetch)

```
1. First HTML/MD shows pagination controls?
     URL contains ?page= / &page= / /page/N, OR link text matches
     Next / 下一页 / › — AND a concrete next-page URL can be derived  → URL_PAGES
2. Else, was the first page fetched via client_wait (a JS app)?
     Scroll one more leg; HTML byte length grows > 10%               → SCROLL
3. Else                                                              → NONE
```

Conservative by design: if no concrete next-page URL can be derived, do **not**
guess `url_pages` — prefer `none` (under-fetch) over blind paging that burns
tokens (req 4).

## Module 3 — Fetch adapter (`fetch_adapters.py`, extended)

`_run_client_wait_fetch` becomes range-aware:

```python
def _run_client_wait_fetch(url, *, target_count=None, max_rounds=40) -> str: ...
```

Scroll until the rendered HTML contains ≥ `target_count` programme-name matches,
**or** content stops growing for 2 rounds, **or** `max_rounds` is reached.
`client_fetch(url, wait=True, target_count=...)` forwards it. When
`target_count is None` (the `all` case) scrolling runs to the no-growth /
`max_rounds` ceiling. (This replaces today's fixed "stop at ≥5 matches".)

## Module 4 — Orchestrator (`orchestrator.py`, extended)

`crawl_index` gains `crawl_range: CrawlRange = CrawlRange.default()`:

```
1. Resolve strategy   registry hit → pinned (carries paginate); miss → classify
2. First fetch        pinned fetch / escalation ladder (unchanged)
3. Resolve mechanism  pinned.paginate if known; else detect_mechanism(first page)
4. paginate(...)      dispatch by mechanism (Module 2)
5. Assemble CrawlOutcome  + pages_fetched + stopped_reason
```

The `ok` message gains the stop reason, e.g.
`成功抓取 30 门课程名字（策略 client_wait×text_heading，翻页 scroll，因达到上限停止）。`
`unsupported` / report paths are unchanged.

## Module 5 — Registry (`registry.py`, extended)

Each of the 5 known universities gains a pinned `paginate`:

| Host | paginate | note |
|---|---|---|
| `courses.leeds.ac.uk` | `url_pages` | `?page=N` listing |
| `www.ucl.ac.uk` | `none` | full list on one page |
| `www.manchester.ac.uk` | `none` | confirmed during implementation; default `none` if single-page |
| `www.polyu.edu.hk` | `none` | filter-gated completeness is out of scope |
| `study.nus.edu.sg` | `scroll` | Salesforce lazy-load |

The exact `url_pages` params for Leeds (page-query name, start index) are
verified against the live site during the implementing task.

## Module 6 — CLI + skill (`cli.py`, `SKILL.md`, extended)

```bash
adm-agent crawl-index <url>              # default: first batch ∧ ≤30
adm-agent crawl-index <url> --limit 200  # first 200
adm-agent crawl-index <url> --all        # everything (safety-capped)
```

`--limit N` → `CrawlRange.of(N)`; `--all` → `CrawlRange.all_()`; neither →
`CrawlRange.default()`. `--limit` and `--all` are mutually exclusive (error if
both). The skill decision table gains a row noting the range parameter and that
`stopped_reason` is relayed verbatim.

## Module 7 — Testing & acceptance

**Deterministic unit tests** (no network / browser / LLM; injected fakes):

- `CrawlRange`: `default` / `of` / `all_` produce the right `limit`·`paginate`.
- `paginate` × 3 mechanisms: a fake multi-page fetch proves accumulation,
  dedup, and truncation to exactly N.
- Each stop signal has its own case: `reached_limit` / `exhausted` /
  `unusable` / `no_growth` / `safety_cap`.
- `detect_mechanism`: feed (a) a first page with `?page=` links, (b) a JS app
  that grows on scroll, (c) a static single page → assert URL_PAGES / SCROLL /
  NONE.
- `registry`: assert each university's `paginate` field (regression guard).

**Integration / acceptance** (manual, like `naming_smoke` today):

- NUS `scroll` with `--all` returns substantially more than the first-batch 10.
- A Leeds `--limit 200` run paginates by URL and truncates at 200.
- A `--all` run on a deep listing stops at a `safety_cap` rather than running
  away.

**CI gate:** deterministic unit tests + pylint 10/10 + no regression of the
existing suite. Network/browser integration validated manually.

## Out of scope (this spec)

- Filter-enumeration pagination (PolyU intake filter) and load-more-button
  clicking — separate mechanisms, not the two the requirements named.
- The detail-field crawl pipeline (still Plan 2 of the parent spec); range
  applies to **names** for now.
- LLM-tier classify/extract (parent spec Plan 2).
