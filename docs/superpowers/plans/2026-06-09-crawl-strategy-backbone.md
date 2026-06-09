# Crawl Strategy System — Plan 1: Deterministic Backbone

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given an index URL, route known universities to a pinned strategy, classify unknown ones by deterministic features, escalate fetching as needed, and export a phenomenon zip when unsupported — all behind one `adm-agent crawl` command a weak LLM can drive.

**Architecture:** A `src/services/crawl_strategy/` package with two axes (FetchStrategy, ExtractStrategy), a data-driven university registry, pure-function extractors (lifted from the existing `index_name_harvest`), a deterministic classifier with a confidence gate, a fetch-escalation ladder, a reporter that zips the phenomenon, and an orchestrator that returns a structured outcome JSON.

**Tech Stack:** Python 3.12, pydantic/dataclasses, existing `src/scrapers` (crawl4ai), `src/client/native_browser` (CDP client), pytest, pylint.

**Scope:** Deterministic tier + reporter + CLI + skill. The LLM classify/extract tier and detail-field pipeline are **Plan 2** (deferred). This plan delivers requirements #1, #3, #4 and the deterministic half of #2, and is sufficient to run the NUS acceptance loop.

---

## File Structure

```
src/services/crawl_strategy/
  __init__.py          # public exports
  types.py             # enums + dataclasses (Strategy, ExtractItem, CrawlOutcome, FetchResult)
  extractors.py        # 5 pure-function extractors, registered by name
  registry.py          # REGISTRY: domain -> Strategy; lookup()
  classifier.py        # feature_signals(), classify() with confidence gate
  fetch_ladder.py      # content_is_usable(), fetch_with_escalation()
  reporter.py          # export_report_zip()
  orchestrator.py      # crawl_index() — the entry point
tests/test_crawl_strategy/
  test_extractors.py
  test_registry.py
  test_classifier.py
  test_fetch_ladder.py
  test_reporter.py
  test_orchestrator.py
```

Existing code reused:
- `src/services/index_name_harvest.py` — its regexes/logic are lifted into `extractors.py` as named pure functions (then `index_name_harvest` re-exports for back-compat).
- `src/scrapers/engine.py` `AdmissionScraper.crawl_page` — server fetch.
- `src/client/native_browser.py` `fetch_browser_payload` — client fetch.
- `src/scrapers/helpers.py` `is_noise_program_name` — name sanity.

---

## Task 1: Types — enums + dataclasses

**Files:**
- Create: `src/services/crawl_strategy/__init__.py`
- Create: `src/services/crawl_strategy/types.py`
- Test: `tests/test_crawl_strategy/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_types.py
from src.services.crawl_strategy.types import (
    FetchMode, ExtractKind, Strategy, ExtractItem, FetchResult, CrawlOutcome,
)


def test_strategy_holds_axes_and_params():
    s = Strategy(fetch=FetchMode.CLIENT_WAIT, extract=ExtractKind.TEXT_HEADING,
                 params={"wait_selector": ".card"})
    assert s.fetch is FetchMode.CLIENT_WAIT
    assert s.extract is ExtractKind.TEXT_HEADING
    assert s.params["wait_selector"] == ".card"


def test_extract_item_name_and_url():
    item = ExtractItem(name_en="AI for Business MSc", detail_url="https://x/y")
    assert item.name_en == "AI for Business MSc"
    assert item.detail_url == "https://x/y"


def test_crawl_outcome_defaults():
    out = CrawlOutcome(status="unsupported", university="nus")
    assert out.status == "unsupported"
    assert out.names == []
    assert out.report_zip is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.crawl_strategy'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/__init__.py
"""Crawl strategy system: registry + classifier + dispatcher."""
```

```python
# src/services/crawl_strategy/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class FetchMode(str, Enum):
    SERVER = "server"
    CLIENT = "client"
    CLIENT_WAIT = "client_wait"
    API = "api"


class ExtractKind(str, Enum):
    HEADING_LINK = "heading_link"
    INLINE_DEGREE = "inline_degree"
    MERGED_COLUMNS = "merged_columns"
    BLOB = "blob"
    TEXT_HEADING = "text_heading"
    LLM = "llm"


@dataclass(frozen=True)
class Strategy:
    fetch: FetchMode
    extract: ExtractKind
    params: Dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        return f"{self.fetch.value}×{self.extract.value}"


@dataclass
class ExtractItem:
    name_en: str
    detail_url: Optional[str] = None


@dataclass
class FetchResult:
    html: str
    markdown: str
    level_used: str
    levels_tried: List[str] = field(default_factory=list)


@dataclass
class CrawlOutcome:
    status: Literal["ok", "llm_fallback", "unsupported"]
    university: str
    names: List[str] = field(default_factory=list)
    items: List[ExtractItem] = field(default_factory=list)
    names_count: int = 0
    details_imported: int = 0
    quarantined: int = 0
    strategy_used: Optional[str] = None
    report_zip: Optional[str] = None
    message_for_user: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_types.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/__init__.py src/services/crawl_strategy/types.py tests/test_crawl_strategy/test_types.py
git commit -m "feat(strategy): types — FetchMode/ExtractKind/Strategy/CrawlOutcome"
```

---

## Task 2: Extractors — pure functions, one per ExtractKind

**Files:**
- Create: `src/services/crawl_strategy/extractors.py`
- Test: `tests/test_crawl_strategy/test_extractors.py`

