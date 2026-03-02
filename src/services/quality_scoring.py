"""Quality scoring utilities for Phase 3 golden-sample regression."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Tuple

from src.core.parser import DataCleaner
from src.models.admission import CurrencyCode, StudyMode
from src.scrapers.helpers import extract_program_name
from src.services.golden_samples import load_manifest, slugify_case_id

logger = logging.getLogger(__name__)

CORE_FIELDS = ("name_en", "tuition", "study_options", "deadlines", "requirements")
VALID_REQUIREMENT_CATEGORIES = {
    "academic_subject",
    "language",
    "standardized_test",
    "portfolio",
    "experience",
    "other",
}
TUITION_NOISE_TOKENS = (
    "deposit",
    "scholarship",
    "funding",
    "living cost",
    "additional cost",
    "application fee",
)
TUITION_PATTERN = re.compile(
    r"(HK\$|US\$|£|RMB|CNY|HKD|USD|GBP)\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def _currency_from_token(token: str) -> CurrencyCode:
    normalized = token.upper()
    if normalized in {"HK$", "HKD"}:
        return CurrencyCode.HKD
    if normalized in {"US$", "USD"}:
        return CurrencyCode.USD
    if normalized in {"RMB", "CNY"}:
        return CurrencyCode.CNY
    if normalized in {"£", "GBP"}:
        return CurrencyCode.GBP
    return CurrencyCode.OTHER


def _extract_tuition_candidates(line: str) -> List[Tuple[float, str]]:
    candidates: List[Tuple[float, str]] = []
    for match in TUITION_PATTERN.finditer(str(line or "")):
        token = match.group(1)
        amount_raw = match.group(2)
        try:
            amount = float(amount_raw.replace(",", ""))
        except ValueError:
            continue
        currency = _currency_from_token(token)
        candidates.append((amount, currency.value))
    return candidates


def _tuition_line_priority(line: str) -> int:
    line_lc = str(line or "").lower()
    if any(token in line_lc for token in TUITION_NOISE_TOKENS):
        return -1

    score = 0
    if "tuition" in line_lc:
        score += 3
    if "fee" in line_lc or "fees" in line_lc or "cost" in line_lc:
        score += 2
    if "international" in line_lc or "uk " in line_lc or "home " in line_lc:
        score += 1
    return score


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_relevant_lines(markdown: str, keywords: Tuple[str, ...]) -> List[str]:
    lines = [line.strip() for line in markdown.splitlines()]
    out: List[str] = []
    for line in lines:
        if not line or len(line) < 4:
            continue
        line_lc = line.lower()
        if any(word in line_lc for word in keywords):
            out.append(line)
    return out


def _extract_requirement_signals(markdown: str, source_url: str) -> List[Dict[str, Any]]:
    patterns = {
        "language": (r"\bielts\b", r"\btoefl\b", r"\bduolingo\b", r"english language"),
        "standardized_test": (r"\bsat\b", r"\bact\b", r"\bgre\b", r"\bgmat\b"),
        "academic_subject": (
            r"a-level",
            r"a level",
            r"\bib diploma\b",
            r"entry requirement",
        ),
    }
    lines = _extract_relevant_lines(
        markdown,
        (
            "ielts",
            "toefl",
            "duolingo",
            "gre",
            "gmat",
            "entry requirement",
            "entry requirements",
            "english language",
            "a-level",
            "a level",
            "ib diploma",
        ),
    )

    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        line_lc = line.lower()
        category = "other"
        for candidate, tokens in patterns.items():
            if any(re.search(token, line_lc) for token in tokens):
                category = candidate
                break
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            {
                "category": category,
                "requirement_text": normalized[:240],
                "evidence_url": source_url,
            }
        )
        if len(items) >= 12:
            break
    return items


def extract_offline_observation(markdown: str, source_url: str) -> Dict[str, Any]:
    name_en = extract_program_name(markdown)

    tuition = None
    markdown_lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    tuition_candidates: List[Tuple[int, int, float, str]] = []
    for idx, line in enumerate(markdown_lines):
        context_start = max(0, idx - 1)
        context_end = min(len(markdown_lines), idx + 2)
        context_line = " ".join(markdown_lines[context_start:context_end])
        priority = _tuition_line_priority(context_line)
        if priority < 0:
            continue

        parsed_candidates = _extract_tuition_candidates(line)
        for amount, currency in parsed_candidates:
            currency_priority = 1 if currency != CurrencyCode.OTHER.value else 0
            tuition_candidates.append((priority, currency_priority, amount, currency))

    if tuition_candidates:
        best_priority, _, best_amount, best_currency = max(
            tuition_candidates,
            key=lambda row: (row[0], row[1], row[2]),
        )
        if best_priority >= 0:
            tuition = {
                "amount": float(best_amount),
                "currency": best_currency,
                "evidence_url": source_url,
            }

    if tuition is None:
        amount, currency = DataCleaner.parse_tuition(markdown)
        if amount is not None and currency is not None:
            numeric_amount = float(amount)
            if currency != CurrencyCode.OTHER or numeric_amount >= 1000:
                tuition = {
                    "amount": numeric_amount,
                    "currency": currency.value,
                    "evidence_url": source_url,
                }

    study_options: List[Dict[str, Any]] = []
    option_lines = _extract_relevant_lines(
        markdown,
        (
            "full-time",
            "full time",
            "part-time",
            "part time",
            "duration",
            "months",
            "month",
            "year",
        ),
    )
    seen_option_keys: set[Tuple[str, int]] = set()
    for line in option_lines:
        for option in DataCleaner.parse_study_options(line):
            mode = str(option.get("mode") or "")
            duration_months = int(option.get("duration_months") or 0)
            key = (mode, duration_months)
            if duration_months <= 0 or key in seen_option_keys:
                continue
            seen_option_keys.add(key)
            study_options.append(
                {
                    "mode": mode,
                    "duration_months": duration_months,
                    "evidence_url": source_url,
                }
            )

    deadline_lines = _extract_relevant_lines(
        markdown,
        (
            "deadline",
            "application closes",
            "applications close",
            "closing date",
            "apply by",
        ),
    )
    deadlines = DataCleaner.parse_deadlines("\n".join(deadline_lines))
    for row in deadlines:
        row["evidence_url"] = source_url

    requirements = _extract_requirement_signals(markdown, source_url)

    return {
        "name_en": name_en,
        "tuition": tuition,
        "study_options": study_options,
        "deadlines": deadlines,
        "requirements": requirements,
        "source_url": source_url,
    }


def _normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _name_similarity(expected_name: str, observed_name: str) -> float:
    if not expected_name:
        return 1.0

    expected_norm = _normalize_name(expected_name)
    observed_norm = _normalize_name(observed_name)
    score = 0.0

    if not observed_norm:
        score = 0.0
    elif expected_norm == observed_norm:
        score = 1.0
    elif expected_norm in observed_norm or observed_norm in expected_norm:
        score = 0.85
    else:
        expected_tokens = set(re.findall(r"[a-z0-9]+", expected_name.lower()))
        observed_tokens = set(re.findall(r"[a-z0-9]+", observed_name.lower()))
        if expected_tokens and observed_tokens:
            overlap = len(expected_tokens & observed_tokens)
            union = len(expected_tokens | observed_tokens)
            score = (overlap / union) if union else 0.0
    return score


def _keywords_coverage(keywords: List[str], text: str) -> float:
    if not keywords:
        return 1.0
    haystack = str(text or "").lower()
    matched = sum(1 for kw in keywords if str(kw).lower() in haystack)
    return matched / len(keywords)


def _field_present(observed: Dict[str, Any], field_name: str) -> bool:
    value = observed.get(field_name)
    if field_name == "name_en":
        return bool(str(value or "").strip())
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return bool(value)


def _score_completeness(observed: Dict[str, Any], required_fields: List[str]) -> float:
    fields = required_fields or ["name_en"]
    present = sum(1 for field in fields if _field_present(observed, field))
    return present / len(fields)


def _score_normalization(observed: Dict[str, Any]) -> float:
    checks: List[float] = []

    tuition = observed.get("tuition")
    if tuition is None:
        checks.append(1.0)
    else:
        currency = str(tuition.get("currency") or "")
        if currency not in {c.value for c in CurrencyCode}:
            checks.append(0.0)
        elif currency == CurrencyCode.OTHER.value:
            checks.append(0.4)
        else:
            checks.append(1.0)

    options = observed.get("study_options") or []
    if not options:
        checks.append(1.0)
    else:
        valid_modes = {m.value for m in StudyMode}
        if not all((opt.get("mode") in valid_modes) for opt in options):
            checks.append(0.0)
        else:
            unknown_count = sum(1 for opt in options if opt.get("mode") == StudyMode.UNKNOWN.value)
            if unknown_count == 0:
                checks.append(1.0)
            else:
                penalty = min(0.6, 0.3 * unknown_count)
                checks.append(max(0.0, 1.0 - penalty))

    requirements = observed.get("requirements") or []
    if not requirements:
        checks.append(1.0)
    else:
        checks.append(
            1.0
            if all((req.get("category") in VALID_REQUIREMENT_CATEGORIES) for req in requirements)
            else 0.0
        )

    return mean(checks) if checks else 0.0


def _score_evidence(observed: Dict[str, Any], required_fields: List[str]) -> float:
    checks: List[float] = []

    tuition = observed.get("tuition")
    if tuition:
        checks.append(1.0 if tuition.get("evidence_url") else 0.0)

    for field_name in ("study_options", "deadlines", "requirements"):
        rows = observed.get(field_name) or []
        if rows:
            coverage = mean(1.0 if row.get("evidence_url") else 0.0 for row in rows)
            checks.append(coverage)

    if checks:
        return mean(checks)

    if any(field in {"tuition", "study_options", "deadlines", "requirements"} for field in required_fields):
        return 0.0
    return 1.0


def score_case(
    *,
    case_id: str,
    case_name: str,
    case_dir: Path,
    case_meta: Dict[str, Any],
) -> Dict[str, Any]:
    expected = _read_json(case_dir / "expected.json")
    metadata = _read_json(case_dir / "metadata.json")
    detail_md_path = case_dir / "detail.md"

    if not detail_md_path.exists():
        return {
            "case_id": case_id,
            "name": case_name,
            "status": "failed",
            "error": f"missing detail markdown: {detail_md_path}",
        }

    markdown = detail_md_path.read_text(encoding="utf-8")
    source_url = (
        metadata.get("pages", {}).get("detail", {}).get("url")
        or case_meta.get("detail_url")
        or ""
    )
    observed = extract_offline_observation(markdown, source_url)

    expected_name = str(expected.get("expected_name") or "")
    expected_keywords = list(expected.get("expected_keywords") or [])
    expected_tuition_currency = str(expected.get("expected_tuition_currency") or "").strip()
    min_requirement_count = int(expected.get("min_requirement_count") or 0)
    required_fields = list(expected.get("required_fields") or ["name_en"])
    case_threshold = float(expected.get("case_threshold") or 0.55)

    completeness = _score_completeness(observed, required_fields)
    name_score = _name_similarity(expected_name, observed.get("name_en") or "")
    keyword_score = _keywords_coverage(expected_keywords, observed.get("name_en") or markdown)
    correctness_components = [name_score, keyword_score]

    if expected_tuition_currency:
        observed_currency = str((observed.get("tuition") or {}).get("currency") or "")
        correctness_components.append(1.0 if observed_currency == expected_tuition_currency else 0.0)

    if min_requirement_count > 0:
        requirement_count = len(observed.get("requirements") or [])
        requirement_count_score = min(1.0, requirement_count / float(min_requirement_count))
        correctness_components.append(requirement_count_score)

    correctness = mean(correctness_components)
    normalization = _score_normalization(observed)
    evidence_coverage = _score_evidence(observed, required_fields)

    overall = (
        0.35 * correctness
        + 0.25 * completeness
        + 0.20 * normalization
        + 0.20 * evidence_coverage
    )
    passed = overall >= case_threshold

    return {
        "case_id": case_id,
        "name": case_name,
        "status": "passed" if passed else "failed",
        "threshold": round(case_threshold, 4),
        "overall_score": round(overall, 4),
        "metrics": {
            "completeness": round(completeness, 4),
            "correctness": round(correctness, 4),
            "normalization_consistency": round(normalization, 4),
            "evidence_coverage": round(evidence_coverage, 4),
            "name_similarity": round(name_score, 4),
            "keyword_coverage": round(keyword_score, 4),
        },
        "required_fields": required_fields,
        "observed": observed,
        "expected": {
            "expected_name": expected_name,
            "expected_keywords": expected_keywords,
            "expected_tuition_currency": expected_tuition_currency,
            "min_requirement_count": min_requirement_count,
        },
    }


def score_manifest(
    manifest_path: str,
    *,
    base_dir: str = "golden_samples/cases",
    output_report_path: str = "golden_samples/reports/quality_report.json",
    global_threshold: float = 0.60,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)

    case_results: List[Dict[str, Any]] = []

    for raw_case in manifest.get("cases", []):
        case = dict(raw_case or {})
        case_id = slugify_case_id(case.get("case_id") or case.get("name"))
        case_name = str(case.get("name") or case_id)
        case_dir = root / case_id
        case_results.append(
            score_case(
                case_id=case_id,
                case_name=case_name,
                case_dir=case_dir,
                case_meta=case,
            )
        )

    passed_cases = [row for row in case_results if row.get("status") == "passed"]
    scored_cases = [row for row in case_results if "overall_score" in row]
    mean_score = mean(row["overall_score"] for row in scored_cases) if scored_cases else 0.0
    pass_rate = (len(passed_cases) / len(case_results)) if case_results else 0.0

    summary = {
        "generated_at": _utc_now_iso(),
        "manifest": str(Path(manifest_path)),
        "global_threshold": round(global_threshold, 4),
        "aggregate": {
            "case_count": len(case_results),
            "scored_case_count": len(scored_cases),
            "passed_case_count": len(passed_cases),
            "failed_case_count": len(case_results) - len(passed_cases),
            "pass_rate": round(pass_rate, 4),
            "mean_score": round(mean_score, 4),
        },
        "cases": case_results,
    }

    output_path = Path(output_report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    global_pass = (summary["aggregate"]["mean_score"] >= global_threshold) and (
        summary["aggregate"]["failed_case_count"] == 0
    )
    summary["global_pass"] = global_pass

    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
