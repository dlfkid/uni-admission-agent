# NUS API FetchStrategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch NUS's full ~244-programme catalogue via its Salesforce Apex API (a config-driven `api` FetchStrategy + JSON extractor), replacing the 10-item render.

**Architecture:** Activate the existing-but-unused `FetchMode.API`; add a config-driven `json_api` extractor (dotted field paths from `Strategy.params`) and an `api_fetch` adapter (server-side `httpx.post`, no browser). The registry pins NUS's endpoint + request body + field paths. The orchestrator gains an injected `api_fetch`, an api-specific usability gate, and JSON-extractor selection. CrawlRange truncation is unchanged (`paginate=none`).

**Tech Stack:** Python 3.12, pytest, httpx (already a dependency), Salesforce Apex guest endpoint.

**Spec:** `docs/superpowers/specs/2026-06-09-nus-api-strategy-design.md`

**Branch:** `feat/nus-api-strategy` (created; spec committed).

---

### Task 1: Capture & commit the NUS Apex golden sample

**Files:**
- Create: `golden_samples/cases/nus_api/response.json` (captured live)

This task needs live network. It captures the real Apex response once so the
extractor/orchestrator tests run against authentic structure.

- [ ] **Step 1: Capture the response**

Run this exactly (it POSTs the guest Apex endpoint with empty filters and saves
the raw JSON):

```bash
mkdir -p golden_samples/cases/nus_api
uv run python - <<'PY'
import json, httpx
URL = ("https://study.nus.edu.sg/webruntime/api/apex/execute"
       "?language=en-US&asGuest=true&htmlEncode=false")
BODY = {"namespace": "", "classname": "@udd/01pIW000000Rkpx",
        "method": "searchProgrammes", "isContinuation": False,
        "params": {"programmeType": "", "interestArea": "[]", "keyword": "",
                   "modeOfStudy": "", "facultyIds": "", "intakePeriod": ""},
        "cacheable": False}
H = {"content-type": "application/json; charset=utf-8",
     "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
     "referer": "https://study.nus.edu.sg/programme"}
r = httpx.post(URL, json=BODY, headers=H, timeout=30.0)
r.raise_for_status()
n = len(r.json().get("returnValue", []))
assert n >= 200, f"expected >=200 programmes, got {n}"
# Save the raw response bytes verbatim — the truest golden sample.
with open("golden_samples/cases/nus_api/response.json", "w", encoding="utf-8") as f:
    f.write(r.text)
print("saved", n, "programmes")
PY
```

Expected: prints `saved 244 programmes` (or a similar count ≥200).

- [ ] **Step 2: Sanity-check the file**

Run: `uv run python -c "import json; d=json.load(open('golden_samples/cases/nus_api/response.json')); rv=d['returnValue']; print(len(rv), rv[0]['programme']['Title__c'])"`
Expected: a count ≥200 and a real programme title.

- [ ] **Step 3: Commit**

```bash
git add golden_samples/cases/nus_api/response.json
git commit -m "test(nus-api): capture Salesforce Apex full-catalogue golden sample"
```

---

### Task 2: Types — add `ExtractKind.JSON_API`

**Files:**
- Modify: `src/services/crawl_strategy/types.py`
- Test: `tests/test_crawl_strategy/test_types.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_types.py`:

```python
def test_json_api_extract_kind_exists():
    from src.services.crawl_strategy.types import ExtractKind
    assert ExtractKind.JSON_API.value == "json_api"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_types.py -v -k json_api`
Expected: FAIL (`JSON_API` undefined).

- [ ] **Step 3: Add the member**

In `src/services/crawl_strategy/types.py`, add to the `ExtractKind` enum, after `TEXT_HEADING` and before `LLM`:

```python
    JSON_API = "json_api"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_types.py -v -k json_api`
Expected: PASS.

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/types.py tests/test_crawl_strategy/test_types.py`
Expected: 10.00/10 exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/types.py tests/test_crawl_strategy/test_types.py
git commit -m "feat(types): add ExtractKind.JSON_API"
```

---

### Task 3: JSON extractor (`json_extractors.py`)

