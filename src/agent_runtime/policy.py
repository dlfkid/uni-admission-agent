"""Policy profile precedence merge and normalization for agent runtime."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PolicyProfile(BaseModel):
    """Effective policy profile for one agent request."""

    auto_run_max_candidates: int = Field(default=10, ge=1, le=200)
    taxonomy_auto_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    taxonomy_keep_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    prefer_browser_provider: str = Field(default="auto")
    require_manual_review_when_low_confidence: bool = Field(default=True)
    llm_fallback_enabled: bool = Field(default=True)
    batch_size: int = Field(default=4, ge=1, le=50)
    detail_concurrency: int = Field(default=4, ge=1, le=20)


class PolicyMergeResult(BaseModel):
    """Merged policy plus normalization warnings."""

    profile: PolicyProfile
    warnings: list[str] = Field(default_factory=list)


_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _coerce_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    default: int,
    warnings: list[str],
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        warnings.append(f"{field_name}: invalid value {value!r}; using default {default}")
        return default

    if parsed < minimum:
        warnings.append(f"{field_name}: clamped from {parsed} to {minimum}")
        return minimum
    if parsed > maximum:
        warnings.append(f"{field_name}: clamped from {parsed} to {maximum}")
        return maximum
    return parsed


def _coerce_float(
    value: Any,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    default: float,
    warnings: list[str],
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        warnings.append(f"{field_name}: invalid value {value!r}; using default {default}")
        return default

    if parsed < minimum:
        warnings.append(f"{field_name}: clamped from {parsed} to {minimum}")
        return minimum
    if parsed > maximum:
        warnings.append(f"{field_name}: clamped from {parsed} to {maximum}")
        return maximum
    return parsed


def _coerce_bool(value: Any, *, field_name: str, default: bool, warnings: list[str]) -> bool:
    if isinstance(value, bool):
        return value

    normalized = str(value or "").strip().lower()
    if normalized in _BOOL_TRUE:
        return True
    if normalized in _BOOL_FALSE:
        return False

    warnings.append(f"{field_name}: invalid value {value!r}; using default {default}")
    return default


def _normalize_policy(raw_policy: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    defaults = PolicyProfile().model_dump(mode="json")
    normalized = dict(defaults)

    for key, value in raw_policy.items():
        if key not in defaults:
            warnings.append(f"{key}: unknown policy key ignored")
            continue

        if key == "auto_run_max_candidates":
            normalized[key] = _coerce_int(
                value,
                field_name=key,
                minimum=1,
                maximum=200,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "taxonomy_auto_threshold":
            normalized[key] = _coerce_float(
                value,
                field_name=key,
                minimum=0.0,
                maximum=1.0,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "taxonomy_keep_threshold":
            normalized[key] = _coerce_float(
                value,
                field_name=key,
                minimum=0.0,
                maximum=1.0,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "prefer_browser_provider":
            provider = str(value or "").strip().lower()
            if provider not in {"auto", "server", "client"}:
                warnings.append(
                    "prefer_browser_provider: invalid value "
                    f"{value!r}; using default {defaults[key]}"
                )
                provider = str(defaults[key])
            normalized[key] = provider
            continue
        if key == "require_manual_review_when_low_confidence":
            normalized[key] = _coerce_bool(
                value,
                field_name=key,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "llm_fallback_enabled":
            normalized[key] = _coerce_bool(
                value,
                field_name=key,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "batch_size":
            normalized[key] = _coerce_int(
                value,
                field_name=key,
                minimum=1,
                maximum=50,
                default=defaults[key],
                warnings=warnings,
            )
            continue
        if key == "detail_concurrency":
            normalized[key] = _coerce_int(
                value,
                field_name=key,
                minimum=1,
                maximum=20,
                default=defaults[key],
                warnings=warnings,
            )
            continue

    if normalized["taxonomy_keep_threshold"] > normalized["taxonomy_auto_threshold"]:
        original_value = normalized["taxonomy_keep_threshold"]
        normalized["taxonomy_keep_threshold"] = normalized["taxonomy_auto_threshold"]
        warnings.append(
            "taxonomy_keep_threshold: clamped from "
            f"{original_value} to {normalized['taxonomy_auto_threshold']} (must be <= taxonomy_auto_threshold)"
        )

    return normalized, warnings


def merge_policy(
    *,
    request_overrides: dict[str, Any] | None,
    client_policy: dict[str, Any] | None,
    server_defaults: dict[str, Any] | None,
) -> PolicyMergeResult:
    """Merge policies with precedence request > client > server and normalize."""
    merged: dict[str, Any] = {}
    merged.update(dict(server_defaults or {}))
    merged.update(dict(client_policy or {}))
    merged.update(dict(request_overrides or {}))

    normalized, warnings = _normalize_policy(merged)
    profile = PolicyProfile.model_validate(normalized)
    return PolicyMergeResult(profile=profile, warnings=warnings)
