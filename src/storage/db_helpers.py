import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import find_dotenv, load_dotenv

from src.models.admission import StudyMode


def _load_env_file(env_file: str) -> bool:
    """Load one .env with encoding fallbacks. Returns True on success."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            load_dotenv(env_file, encoding=encoding, override=True)
            return True
        except (UnicodeDecodeError, LookupError):
            continue
    return False


def load_database_env() -> None:
    """Load .env with encoding fallbacks for Windows GBK/GB18030 locales.

    Discovery order:
      1. cwd-based search (``find_dotenv(usecwd=True)``) — dev mode, where
         the project ``.env`` sits at the repo root the user runs from.
      2. caller-file-based search (``find_dotenv()``) — secondary heuristic.
      3. ``~/.uni-agent/.env`` — the canonical location written by the
         install flow for a packaged binary. A user who installed via the
         skill and runs ``adm-agent serve`` from their HOME dir (or anywhere
         that isn't ~/.uni-agent) would otherwise never load their keys.

    cwd/caller discovery wins over the home fallback so dev work with a
    project-local ``.env`` is unaffected.
    """
    env_file = find_dotenv(usecwd=True) or find_dotenv()
    if env_file and _load_env_file(env_file):
        return

    home_env = Path.home() / ".uni-agent" / ".env"
    if home_env.is_file() and _load_env_file(str(home_env)):
        return

    load_dotenv()


def patch_psycopg2_for_gbk() -> None:
    """Patch psycopg2.connect for GBK locale decoding on Windows."""
    try:
        import psycopg2  # pylint: disable=import-outside-toplevel
        from psycopg2 import OperationalError  # pylint: disable=import-outside-toplevel
    except ImportError:
        return

    original_connect = psycopg2.connect

    def _gbk_safe_connect(*args, **kwargs):
        try:
            return original_connect(*args, **kwargs)
        except UnicodeDecodeError as exc:
            raw_bytes: bytes = exc.object  # type: ignore[assignment]
            try:
                decoded = raw_bytes.decode("gbk", errors="replace")
            except Exception:  # pragma: no cover
                decoded = raw_bytes.decode("latin-1", errors="replace")
            raise OperationalError(
                "psycopg2 connection failed (GBK error message re-decoded): "
                f"{decoded}"
            ) from exc

    psycopg2.connect = _gbk_safe_connect  # type: ignore[assignment]


def normalize_text_payload(value: Any) -> Any:
    """Normalize payload values for DB writes."""
    if isinstance(value, dict):
        return {
            normalize_text_payload(k): normalize_text_payload(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [normalize_text_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_text_payload(item) for item in value)
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return value


def catalog_key(program_group_code: Optional[str], name_en: str) -> str:
    if program_group_code and program_group_code.strip():
        return f"group:{program_group_code.strip().lower()}"
    normalized = re.sub(r"[^a-z0-9]+", "-", (name_en or "").lower()).strip("-")
    return f"name:{normalized or 'unnamed'}"


def value_should_apply(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def parse_study_mode(value: Any) -> StudyMode:
    text_value = str(value or "").strip().lower()
    if text_value in {"fulltime", "full_time", "full time", "ft"}:
        return StudyMode.FULL_TIME
    if text_value in {"parttime", "part_time", "part time", "pt"}:
        return StudyMode.PART_TIME
    if text_value in {"hybrid", "mixed", "blended"}:
        return StudyMode.HYBRID
    return StudyMode.UNKNOWN


def parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def extract_requirements_from_extra_metadata(
    extra_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(extra_metadata, dict):
        return []

    out: list[dict[str, Any]] = []
    explicit = extra_metadata.get("requirements")
    if isinstance(explicit, list):
        for idx, item in enumerate(explicit):
            if isinstance(item, dict):
                out.append(
                    {
                        "category": item.get("category", "other"),
                        "subject_name": item.get("subject_name") or item.get("subject"),
                        "framework": item.get("framework"),
                        "minimum_value": item.get("minimum_value") or item.get("score"),
                        "unit": item.get("unit"),
                        "applicant_scope": item.get("applicant_scope", "all"),
                        "requirement_text": item.get("requirement_text") or item.get("text") or "",
                        "evidence_url": item.get("evidence_url"),
                        "sort_order": idx,
                    }
                )

    keywords = (
        "requirement",
        "entry",
        "subject",
        "grade",
        "ielts",
        "toefl",
        "sat",
        "act",
        "gmat",
        "gre",
    )
    idx = len(out)
    for key, value in extra_metadata.items():
        key_str = str(key).strip()
        value_str = str(value).strip()
        if not key_str or not value_str:
            continue
        if not any(keyword in key_str.lower() for keyword in keywords):
            continue
        out.append(
            {
                "category": "academic_subject",
                "subject_name": key_str,
                "requirement_text": value_str,
                "sort_order": idx,
            }
        )
        idx += 1

    return out
