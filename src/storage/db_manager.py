import os
import logging
import re
import json
import hashlib
import threading
from datetime import datetime, timezone
from typing import Any, Optional, Tuple, List, Dict

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy import text
from sqlmodel import create_engine, Session, select, SQLModel, col
from sqlalchemy_utils import database_exists, create_database

from src.core.paths import get_data_dir

from src.models.admission import (
    University,
    Program,
    ProgramCatalog,
    CurrencyCode,
)
from src.models.requirement import (
    SubjectDim,
    ExamDim,
    FrameworkDim,
    RequirementEvidence,
    RequirementVersion,
    ProgramStudyOption,
    ProgramDeadline,
    ProgramRequirement,
    RequirementCategory,
    StudyMode,
)
from src.models.ingestion import IngestionJob, IngestionTask  # noqa: F401
from src.models.taxonomy import SubjectTaxonomy  # noqa: F401
from src.models.scraper_models import ProgramContext
from src.storage.db_helpers import (
    load_database_env,
    patch_psycopg2_for_gbk,
    normalize_text_payload as _normalize_text_payload,
    catalog_key,
    value_should_apply,
    parse_study_mode,
    parse_datetime,
    extract_requirements_from_extra_metadata,
)

load_database_env()

logger = logging.getLogger(__name__)

patch_psycopg2_for_gbk()


def _attach_sqlite_pragmas(engine) -> None:
    """Apply per-connection PRAGMAs for SQLite engines.

    WAL gives concurrent readers while a writer is committing, busy_timeout
    waits instead of failing on transient locks, and foreign_keys turns on
    referential integrity enforcement (off by default in SQLite).
    """
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


