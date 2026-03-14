"""Typed models for low-confidence onhold review workflow."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OnholdItem(BaseModel):
    """One low-confidence candidate deferred for user confirmation."""

    index: int = Field(ge=1)
    item_id: str
    source_url: str
    program_name_candidate: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    hold_reason: str


class OnholdReviewSummary(BaseModel):
    """Aggregated onhold summary returned by runtime before user selection."""

    onhold_count: int = 0
    discarded_count: int = 0
    onhold_items: list[OnholdItem] = Field(default_factory=list)


class OnholdApplySummary(BaseModel):
    """Summary after applying selected onhold indices."""

    total_onhold: int
    selected_count: int
    discarded_count: int
    invalid_indices: list[int] = Field(default_factory=list)
    applied_items: list[OnholdItem] = Field(default_factory=list)
    discarded_items: list[OnholdItem] = Field(default_factory=list)


def build_onhold_items(raw_items: list[dict[str, Any]]) -> list[OnholdItem]:
    """Create stable indexed onhold items sorted by confidence descending."""
    ranked = sorted(
        list(raw_items or []),
        key=lambda row: float((row or {}).get("confidence") or 0.0),
        reverse=True,
    )

    output: list[OnholdItem] = []
    for i, row in enumerate(ranked, start=1):
        item = dict(row or {})
        output.append(
            OnholdItem(
                index=i,
                item_id=str(item.get("item_id") or f"hold-{i}"),
                source_url=str(item.get("url") or item.get("source_url") or ""),
                program_name_candidate=(
                    str(item.get("program_name_candidate")).strip()
                    if item.get("program_name_candidate") is not None
                    else None
                ),
                confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
                hold_reason=str(item.get("hold_reason") or "low_confidence"),
            )
        )
    return output
