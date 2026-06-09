"""Orchestrator — wires registry → fetch → classify/extract → outcome.

Fetch callables are injected so the orchestrator is fully unit-testable
without a real network or browser.  The LLM fallback tier is a future
plan; an unknown page that cannot be classified yields ``unsupported``
and exports a phenomenon report for developer review.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from src.services.crawl_strategy import registry as registry_mod
from src.services.crawl_strategy.classifier import classify, feature_signals
from src.services.crawl_strategy.extractors import get_extractor
from src.services.crawl_strategy.fetch_ladder import (
    content_is_usable, fetch_with_escalation,
)
from src.services.crawl_strategy.paginator import detect_mechanism, paginate
from src.services.crawl_strategy.reporter import export_report_zip
from src.services.crawl_strategy.types import (
    CrawlOutcome, CrawlRange, FetchMode, Strategy,
)

_REASON_ZH = {
    "reached_limit": "因达到上限停止",
    "exhausted": "已抓完全部",
    "unusable": "因翻页中遇到无法解析的页面停止",
    "safety_cap": "因达到安全翻页上限停止",
}

ServerFetch = Callable[[str], Tuple[str, str]]
ClientFetch = Callable[..., Tuple[str, str]]


def _university_slug(index_url: str) -> str:
    host = urlsplit(index_url).netloc.lower()
    parts = [p for p in host.split(".") if p not in ("www", "study", "courses")]
    return parts[0] if parts else host


def _do_fetch(
    index_url: str,
    pinned: Optional[Strategy],
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
) -> Tuple[str, str, str, list]:
    """Return (html, md, fetch_level, levels_tried) using pinned or escalation."""
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


def crawl_index(
    index_url: str,
    *,
    crawl_range: Optional[CrawlRange] = None,
    server_fetch: ServerFetch,
    client_fetch: ClientFetch,
    report_out: "Path | str",
    timestamp: str,
) -> CrawlOutcome:
    """Crawl a programme-index page and return a :class:`CrawlOutcome`.

    Args:
        index_url:    URL of the university's programme-listing page.
        server_fetch: Callable ``(url) -> (html, markdown)`` for plain HTTP.
        client_fetch: Callable ``(url, **kw) -> (html, markdown)`` for headless browser.
        report_out:   Directory to write phenomenon reports when the page is unsupported.
        timestamp:    ISO-ish timestamp string used in the report zip filename.

    Returns:
        A :class:`CrawlOutcome` with ``status="ok"`` on success, or
        ``status="unsupported"`` when no strategy matched (report zip included).
    """
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

    # Compute feature signals for the report — reuse classify result when
    # available (unknown path), otherwise compute signals directly (pinned path).
    if cr is not None:
        report_scores = cr.scores
    else:
        report_scores = feature_signals(md, index_url)

    zip_path = export_report_zip(
        out_dir=report_out, index_url=index_url, html=html, markdown=md,
        params={
            "university_guess": uni,
            "fetch_level_used": fetch_level,
            "fetch_levels_tried": levels_tried,
            "content_signal": {"chars": len(md or ""),
                               "usable": content_is_usable(md)},
            "feature_signals": report_scores,
            "strategy_scores": report_scores,
            "llm_classified_as": None,
            "llm_extract_count": 0,
            "outcome": "unsupported",
        },
        run_log="\n".join(str(lvl) for lvl in levels_tried),
        timestamp=timestamp,
    )
    if pinned:
        msg = (
            f"已知策略（{pinned.label()}）抓取失败：页面内容不足或结构已变。"
            f"现象报告已导出到 {zip_path}。"
        )
    else:
        msg = (
            f"这所大学（{uni}）暂不支持。现象报告已导出到 {zip_path}，"
            "发给开发者即可加入支持。"
        )
    return CrawlOutcome(
        status="unsupported", university=uni, report_zip=zip_path,
        message_for_user=msg,
    )