The four link-based extractors lift the proven logic from
`src/services/index_name_harvest.py` (heading-link, inline-degree,
merged-columns duration strip, blob). `text_heading` is new (NUS-shaped:
program name is a heading, detail URL is a nearby "Learn More" link) and
will be finalized during the NUS loop — here it ships as a working
extractor against a synthetic fixture so the registry/classifier can
reference it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_extractors.py
from src.services.crawl_strategy.extractors import EXTRACTORS, get_extractor
from src.services.crawl_strategy.types import ExtractKind

BASE = "https://courses.leeds.ac.uk/course-search/masters-courses"


def test_heading_link_extracts_name_and_url():
    md = "##  [Accounting and Finance MSc](https://courses.leeds.ac.uk/f921/accounting-and-finance-msc) Duration\n"
    items = get_extractor(ExtractKind.HEADING_LINK)(md, BASE)
    assert [i.name_en for i in items] == ["Accounting and Finance MSc"]
    assert items[0].detail_url.endswith("/f921/accounting-and-finance-msc")


def test_inline_degree_extracts_ucl_style():
    md = ("[Search](https://www.ucl.ac.uk/x#tab1)\n"
          "[Anthropology BSc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/anthropology-bsc)\n")
    items = get_extractor(ExtractKind.INLINE_DEGREE)(md, "https://www.ucl.ac.uk/")
    assert [i.name_en for i in items] == ["Anthropology BSc"]


def test_merged_columns_strips_duration():
    md = "[Accounting MSc (1 year)](https://www.manchester.ac.uk/study/masters/courses/list/10867/msc-accounting/)\n"
    items = get_extractor(ExtractKind.MERGED_COLUMNS)(md, "https://www.manchester.ac.uk/")
    assert [i.name_en for i in items] == ["Accounting MSc"]


def test_blob_extracts_english_name():
    md = ("[ 02022 | Sept 2026 Entry  Full-time - 1 year  Business Management - MSc - Master of Science  "
          "商業管理 ](https://www.polyu.edu.hk/study/pg/tpg/2026/02022)\n")
    items = get_extractor(ExtractKind.BLOB)(md, "https://www.polyu.edu.hk/")
    assert [i.name_en for i in items] == ["Business Management MSc"]


def test_text_heading_pairs_name_with_learn_more():
    # NUS-shaped: a program-name heading, then a separate "Learn More" link.
    md = ("### Doctor of Engineering (Biomedical Engineering)\n"
          "Intake Period: Aug\n"
          "[Learn More](https://nus.edu.sg/programme/doctor-of-engineering-biomedical)\n")
    items = get_extractor(ExtractKind.TEXT_HEADING)(md, "https://study.nus.edu.sg/")
    assert items[0].name_en == "Doctor of Engineering (Biomedical Engineering)"
    assert items[0].detail_url.endswith("/doctor-of-engineering-biomedical")