class DatabaseManager:
    _instance: Optional["DatabaseManager"] = None
    _lock = threading.Lock()
    initialized: bool

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseManager, cls).__new__(cls)
                    cls._instance.initialized = False
        return cls._instance

    @staticmethod
    def _sanitize_db_url(url: str) -> str:
        """Ensure psycopg2-safe URL and UTF-8 client encoding."""
        try:
            url.encode("ascii")
        except UnicodeEncodeError:
            try:
                raw = url.encode("latin-1")
                for enc in ("utf-8", "gb18030"):
                    try:
                        url = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            except UnicodeEncodeError:
                pass

        if "client_encoding" not in url and url.startswith("postgresql"):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}client_encoding=utf8"
        return url

    @staticmethod
    def _default_sqlite_url() -> str:
        """Resolve the default SQLite URL, ensuring the parent dir exists.

        Dev mode: ``<project>/data/admission.db``.
        Frozen mode: ``~/.uni-agent/admission.db`` (see ``get_data_dir``).
        """
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{data_dir / 'admission.db'}"

    def init_db(self, db_url: Optional[str] = None):
        """Initialize DB engine and sync minimal additive schema drift."""
        if getattr(self, "engine", None):
            return

        if not db_url:
            db_url = os.getenv("DATABASE_URL")
            if not db_url:
                db_url = self._default_sqlite_url()

        db_url = self._sanitize_db_url(db_url)

        # Dialect-specific engine knobs:
        #   • Postgres needs explicit utf8 client_encoding (matches
        #     _sanitize_db_url URL injection)
        #   • SQLite needs no connect_args; PRAGMAs are applied on each
        #     new connection via an event listener below.
        connect_args: dict[str, Any] = {}
        if db_url.startswith("postgresql"):
            connect_args["client_encoding"] = "utf8"
        # `check_same_thread=False` lets SQLAlchemy share a connection
        # across the FastAPI thread pool — safe because the connection
        # is serialized by the pool, not by SQLite's check.
        elif db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self.engine = create_engine(db_url, connect_args=connect_args)

        if self.engine.dialect.name == "sqlite":
            _attach_sqlite_pragmas(self.engine)

        if not database_exists(self.engine.url):
            logger.info("Database does not exist. Creating: %s", self.engine.url)
            create_database(self.engine.url)

        # Keep lightweight self-healing for additive columns.
        SQLModel.metadata.create_all(self.engine)
        self._sync_schema()
        logger.info("Database initialized successfully.")

    def _sync_schema(self) -> None:
        """Forward-only schema sync for missing columns."""
        inspector = sa_inspect(self.engine)
        existing_tables = set(inspector.get_table_names())

        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            db_columns = {col_meta["name"] for col_meta in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in db_columns:
                    continue

                col_type = column.type.compile(dialect=self.engine.dialect)
                nullable = "NULL" if column.nullable else "NOT NULL"
                default_clause = ""
                if column.server_default is not None:
                    default_clause = f"DEFAULT {column.server_default.arg}"
                elif column.default is not None:
                    default_clause = "DEFAULT NULL" if column.nullable else "DEFAULT ''"
                elif column.nullable:
                    default_clause = "DEFAULT NULL"

                ddl = (
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{column.name}" {col_type} {nullable} {default_clause}'
                )

                logger.info("Auto-sync schema: %s", ddl.strip())
                with self.engine.connect() as conn:
                    conn.execute(text(ddl))
                    conn.commit()

    def get_session(self) -> Session:
        if not getattr(self, "engine", None):
            self.init_db()
        return Session(self.engine)

    # ------------------------------------------------------------------
    #  Core upsert/query
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_dim_key(value: Any, prefix: str) -> Optional[str]:
        text = str(value or "").strip().lower()
        if not text:
            return None
        normalized = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
        if not normalized:
            normalized = prefix
        return normalized

    @staticmethod
    def _infer_exam_fields(item: dict[str, Any]) -> dict[str, Optional[str]]:
        text_parts = [
            str(item.get("subject_name") or item.get("subject") or "").strip().lower(),
            str(item.get("framework") or "").strip().lower(),
            str(item.get("requirement_text") or item.get("text") or "").strip().lower(),
        ]
        merged = " ".join(part for part in text_parts if part)

        explicit_exam_name = str(item.get("exam_name") or "").strip()
        if explicit_exam_name:
            code = DatabaseManager._normalize_dim_key(explicit_exam_name, "exam")
            return {
                "exam_code": code,
                "exam_display_name": explicit_exam_name,
                "exam_family": str(item.get("exam_family") or "").strip() or None,
            }

        patterns = (
            ("ielts", "IELTS", "language"),
            ("toefl", "TOEFL", "language"),
            ("sat", "SAT", "standardized"),
            ("act", "ACT", "standardized"),
            ("gre", "GRE", "standardized"),
            ("gmat", "GMAT", "standardized"),
            ("a-level", "A-Level", "curriculum"),
            ("a level", "A-Level", "curriculum"),
            ("ib", "IB", "curriculum"),
            ("ap", "AP", "curriculum"),
        )
        for token, display_name, family in patterns:
            if token in merged:
                return {
                    "exam_code": DatabaseManager._normalize_dim_key(display_name, "exam"),
                    "exam_display_name": display_name,
                    "exam_family": family,
                }

        category = str(item.get("category") or "").strip().lower()
        if category == RequirementCategory.STANDARDIZED_TEST.value:
            fallback_name = str(
                item.get("subject_name")
                or item.get("subject")
                or item.get("framework")
                or "Standardized Test"
            ).strip()
            if fallback_name:
                return {
                    "exam_code": DatabaseManager._normalize_dim_key(fallback_name, "exam"),
                    "exam_display_name": fallback_name,
                    "exam_family": "standardized",
                }

        return {
            "exam_code": None,
            "exam_display_name": None,
            "exam_family": None,
        }

    @staticmethod
    def _normalize_requirement_item(
        item: dict[str, Any],
        sort_order: int,
        default_evidence_url: Optional[str] = None,
    ) -> dict[str, Any]:
        category = str(item.get("category") or "other").strip().lower()
        allowed = {c.value for c in RequirementCategory}
        if category not in allowed:
            category = RequirementCategory.OTHER.value

        requirement_text = str(
            item.get("requirement_text") or item.get("text") or item.get("minimum_value") or ""
        ).strip()

        subject_name = str(item.get("subject_name") or item.get("subject") or "").strip() or None
        framework = str(item.get("framework") or "").strip() or None
        evidence_url = str(item.get("evidence_url") or "").strip() or default_evidence_url
        exam_fields = DatabaseManager._infer_exam_fields(item)

        return {
            "category": RequirementCategory(category),
            "subject_name": subject_name,
            "framework": framework,
            "minimum_value": str(item.get("minimum_value") or item.get("score") or "").strip() or None,
            "unit": str(item.get("unit") or "").strip() or None,
            "applicant_scope": str(item.get("applicant_scope") or "all").strip() or "all",
            "requirement_text": requirement_text,
            "evidence_url": evidence_url,
            "evidence_snippet": (
                str(item.get("evidence_snippet") or item.get("source_snippet") or "").strip() or None
            ),
            "evidence_locator_type": (
                str(item.get("evidence_locator_type") or "").strip() or None
            ),
            "evidence_locator_value": (
                str(item.get("evidence_locator_value") or item.get("evidence_locator") or "").strip() or None
            ),
            "evidence_captured_at": parse_datetime(item.get("evidence_captured_at")),
            "exam_code": exam_fields["exam_code"],
            "exam_display_name": exam_fields["exam_display_name"],
            "exam_family": exam_fields["exam_family"],
            "sort_order": sort_order,
        }

    @staticmethod
    def _signature_requirement_item(item: dict[str, Any]) -> str:
        payload = {
            "category": (
                item.get("category").value
                if isinstance(item.get("category"), RequirementCategory)
                else str(item.get("category") or "").strip().lower()
            ),
            "subject_name": str(item.get("subject_name") or "").strip().lower(),
            "framework": str(item.get("framework") or "").strip().lower(),
            "minimum_value": str(item.get("minimum_value") or "").strip().lower(),
            "unit": str(item.get("unit") or "").strip().lower(),
            "applicant_scope": str(item.get("applicant_scope") or "").strip().lower(),
            "requirement_text": str(item.get("requirement_text") or "").strip().lower(),
            "exam_code": str(item.get("exam_code") or "").strip().lower(),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _requirements_fingerprint(items: list[dict[str, Any]]) -> str:
        signatures = sorted(
            DatabaseManager._signature_requirement_item(item)
            for item in items
            if str(item.get("requirement_text") or "").strip()
        )
        blob = "|".join(signatures)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _diff_requirements(
        old_items: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        old_set = {DatabaseManager._signature_requirement_item(item) for item in old_items}
        new_set = {DatabaseManager._signature_requirement_item(item) for item in new_items}
        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)
        return {
            "old_count": len(old_set),
            "new_count": len(new_set),
            "added_count": len(added),
            "removed_count": len(removed),
            "added": [json.loads(item) for item in added[:20]],
            "removed": [json.loads(item) for item in removed[:20]],
        }

    def _sync_study_option_records(
        self,
        session: Session,
        program_id: int,
        payload: list[dict[str, Any]],
    ) -> None:
        existing = session.exec(
            select(ProgramStudyOption).where(ProgramStudyOption.program_id == program_id)
        ).all()
        existing_by_key: dict[tuple[StudyMode, Optional[int]], ProgramStudyOption] = {}
        for row in existing:
            key = (row.mode, row.duration_months)
            if key in existing_by_key:
                session.delete(row)
                continue
            existing_by_key[key] = row

        payload_by_key: dict[tuple[StudyMode, Optional[int]], dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            mode = parse_study_mode(item.get("mode"))
            duration_raw = item.get("duration_months")
            duration = int(duration_raw) if str(duration_raw or "").isdigit() else None
            dedupe_key = (mode, duration)
            if dedupe_key in payload_by_key:
                continue
            payload_by_key[dedupe_key] = item

        for key, item in payload_by_key.items():
            mode, duration = key
            notes = str(item.get("notes") or item.get("description") or "").strip() or None
            existing_row = existing_by_key.get(key)
            if existing_row is not None:
                existing_row.notes = notes
                existing_row.updated_at = datetime.now(timezone.utc)
                session.add(existing_row)
                continue
            session.add(
                ProgramStudyOption(
                    program_id=program_id,
                    mode=mode,
                    duration_months=duration,
                    notes=notes,
                )
            )

        for key, row in existing_by_key.items():
            if key not in payload_by_key:
                session.delete(row)

    def _sync_deadline_records(
        self,
        session: Session,
        program_id: int,
        payload: list[dict[str, Any]],
    ) -> None:
        def _normalize_deadline_cutoff(raw_value: Any) -> Optional[datetime]:
            parsed = parse_datetime(raw_value)
            if parsed is None:
                return None
            day = parsed.date()
            return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

        existing = session.exec(
            select(ProgramDeadline).where(ProgramDeadline.program_id == program_id)
        ).all()
        existing_by_key: dict[
            tuple[Optional[int], Optional[str], Optional[datetime]],
            ProgramDeadline,
        ] = {}
        for row in existing:
            round_raw = row.round
            round_value = int(round_raw) if str(round_raw or "").isdigit() else None
            description = str(row.description or "").strip() or None
            cutoff_date = _normalize_deadline_cutoff(row.cutoff_date)
            key = (round_value, description, cutoff_date)
            if key in existing_by_key:
                session.delete(row)
                continue
            existing_by_key[key] = row

        payload_by_key: dict[
            tuple[Optional[int], Optional[str], Optional[datetime]],
            dict[str, Any],
        ] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            round_raw = item.get("round")
            round_value = int(round_raw) if str(round_raw or "").isdigit() else None
            description = str(item.get("description") or "").strip() or None
            cutoff_date = _normalize_deadline_cutoff(item.get("cutoff_date"))
            dedupe_key = (round_value, description, cutoff_date)
            if dedupe_key in payload_by_key:
                continue
            payload_by_key[dedupe_key] = item

        keys_to_delete = [key for key in existing_by_key if key not in payload_by_key]
        for key in keys_to_delete:
            session.delete(existing_by_key[key])
        if keys_to_delete:
            # Flush deletions before inserts to avoid transient unique-key collisions.
            session.flush()

        for key in payload_by_key:
            round_value, description, cutoff_date = key
            existing_row = existing_by_key.get(key)
            if existing_row is not None and key not in keys_to_delete:
                existing_row.round = round_value
                existing_row.description = description
                existing_row.cutoff_date = cutoff_date
                existing_row.updated_at = datetime.now(timezone.utc)
                session.add(existing_row)
                continue
            session.add(
                ProgramDeadline(
                    program_id=program_id,
                    round=round_value,
                    description=description,
                    cutoff_date=cutoff_date,
                )
            )

    def _upsert_subject_dim(self, session: Session, subject_name: Optional[str]) -> Optional[SubjectDim]:
        normalized_name = self._normalize_dim_key(subject_name, "subject")
        if not normalized_name:
            return None
        canonical_name = str(subject_name or "").strip()

        existing = session.exec(
            select(SubjectDim).where(SubjectDim.normalized_name == normalized_name)
        ).first()
        if existing:
            if canonical_name and existing.canonical_name != canonical_name:
                aliases = list(existing.aliases or [])
                if existing.canonical_name and existing.canonical_name not in aliases:
                    aliases.append(existing.canonical_name)
                if canonical_name not in aliases and canonical_name != existing.canonical_name:
                    aliases.append(canonical_name)
                existing.aliases = aliases
                existing.canonical_name = canonical_name
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.flush()
            return existing

        created = SubjectDim(
            normalized_name=normalized_name,
            canonical_name=canonical_name or normalized_name,
            aliases=[],
        )
        session.add(created)
        session.flush()
        return created

    def _upsert_framework_dim(self, session: Session, framework: Optional[str]) -> Optional[FrameworkDim]:
        code = self._normalize_dim_key(framework, "framework")
        if not code:
            return None
        display_name = str(framework or "").strip() or code

        existing = session.exec(
            select(FrameworkDim).where(FrameworkDim.code == code)
        ).first()
        if existing:
            if display_name and existing.display_name != display_name:
                existing.display_name = display_name
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.flush()
            return existing

        created = FrameworkDim(
            code=code,
            display_name=display_name,
            region=None,
        )
        session.add(created)
        session.flush()
        return created

    def _upsert_exam_dim(
        self,
        session: Session,
        exam_code: Optional[str],
        exam_display_name: Optional[str],
        exam_family: Optional[str],
    ) -> Optional[ExamDim]:
        if not exam_code:
            return None
        display_name = exam_display_name or exam_code

        existing = session.exec(select(ExamDim).where(ExamDim.code == exam_code)).first()
        if existing:
            changed = False
            if display_name and existing.display_name != display_name:
                existing.display_name = display_name
                changed = True
            if exam_family and existing.family != exam_family:
                existing.family = exam_family
                changed = True
            if changed:
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.flush()
            return existing

        created = ExamDim(
            code=exam_code,
            display_name=display_name,
            family=exam_family,
        )
        session.add(created)
        session.flush()
        return created

    def _upsert_requirement_evidence(
        self,
        session: Session,
        normalized: dict[str, Any],
        default_source_url: Optional[str],
    ) -> Optional[RequirementEvidence]:
        source_url = normalized.get("evidence_url") or default_source_url
        page_snippet = normalized.get("evidence_snippet") or normalized.get("requirement_text")
        page_snippet = (str(page_snippet).strip() or None) if page_snippet else None
        if page_snippet:
            page_snippet = page_snippet[:1000]

        locator_type = normalized.get("evidence_locator_type") or ("url" if source_url else "text")
        locator_value = (
            normalized.get("evidence_locator_value")
            or source_url
            or normalized.get("subject_name")
            or normalized.get("framework")
        )
        locator_value = str(locator_value).strip() if locator_value else None

        if not source_url and not page_snippet:
            return None

        content_key = "|".join(
            [
                str(source_url or "").strip(),
                str(page_snippet or "").strip(),
                str(locator_type or "").strip(),
                str(locator_value or "").strip(),
            ]
        )
        content_hash = hashlib.sha256(content_key.encode("utf-8")).hexdigest()

        existing = session.exec(
            select(RequirementEvidence).where(RequirementEvidence.content_hash == content_hash)
        ).first()
        if existing:
            return existing

        captured_at = normalized.get("evidence_captured_at") or datetime.now(timezone.utc)
        created = RequirementEvidence(
            source_url=source_url,
            page_title=None,
            page_snippet=page_snippet,
            locator_type=locator_type,
            locator_value=locator_value,
            captured_at=captured_at,
            crawled_at=datetime.now(timezone.utc),
            content_hash=content_hash,
        )
        session.add(created)
        session.flush()
        return created

    def _get_latest_requirement_version(
        self,
        session: Session,
        program_id: int,
    ) -> Optional[RequirementVersion]:
        return session.exec(
            select(RequirementVersion)
            .where(RequirementVersion.program_id == program_id)
            .order_by(col(RequirementVersion.version_no).desc())
        ).first()

    def _list_version_requirements(
        self,
        session: Session,
        version_id: int,
    ) -> list[dict[str, Any]]:
        rows = session.exec(
            select(ProgramRequirement, ExamDim)
            .join(ExamDim, ExamDim.id == ProgramRequirement.exam_dim_id, isouter=True)
            .where(ProgramRequirement.version_id == version_id)
            .order_by(col(ProgramRequirement.sort_order), col(ProgramRequirement.id))
        ).all()

        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, tuple) and len(row) == 2:
                requirement, exam_dim = row
            else:
                try:
                    requirement = row[0]
                    exam_dim = row[1]
                except Exception:
                    requirement = row
                    exam_dim = None
            out.append(
                {
                    "category": requirement.category,
                    "subject_name": requirement.subject_name,
                    "framework": requirement.framework,
                    "minimum_value": requirement.minimum_value,
                    "unit": requirement.unit,
                    "applicant_scope": requirement.applicant_scope,
                    "requirement_text": requirement.requirement_text,
                    "exam_code": exam_dim.code if exam_dim else None,
                }
            )
        return out

    def _sync_requirement_records(
        self,
        session: Session,
        program_id: int,
        payload: list[dict[str, Any]],
        source_url: Optional[str] = None,
    ) -> Optional[RequirementVersion]:
        normalized_payload: list[dict[str, Any]] = []
        for idx, item in enumerate(payload):
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_requirement_item(item, idx, source_url)
            if not normalized["requirement_text"]:
                continue
            normalized_payload.append(normalized)

        latest_version = self._get_latest_requirement_version(session, program_id)
        if not normalized_payload and latest_version is None:
            return None

        old_items = (
            self._list_version_requirements(session, latest_version.id)
            if latest_version and latest_version.id is not None
            else []
        )
        old_fingerprint = self._requirements_fingerprint(old_items)
        new_fingerprint = self._requirements_fingerprint(normalized_payload)
        if latest_version and old_fingerprint == new_fingerprint:
            return latest_version

        now = datetime.now(timezone.utc)
        if latest_version and latest_version.valid_to is None:
            latest_version.valid_to = now
            session.add(latest_version)

        diff_payload = self._diff_requirements(old_items, normalized_payload)
        added_count = diff_payload.get("added_count", 0)
        removed_count = diff_payload.get("removed_count", 0)
        if latest_version is None:
            change_summary = "Initial requirement snapshot"
            new_version_no = 1
        else:
            change_summary = f"Requirements updated (+{added_count}/-{removed_count})"
            new_version_no = int(latest_version.version_no or 0) + 1

        version = RequirementVersion(
            program_id=program_id,
            version_no=new_version_no,
            effective_at=now,
            valid_from=now,
            valid_to=None,
            change_summary=change_summary,
            diff_payload=diff_payload,
        )
        session.add(version)
        session.flush()

        for normalized in normalized_payload:
            subject_dim = self._upsert_subject_dim(session, normalized.get("subject_name"))
            framework_dim = self._upsert_framework_dim(session, normalized.get("framework"))
            exam_dim = self._upsert_exam_dim(
                session,
                normalized.get("exam_code"),
                normalized.get("exam_display_name"),
                normalized.get("exam_family"),
            )
            evidence = self._upsert_requirement_evidence(session, normalized, source_url)

            session.add(
                ProgramRequirement(
                    program_id=program_id,
                    version_id=version.id,
                    category=normalized["category"],
                    subject_name=normalized.get("subject_name"),
                    framework=normalized.get("framework"),
                    minimum_value=normalized.get("minimum_value"),
                    unit=normalized.get("unit"),
                    applicant_scope=normalized.get("applicant_scope"),
                    requirement_text=normalized.get("requirement_text") or "",
                    evidence_url=normalized.get("evidence_url"),
                    sort_order=normalized.get("sort_order") or 0,
                    subject_dim_id=subject_dim.id if subject_dim else None,
                    exam_dim_id=exam_dim.id if exam_dim else None,
                    framework_dim_id=framework_dim.id if framework_dim else None,
                    evidence_id=evidence.id if evidence else None,
                )
            )
        return version

    def get_university_by_slug(self, slug: str) -> Optional[University]:
        with self.get_session() as session:
            return session.exec(select(University).where(University.slug == slug)).first()

    def delete_program_snapshot(self, program_id: int) -> bool:
        """Delete one year-specific program snapshot by ID."""
        with self.get_session() as session:
            program = session.get(Program, program_id)
            if not program:
                return False

            if program.id is None:
                return False

            catalog_id = program.program_catalog_id

            requirement_rows = session.exec(
                select(ProgramRequirement).where(ProgramRequirement.program_id == program.id)
            ).all()
            for row in requirement_rows:
                session.delete(row)

            requirement_versions = session.exec(
                select(RequirementVersion).where(RequirementVersion.program_id == program.id)
            ).all()
            for row in requirement_versions:
                session.delete(row)

            study_option_rows = session.exec(
                select(ProgramStudyOption).where(ProgramStudyOption.program_id == program.id)
            ).all()
            for row in study_option_rows:
                session.delete(row)

            deadline_rows = session.exec(
                select(ProgramDeadline).where(ProgramDeadline.program_id == program.id)
            ).all()
            for row in deadline_rows:
                session.delete(row)

            session.delete(program)
            session.flush()

            if catalog_id is not None:
                has_sibling = session.exec(
                    select(Program.id).where(
                        Program.program_catalog_id == catalog_id,
                        Program.id != program.id,
                    )
                ).first()
                if has_sibling is None:
                    catalog = session.get(ProgramCatalog, catalog_id)
                    if catalog is not None:
                        session.delete(catalog)

            session.commit()
            return True

    def patch_program_snapshot(
        self,
        program_id: int,
        patch_payload: dict[str, Any],
    ) -> Optional[Program]:
        """Patch one year-specific program snapshot by ID."""
        with self.get_session() as session:
            program = session.get(Program, program_id)
            if not program:
                return None

            if program.id is None:
                raise ValueError("Program ID is missing.")

            now = datetime.now(timezone.utc)

            for field_name in (
                "name_en",
                "name_zh",
                "faculty",
                "program_group_code",
            ):
                if field_name in patch_payload:
                    setattr(program, field_name, patch_payload[field_name])

            if "tuition_amount" in patch_payload:
                program.tuition_amount = patch_payload["tuition_amount"]

            if "currency" in patch_payload:
                currency = patch_payload["currency"]
                if not value_should_apply(currency):
                    program.currency = None
                else:
                    normalized_currency = str(currency).strip().upper()
                    try:
                        program.currency = CurrencyCode(normalized_currency)
                    except ValueError as exc:
                        raise ValueError(
                            f"Unsupported currency code: {currency}"
                        ) from exc

            if "source_url" in patch_payload:
                source_url_raw = patch_payload["source_url"]
                source_url = str(source_url_raw).strip() if source_url_raw else None
                program.source_url = source_url
                extra_metadata = dict(program.extra_metadata or {})
                if source_url:
                    extra_metadata["source_url"] = source_url
                else:
                    extra_metadata.pop("source_url", None)
                program.extra_metadata = extra_metadata

            if "study_options" in patch_payload:
                study_options = patch_payload["study_options"] or []
                if not isinstance(study_options, list):
                    raise ValueError("study_options must be a list.")
                normalized_study_options = [item for item in study_options if isinstance(item, dict)]
                self._sync_study_option_records(
                    session,
                    program.id,
                    normalized_study_options,
                )
                program.study_options = normalized_study_options

            if "deadlines" in patch_payload:
                deadlines = patch_payload["deadlines"] or []
                if not isinstance(deadlines, list):
                    raise ValueError("deadlines must be a list.")
                normalized_deadlines = [item for item in deadlines if isinstance(item, dict)]
                self._sync_deadline_records(
                    session,
                    program.id,
                    normalized_deadlines,
                )
                program.deadlines = normalized_deadlines

            if "requirements" in patch_payload:
                requirements = patch_payload["requirements"] or []
                if not isinstance(requirements, list):
                    raise ValueError("requirements must be a list.")
                normalized_requirements = [item for item in requirements if isinstance(item, dict)]
                source_url = str(program.source_url or "").strip() or None
                self._sync_requirement_records(
                    session,
                    program.id,
                    normalized_requirements,
                    source_url=source_url,
                )

            program.updated_at = now
            session.add(program)

            if program.program_catalog_id is not None:
                catalog = session.get(ProgramCatalog, program.program_catalog_id)
                if catalog is not None:
                    if (
                        "program_group_code" in patch_payload
                        and value_should_apply(program.program_group_code)
                    ):
                        catalog.program_group_code = program.program_group_code
                    if "faculty" in patch_payload and value_should_apply(program.faculty):
                        catalog.faculty = program.faculty
                    if "name_en" in patch_payload and value_should_apply(program.name_en):
                        catalog.canonical_name_en = program.name_en
                    if "name_zh" in patch_payload and value_should_apply(program.name_zh):
                        catalog.canonical_name_zh = program.name_zh
                    catalog.updated_at = now
                    session.add(catalog)

            session.commit()
            session.refresh(program)
            return program

    def upsert_program(
        self,
        program_data: dict,
        univ_slug: str,
        *,
        enable_auto_translation: bool = True,
    ) -> Tuple[Program, bool]:
        """Upsert a year-specific program snapshot and normalized child records."""
        with self.get_session() as session:
            # 1) Ensure university exists.
            univ = session.exec(select(University).where(University.slug == univ_slug)).first()
            if not univ:
                univ = University(name=univ_slug, slug=univ_slug)
                session.add(univ)
                session.commit()
                session.refresh(univ)
            else:
                univ.updated_at = datetime.now(timezone.utc)
                session.add(univ)
                session.commit()
                session.refresh(univ)

            # 2) Name sanity / translation fallback.
            name_en = program_data.get("name_en")
            name_zh = program_data.get("name_zh")
            if enable_auto_translation and ((not name_en and name_zh) or (not name_zh and name_en)):
                try:
                    from src.agents.translation_agent import TranslationAgent

                    translator = TranslationAgent()
                    if not name_en and name_zh:
                        name_en = translator.translate_program_name(name_zh, to_lang="en")
                        program_data["name_en"] = name_en
                    elif not name_zh and name_en:
                        name_zh = translator.translate_program_name(name_en, to_lang="zh")
                        program_data["name_zh"] = name_zh
                except Exception as exc:
                    logger.error("Auto-translation failed: %s", exc)

            if not program_data.get("name_en") or not program_data.get("academic_year"):
                raise ValueError("Program data must contain 'name_en' and 'academic_year'")

            full_data = _normalize_text_payload(program_data.copy())
            full_data.setdefault("extra_metadata", {})
            if not full_data.get("source_url"):
                source_from_meta = full_data["extra_metadata"].get("source_url")
                if source_from_meta:
                    full_data["source_url"] = str(source_from_meta)

            # 3) Resolve catalog identity. Prefer source_url over name so two
            #    courses with the same mis-extracted name don't collapse.
            group_code = full_data.get("program_group_code")
            resolved_catalog_key = catalog_key(
                group_code,
                full_data["name_en"],
                source_url=full_data.get("source_url"),
            )
            catalog = session.exec(
                select(ProgramCatalog).where(
                    ProgramCatalog.university_id == univ.id,
                    ProgramCatalog.catalog_key == resolved_catalog_key,
                )
            ).first()
            # Legacy name-merge: only when the identity is genuinely
            # name-based (no group code AND no source URL). When we have a
            # URL key, matching by name would re-collapse two distinct
            # courses that share a mis-extracted name — the very bug the
            # URL key exists to prevent.
            if (
                not catalog
                and not value_should_apply(group_code)
                and resolved_catalog_key.startswith("name:")
            ):
                existing_same_name = session.exec(
                    select(Program).where(
                        Program.university_id == univ.id,
                        Program.academic_year == full_data["academic_year"],
                        Program.name_en == full_data["name_en"],
                    )
                ).first()
                existing_catalog_id = (
                    getattr(existing_same_name, "program_catalog_id", None)
                    if existing_same_name is not None
                    else None
                )
                if existing_catalog_id is not None:
                    catalog = session.get(ProgramCatalog, existing_catalog_id)
            if not catalog:
                catalog = ProgramCatalog(
                    university_id=univ.id,
                    catalog_key=resolved_catalog_key,
                    program_group_code=group_code,
                    canonical_name_en=full_data.get("name_en"),
                    canonical_name_zh=full_data.get("name_zh"),
                    faculty=full_data.get("faculty"),
                )
                session.add(catalog)
                session.commit()
                session.refresh(catalog)
            else:
                if value_should_apply(group_code):
                    catalog.program_group_code = group_code
                if value_should_apply(full_data.get("name_en")):
                    catalog.canonical_name_en = full_data.get("name_en")
                if value_should_apply(full_data.get("name_zh")):
                    catalog.canonical_name_zh = full_data.get("name_zh")
                if value_should_apply(full_data.get("faculty")):
                    catalog.faculty = full_data.get("faculty")
                catalog.updated_at = datetime.now(timezone.utc)
                session.add(catalog)
                session.commit()
                session.refresh(catalog)

            # 4) Upsert year-version record.
            existing = session.exec(
                select(Program).where(
                    Program.program_catalog_id == catalog.id,
                    Program.academic_year == full_data["academic_year"],
                )
            ).first()

            created = existing is None
            program = existing or Program(
                university_id=univ.id,
                program_catalog_id=catalog.id,
                academic_year=full_data["academic_year"],
                name_en=full_data["name_en"],
            )

            program.university_id = univ.id
            program.program_catalog_id = catalog.id

            for field_name in (
                "name_en",
                "name_zh",
                "program_group_code",
                "faculty",
                "is_active",
                "is_discontinued",
                "tuition_amount",
                "currency",
                "study_options",
                "deadlines",
                "extra_metadata",
                "source_url",
            ):
                if field_name not in full_data:
                    continue
                value = full_data[field_name]
                if field_name == "currency" and isinstance(value, str):
                    try:
                        value = CurrencyCode(value)
                    except ValueError:
                        value = None
                if value_should_apply(value):
                    setattr(program, field_name, value)
                elif getattr(program, field_name, None) is None:
                    setattr(program, field_name, value)

            program.updated_at = datetime.now(timezone.utc)
            session.add(program)
            session.commit()
            session.refresh(program)

            # 5) Sync normalized child records only when fresh payload supplied.
            if "study_options" in full_data:
                self._sync_study_option_records(
                    session, program.id, full_data.get("study_options") or []
                )
            if "deadlines" in full_data:
                self._sync_deadline_records(
                    session, program.id, full_data.get("deadlines") or []
                )

            if "requirements" in full_data:
                requirement_payload = full_data.get("requirements") or []
                self._sync_requirement_records(
                    session,
                    program.id,
                    requirement_payload,
                    source_url=full_data.get("source_url"),
                )
            else:
                extra_requirements = extract_requirements_from_extra_metadata(
                    full_data.get("extra_metadata") or {}
                )
                if extra_requirements:
                    self._sync_requirement_records(
                        session,
                        program.id,
                        extra_requirements,
                        source_url=full_data.get("source_url"),
                    )

            session.commit()
            session.refresh(program)
            return program, created

    def get_program_history(self, program_group_code: str) -> List[Program]:
        with self.get_session() as session:
            stmt = (
                select(Program)
                .join(ProgramCatalog, ProgramCatalog.id == Program.program_catalog_id)
                .where(ProgramCatalog.program_group_code == program_group_code)
                .order_by(col(Program.academic_year))
            )
            return list(session.exec(stmt).all())

    def get_program_group_map(self, university_id: int) -> Dict[str, str]:
        with self.get_session() as session:
            stmt = (
                select(Program, ProgramCatalog)
                .join(ProgramCatalog, ProgramCatalog.id == Program.program_catalog_id)
                .where(
                    ProgramCatalog.university_id == university_id,
                    ProgramCatalog.program_group_code.is_not(None),  # type: ignore
                )
                .order_by(col(Program.academic_year).desc())
            )
            rows = session.exec(stmt).all()

            out: Dict[str, str] = {}
            seen_catalog: set[int] = set()
            for row in rows:
                if isinstance(row, tuple) and len(row) == 2:
                    program, catalog = row
                    catalog_id = catalog.id
                    group_code = catalog.program_group_code
                else:
                    program = row
                    catalog_id = getattr(program, "program_catalog_id", None)
                    group_code = getattr(program, "program_group_code", None)

                if catalog_id in seen_catalog:
                    continue
                if catalog_id is not None:
                    seen_catalog.add(catalog_id)
                if program.name_en and group_code:
                    out[program.name_en] = group_code
            return out

    def get_program_contexts(self, university_id: int) -> List[ProgramContext]:
        with self.get_session() as session:
            stmt = (
                select(Program, ProgramCatalog)
                .join(ProgramCatalog, ProgramCatalog.id == Program.program_catalog_id)
                .where(
                    ProgramCatalog.university_id == university_id,
                    ProgramCatalog.program_group_code.is_not(None),  # type: ignore
                )
                .order_by(col(Program.academic_year).desc())
            )
            rows = session.exec(stmt).all()

            contexts: List[ProgramContext] = []
            seen: set[tuple[str, str]] = set()
            for row in rows:
                if isinstance(row, tuple) and len(row) == 2:
                    program, catalog = row
                    group_code = catalog.program_group_code
                else:
                    program = row
                    group_code = getattr(program, "program_group_code", None)

                if not program.name_en or not group_code:
                    continue
                key = (program.name_en, group_code)
                if key in seen:
                    continue
                seen.add(key)
                contexts.append(
                    ProgramContext(
                        name_en=program.name_en,
                        program_group_code=group_code,
                        faculty=program.faculty,
                        tuition_amount=float(program.tuition_amount) if program.tuition_amount else None,
                        currency=program.currency.value if program.currency else None,
                    )
                )
            return contexts

    # ------------------------------------------------------------------
    #  Quarantine — extraction results that failed the quality gate
    # ------------------------------------------------------------------

    def upsert_quarantine(
        self,
        *,
        university_slug: str,
        program_data: dict,
        reason,
        signals: dict,
    ):
        """Record a quality-gate rejection.

        Delegates to :class:`QuarantineRepo`; opens and closes a session
        so callers don't have to manage one.
        """
        from src.services.quality_gate import QuarantineReason  # noqa: F401
        from src.storage.quarantine_repo import QuarantineRepo

        with self.get_session() as session:
            repo = QuarantineRepo(session)
            return repo.record(
                university_slug=university_slug,
                program_data=program_data,
                reason=reason,
                signals=signals,
            )

    def list_quarantine(
        self,
        *,
        university_slug: Optional[str] = None,
        year: Optional[int] = None,
    ):
        """Return quarantine entries filtered by university/year."""
        from src.storage.quarantine_repo import QuarantineRepo

        with self.get_session() as session:
            repo = QuarantineRepo(session)
            return repo.list_for(university_slug=university_slug, year=year)

    def clear_quarantine(
        self,
        *,
        university_slug: Optional[str] = None,
        source_url: Optional[str] = None,
        reason=None,
    ) -> int:
        """Delete quarantine rows matching filters; returns the count.

        At least one filter is required — full-table deletion is not
        exposed via this entry point.
        """
        from src.storage.quarantine_repo import QuarantineRepo

        with self.get_session() as session:
            repo = QuarantineRepo(session)
            return repo.clear(
                university_slug=university_slug,
                source_url=source_url,
                reason=reason,
            )

    # ------------------------------------------------------------------
    #  Extraction audit — index → detail funnel tracking
    # ------------------------------------------------------------------

    def record_extraction_audit(
        self,
        *,
        university_slug: str,
        academic_year: int,
        index_url: str,
        raw_link_count: int,
        llm_filtered_count: int,
        candidate_count: int,
        extracted_count: int,
        quarantined_count: int,
        recovered_count: int = 0,
        job_uid: Optional[str] = None,
        dropped_links: Optional[list] = None,
        pagination_stop_reason: Optional[str] = None,
    ):
        """Persist one index→detail funnel record (with optional dropped links
        and pagination stop reason)."""
        from src.storage.audit_repo import ExtractionAuditRepo

        with self.get_session() as session:
            repo = ExtractionAuditRepo(session)
            return repo.record(
                university_slug=university_slug,
                academic_year=academic_year,
                index_url=index_url,
                raw_link_count=raw_link_count,
                llm_filtered_count=llm_filtered_count,
                candidate_count=candidate_count,
                extracted_count=extracted_count,
                quarantined_count=quarantined_count,
                recovered_count=recovered_count,
                job_uid=job_uid,
                dropped_links=dropped_links,
                pagination_stop_reason=pagination_stop_reason,
            )

    def list_audit_dropped_links(self, *, audit_id: int):
        """Return per-link dropped records for one audit row."""
        from src.storage.audit_repo import ExtractionAuditRepo

        with self.get_session() as session:
            repo = ExtractionAuditRepo(session)
            return repo.list_dropped_links(audit_id=audit_id)

    # ------------------------------------------------------------------
    #  Unified diagnostics cleanup — wipes quarantine + audit (+ links)
    #  for one university, optionally scoped to a single academic year.
    # ------------------------------------------------------------------

    def clear_diagnostics(
        self,
        *,
        university_slug: str,
        year: Optional[int] = None,
    ) -> dict:
        """Clear all diagnostic records (quarantine + audit + audit_link)
        for one university. Optional year filter scopes the delete.

        Returns a structured count: ``{"quarantine_deleted": N,
        "audits_deleted": M, "links_deleted": L}``.
        """
        if not university_slug:
            raise ValueError("clear_diagnostics requires a non-empty university_slug")

        from src.storage.audit_repo import ExtractionAuditRepo
        from src.storage.quarantine_repo import QuarantineRepo

        with self.get_session() as session:
            q_repo = QuarantineRepo(session)
            quarantine_deleted = q_repo.clear(
                university_slug=university_slug,
                year=year,
            )
            a_repo = ExtractionAuditRepo(session)
            audit_result = a_repo.clear_for_university(
                university_slug=university_slug,
                year=year,
            )
            return {
                "quarantine_deleted": quarantine_deleted,
                "audits_deleted": audit_result["audits_deleted"],
                "links_deleted": audit_result["links_deleted"],
            }

    def list_extraction_audit(
        self,
        *,
        university_slug: Optional[str] = None,
        year: Optional[int] = None,
        limit: Optional[int] = None,
    ):
        """List funnel records (newest first) filtered by university/year."""
        from src.storage.audit_repo import ExtractionAuditRepo

        with self.get_session() as session:
            repo = ExtractionAuditRepo(session)
            return repo.list_for(
                university_slug=university_slug, year=year, limit=limit
            )