**Files:**
- Create: `src/services/crawl_strategy/json_extractors.py`
- Test: `tests/test_crawl_strategy/test_json_extractors.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_crawl_strategy/test_json_extractors.py`:

```python
import json
from pathlib import Path

from src.services.crawl_strategy.json_extractors import (
    _dig, json_is_usable, make_json_api_extractor,
)

_SAMPLE = json.dumps({"returnValue": [
    {"programme": {"Title__c": "Master of Science in Data Science",
                   "Program_Page_Link__c": "https://x.edu/ds"}},
    {"programme": {"Title__c": "Bachelor of L&#39;Arts",
                   "Program_Page_Link__c": "https://x.edu/arts"}},
    {"programme": {"Title__c": "Master of Science in Data Science",
                   "Program_Page_Link__c": "https://x.edu/dup"}},   # dup name
    {"programme": {"Title__c": "",
                   "Program_Page_Link__c": "https://x.edu/empty"}},  # no name
    {"programme": {"Program_Page_Link__c": "https://x.edu/missing"}},  # no title key
]})

_PATHS = ("returnValue", "programme.Title__c", "programme.Program_Page_Link__c")


def test_dig_nested():
    assert _dig({"a": {"b": {"c": 1}}}, "a.b.c") == 1


def test_dig_missing_returns_none():
    assert _dig({"a": {}}, "a.b.c") is None
    assert _dig({"a": 5}, "a.b") is None


def test_json_is_usable():
    assert json_is_usable(_SAMPLE, "returnValue") is True
    assert json_is_usable(json.dumps({"returnValue": []}), "returnValue") is False
    assert json_is_usable("not json at all", "returnValue") is False
    assert json_is_usable("", "returnValue") is False


def test_extractor_names_urls_unescape_dedup_skip():
    ext = make_json_api_extractor(*_PATHS)
    items = ext(_SAMPLE, "https://study.nus.edu.sg/programme")
    names = [i.name_en for i in items]
    # dup name dropped, empty + missing skipped, &#39; unescaped to '
    assert names == ["Master of Science in Data Science", "Bachelor of L'Arts"]
    assert items[0].detail_url == "https://x.edu/ds"


def test_extractor_handles_invalid_json():
    ext = make_json_api_extractor(*_PATHS)
    assert ext("not json", "https://x.edu") == []


_FIXTURE = (Path(__file__).parent.parent.parent
            / "golden_samples" / "cases" / "nus_api" / "response.json")


def test_golden_fixture_real_structure():
    ext = make_json_api_extractor(*_PATHS)
    items = ext(_FIXTURE.read_text(encoding="utf-8"), "https://study.nus.edu.sg/programme")
    assert len(items) >= 200
    assert all(i.name_en for i in items)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_json_extractors.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

Create `src/services/crawl_strategy/json_extractors.py`:

```python
"""Config-driven JSON extraction for the ``api`` FetchStrategy.

Pure and JSON-only (markdown extractors live in ``extractors.py``).  An api
site's registry params specify dotted field paths; ``make_json_api_extractor``
turns them into the standard ``Extractor(content, base_url) -> [ExtractItem]``
callable used everywhere else.
"""
from __future__ import annotations

import html
import json
from typing import Any, Callable, List

from src.scrapers.helpers import is_noise_program_name
from src.services.crawl_strategy.types import ExtractItem

Extractor = Callable[[str, str], List[ExtractItem]]


def _dig(obj: Any, dotted_path: str) -> Any:
    """Walk *obj* by a dotted path like ``a.b.c``; None if any step is missing."""
    cur = obj
    for seg in dotted_path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _parse_items(text: str, items_path: str) -> list:
    """Parse *text* as JSON and return items_path as a list (else [])."""
    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        return []
    items = _dig(data, items_path)
    return items if isinstance(items, list) else []


def json_is_usable(text: str, items_path: str) -> bool:
    """True iff *text* is JSON whose *items_path* yields a non-empty list."""
    return len(_parse_items(text, items_path)) > 0


def _clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return html.unescape(raw).strip()