def test_every_extractkind_except_llm_is_registered():
    for kind in ExtractKind:
        if kind is ExtractKind.LLM:
            continue
        assert kind in EXTRACTORS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_extractors.py -v`
Expected: FAIL — `ModuleNotFoundError: ... extractors`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/extractors.py
from __future__ import annotations

import re
from typing import Callable, Dict, List
from urllib.parse import urljoin, urlsplit

from src.scrapers.helpers import is_noise_program_name
from src.services.crawl_strategy.types import ExtractItem, ExtractKind

Extractor = Callable[[str, str], List[ExtractItem]]

# --- shared helpers (lifted from index_name_harvest) ---
_HEADING_LINK_RE = re.compile(r"^\s{0,3}#{1,4}\s+\[([^\]]+)\]\(\s*([^)\s]+)", re.MULTILINE)
_ANY_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)")
_DEGREE_SUFFIX_RE = re.compile(
    r"\b(?:BA|BSc|BASc|BEng|LLB|MArch|MBA|MChem|MComp|MEng|MMath|MPhil|MRes|"
    r"MSci|MSc|MA|LLM|PhD|DPhil|PGDip|PGCert|FdA|FdSc)\b\s*(?:\([^)]*\))?\s*$")
_DURATION_SUFFIX_RE = re.compile(
    r"\s*\(\s*\d+(?:\s*(?:or|to|and|-|–|/)\s*\d+)?\s*years?\s*\)\s*$", re.IGNORECASE)
_BLOB_NAME_RE = re.compile(
    r"(?:years?|term|\))\s{2,}([A-Z][A-Za-z0-9 ,&'./-]+?)\s+-\s+"
    r"(MSc|MA|MBA|MEng|MArch|MFin|MPhil|LLM|MSocSc|MScM|Master|PhD|EdD|DBA)\s+-\s+Master")
# text_heading: a markdown heading line that looks like a program name,
# followed (within a few lines) by a "Learn More" style detail link.
_TEXT_HEADING_RE = re.compile(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", re.MULTILINE)
_LEARN_MORE_RE = re.compile(r"\[(?:\s*Learn More[^\]]*)\]\(\s*([^)\s]+)", re.IGNORECASE)


def _clean(text: str) -> str:
    name = re.sub(r"\s+", " ", str(text or "")).strip()
    return _DURATION_SUFFIX_RE.sub("", name).strip()


def _canon(url: str) -> str:
    try:
        p = urlsplit(url)
    except ValueError:
        return url.rstrip("/")
    if not p.scheme or not p.netloc:
        return url.rstrip("/")
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/') or '/'}"


def _dedup(items: List[ExtractItem]) -> List[ExtractItem]:
    seen_url, seen_name, out = set(), set(), []
    for it in items:
        if not it.name_en or is_noise_program_name(it.name_en):
            continue
        ukey = _canon(it.detail_url) if it.detail_url else ""
        nkey = it.name_en.casefold()
        if (ukey and ukey in seen_url) or nkey in seen_name:
            continue
        if ukey:
            seen_url.add(ukey)
        seen_name.add(nkey)
        out.append(it)
    return out


def extract_heading_link(markdown: str, base_url: str) -> List[ExtractItem]:
    out = [ExtractItem(_clean(m.group(1)), urljoin(base_url, m.group(2)))
           for m in _HEADING_LINK_RE.finditer(markdown or "")]
    return _dedup(out)


def _looks_like_program(text: str) -> bool:
    return bool(_DEGREE_SUFFIX_RE.search(str(text or "").strip()))


def extract_inline_degree(markdown: str, base_url: str) -> List[ExtractItem]:
    out = []
    for m in _ANY_LINK_RE.finditer(markdown or ""):
        if _looks_like_program(m.group(1)):
            out.append(ExtractItem(_clean(m.group(1)), urljoin(base_url, m.group(2))))
    return _dedup(out)


def extract_merged_columns(markdown: str, base_url: str) -> List[ExtractItem]:
    # Same shape as inline_degree; _clean() strips the trailing duration.
    return extract_inline_degree(markdown, base_url)


def _blob_name(text: str) -> str | None:
    m = _BLOB_NAME_RE.search(str(text or ""))
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    return f"{name} {m.group(2).strip()}" if name else None


def extract_blob(markdown: str, base_url: str) -> List[ExtractItem]:
    out = []
    for m in _ANY_LINK_RE.finditer(markdown or ""):
        name = _blob_name(m.group(1))
        if name:
            out.append(ExtractItem(name, urljoin(base_url, m.group(2))))
    return _dedup(out)


def extract_text_heading(markdown: str, base_url: str) -> List[ExtractItem]:
    """Program name is a heading; detail URL is the next 'Learn More' link."""
    lines = (markdown or "").splitlines()
    out: List[ExtractItem] = []
    pending_name: str | None = None
    for line in lines:
        hm = _TEXT_HEADING_RE.match(line)
        if hm:
            cand = _clean(hm.group(1))
            pending_name = cand if cand and not is_noise_program_name(cand) else None
            continue
        lm = _LEARN_MORE_RE.search(line)
        if lm and pending_name:
            out.append(ExtractItem(pending_name, urljoin(base_url, lm.group(1))))
            pending_name = None
    return _dedup(out)


EXTRACTORS: Dict[ExtractKind, Extractor] = {
    ExtractKind.HEADING_LINK: extract_heading_link,
    ExtractKind.INLINE_DEGREE: extract_inline_degree,
    ExtractKind.MERGED_COLUMNS: extract_merged_columns,
    ExtractKind.BLOB: extract_blob,
    ExtractKind.TEXT_HEADING: extract_text_heading,
}


def get_extractor(kind: ExtractKind) -> Extractor:
    return EXTRACTORS[kind]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_extractors.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/extractors.py tests/test_crawl_strategy/test_extractors.py
git commit -m "feat(strategy): pure-function extractors for 5 layout kinds"
```

---

## Task 3: Registry — domain → pinned Strategy

**Files:**
- Create: `src/services/crawl_strategy/registry.py`
- Test: `tests/test_crawl_strategy/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_registry.py
from src.services.crawl_strategy.registry import lookup, REGISTRY
from src.services.crawl_strategy.types import FetchMode, ExtractKind


def test_known_leeds_pinned_to_server_heading():
    s = lookup("https://courses.leeds.ac.uk/course-search/masters-courses")
    assert s is not None
    assert s.fetch is FetchMode.SERVER
    assert s.extract is ExtractKind.HEADING_LINK


def test_known_ucl_pinned_to_client_inline():
    s = lookup("https://www.ucl.ac.uk/prospective-students/undergraduate/degrees")
    assert s.fetch is FetchMode.CLIENT
    assert s.extract is ExtractKind.INLINE_DEGREE


def test_unknown_domain_returns_none():
    assert lookup("https://example.edu/programmes") is None


def test_subdomain_and_scheme_insensitive():
    assert lookup("http://COURSES.leeds.ac.uk/anything") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/registry.py
from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urlsplit

from src.services.crawl_strategy.types import ExtractKind, FetchMode, Strategy

# Domain (host) → pinned, proven Strategy. Adding a university = add a row
# here + a golden sample + (if needed) a new extractor. Never touch
# orchestration code.
REGISTRY: Dict[str, Strategy] = {
    "courses.leeds.ac.uk": Strategy(FetchMode.SERVER, ExtractKind.HEADING_LINK),
    "www.ucl.ac.uk": Strategy(FetchMode.CLIENT, ExtractKind.INLINE_DEGREE),
    "www.manchester.ac.uk": Strategy(FetchMode.CLIENT, ExtractKind.MERGED_COLUMNS),
    "www.polyu.edu.hk": Strategy(FetchMode.CLIENT, ExtractKind.BLOB),
}


def lookup(index_url: str) -> Optional[Strategy]:
    host = urlsplit(str(index_url or "").strip()).netloc.lower()
    return REGISTRY.get(host)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_registry.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/registry.py tests/test_crawl_strategy/test_registry.py
git commit -m "feat(strategy): data-driven university registry"
```

