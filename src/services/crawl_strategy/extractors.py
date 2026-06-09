from __future__ import annotations

import re
from typing import Callable, Dict, List
from urllib.parse import urljoin, urlsplit

from src.scrapers.helpers import is_noise_program_name
from src.services.crawl_strategy.types import ExtractItem, ExtractKind

Extractor = Callable[[str, str], List[ExtractItem]]

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
_TEXT_HEADING_RE = re.compile(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", re.MULTILINE)
_LEARN_MORE_RE = re.compile(r"\[(?:\s*Learn More[^\]]*)\]\(\s*([^)\s]+)", re.IGNORECASE)

_PROGRAM_PREFIX_RE = re.compile(
    r"^(?:Doctor of |Master of |Bachelor of |"
    r"Graduate Diploma (?:in|of) |Graduate Certificate (?:in|of) )",
    re.IGNORECASE,
)
_DETAIL_URL_PATH_RE = re.compile(r"(?:programme|course)", re.IGNORECASE)
_DETAIL_URL_EXCLUDE_RE = re.compile(r"(?:resource|org-asset)", re.IGNORECASE)


def _clean(text: str) -> str:
    name = re.sub(r"\s+", " ", str(text or "")).strip()
    return _DURATION_SUFFIX_RE.sub("", name).strip()


def _canon(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.rstrip("/")
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/') or '/'}"


def _dedup(items: List[ExtractItem]) -> List[ExtractItem]:
    seen_url: set[str] = set()
    seen_name: set[str] = set()
    out: List[ExtractItem] = []
    for item in items:
        if not item.name_en or is_noise_program_name(item.name_en):
            continue
        ukey = _canon(item.detail_url) if item.detail_url else ""
        nkey = item.name_en.casefold()
        if (ukey and ukey in seen_url) or nkey in seen_name:
            continue
        if ukey:
            seen_url.add(ukey)
        seen_name.add(nkey)
        out.append(item)
    return out


def extract_heading_link(markdown: str, base_url: str) -> List[ExtractItem]:
    out = [ExtractItem(_clean(m.group(1)), urljoin(base_url, m.group(2)))
           for m in _HEADING_LINK_RE.finditer(markdown or "")]
    return _dedup(out)


def _looks_like_program(text: str) -> bool:
    return bool(_DEGREE_SUFFIX_RE.search(str(text or "").strip()))


def extract_inline_degree(markdown: str, base_url: str) -> List[ExtractItem]:
    out: List[ExtractItem] = []
    for match in _ANY_LINK_RE.finditer(markdown or ""):
        if _looks_like_program(match.group(1)):
            out.append(ExtractItem(_clean(match.group(1)), urljoin(base_url, match.group(2))))
    return _dedup(out)


def extract_merged_columns(markdown: str, base_url: str) -> List[ExtractItem]:
    return extract_inline_degree(markdown, base_url)


def _blob_name(text: str) -> str | None:
    match = _BLOB_NAME_RE.search(str(text or ""))
    if not match:
        return None
    name = re.sub(r"\s+", " ", match.group(1)).strip()
    return f"{name} {match.group(2).strip()}" if name else None


def extract_blob(markdown: str, base_url: str) -> List[ExtractItem]:
    out: List[ExtractItem] = []
    for match in _ANY_LINK_RE.finditer(markdown or ""):
        name = _blob_name(match.group(1))
        if name:
            out.append(ExtractItem(name, urljoin(base_url, match.group(2))))
    return _dedup(out)


def _is_program_heading(text: str) -> bool:
    """Return True if *text* looks like a degree-programme name."""
    stripped = str(text or "").strip()
    if not stripped or is_noise_program_name(stripped):
        return False
    return bool(_PROGRAM_PREFIX_RE.match(stripped) or _DEGREE_SUFFIX_RE.search(stripped))


def _pick_detail_url(lines_between: List[str], base_url: str) -> str | None:
    """Return the first link URL from *lines_between* that looks like a programme
    detail page (path contains 'programme' or 'course', NOT 'resource'/'org-asset').
    Returns None if no qualifying URL is found.
    """
    for line in lines_between:
        for m in _ANY_LINK_RE.finditer(line):
            raw_url = m.group(2)
            try:
                path = urlsplit(raw_url).path
            except ValueError:
                path = raw_url
            if (_DETAIL_URL_PATH_RE.search(path)
                    and not _DETAIL_URL_EXCLUDE_RE.search(path)):
                return urljoin(base_url, raw_url)
    return None


def extract_text_heading(markdown: str, base_url: str) -> List[ExtractItem]:
    """Emit every heading whose text matches a programme-name shape.

    detail_url is set only when a link whose URL path contains 'programme' or
    'course' (but not 'resource'/'org-asset') appears between this heading and
    the next one.  Works for both simple ``[Learn More](url)`` and complex
    Salesforce-style pages where the real URL is only in the outer link target.
    """
    out: List[ExtractItem] = []
    lines = (markdown or "").splitlines()
    n = len(lines)
    i = 0
    while i < n:
        heading = _TEXT_HEADING_RE.match(lines[i])
        if heading:
            cand = _clean(heading.group(1))
            if _is_program_heading(cand):
                # Collect lines until the next heading
                j = i + 1
                while j < n and not _TEXT_HEADING_RE.match(lines[j]):
                    j += 1
                detail = _pick_detail_url(lines[i + 1:j], base_url)
                out.append(ExtractItem(cand, detail))
            i += 1
        else:
            i += 1
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
