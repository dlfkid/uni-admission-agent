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


def _over(items: List[ExtractItem], limit: Optional[int]) -> bool:
    return limit is not None and len(items) > limit


def _set_query(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != param.lower()]
    q.append((param, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(q), parts.fragment))


def _derive_next_url(current_url: str, page_idx: int, markdown: str,
                     params: dict) -> Optional[str]:
    """Return the next page's URL, or None if one cannot be derived.

    Priority: registry-pinned page param -> an existing ?page=N in the URL ->
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
        return server_fetch
    return client_fetch


def _single(first_md: str, index_url: str, crawl_range: CrawlRange,
            extract: Extract) -> PaginateResult:
    items = extract(first_md, index_url)
    reason = "reached_limit" if _over(items, crawl_range.limit) else "exhausted"
    return PaginateResult(_truncate(items, crawl_range.limit), 1, reason)


def _paginate_url(*, index_url: str, strategy: Strategy, first_md: str,
                  crawl_range: CrawlRange, server_fetch: Fetch,
                  client_fetch: Fetch, extract: Extract,
                  is_usable: Callable[[str], bool]) -> PaginateResult:
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
        if not is_usable(md):
            return PaginateResult(acc, pages, "unusable")
        before = len(acc)
        _absorb(acc, seen, extract(md, nxt))
        cur_url, cur_md = nxt, md
        if len(acc) == before:
            return PaginateResult(acc, pages, "exhausted")


def _paginate_scroll(*, index_url: str, crawl_range: CrawlRange,
                     client_fetch: Fetch, extract: Extract,
                     first_md: str) -> PaginateResult:
    del first_md  # scroll always re-fetches with the proper target_count
    _html, md = client_fetch(index_url, wait=True, target_count=crawl_range.limit)
    items = extract(md, index_url)
    reason = "reached_limit" if _over(items, crawl_range.limit) else "exhausted"
    return PaginateResult(_truncate(items, crawl_range.limit), 1, reason)


def paginate(*, mechanism: PaginateMode, crawl_range: CrawlRange, index_url: str,
             strategy: Strategy, first_html: str, first_md: str,
             server_fetch: Fetch, client_fetch: Fetch,
             extract: Extract,
             is_usable: Callable[[str], bool] = content_is_usable) -> PaginateResult:
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
            client_fetch=client_fetch, extract=extract,
            is_usable=is_usable)
    return _single(first_md, index_url, crawl_range, extract)


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
