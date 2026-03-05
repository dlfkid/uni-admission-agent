from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import select

from src.models.taxonomy import SubjectTaxonomy
from src.storage.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_SUBJECT_TAXONOMY_SEED_PATH = "golden_samples/program_names/cleaned_programs_names.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


class SubjectTaxonomyRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db_manager = db_manager or DatabaseManager()

    def upsert_many(self, entries: list[dict]) -> dict:
        if not entries:
            return {"inserted": 0, "updated": 0}

        inserted = 0
        updated = 0
        with self.db_manager.get_session() as session:
            for entry in entries:
                normalized = str(entry.get("normalized_name") or "").strip()
                if not normalized:
                    continue

                existing = session.exec(
                    select(SubjectTaxonomy).where(SubjectTaxonomy.normalized_name == normalized)
                ).first()

                aliases = [str(item).strip() for item in entry.get("aliases") or [] if str(item).strip()]
                status = str(entry.get("status") or "active").strip() or "active"
                first_seen_url = entry.get("first_seen_url")
                confidence = entry.get("confidence")
                source = str(entry.get("source") or "seed").strip() or "seed"
                name_en = str(entry.get("name_en") or "").strip()

                if existing:
                    existing.name_en = name_en or existing.name_en
                    existing.source = source or existing.source
                    existing.status = status
                    existing.first_seen_url = first_seen_url or existing.first_seen_url
                    existing.confidence = confidence if confidence is not None else existing.confidence
                    existing.aliases = sorted(
                        {*(existing.aliases or []), *aliases, existing.name_en}
                    )
                    existing.updated_at = _utc_now()
                    updated += 1
                    continue

                session.add(
                    SubjectTaxonomy(
                        name_en=name_en,
                        normalized_name=normalized,
                        aliases=sorted({*aliases, name_en}),
                        source=source,
                        first_seen_url=first_seen_url,
                        confidence=confidence,
                        status=status,
                        updated_at=_utc_now(),
                    )
                )
                inserted += 1
            session.commit()

        return {"inserted": inserted, "updated": updated}

    def list_active(self) -> list[dict]:
        with self.db_manager.get_session() as session:
            rows = session.exec(
                select(SubjectTaxonomy).where(SubjectTaxonomy.status == "active")
            ).all()

        entries: list[dict] = []
        for row in rows:
            entries.append(
                {
                    "id": row.id,
                    "name_en": row.name_en,
                    "normalized_name": row.normalized_name,
                    "aliases": list(row.aliases or []),
                    "source": row.source,
                    "first_seen_url": row.first_seen_url,
                    "confidence": row.confidence,
                    "status": row.status,
                }
            )
        return entries


