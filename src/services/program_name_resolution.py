"""Program-name resolution with deterministic ranking and one-shot LLM fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.scrapers.helpers import build_url_name_signal, is_noise_program_name

_LOW_THRESHOLD_DEFAULT = 0.80
_CONFLICT_DELTA_DEFAULT = 0.05
_REQUIREMENT_SENTENCE_RE = re.compile(
    r"\b(entry requirements?|a bachelor degree|hons|ielts|to apply)\b",
    re.IGNORECASE,
)
_GENERIC_TITLE_RE = re.compile(r"^(study with us|courses?|programmes?)$", re.IGNORECASE)


@dataclass
class NameResolutionResult:
    status: Literal["resolved", "unresolved"]
    name: str
    confidence: float
    source: str
    reason: str
    top_candidates: list[dict[str, Any]]


def resolve_program_name(
    markdown_name: str = "",
    selected_anchor_text: str = "",
    detail_url: str = "",
    html_title: str = "",
    is_index_mode: bool = True,
    taxonomy_matches: list[dict[str, Any]] | None = None,
    router: Any | None = None,
    llm_fallback_enabled: bool = True,
    low_threshold: float = _LOW_THRESHOLD_DEFAULT,
    conflict_delta: float = _CONFLICT_DELTA_DEFAULT,
) -> NameResolutionResult:
    del taxonomy_matches  # Kept for interface compatibility with pipeline integration.

    ranked = _rank_candidates(
        _build_candidates(
            markdown_name=markdown_name,
            selected_anchor_text=selected_anchor_text,
            detail_url=detail_url,
            html_title=html_title,
            is_index_mode=is_index_mode,
        )
    )
    if not ranked:
        return _unresolved("no_candidates", ranked)

    top = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    runner_up_score = float(runner_up.get("score", 0.0)) if isinstance(runner_up, dict) else 0.0
    has_conflict = (
        isinstance(runner_up, dict) and (top["score"] - runner_up_score) < conflict_delta
    )
    if top["score"] >= low_threshold and not has_conflict:
        return _resolved(top["name"], top["score"], top["source"], "rule_high_confidence", ranked)

    if not llm_fallback_enabled or router is None:
        return _unresolved("low_confidence", ranked)

    evidence_pack = build_evidence_pack(
        markdown_name=markdown_name,
        selected_anchor_text=selected_anchor_text,
        detail_url=detail_url,
        html_title=html_title,
        ranked_candidates=ranked,
    )
    llm_result = _resolve_with_llm_once(router=router, evidence_pack=evidence_pack)
    llm_name = str(llm_result.get("name") or "").strip()
    llm_confidence = float(llm_result.get("confidence") or 0.0)
    if llm_name and llm_confidence >= low_threshold and not is_noise_program_name(llm_name):
        return _resolved(llm_name, llm_confidence, "llm", "llm_fallback", ranked)

    return _unresolved("llm_low_confidence", ranked)


def build_evidence_pack(
    markdown_name: str,
    selected_anchor_text: str,
    detail_url: str,
    html_title: str,
    ranked_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "anchor_text": selected_anchor_text,
        "url": detail_url,
        "slug": build_url_name_signal(detail_url),
        "title": html_title,
        "markdown_name": markdown_name,
        "candidates": ranked_candidates[:5],
    }


def _build_candidates(
    markdown_name: str,
    selected_anchor_text: str,
    detail_url: str,
    html_title: str,
    is_index_mode: bool,
) -> list[dict[str, Any]]:
    del is_index_mode
    raw_candidates = [
        {"name": selected_anchor_text, "source": "anchor"},
        {"name": build_url_name_signal(detail_url), "source": "slug"},
        {"name": html_title.split("|")[0].strip(), "source": "title"},
        {"name": markdown_name, "source": "markdown"},
    ]
    candidates: list[dict[str, Any]] = []
    for item in raw_candidates:
        name = str(item["name"] or "").strip()
        if not name:
            continue
        if is_noise_program_name(name):
            continue
        if item["source"] == "title" and _GENERIC_TITLE_RE.search(name):
            continue
        if _REQUIREMENT_SENTENCE_RE.search(name):
            continue
        candidates.append({"name": name, "source": item["source"]})
    return candidates


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_weight = {"anchor": 0.98, "slug": 0.76, "title": 0.82, "markdown": 0.62}
    ranked = []
    seen = set()
    for candidate in candidates:
        key = candidate["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        score = source_weight.get(candidate["source"], 0.5)
        ranked.append({**candidate, "score": score})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def _resolve_with_llm_once(router: Any, evidence_pack: dict[str, Any]) -> dict[str, Any]:
    prompt_path = Path(__file__).resolve().parents[1] / "agents" / "prompts" / "resolve_program_name.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace(
        "{evidence}",
        json.dumps(evidence_pack, ensure_ascii=False),
    )
    response = router.generate(prompt, dict)
    response_text = response if isinstance(response, str) else getattr(response, "text", "")
    try:
        parsed = json.loads(response_text)
    except Exception:
        return {"name": "", "confidence": 0.0}
    return parsed if isinstance(parsed, dict) else {"name": "", "confidence": 0.0}


def _resolved(
    name: str,
    confidence: float,
    source: str,
    reason: str,
    ranked: list[dict[str, Any]],
) -> NameResolutionResult:
    return NameResolutionResult(
        status="resolved",
        name=name,
        confidence=confidence,
        source=source,
        reason=reason,
        top_candidates=ranked[:5],
    )


def _unresolved(reason: str, ranked: list[dict[str, Any]]) -> NameResolutionResult:
    return NameResolutionResult(
        status="unresolved",
        name="",
        confidence=0.0,
        source="none",
        reason=reason,
        top_candidates=ranked[:5],
    )
