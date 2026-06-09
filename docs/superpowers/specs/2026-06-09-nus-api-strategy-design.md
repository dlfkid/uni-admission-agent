# NUS API FetchStrategy — Design Spec

**Date:** 2026-06-09
**Status:** Approved (brainstorming) → ready for implementation plan
**Branch:** `feat/nus-api-strategy`
**Builds on:** the crawl-strategy backbone (#36) and the pagination/range axis (#37), both merged.

## Problem

NUS (`study.nus.edu.sg/programme`) renders only ~10 programmes on screen and,
as a live probe confirmed, **does not scroll-load more** — its DOM is
byte-for-byte constant across scrolling. So neither `scroll` nor `url_pages`
can reach NUS's full catalogue, and NUS is currently pinned `none`, returning
just the 10 rendered programmes.

## Key finding (empirical)

NUS's full catalogue is served by a **Salesforce Apex API** that returns
everything in a single call — no pagination, no scrolling, no browser:

- `POST https://study.nus.edu.sg/webruntime/api/apex/execute?language=en-US&asGuest=true&htmlEncode=false`
- JSON body: `{"namespace":"","classname":"@udd/01pIW000000Rkpx","method":"searchProgrammes","isContinuation":false,"params":{"programmeType":"","interestArea":"[]","keyword":"","modeOfStudy":"","facultyIds":"","intakePeriod":""},"cacheable":false}`
- Empty filter params → the whole list. A plain server-side `httpx.post`
  (no browser, no auth — `asGuest=true`) returns **HTTP 200, ~303 KB**,
  containing **244 programmes** (vs the 10 rendered).
- Each item: `returnValue[].programme` with `Title__c` (the programme name,
  e.g. "Executive Master in AI and Digital Transformation"),
  `Program_Page_Link__c` (the detail page URL), plus metadata (`Type__c`,
  `Mode_of_Study__c`, `Area_of_Interest__c`, `Faculty_Reference__c`).

So the right solution is **not pagination** — it is the `api` FetchStrategy the
original crawl-strategy spec already reserved as "developer-authored after a
report." NUS stays `paginate=none`; completeness comes from the fetch layer.

## Core decisions (from brainstorming)

- **Config-driven `api`, not NUS-hardcoded.** Activate the existing-but-unused
  `FetchMode.API`. The registry pins the endpoint + request body + JSON field
  paths in `Strategy.params`; NUS is the first user. Future API sites add one
  registry row — no orchestration code changes.
- **Brittle classname ID → hardcode + report on failure.** The body's
  `classname:"@udd/01pIW000000Rkpx"` carries an internal Salesforce ID that may
  change on a NUS redeploy. It lives in the registry params. On failure (ID
  stale / endpoint error / empty array) the API yields unusable content and the
  existing "已知策略抓取失败" report path fires — a developer updates the one
  config line. **No silent fallback** to the 10-item render (that would mask the
  breakage and mislead).
- **Name = `Title__c`, detail_url = `Program_Page_Link__c`.** HTML entities
  (e.g. `&#39;`) are unescaped.
- **CrawlRange still applies.** The API returns all 244 in one call; `paginate=
  none` → `_single` truncates to the range (`--limit 30` → 30, `--all` → 244).

## Architecture: activate `FetchMode.API` + a config-driven JSON extractor

```
Strategy(fetch=API, extract=JSON_API, params={api config}, paginate=NONE)

NUS registry params:
  endpoint:        "https://study.nus.edu.sg/webruntime/api/apex/execute?language=en-US&asGuest=true&htmlEncode=false"
  body:            { classname, method: "searchProgrammes", params: {all empty}, ... }
  items_path:      "returnValue"
  name_path:       "programme.Title__c"
  detail_url_path: "programme.Program_Page_Link__c"
```

### Data flow

```
crawl_index(NUS, crawl_range)
  1. registry hit → Strategy(API, JSON_API, params, paginate=NONE)
  2. _do_fetch dispatches FetchMode.API → api_fetch(endpoint, body) → raw JSON string (~303 KB)
  3. usability: json_is_usable(json, items_path) (NOT the markdown link gate)
  4. paginate(mechanism=none) → _single → extract_json_api(json) → 244 ExtractItems
  5. truncate to crawl_range → CrawlOutcome
```

### Two nuances that must be handled

1. **The usability gate must change for `api`.** The existing `content_is_usable`
   counts markdown `[x](y)` links; a JSON payload has none and would be wrongly
   rejected → report path. The `api` path uses its own gate:
   `json_is_usable(text, items_path)` = "parses as JSON **and** `items_path`
   yields ≥1 element." A stale-ID/error/empty response fails this → existing
   report path (no silent fallback).
2. **Config must reach the extractor.** Markdown extractors have signature
   `(markdown, base_url)` and carry no config. The JSON extractor needs three
   paths. Solution: a factory `make_json_api_extractor(items_path, name_path,
   detail_url_path) -> Extractor`; the orchestrator builds it from
   `Strategy.params` when `kind is JSON_API` (instead of `get_extractor(kind)`).
   Paths are dotted (`programme.Title__c`) → nested lookup.

## Module 1 — JSON extractor (`json_extractors.py`, new)

Pure, browser-free, JSON-only (keeps `extractors.py` markdown-focused).

```python
def _dig(obj: Any, dotted_path: str) -> Any:
    """Walk a dict by a 'a.b.c' path; return None if any segment is missing."""

def json_is_usable(text: str, items_path: str) -> bool:
    """True iff *text* parses as JSON and items_path yields a non-empty list."""

def make_json_api_extractor(items_path: str, name_path: str,
                            detail_url_path: str) -> Extractor:
    """Return an Extractor(content, base_url) that parses *content* as JSON,
    reads items_path -> list, and for each item emits ExtractItem(
    name=html.unescape(_dig(item, name_path)),
    detail_url=_dig(item, detail_url_path) or None), skipping items with no
    name. Reuses the existing dedup (by name)."""
```

- `name_path`/`detail_url_path` are relative to each item (e.g. items_path
  `returnValue`, name_path `programme.Title__c`).
- Items with a missing/empty name are skipped (not emitted as blanks).
- Reuses `extractors._dedup` (or an equivalent) for cross-item dedup by name.

## Module 2 — API fetch adapter (`fetch_adapters.py`, extended)

```python
def api_fetch(endpoint: str, *, body: dict,
              headers: Optional[dict] = None) -> str:
    """POST *body* as JSON to *endpoint* and return the response text.
    Returns "" on any error (network, non-2xx). Sends a browser-like
    User-Agent and content-type: application/json by default."""
```

- Uses `httpx.post(endpoint, json=body, headers=..., timeout=30)`.
- Default headers: `content-type: application/json; charset=utf-8`, a
  browser-like `user-agent`, and the NUS `referer` (overridable via params).
- Import `httpx` inside the function (consistent with the module's
  import-inside-helper pattern) so importing the module stays cheap.

## Module 3 — Types (`types.py`, extended)

Add `ExtractKind.JSON_API = "json_api"`. `FetchMode.API = "api"` already exists.

## Module 4 — Registry (`registry.py`, extended)

Re-pin NUS:

```python
"study.nus.edu.sg": Strategy(
    FetchMode.API, ExtractKind.JSON_API,
    params={
        "endpoint": "https://study.nus.edu.sg/webruntime/api/apex/execute"
                    "?language=en-US&asGuest=true&htmlEncode=false",
        "body": {"namespace": "", "classname": "@udd/01pIW000000Rkpx",
                 "method": "searchProgrammes", "isContinuation": False,
                 "params": {"programmeType": "", "interestArea": "[]",
                            "keyword": "", "modeOfStudy": "", "facultyIds": "",
                            "intakePeriod": ""},
                 "cacheable": False},
        "items_path": "returnValue",
        "name_path": "programme.Title__c",
        "detail_url_path": "programme.Program_Page_Link__c",
    },
    paginate=PaginateMode.NONE),
```

## Module 5 — Orchestrator (`orchestrator.py`, extended)

Three minimal, localized branches (the same kind of new-mechanism wiring that
`client_wait` needed — `api` is a genuinely new fetch mode):

1. **`_do_fetch`**: when the pinned strategy's `fetch is FetchMode.API`, call
   `api_fetch(params["endpoint"], body=params["body"], headers=params.get("headers"))`;
   return `(json, json, "api", ["api"])`.
2. **Usability gate**: when `strategy.fetch is FetchMode.API`, use
   `json_is_usable(md, params["items_path"])` instead of `content_is_usable(md)`.
3. **Extractor selection**: when `kind is ExtractKind.JSON_API`, build
   `make_json_api_extractor(items_path, name_path, detail_url_path)` from params
   instead of `get_extractor(kind)`.

Everything else is unchanged: `paginate(mechanism=NONE)` truncates to the range;
on failure the existing pinned-failure report path fires with the "已知策略
（api×json_api）抓取失败" message. The `ok` message reads e.g.
`成功抓取 244 门课程名字（策略 api×json_api，翻页 none，已抓完全部）。`

## Module 6 — CLI / skill

No CLI change — `api` is transparent; `--limit`/`--all` already work via the
range axis. SKILL.md gets a one-line note that NUS now returns its full
catalogue via its API.

## Module 7 — Testing & acceptance

**Deterministic unit tests** (no network; injected fakes / golden JSON):

- Golden sample: `golden_samples/cases/nus_api/response.json` — the real Apex
  response (reproducible; also seeds future detail-field work).
- `_dig`: nested path resolution; missing path → None.
- `make_json_api_extractor` × the golden JSON: assert **244** items, name ==
  `Title__c`, detail_url == `Program_Page_Link__c`, `&#39;`-style entities
  unescaped, items with no name skipped, dedup applied.
- `json_is_usable`: items present → True; empty `returnValue` → False; non-JSON
  → False.
- `api_fetch`: monkeypatch `httpx.post` to return the golden JSON; assert it
  POSTs the configured endpoint + body and returns the text; non-2xx / raise → "".
- `registry`: NUS pinned to `(API, JSON_API)`, params carry endpoint, the
  `searchProgrammes` method, and the three paths.
- `orchestrator`: a fake `api_fetch` returning the golden JSON →
  `crawl_index(NUS)` → `ok`, `names_count == 244`; with `CrawlRange.of(30)` → 30;
  an empty/invalid JSON → `unsupported` + report (no fallback).
- The existing `nus_render` `text_heading` test **stays** — it tests that
  extractor, not NUS routing; NUS moving to `api` does not affect it.

**Integration / acceptance** (manual, like `naming_smoke`):

- Live NUS `crawl-index --all` → **far more than the 10 rendered** (~240+ at
  time of writing; the live catalogue drifts, so assert "much greater than 10",
  not an exact count — only the frozen golden-fixture test asserts the exact
  244), `strategy_used == api×json_api`, `stopped_reason == exhausted`.
- Live NUS `crawl-index --limit 30` → exactly 30.

**CI gate:** deterministic unit tests + pylint 10/10 + no regression of the
existing suite.

## Out of scope (this spec)

- The detail-field crawl pipeline (parent-spec Plan 2). This delivers full
  **names** for NUS; `Program_Page_Link__c` is captured for later detail work.
- Auto-discovering the Apex classname (browser-sniffed) — deliberately rejected
  for hardcode-and-report.
- Applying the `api` strategy to any non-NUS site (none is known yet); the
  mechanism is built config-driven so a future site is a registry row.