def make_json_api_extractor(items_path: str, name_path: str,
                            detail_url_path: str) -> Extractor:
    """Return an Extractor that parses JSON and emits one ExtractItem per element
    of *items_path*, reading *name_path* / *detail_url_path* (dotted, relative to
    each element).  Names are HTML-unescaped; items with no usable name (or a
    noise name) are skipped; results are de-duplicated by name (casefold)."""

    def _extract(content: str, base_url: str) -> List[ExtractItem]:
        del base_url  # api detail URLs are absolute in the payload
        out: List[ExtractItem] = []
        seen: set = set()
        for element in _parse_items(content, items_path):
            name = _clean_name(_dig(element, name_path))
            if not name or is_noise_program_name(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            url = _dig(element, detail_url_path)
            out.append(ExtractItem(name, url if isinstance(url, str) and url else None))
        return out

    return _extract
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_json_extractors.py -v`
Expected: PASS (6 passed). If `test_golden_fixture_real_structure` fails on
`is_noise_program_name` dropping below 200, inspect which titles were dropped;
the lower bound may need adjusting to the fixture's real post-filter count, but
do NOT loosen below a clear majority of the ~244.

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/json_extractors.py tests/test_crawl_strategy/test_json_extractors.py`
Expected: 10.00/10 exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/json_extractors.py tests/test_crawl_strategy/test_json_extractors.py
git commit -m "feat(json): config-driven json_api extractor + json_is_usable"
```

---

### Task 4: API fetch adapter (`api_fetch`)

**Files:**
- Modify: `src/services/crawl_strategy/fetch_adapters.py`
- Test: `tests/test_crawl_strategy/test_fetch_adapters.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_fetch_adapters.py` (the module is
already imported as `fa` at the top from earlier work):

```python
def test_api_fetch_posts_body_and_returns_text(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        text = '{"returnValue":[1,2]}'

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    out = fa.api_fetch("https://x.edu/api", body={"method": "searchProgrammes"})
    assert out == '{"returnValue":[1,2]}'
    assert captured["url"] == "https://x.edu/api"
    assert captured["json"] == {"method": "searchProgrammes"}
    assert "content-type" in {k.lower() for k in captured["headers"]}


def test_api_fetch_returns_empty_on_non_2xx(monkeypatch):
    class _Resp:
        status_code = 500
        text = "err"

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    assert fa.api_fetch("https://x.edu/api", body={}) == ""


def test_api_fetch_returns_empty_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", boom)
    assert fa.api_fetch("https://x.edu/api", body={}) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v -k api_fetch`
Expected: FAIL (`api_fetch` undefined).

- [ ] **Step 3: Implement**

In `src/services/crawl_strategy/fetch_adapters.py`, add a module-level constant
near the other constants:

```python
_API_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
```

And add this function (place it near `server_fetch`/`client_fetch`):

```python
def api_fetch(endpoint: str, *, body: dict,
              headers: Optional[dict] = None) -> str:
    """POST *body* as JSON to *endpoint*; return the response text.

    Returns "" on any non-2xx status or transport error.  Sends a browser-like
    User-Agent and ``content-type: application/json`` by default; *headers*
    (from the strategy params) override/extend these.
    """
    import httpx  # pylint: disable=import-outside-toplevel
    hdrs = {"content-type": "application/json; charset=utf-8",
            "user-agent": _API_USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        resp = httpx.post(endpoint, json=body, headers=hdrs, timeout=30.0)
        if resp.status_code // 100 != 2:
            return ""
        return resp.text
    except Exception:  # pylint: disable=broad-except
        return ""
```

`Optional` is already imported in this file.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v -k api_fetch`
Expected: PASS (3 passed).

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/fetch_adapters.py tests/test_crawl_strategy/test_fetch_adapters.py`
Expected: 10.00/10 exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/fetch_adapters.py tests/test_crawl_strategy/test_fetch_adapters.py
git commit -m "feat(fetch): api_fetch adapter (server-side JSON POST)"
```

---

### Task 5: Registry — re-pin NUS to the api strategy

**Files:**
- Modify: `src/services/crawl_strategy/registry.py`
- Test: `tests/test_crawl_strategy/test_registry.py` (append + amend the NUS line)

- [ ] **Step 1: Write/adjust the failing test**

In `tests/test_crawl_strategy/test_registry.py`, the `test_known_sites_pin_paginate_mechanism` test currently asserts NUS `paginate is PaginateMode.NONE` — that stays true. ADD a new test:

```python
def test_nus_pinned_to_api_json_strategy():
    from src.services.crawl_strategy.types import ExtractKind, FetchMode
    s = lookup("https://study.nus.edu.sg/programme")
    assert s.fetch is FetchMode.API
    assert s.extract is ExtractKind.JSON_API
    assert s.params["items_path"] == "returnValue"
    assert s.params["name_path"] == "programme.Title__c"
    assert s.params["detail_url_path"] == "programme.Program_Page_Link__c"
    assert s.params["body"]["method"] == "searchProgrammes"
    assert "apex/execute" in s.params["endpoint"]
```

Also DELETE the now-stale assertion in `test_known_nus_pinned_to_client_wait_text_heading` (NUS is no longer client_wait/text_heading). Replace that whole test function with:

```python
def test_known_nus_pinned_to_api():
    s = lookup("https://study.nus.edu.sg/programme")
    assert s.fetch is FetchMode.API
    assert s.extract is ExtractKind.JSON_API
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v -k "nus or api"`
Expected: FAIL (NUS still pinned to CLIENT_WAIT/TEXT_HEADING).

- [ ] **Step 3: Implement**

In `src/services/crawl_strategy/registry.py`, replace the NUS entry (currently
`Strategy(FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING, paginate=PaginateMode.NONE)`
with its NONE comment) with:

```python
    # NUS serves its full catalogue from a guest Salesforce Apex endpoint in one
    # POST (searchProgrammes, empty filters) — fetchable server-side, no browser.
    # The classname carries an internal Salesforce ID that may change on a NUS
    # redeploy; if it does, the api fetch yields unusable content and the normal
    # "known strategy failed" report fires so a developer can update it here.
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

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v`
Expected: PASS (all registry tests, including the two new/updated NUS ones).

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/registry.py tests/test_crawl_strategy/test_registry.py`
Expected: 10.00/10 exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/registry.py tests/test_crawl_strategy/test_registry.py
git commit -m "feat(registry): re-pin NUS to api×json_api full-catalogue strategy"
```

---

### Task 6: Orchestrator — wire the api fetch + JSON usability + JSON extractor

**Files:**
- Modify: `src/services/crawl_strategy/orchestrator.py`
- Test: `tests/test_crawl_strategy/test_orchestrator.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crawl_strategy/test_orchestrator.py`:

```python
import json as _json


def _nus_api_json(n=5):
    return _json.dumps({"returnValue": [
        {"programme": {"Title__c": f"Master of Thing {i}",
                       "Program_Page_Link__c": f"https://study.nus.edu.sg/p{i}"}}
        for i in range(n)]})


def test_nus_api_strategy_returns_full_catalogue(tmp_path):
    captured = {}

    def api(endpoint, *, body, headers=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return _nus_api_json(5)

    out = crawl_index(
        "https://study.nus.edu.sg/programme", crawl_range=CrawlRange.all_(),
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        api_fetch=api, report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 5
    assert out.strategy_used == "api×json_api"
    assert "apex/execute" in captured["endpoint"]
    assert captured["body"]["method"] == "searchProgrammes"


def test_nus_api_strategy_respects_limit(tmp_path):
    def api(endpoint, *, body, headers=None):
        return _nus_api_json(50)

    out = crawl_index(
        "https://study.nus.edu.sg/programme", crawl_range=CrawlRange.of(20),
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        api_fetch=api, report_out=tmp_path, timestamp="t")
    assert out.names_count == 20
    assert out.stopped_reason == "reached_limit"


def test_nus_api_failure_reports(tmp_path):
    def api(endpoint, *, body, headers=None):
        return ""   # stale ID / endpoint error -> empty

    out = crawl_index(
        "https://study.nus.edu.sg/programme",
        server_fetch=lambda u: ("", ""), client_fetch=lambda u, **k: ("", ""),
        api_fetch=api, report_out=tmp_path, timestamp="20260609-120000")
    assert out.status == "unsupported"
    assert out.report_zip is not None
    assert "已知策略" in out.message_for_user
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v -k "api"`
Expected: FAIL (`crawl_index` has no `api_fetch`; api fetch not wired).

- [ ] **Step 3: Implement**

In `src/services/crawl_strategy/orchestrator.py`:

(a) Extend imports:

```python
from src.services.crawl_strategy.extractors import get_extractor
from src.services.crawl_strategy.json_extractors import (
    json_is_usable, make_json_api_extractor,
)
from src.services.crawl_strategy.types import (
    CrawlOutcome, CrawlRange, ExtractKind, FetchMode, Strategy,
)
```

(b) Add an `ApiFetch` type alias next to the other alias lines:

```python
ApiFetch = Callable[..., str]
```

(c) Change `_do_fetch` to accept and use `api_fetch`. Replace the whole function with:

```python
def _do_fetch(
    index_url: str,
    pinned: Optional[Strategy],
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    api_fetch: Optional[ApiFetch],
) -> Tuple[str, str, str, list]:
    """Return (html, md, fetch_level, levels_tried) using pinned or escalation."""
    if pinned and pinned.fetch is FetchMode.API:
        endpoint = pinned.params.get("endpoint", index_url)
        body = pinned.params.get("body", {})
        headers = pinned.params.get("headers")
        text = api_fetch(endpoint, body=body, headers=headers) if api_fetch else ""
        return text, text, "api", ["api"]
    if pinned and pinned.fetch is FetchMode.SERVER:
        html, md = server_fetch(index_url)
        return html, md, "server", ["server"]
    if pinned:
        if pinned.fetch is FetchMode.CLIENT_WAIT:
            # Merge wait=True first so params can override if already present,
            # preventing a duplicate-keyword-argument TypeError.
            kwargs = {"wait": True, **pinned.params}
            html, md = client_fetch(index_url, **kwargs)
        else:
            html, md = client_fetch(index_url, **pinned.params)
        return html, md, pinned.fetch.value, [pinned.fetch.value]
    fr = fetch_with_escalation(
        index_url, server_fetch=server_fetch, client_fetch=client_fetch
    )
    return fr.html, fr.markdown, fr.level_used, fr.levels_tried
```

(d) Add `api_fetch` to `crawl_index`'s signature (keyword-only, default None):

```python
def crawl_index(
    index_url: str,
    *,
    crawl_range: Optional[CrawlRange] = None,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    api_fetch: Optional[ApiFetch] = None,
    report_out: "Path | str",
    timestamp: str,
) -> CrawlOutcome:
```

(e) Pass `api_fetch` into `_do_fetch`:

```python
    html, md, fetch_level, levels_tried = _do_fetch(
        index_url, pinned, server_fetch, client_fetch, api_fetch
    )
```

(f) Replace the extract block (`items = [] ... extract=get_extractor(kind))`) so
the usability gate and extractor are api-aware:

```python
    if pinned and pinned.fetch is FetchMode.API:
        usable = json_is_usable(md, pinned.params["items_path"])
        extractor = make_json_api_extractor(
            pinned.params["items_path"], pinned.params["name_path"],
            pinned.params["detail_url_path"])
    else:
        usable = content_is_usable(md)
        extractor = get_extractor(kind) if kind is not None else None

    items = []
    pages_fetched = 0
    stopped_reason = ""
    if confident and kind is not None and usable and extractor is not None:
        pr = paginate(
            mechanism=mechanism, crawl_range=crawl_range, index_url=index_url,
            strategy=strategy, first_html=html, first_md=md,
            server_fetch=server_fetch, client_fetch=client_fetch,
            extract=extractor)
        items = pr.items
        pages_fetched = pr.pages_fetched
        stopped_reason = pr.stopped_reason
```

(g) In the report params dict, replace the `"usable": content_is_usable(md)`
line with the already-computed `usable`:

```python
            "content_signal": {"chars": len(md or ""),
                               "usable": usable},
```

Leave everything else unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v`
Expected: PASS (all existing + 3 new api tests). Existing non-api tests still
pass because `api_fetch` defaults to None and is only used when a pinned
strategy's fetch is API.

- [ ] **Step 5: pylint**

Run: `uv run pylint src/services/crawl_strategy/orchestrator.py tests/test_crawl_strategy/test_orchestrator.py`
Expected: 10.00/10 exit 0. If `too-many-locals`/`too-many-branches` fires on
`crawl_index`, report DONE_WITH_CONCERNS rather than adding a disable; the
controller will decide whether to extract a helper.

- [ ] **Step 6: Commit**

```bash
git add src/services/crawl_strategy/orchestrator.py tests/test_crawl_strategy/test_orchestrator.py
git commit -m "feat(orchestrator): wire api_fetch + JSON usability gate + json_api extractor"
```

---

### Task 7: CLI wiring, skill note, full suite + lint + live acceptance

**Files:**
- Modify: `src/cmd/cli.py` (pass `api_fetch`)
- Modify: `skills/uni-admission-crawl/SKILL.md` (one-line note)
- Test: full suite + live

- [ ] **Step 1: Wire `api_fetch` into the CLI call**

In `src/cmd/cli.py`, find the `crawl_index(...)` call inside `crawl_index_cmd`
and add `api_fetch=fetch_adapters.api_fetch` alongside the other fetches:

```python
    outcome = crawl_index(
        index_url,
        crawl_range=crawl_range,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        api_fetch=fetch_adapters.api_fetch,
        report_out=out_dir, timestamp=timestamp,
    )
```

- [ ] **Step 2: Skill note**

In `skills/uni-admission-crawl/SKILL.md`, under the strategy table row for NUS
(or the strategy section), add a line:

```markdown
- NUS (`study.nus.edu.sg`) returns its **full** programme catalogue via its
  Salesforce Apex API (`api×json_api`), not just the ~10 rendered on screen.
```

- [ ] **Step 3: Full suite + lint gate**

Run: `uv run pytest -q`
Expected: all pass, no regressions.

Run: `uv run pylint $(git ls-files '*.py')`
Expected: 10.00/10, exit 0. Fix any message in the file it points to.

- [ ] **Step 4: Live acceptance — full catalogue**

Run: `uv run python -m src.cmd.cli crawl-index 'https://study.nus.edu.sg/programme' --all --json`
Expected: `status: ok`, `strategy_used: api×json_api`, `names_count` far greater
than 10 (~240+), `stopped_reason: exhausted`. Record the count. If `names_count`
is 0 with an `unsupported`/report result, the Apex classname ID has likely
changed — re-capture via Task 1's script, read the new request in a browser
(network tab) for the current classname, and update `registry.py`.

- [ ] **Step 5: Live acceptance — range truncation**

Run: `uv run python -m src.cmd.cli crawl-index 'https://study.nus.edu.sg/programme' --limit 30 --json`
Expected: `names_count: 30`, `stopped_reason: reached_limit`.

- [ ] **Step 6: Commit**

```bash
git add src/cmd/cli.py skills/uni-admission-crawl/SKILL.md
git commit -m "feat(cli+skill): pass api_fetch; document NUS full-catalogue api"
```

---

## Notes for the implementer

- **TDD discipline:** every code task is test-first.
- **pylint is strict:** the CI gate fails on ANY message. Use `del arg` for
  interface-only params, import heavy deps (`httpx`) inside the function as the
  module already does, and keep functions small.
- **Index name authoritative:** the JSON `Title__c` is the name; never re-derive
  from anything else.
- **Task 1 needs live network**; Tasks 2–6 are fully offline (inline JSON +
  golden fixture + monkeypatched httpx). Task 7 steps 4–5 need live network.
- **No silent fallback:** when the api fetch fails, the flow must reach the
  existing "已知策略（api×json_api）抓取失败" report path — do not fall back to a
  browser render.
