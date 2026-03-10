"""Two-stage page-type classifier for auto mode."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from src.scrapers.helpers import load_prompt

_INDEX_HINT_RE = re.compile(
    r"\b(find your (course|programme|programmes?)|course search|search degree programmes?|"
    r"showing\s+\d+\s+courses?|filters?|browse( by)? subject|list of courses?|"
    r"all programmes|all programs)\b",
    re.IGNORECASE,
)
_DETAIL_HINT_RE = re.compile(
    r"\b(tuition fees?|entry requirements?|admission requirements?|course structure|"
    r"modules?|how to apply|study mode|start date|application deadlines?)\b",
    re.IGNORECASE,
)
_DETAIL_URL_RE = re.compile(
    r"/(courses?/list/\d+|tpg/\d{4}/[0-9a-z\-]{4,}|"
    r"programmes?/[0-9a-z\-]*-[0-9a-z\-]+|programs?/[0-9a-z\-]*-[0-9a-z\-]+|"
    r"programmes?/undergraduate/\d+[0-9a-z\-]*-[0-9a-z\-]+|"
    r"programs?/undergraduate/\d+[0-9a-z\-]*-[0-9a-z\-]+)",
    re.IGNORECASE,
)
_DETAIL_DEGREE_HEADING_RE = re.compile(
    r"(?m)^\s*#\s+.*\b(msc|ma|mba|llm|mres|mphil|master|bsc|ba|phd|doctor)\b",
    re.IGNORECASE,
)
_DETAIL_COURSE_LIST_ID_RE = re.compile(r"/courses?/list/\d+/", re.IGNORECASE)


@dataclass
class PageTypeDecision:
    page_type: Literal["index", "detail"]
    confidence: float
    decision_source: Literal["rule", "llm", "rule_fallback"]
    reasons: list[str]
    scores: dict[str, float]


class _PageTypeLLMOutput(BaseModel):
    page_type: str = ""
    confidence: float = 0.0
    reason: str = ""


def classify_page_type_auto(
    *,
    url: str,
    markdown: str,
    html: str,
    link_count: int,
    router: Any | None,
    margin_high: float = 0.35,
    llm_confidence_pass: float = 0.70,
) -> PageTypeDecision:
    index_score, detail_score, reasons = _score_rule_signals(url, markdown, html, link_count)
    margin = abs(index_score - detail_score)
    preferred = "index" if index_score >= detail_score else "detail"

    if margin >= margin_high:
        confidence = min(0.99, 0.55 + margin)
        return PageTypeDecision(
            page_type=preferred,
            confidence=confidence,
            decision_source="rule",
            reasons=reasons,
            scores={"index": index_score, "detail": detail_score},
        )

    llm_reasons = list(reasons)
    if router is not None:
        llm_out = _classify_with_llm_once(
            router=router,
            url=url,
            markdown=markdown,
            html=html,
            link_count=link_count,
            rule_index_score=index_score,
            rule_detail_score=detail_score,
        )
        page_type = str(llm_out.get("page_type") or "").strip().lower()
        confidence = float(llm_out.get("confidence") or 0.0)
        reason = str(llm_out.get("reason") or "").strip()
        if reason:
            llm_reasons.append(f"llm:{reason}")
        if page_type in {"index", "detail"} and confidence >= llm_confidence_pass:
            return PageTypeDecision(
                page_type=page_type,
                confidence=confidence,
                decision_source="llm",
                reasons=llm_reasons,
                scores={"index": index_score, "detail": detail_score},
            )

    return PageTypeDecision(
        page_type=preferred,
        confidence=min(0.69, 0.5 + (margin / 2.0)),
        decision_source="rule_fallback",
        reasons=llm_reasons + ["fallback:uncertain_or_llm_failed"],
        scores={"index": index_score, "detail": detail_score},
    )


def _score_rule_signals(url: str, markdown: str, html: str, link_count: int) -> tuple[float, float, list[str]]:
    reasons: list[str] = []
    index_score = 0.0
    detail_score = 0.0

    content = str(markdown or "")
    links = max(0, int(link_count or 0))
    index_hits = len(_INDEX_HINT_RE.findall(content))
    detail_hits = len(_DETAIL_HINT_RE.findall(content))
    if index_hits:
        index_score += min(0.45, index_hits * 0.12)
        reasons.append(f"rule:index_content_hits={index_hits}")
    detail_multiplier = 0.12
    detail_cap = 0.70
    if links >= 80 and index_hits >= 2:
        detail_multiplier = 0.03
        detail_cap = 0.30
    if detail_hits:
        detail_score += min(detail_cap, detail_hits * detail_multiplier)
        reasons.append(f"rule:detail_content_hits={detail_hits}")

    if links >= 20:
        index_score += 0.08
        reasons.append("rule:high_link_density")
    elif links <= 10 and detail_hits >= 1:
        detail_score += 0.12
        reasons.append("rule:low_link_density")

    path = ""
    try:
        path = urlparse(str(url or "")).path.lower()
    except Exception:
        path = ""
    if _DETAIL_COURSE_LIST_ID_RE.search(path):
        detail_score += 0.50
        reasons.append("rule:detail_course_id_url_signal")
    if "course-search" in path or "find-your-programmes" in path or path.rstrip("/").endswith("/courses/list"):
        index_score += 0.3
        reasons.append("rule:index_url_signal")
    if _DETAIL_URL_RE.search(path):
        detail_score += 0.35
        reasons.append("rule:detail_url_signal")

    if _DETAIL_DEGREE_HEADING_RE.search(content) and links <= 25:
        detail_score += 0.20
        reasons.append("rule:detail_degree_heading")

    title = _extract_html_title(str(html or ""))
    if title and _INDEX_HINT_RE.search(title):
        index_score += 0.2
        reasons.append("rule:index_title_signal")
    if title and _DETAIL_HINT_RE.search(title):
        detail_score += 0.2
        reasons.append("rule:detail_title_signal")

    return index_score, detail_score, reasons


def _extract_html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    normalized = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
    return normalized


def _classify_with_llm_once(
    *,
    router: Any,
    url: str,
    markdown: str,
    html: str,
    link_count: int,
    rule_index_score: float,
    rule_detail_score: float,
) -> dict[str, Any]:
    prompt_template = load_prompt("classify_page_type_auto.txt")
    evidence = {
        "url": url,
        "title": _extract_html_title(html),
        "link_count": int(link_count or 0),
        "rule_scores": {"index": rule_index_score, "detail": rule_detail_score},
        "markdown_preview": str(markdown or "")[:2500],
    }
    prompt = prompt_template.replace("{evidence}", json.dumps(evidence, ensure_ascii=False))
    response = router.generate(prompt, _PageTypeLLMOutput)
    text = response if isinstance(response, str) else getattr(response, "text", "")
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