---

## Task 4: Classifier — feature signals + deterministic classify + confidence gate

**Files:**
- Create: `src/services/crawl_strategy/classifier.py`
- Test: `tests/test_crawl_strategy/test_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_classifier.py
from src.services.crawl_strategy.classifier import classify, feature_signals
from src.services.crawl_strategy.types import ExtractKind

LEEDS = "https://courses.leeds.ac.uk/course-search/masters-courses"


def test_classify_heading_link_page():
    md = "".join(
        f"##  [Programme {i} MSc](https://courses.leeds.ac.uk/c{i}/programme-{i}-msc) Duration\n"
        for i in range(8))
    result = classify(md, LEEDS)
    assert result.kind is ExtractKind.HEADING_LINK
    assert result.confident is True
    assert result.count >= 8


def test_classify_inline_degree_page():
    md = "".join(
        f"[Programme {i} BSc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/programme-{i}-bsc)\n"
        for i in range(8))
    result = classify(md, "https://www.ucl.ac.uk/")
    assert result.kind is ExtractKind.INLINE_DEGREE
    assert result.confident is True


def test_nav_only_page_is_not_confident():
    md = "[Home](https://x/)\n[Search](https://x/s)\n[Apply Now](https://x/a)\n"
    result = classify(md, "https://x/")
    assert result.confident is False
    assert result.kind is None


def test_feature_signals_counts():
    md = "##  [A MSc](https://x/a-msc)\n[B BSc](https://x/b-bsc)\n"
    sig = feature_signals(md, "https://x/")
    assert sig["heading_link"] >= 1
    assert sig["link_total"] >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_classifier.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/classifier.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from src.services.crawl_strategy.extractors import EXTRACTORS
from src.services.crawl_strategy.types import ExtractKind

_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)")
# Minimum course-like rows for a confident deterministic match, and the
# order we prefer when several kinds tie (more specific first).
_MIN_CONFIDENT = 5
_PREFERENCE = [
    ExtractKind.BLOB,
    ExtractKind.MERGED_COLUMNS,
    ExtractKind.HEADING_LINK,
    ExtractKind.INLINE_DEGREE,
    ExtractKind.TEXT_HEADING,
]


@dataclass
class ClassifyResult:
    kind: Optional[ExtractKind]
    confident: bool
    count: int
    scores: Dict[str, int]


def feature_signals(markdown: str, base_url: str) -> Dict[str, int]:
    scores = {k.value: len(EXTRACTORS[k](markdown, base_url))
              for k in EXTRACTORS}
    scores["link_total"] = len(_LINK_RE.findall(markdown or ""))
    return scores


def classify(markdown: str, base_url: str) -> ClassifyResult:
    scores = {k: len(EXTRACTORS[k](markdown, base_url)) for k in _PREFERENCE}
    best_kind = max(_PREFERENCE, key=lambda k: (scores[k], -_PREFERENCE.index(k)))
    best = scores[best_kind]
    confident = best >= _MIN_CONFIDENT
    return ClassifyResult(
        kind=best_kind if confident else None,
        confident=confident,
        count=best,
        scores={k.value: v for k, v in scores.items()},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_classifier.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/classifier.py tests/test_crawl_strategy/test_classifier.py
git commit -m "feat(strategy): deterministic feature classifier + confidence gate"
```

---

## Task 5: Fetch ladder — content-usable gate + escalation

**Files:**
- Create: `src/services/crawl_strategy/fetch_ladder.py`
- Test: `tests/test_crawl_strategy/test_fetch_ladder.py`

The fetch functions are injected (dependency injection) so the ladder is
unit-testable with fakes — no real network/browser in tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_fetch_ladder.py
from src.services.crawl_strategy.fetch_ladder import content_is_usable, fetch_with_escalation
from src.services.crawl_strategy.types import FetchMode


def test_cloudflare_challenge_not_usable():
    md = "# Just a moment...\nVerifying you are human. cloudflare"
    assert content_is_usable(md) is False


def test_empty_page_not_usable():
    assert content_is_usable("\n\n  ") is False


def test_real_listing_is_usable():
    md = "".join(f"## [Programme {i} MSc](https://x/p{i}-msc)\n" for i in range(10))
    assert content_is_usable(md) is True


def test_escalation_stops_at_first_usable():
    calls = []

    def server(url):
        calls.append("server")
        return ("", "")  # empty → escalate

    def client(url, **kw):
        calls.append("client")
        md = "".join(f"## [P{i} MSc](https://x/p{i}-msc)\n" for i in range(10))
        return ("<html>", md)

    res = fetch_with_escalation(
        "https://x/programmes",
        server_fetch=server, client_fetch=client,
    )
    assert res.level_used == FetchMode.CLIENT.value
    assert calls == ["server", "client"]
    assert "P0 MSc" in res.markdown