class SubjectTaxonomyService:
    def __init__(
        self,
        repository: Optional[SubjectTaxonomyRepository] = None,
        cache_size: int = 256,
    ) -> None:
        self.repository = repository or SubjectTaxonomyRepository()
        self.cache_size = max(1, int(cache_size))
        self._normalized_index: dict[str, dict] = {}
        self._token_index: dict[str, set[str]] = {}
        self._cache: "OrderedDict[Tuple[Tuple[str, ...], int], list[dict]]" = OrderedDict()

    @property
    def token_index(self) -> dict[str, set[str]]:
        return self._token_index

    @property
    def memory_entry_count(self) -> int:
        return len(self._normalized_index)

    def sync_seed_from_json(self, path: str) -> dict:
        seed_path = Path(path)
        if not seed_path.exists():
            return {
                "path": str(seed_path),
                "loaded": 0,
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "missing": True,
            }

        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        entries = self._prepare_entries(payload)
        upsert_result = self.repository.upsert_many(entries)
        self.reload_memory_index()
        return {
            "path": str(seed_path),
            "loaded": len(entries),
            "inserted": int(upsert_result.get("inserted") or 0),
            "updated": int(upsert_result.get("updated") or 0),
            "skipped": 0,
            "missing": False,
        }

    def reload_memory_index(self) -> None:
        rows = self.repository.list_active()
        normalized_index: dict[str, dict] = {}
        token_index: dict[str, set[str]] = {}

        for row in rows:
            normalized = str(row.get("normalized_name") or "").strip()
            if not normalized:
                continue

            name_en = str(row.get("name_en") or "").strip()
            aliases = [str(item).strip() for item in row.get("aliases") or [] if str(item).strip()]
            normalized_index[normalized] = {
                "id": row.get("id"),
                "name_en": name_en,
                "normalized_name": normalized,
                "aliases": aliases,
                "source": row.get("source"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "first_seen_url": row.get("first_seen_url"),
            }

            token_sources = [name_en, *aliases]
            for text in token_sources:
                for token in _tokenize(text):
                    token_index.setdefault(token, set()).add(normalized)

        self._normalized_index = normalized_index
        self._token_index = token_index
        self._cache.clear()

    def match_signals(self, signals: list[str], top_k: int = 3) -> list[dict]:
        if not signals or not self._normalized_index:
            return []

        bounded_top_k = max(1, int(top_k))
        normalized_signals = tuple(
            signal for signal in [normalize_name(item) for item in signals] if signal
        )
        if not normalized_signals:
            return []

        cache_key = (normalized_signals, bounded_top_k)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return list(self._cache[cache_key])

        candidate_keys = self._candidate_keys(signals, normalized_signals)
        if not candidate_keys:
            return []

        scores: dict[str, float] = {}
        for key in candidate_keys:
            entry = self._normalized_index.get(key)
            if not entry:
                continue
            best_score = 0.0
            for signal in signals:
                best_score = max(best_score, self._score_signal_for_entry(signal, entry))
            if best_score > 0:
                scores[key] = best_score

        ranked = sorted(
            (
                {
                    "name_en": self._normalized_index[key]["name_en"],
                    "normalized_name": key,
                    "source": self._normalized_index[key].get("source"),
                    "score": round(score, 4),
                }
                for key, score in scores.items()
            ),
            key=lambda item: (-item["score"], item["name_en"]),
        )

        result = ranked[:bounded_top_k]
        self._cache[cache_key] = result
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return list(result)

    def _prepare_entries(self, payload: Any) -> list[dict]:
        dedup: dict[str, dict] = {}
        for item in self._iter_seed_items(payload):
            name = str(item.get("name_en") or "").strip()
            normalized = normalize_name(name)
            if not normalized:
                continue
            aliases = [str(alias).strip() for alias in item.get("aliases") or [] if str(alias).strip()]
            source = str(item.get("source") or "seed").strip() or "seed"
            status = str(item.get("status") or "active").strip() or "active"
            confidence = item.get("confidence")
            first_seen_url = item.get("first_seen_url")

            if normalized in dedup:
                merged_aliases = sorted(
                    {
                        *(dedup[normalized].get("aliases") or []),
                        *aliases,
                        name,
                    }
                )
                dedup[normalized]["aliases"] = merged_aliases
                continue

            dedup[normalized] = {
                "name_en": name,
                "normalized_name": normalized,
                "aliases": sorted({*aliases, name}),
                "source": source,
                "status": status,
                "confidence": confidence,
                "first_seen_url": first_seen_url,
            }

        return list(dedup.values())

    def _iter_seed_items(self, payload: Any) -> Iterable[dict]:
        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ("program_names", "names", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    items = value
                    break

        for item in items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    yield {"name_en": text}
                continue

            if isinstance(item, dict):
                name = str(
                    item.get("name_en")
                    or item.get("name")
                    or item.get("program_name")
                    or ""
                ).strip()
                if not name:
                    continue
                aliases = item.get("aliases")
                yield {
                    "name_en": name,
                    "aliases": aliases if isinstance(aliases, list) else [],
                    "source": item.get("source") or "seed",
                    "status": item.get("status") or "active",
                    "confidence": item.get("confidence"),
                    "first_seen_url": item.get("first_seen_url"),
                }

    def _candidate_keys(
        self,
        raw_signals: list[str],
        normalized_signals: Tuple[str, ...],
    ) -> set[str]:
        keys = set()
        for normalized in normalized_signals:
            if normalized in self._normalized_index:
                keys.add(normalized)
        for signal in raw_signals:
            for token in _tokenize(signal):
                keys.update(self._token_index.get(token, set()))
        return keys

    def _score_signal_for_entry(self, signal: str, entry: dict) -> float:
        candidates = [str(entry.get("name_en") or "").strip(), *(entry.get("aliases") or [])]
        signal_normalized = normalize_name(signal)
        signal_tokens = set(_tokenize(signal))
        best = 0.0
        for candidate in candidates:
            normalized = normalize_name(candidate)
            if not normalized:
                continue
            if signal_normalized == normalized:
                return 1.0

            ratio = SequenceMatcher(None, signal_normalized, normalized).ratio()
            if signal_normalized and normalized and (
                normalized in signal_normalized or signal_normalized in normalized
            ):
                ratio = max(ratio, 0.9)

            candidate_tokens = set(_tokenize(candidate))
            if signal_tokens and candidate_tokens:
                overlap = len(signal_tokens & candidate_tokens) / max(
                    len(signal_tokens | candidate_tokens),
                    1,
                )
                ratio = max(ratio, overlap * 0.95)

            best = max(best, ratio)
        return best


_SERVICE_SINGLETON: Optional[SubjectTaxonomyService] = None


def get_subject_taxonomy_service() -> SubjectTaxonomyService:
    global _SERVICE_SINGLETON
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = SubjectTaxonomyService()
    return _SERVICE_SINGLETON


def bootstrap_subject_taxonomy(
    seed_path: str = DEFAULT_SUBJECT_TAXONOMY_SEED_PATH,
) -> dict:
    service = get_subject_taxonomy_service()
    try:
        result = service.sync_seed_from_json(seed_path)
        logger.info(
            "Subject taxonomy bootstrap: loaded=%s inserted=%s updated=%s missing=%s path=%s",
            result.get("loaded"),
            result.get("inserted"),
            result.get("updated"),
            result.get("missing"),
            result.get("path"),
        )
        return result
    except Exception as exc:
        logger.warning("Subject taxonomy bootstrap skipped: %s", exc)
        return {
            "path": str(seed_path),
            "loaded": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "missing": True,
            "error": str(exc),
        }