def test_escalation_exhausted_returns_last_empty():
    def empty_server(url):
        return ("", "")

    def empty_client(url, **kw):
        return ("", "")

    res = fetch_with_escalation(
        "https://x/programmes",
        server_fetch=empty_server, client_fetch=empty_client,
    )
    assert res.level_used == FetchMode.CLIENT_WAIT.value
    assert res.levels_tried == ["server", "client", "client_wait"]
    assert content_is_usable(res.markdown) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_ladder.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/fetch_ladder.py
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

from src.services.crawl_strategy.types import FetchMode, FetchResult

# (html, markdown) producers. client_fetch takes wait kwargs.
ServerFetch = Callable[[str], Tuple[str, str]]
ClientFetch = Callable[..., Tuple[str, str]]

_CF_RE = re.compile(r"just a moment|verifying you are human|cloudflare|安全验证|checking your browser", re.IGNORECASE)
_MIN_CHARS = 400
_MIN_LINKS = 5
_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")


def content_is_usable(markdown: str) -> bool:
    md = str(markdown or "").strip()
    if len(md) < _MIN_CHARS:
        return False
    if _CF_RE.search(md):
        return False
    return len(_LINK_RE.findall(md)) >= _MIN_LINKS


def fetch_with_escalation(
    index_url: str,
    *,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    wait_selector: Optional[str] = None,
) -> FetchResult:
    tried = []

    tried.append("server")
    html, md = server_fetch(index_url)
    if content_is_usable(md):
        return FetchResult(html, md, FetchMode.SERVER.value, tried)

    tried.append("client")
    html, md = client_fetch(index_url)
    if content_is_usable(md):
        return FetchResult(html, md, FetchMode.CLIENT.value, tried)

    tried.append("client_wait")
    html, md = client_fetch(index_url, wait_selector=wait_selector, wait=True)
    return FetchResult(html, md, FetchMode.CLIENT_WAIT.value, tried)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_ladder.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/fetch_ladder.py tests/test_crawl_strategy/test_fetch_ladder.py
git commit -m "feat(strategy): fetch escalation ladder + content-usable gate"
```

---

## Task 6: Reporter — phenomenon zip export

**Files:**
- Create: `src/services/crawl_strategy/reporter.py`
- Test: `tests/test_crawl_strategy/test_reporter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_reporter.py
import json
import zipfile
from pathlib import Path

from src.services.crawl_strategy.reporter import export_report_zip


def test_export_writes_zip_with_required_members(tmp_path):
    zip_path = export_report_zip(
        out_dir=tmp_path,
        index_url="https://study.nus.edu.sg/programme",
        html="<html>nus</html>",
        markdown="# NUS\nFind a programme",
        params={
            "fetch_level_used": "client_wait",
            "fetch_levels_tried": ["server", "client", "client_wait"],
            "content_signal": {"chars": 17000, "links": 70, "degree_hits": 0, "nav_ratio": 0.9},
            "feature_signals": {"heading_link": 0, "inline_degree": 0, "blob": 0, "text_heading": 0},
            "strategy_scores": {"heading_link": 0},
            "llm_classified_as": None,
            "llm_extract_count": 0,
            "outcome": "unsupported",
        },
        run_log="server→empty\nclient→empty\nclient_wait→17KB nav only\n",
        timestamp="20260609-120000",
    )
    p = Path(zip_path)
    assert p.exists() and p.suffix == ".zip"
    with zipfile.ZipFile(p) as zf:
        names = set(zf.namelist())
        assert {"index.html", "index.md", "params.json", "run.log"} <= names
        params = json.loads(zf.read("params.json"))
        assert params["outcome"] == "unsupported"
        assert params["index_url"] == "https://study.nus.edu.sg/programme"


def test_zip_named_by_domain_and_timestamp(tmp_path):
    zip_path = export_report_zip(
        out_dir=tmp_path, index_url="https://study.nus.edu.sg/programme",
        html="x", markdown="y", params={"outcome": "unsupported"},
        run_log="", timestamp="20260609-120000",
    )
    assert Path(zip_path).name == "study.nus.edu.sg-20260609-120000.zip"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_reporter.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/reporter.py
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit


def export_report_zip(
    *,
    out_dir: Path | str,
    index_url: str,
    html: str,
    markdown: str,
    params: Dict[str, Any],
    run_log: str,
    timestamp: str,
) -> str:
    """Capture the phenomenon (raw page + objective params + log) as a zip.

    No diagnosis, no conclusions — a senior developer LLM consumes this
    offline to author a strategy + golden sample.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    host = urlsplit(str(index_url or "").strip()).netloc.lower() or "unknown"
    zip_path = out / f"{host}-{timestamp}.zip"

    full_params = {"index_url": index_url, "timestamp": timestamp, **params}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html or "")
        zf.writestr("index.md", markdown or "")
        zf.writestr("params.json", json.dumps(full_params, ensure_ascii=False, indent=2))
        zf.writestr("run.log", run_log or "")
    return str(zip_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_reporter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/reporter.py tests/test_crawl_strategy/test_reporter.py
git commit -m "feat(strategy): reporter — phenomenon zip export"
```

---

## Task 7: Orchestrator — crawl_index() wiring (names-only, deterministic tier)

**Files:**
- Create: `src/services/crawl_strategy/orchestrator.py`
- Test: `tests/test_crawl_strategy/test_orchestrator.py`

The orchestrator wires registry → fetch → (pinned or classified) extract →
outcome. Fetch + the clock are injected so it's unit-testable without
network/browser/LLM. LLM tier is Plan 2 — here, an unknown page that the
deterministic classifier can't place yields `unsupported` + a report.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_orchestrator.py
from pathlib import Path

from src.services.crawl_strategy.orchestrator import crawl_index


def _leeds_md():
    return "".join(
        f"##  [Programme {i} MSc](https://courses.leeds.ac.uk/c{i}/programme-{i}-msc) Duration\n"
        for i in range(15))


def test_known_university_uses_pinned_strategy(tmp_path):
    def server(url):
        return ("<html>", _leeds_md())

    def client(url, **kw):
        raise AssertionError("known Leeds is server-pinned; client must not be called")

    out = crawl_index(
        "https://courses.leeds.ac.uk/course-search/masters-courses",
        server_fetch=server, client_fetch=client,
        report_out=tmp_path, timestamp="t",
    )
    assert out.status == "ok"
    assert out.names_count == 15
    assert out.strategy_used == "server×heading_link"
    assert out.report_zip is None


def test_unknown_known_structure_classifies(tmp_path):
    md = "".join(
        f"[Programme {i} BSc](https://example.edu/degrees/programme-{i}-bsc)\n"
        for i in range(9))

    def server(url):
        return ("<html>", md)

    def client(url, **kw):
        return ("", "")

    out = crawl_index("https://example.edu/degrees",
                      server_fetch=server, client_fetch=client,
                      report_out=tmp_path, timestamp="t")
    assert out.status == "ok"
    assert out.names_count == 9
    assert out.strategy_used.endswith("inline_degree")


def test_unsupported_page_exports_report(tmp_path):
    def server(url):
        return ("<html>nav</html>", "[Home](https://x/)\n[Apply](https://x/a)\n")

    def client(url, **kw):
        return ("<html>nav</html>", "[Home](https://x/)\n[Apply](https://x/a)\n")

    out = crawl_index("https://newuni.edu/programmes",
                      server_fetch=server, client_fetch=client,
                      report_out=tmp_path, timestamp="20260609-120000")
    assert out.status == "unsupported"
    assert out.report_zip is not None
    assert Path(out.report_zip).exists()
    assert out.message_for_user  # non-empty user-facing line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/orchestrator.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from src.services.crawl_strategy import registry as registry_mod
from src.services.crawl_strategy.classifier import classify
from src.services.crawl_strategy.extractors import get_extractor
from src.services.crawl_strategy.fetch_ladder import (
    content_is_usable, fetch_with_escalation,
)
from src.services.crawl_strategy.reporter import export_report_zip
from src.services.crawl_strategy.types import CrawlOutcome, FetchMode, Strategy

ServerFetch = Callable[[str], Tuple[str, str]]
ClientFetch = Callable[..., Tuple[str, str]]


def _university_slug(index_url: str) -> str:
    host = urlsplit(index_url).netloc.lower()
    parts = [p for p in host.split(".") if p not in ("www", "study", "courses")]
    return parts[0] if parts else host


def crawl_index(
    index_url: str,
    *,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    report_out: Path | str,
    timestamp: str,
) -> CrawlOutcome:
    uni = _university_slug(index_url)
    pinned: Optional[Strategy] = registry_mod.lookup(index_url)

    # 1. Fetch — pinned mode for known unis, else escalate.
    if pinned and pinned.fetch is FetchMode.SERVER:
        html, md = server_fetch(index_url)
        fetch_level, levels_tried = "server", ["server"]
    elif pinned:
        html, md = client_fetch(index_url, **pinned.params)
        fetch_level, levels_tried = pinned.fetch.value, [pinned.fetch.value]
    else:
        fr = fetch_with_escalation(index_url, server_fetch=server_fetch,
                                   client_fetch=client_fetch)
        html, md, fetch_level, levels_tried = fr.html, fr.markdown, fr.level_used, fr.levels_tried

    # 2. Choose extractor — pinned kind or classify.
    if pinned:
        kind, confident = pinned.extract, True
    else:
        cr = classify(md, index_url)
        kind, confident = cr.kind, cr.confident

    # 3. Extract (if we have a usable page and a confident kind).
    items = []
    if confident and kind is not None and content_is_usable(md):
        items = get_extractor(kind)(md, index_url)

    if items:
        names = [it.name_en for it in items]
        strat = f"{fetch_level}×{kind.value}"
        return CrawlOutcome(
            status="ok", university=uni, names=names, items=items,
            names_count=len(names), strategy_used=strat,
            message_for_user=f"成功抓取 {len(names)} 门课程名字（策略 {strat}）。",
        )

    # 4. Unsupported → export phenomenon report. (LLM tier is Plan 2.)
    scores = classify(md, index_url).scores
    zip_path = export_report_zip(
        out_dir=report_out, index_url=index_url, html=html, markdown=md,
        params={
            "university_guess": uni,
            "fetch_level_used": fetch_level,
            "fetch_levels_tried": levels_tried,
            "content_signal": {"chars": len(md or ""),
                               "usable": content_is_usable(md)},
            "feature_signals": scores,
            "strategy_scores": scores,
            "llm_classified_as": None,
            "llm_extract_count": 0,
            "outcome": "unsupported",
        },
        run_log="\n".join(f"{lvl}" for lvl in levels_tried),
        timestamp=timestamp,
    )
    return CrawlOutcome(
        status="unsupported", university=uni, report_zip=zip_path,
        message_for_user=(
            f"这所大学（{uni}）暂不支持。现象报告已导出到 {zip_path}，"
            "发给开发者即可加入支持。"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_orchestrator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/orchestrator.py tests/test_crawl_strategy/test_orchestrator.py
git commit -m "feat(strategy): orchestrator — registry→fetch→classify→extract→outcome"
```

---

## Task 8: Real fetch adapters (server + client) for the orchestrator

**Files:**
- Create: `src/services/crawl_strategy/fetch_adapters.py`
- Test: `tests/test_crawl_strategy/test_fetch_adapters.py`

Adapters bridge the orchestrator's injected fetch signature to the real
`AdmissionScraper` (server) and `native_browser` (client). Tested by
mocking the underlying calls — no real network/browser.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_fetch_adapters.py
from unittest.mock import patch

from src.services.crawl_strategy import fetch_adapters


def test_server_adapter_returns_html_and_markdown():
    class _Page:
        html = "<html>x</html>"
        markdown = "# md"

    with patch.object(fetch_adapters, "_run_server_crawl", return_value=_Page()):
        html, md = fetch_adapters.server_fetch("https://x/")
    assert html == "<html>x</html>"
    assert md == "# md"


def test_client_adapter_converts_payload_to_markdown():
    with patch.object(fetch_adapters, "_run_client_fetch",
                      return_value={"html_content": "<html>c</html>"}), \
         patch.object(fetch_adapters, "_html_to_markdown", return_value="# client md"):
        html, md = fetch_adapters.client_fetch("https://x/")
    assert html == "<html>c</html>"
    assert md == "# client md"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crawl_strategy/fetch_adapters.py
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, Tuple


def _run_server_crawl(url: str):
    from src.scrapers.engine import AdmissionScraper
    scraper = AdmissionScraper()
    return asyncio.run(scraper.crawl_page(url))


def _clean_browser_path() -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        return path if path and Path(path).exists() else None
    except Exception:
        return None


def _run_client_fetch(url: str, *, wait: bool = False,
                      wait_selector: Optional[str] = None) -> dict:
    from src.client.native_browser import fetch_browser_payload
    # wait/wait_selector are honored once Plan 2/NUS adds client_wait support;
    # today the basic CDP fetch already renders most JS.
    return fetch_browser_payload(
        url=url, page_type_hint="detail",
        browser_path=_clean_browser_path(), debug_port=9333, launch_timeout=45.0)


def _html_to_markdown(html: str, base_url: str) -> str:
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    obj = DefaultMarkdownGenerator().generate_markdown(input_html=html or "", base_url=base_url)
    return getattr(obj, "raw_markdown", "") or ""


def server_fetch(url: str) -> Tuple[str, str]:
    page = _run_server_crawl(url)
    return (getattr(page, "html", "") or "", getattr(page, "markdown", "") or "")


def client_fetch(url: str, *, wait: bool = False,
                 wait_selector: Optional[str] = None, **_: Any) -> Tuple[str, str]:
    payload = _run_client_fetch(url, wait=wait, wait_selector=wait_selector)
    html = str(payload.get("html_content") or "")
    return (html, _html_to_markdown(html, url) if html else "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_fetch_adapters.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crawl_strategy/fetch_adapters.py tests/test_crawl_strategy/test_fetch_adapters.py
git commit -m "feat(strategy): real server+client fetch adapters"
```

---

## Task 9: CLI — `adm-agent crawl-index` emitting outcome JSON

**Files:**
- Modify: `src/cmd/cli.py` (add a command near the existing `crawl`)
- Test: `tests/test_crawl_strategy/test_cli_crawl_index.py`

A new command keeps the existing `crawl` untouched. It prints the
`CrawlOutcome` as JSON so a weak agent reads `status` + `message_for_user`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_cli_crawl_index.py
import json
from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd.cli import app
from src.services.crawl_strategy.types import CrawlOutcome

runner = CliRunner()


def test_crawl_index_prints_outcome_json():
    fake = CrawlOutcome(status="ok", university="leeds", names=["A MSc", "B MSc"],
                        names_count=2, strategy_used="server×heading_link",
                        message_for_user="成功抓取 2 门课程名字。")
    with patch("src.cmd.cli.crawl_index", return_value=fake):
        result = runner.invoke(app, ["crawl-index", "https://courses.leeds.ac.uk/x", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["names_count"] == 2
    assert payload["message_for_user"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_cli_crawl_index.py -v`
Expected: FAIL — no command `crawl-index` / import error

- [ ] **Step 3: Write minimal implementation**

Add to `src/cmd/cli.py` (imports near the top with other service imports):

```python
from src.services.crawl_strategy.orchestrator import crawl_index
from src.services.crawl_strategy import fetch_adapters
from src.core.paths import get_data_dir
```

Add the command (placed after the existing `crawl` command):

```python
@app.command(name="crawl-index")
def crawl_index_cmd(
    index_url: str = typer.Argument(..., help="University programme index URL"),
    names_only: bool = typer.Option(True, "--names-only/--with-details",
                                    help="Names only (default) or also crawl details"),
    report_out: Optional[str] = typer.Option(None, "--report-out",
                                             help="Directory for phenomenon report zips"),
    as_json: bool = typer.Option(False, "--json", help="Print outcome as JSON"),
) -> None:
    """Classify an index page and crawl program names (deterministic tier)."""
    import dataclasses
    import json as _json
    from datetime import datetime, timezone

    del names_only  # detail crawl is Plan 2; names-only for now
    out_dir = report_out or str(get_data_dir() / "reports")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    outcome = crawl_index(
        index_url,
        server_fetch=fetch_adapters.server_fetch,
        client_fetch=fetch_adapters.client_fetch,
        report_out=out_dir, timestamp=timestamp,
    )
    if as_json:
        payload = dataclasses.asdict(outcome)
        payload.pop("items", None)  # items are internal; names is the public list
        typer.echo(_json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(outcome.message_for_user)
        for name in outcome.names:
            typer.echo(f"  - {name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_cli_crawl_index.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cmd/cli.py tests/test_crawl_strategy/test_cli_crawl_index.py
git commit -m "feat(strategy): adm-agent crawl-index CLI emitting outcome JSON"
```

---

## Task 10: Thin skill decision table

**Files:**
- Modify: `skills/uni-admission-crawl/SKILL.md`
- Test: `tests/test_crawl_strategy/test_skill_decision_table.py`

The skill becomes a status→action table the weak agent follows. The test
asserts the SKILL.md documents all three statuses and the command.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crawl_strategy/test_skill_decision_table.py
from pathlib import Path

SKILL = Path("skills/uni-admission-crawl/SKILL.md")


def test_skill_documents_crawl_index_and_three_statuses():
    text = SKILL.read_text(encoding="utf-8")
    assert "crawl-index" in text
    for status in ("ok", "llm_fallback", "unsupported"):
        assert status in text
    # the weak agent relays the tool's message, not its own reasoning
    assert "message_for_user" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v`
Expected: FAIL — strings not present yet

- [ ] **Step 3: Write minimal implementation**

Append a section to `skills/uni-admission-crawl/SKILL.md`:

```markdown
## Strategy-based crawl (preferred entry)

Run the tool — it classifies the page and picks a strategy itself. You do
NOT analyze the page or choose a strategy.

```bash
adm-agent crawl-index '<INDEX_URL>' --json
```

Read `status` from the JSON and act per this table. Relay the tool's
`message_for_user` verbatim — do not write your own analysis.

| status | what you do |
|---|---|
| `ok` | Relay `message_for_user`, then list the names. |
| `llm_fallback` | Relay `message_for_user`. Tell the user the result came via the generic path and the report at `report_zip` can be sent to the developer to add a proper strategy. |
| `unsupported` | Relay `message_for_user`. The phenomenon report was exported to `report_zip` — tell the user to send that file to the developer to add support. |

Never open or interpret the report's contents; that is the developer's job.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawl_strategy/test_skill_decision_table.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add skills/uni-admission-crawl/SKILL.md tests/test_crawl_strategy/test_skill_decision_table.py
git commit -m "docs(skill): strategy-crawl decision table for weak agents"
```

---

## Task 11: Full-suite + pylint gate

**Files:** none (verification)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing 837 + new ~26)

- [ ] **Step 2: Run pylint exactly as CI does**

Run: `uv run pylint $(git ls-files '*.py')`
Expected: 10.00/10, exit 0

- [ ] **Step 3: Fix any findings inline, re-run both**

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "chore(strategy): lint + suite green"
```

---

## NUS acceptance loop (manual, after the backbone lands)

Not a code task in this plan — the deliverable boundary. With the backbone
in place:

1. Run live: `adm-agent crawl-index 'https://study.nus.edu.sg/programme' --json`
   (client-mode fetch happens inside; needs a real browser).
2. Expect `status: unsupported` and a zip at `~/.uni-agent/reports/study.nus.edu.sg-*.zip`
   containing the real `index.html` + objective `params.json` + `run.log`.
3. Developer (senior LLM, offline) opens the zip, authors the NUS
   `text_heading` extractor refinements + a `client_wait` registry row +
   a golden sample from the captured page.
4. Re-run → NUS is now registry-pinned and crawls stably.

This exercises requirement #3 end-to-end and is the acceptance gate for
the reporter.

---

## Self-Review

**Spec coverage:**
- Module 1 (strategy + registry) → Tasks 1, 3 ✓
- Module 2 deterministic classify → Task 4 ✓; LLM tier (steps 2-3) → **Plan 2** (declared) ✓
- Module 3 (fetch ladder) → Task 5 + adapters Task 8 ✓
- Module 4 (detail pipeline) → **Plan 2** (declared; names-only here) ✓
- Module 5 (reporter) → Task 6, wired in Task 7 ✓
- Module 6 (weak-agent CLI/skill) → Tasks 9, 10 ✓
- Module 7 (testing/acceptance) → per-task TDD + Task 11 + NUS loop ✓

**Placeholder scan:** no TBD/TODO; every code step has full code; the
`client_wait` wait params are accepted now and exercised in the NUS loop
(noted, not a placeholder).

**Type consistency:** `Strategy(fetch, extract, params)`, `ExtractItem(name_en, detail_url)`,
`CrawlOutcome(status, university, names, items, names_count, strategy_used, report_zip, message_for_user)`,
`crawl_index(index_url, *, server_fetch, client_fetch, report_out, timestamp)`,
`fetch_with_escalation(index_url, *, server_fetch, client_fetch, wait_selector=None)`,
`export_report_zip(*, out_dir, index_url, html, markdown, params, run_log, timestamp)` —
consistent across tasks.
