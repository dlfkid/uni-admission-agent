from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import JSON, UniqueConstraint, Column, Enum as SqlEnum, Index, Numeric
from sqlmodel import SQLModel, Field, Relationship

from src.models.admission import StudyMode, TuitionBasis, TuitionScope, CurrencyCode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RequirementCategory(str, Enum):
    ACADEMIC_SUBJECT = "academic_subject"
    LANGUAGE = "language"
    STANDARDIZED_TEST = "standardized_test"
    PORTFOLIO = "portfolio"
    EXPERIENCE = "experience"
    OTHER = "other"


def _enum_values(enum_cls: type[Enum]) -> List[str]:
    return [member.value for member in enum_cls]


STUDY_MODE_ENUM = SqlEnum(
    StudyMode,
    name="studymode",
    values_callable=_enum_values,
)
REQUIREMENT_CATEGORY_ENUM = SqlEnum(
    RequirementCategory,
    name="requirementcategory",
    values_callable=_enum_values,
)
TUITION_BASIS_ENUM = SqlEnum(TuitionBasis, name="tuitionbasis", values_callable=_enum_values)
TUITION_SCOPE_ENUM = SqlEnum(TuitionScope, name="tuitionscope", values_callable=_enum_values)
# Postgres never had a native `currencycode` enum type (Program.currency has
# always been a plain sa.String(16) column, per the initial migration); keep
# it that way here too — native_enum=False stores it as VARCHAR(16) while
# reads still come back as CurrencyCode members.
CURRENCY_CODE_ENUM = SqlEnum(
    CurrencyCode, name="currencycode", native_enum=False, length=16, values_callable=_enum_values,
)


class SubjectDim(SQLModel, table=True):
    __tablename__ = "subject_dim"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_subject_dim_normalized_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    normalized_name: str = Field(index=True)
    canonical_name: str = Field(index=True)
    aliases: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_utc_now)

    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="subject_dim")


class ExamDim(SQLModel, table=True):
    __tablename__ = "exam_dim"
    __table_args__ = (
        UniqueConstraint("code", name="uq_exam_dim_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    display_name: str = Field(index=True)
    family: Optional[str] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)

    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="exam_dim")


class FrameworkDim(SQLModel, table=True):
    __tablename__ = "framework_dim"
    __table_args__ = (
        UniqueConstraint("code", name="uq_framework_dim_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    display_name: str = Field(index=True)
    region: Optional[str] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)

    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="framework_dim")


class RequirementEvidence(SQLModel, table=True):
    __tablename__ = "requirement_evidence"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_url: Optional[str] = Field(default=None)
    page_title: Optional[str] = Field(default=None)
    page_snippet: Optional[str] = Field(default=None)
    locator_type: Optional[str] = Field(default=None, index=True)
    locator_value: Optional[str] = Field(default=None)
    captured_at: Optional[datetime] = Field(default=None, index=True)
    crawled_at: datetime = Field(default_factory=_utc_now, index=True)
    content_hash: Optional[str] = Field(default=None, index=True)

    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="evidence")


class RequirementVersion(SQLModel, table=True):
    __tablename__ = "requirement_version"
    __table_args__ = (
        UniqueConstraint("program_id", "version_no", name="uq_requirement_version_program_no"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    version_no: int = Field(default=1, index=True)
    effective_at: datetime = Field(default_factory=_utc_now, index=True)
    valid_from: datetime = Field(default_factory=_utc_now, index=True)
    valid_to: Optional[datetime] = Field(default=None, index=True)
    change_summary: Optional[str] = Field(default=None)
    diff_payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    program: "Program" = Relationship(back_populates="requirement_versions")
    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="version")


class ProgramStudyOption(SQLModel, table=True):
    __tablename__ = "program_study_option"
    __table_args__ = (
        UniqueConstraint(
            "program_id",
            "mode",
            "duration_months",
            name="uq_program_study_option",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    mode: StudyMode = Field(
        default=StudyMode.UNKNOWN,
        sa_column=Column(STUDY_MODE_ENUM, nullable=False, index=True),
    )
    duration_months: Optional[int] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    program: "Program" = Relationship(back_populates="study_option_records")


class ProgramDeadline(SQLModel, table=True):
    __tablename__ = "program_deadline"
    __table_args__ = (
        UniqueConstraint(
            "program_id",
            "round",
            "description",
            "cutoff_date",
            name="uq_program_deadline",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    round: Optional[int] = Field(default=None, index=True)
    description: Optional[str] = Field(default=None)
    cutoff_date: Optional[datetime] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    program: "Program" = Relationship(back_populates="deadline_records")


class ProgramTuitionFee(SQLModel, table=True):
    """One tuition figure as the page states it.

    A programme page rarely publishes a single fee: CUHK prices by study mode,
    EdUHK and the UK universities by applicant scope, PolyU gives a programme
    total and a per-credit rate. Each such statement is one row here; the
    coarse Program.tuition_amount is derived from these rows in code
    (src/agents/tuition_headline.py). Synced by (mode, scope, basis) on
    re-crawl, like program_study_option — not versioned.
    """

    __tablename__ = "program_tuition_fee"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "study_mode", "applicant_scope", "basis",
            name="uq_program_tuition_fee",
        ),
        Index(
            "ix_program_tuition_fee_filter",
            "applicant_scope", "study_mode", "basis", "amount",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    currency: CurrencyCode = Field(sa_column=Column(CURRENCY_CODE_ENUM, nullable=False))
    basis: TuitionBasis = Field(sa_column=Column(TUITION_BASIS_ENUM, nullable=False, index=True))
    study_mode: StudyMode = Field(
        default=StudyMode.UNKNOWN,
        sa_column=Column(STUDY_MODE_ENUM, nullable=False, index=True),
    )
    applicant_scope: TuitionScope = Field(
        default=TuitionScope.ALL,
        sa_column=Column(TUITION_SCOPE_ENUM, nullable=False, index=True),
    )
    scope_label: Optional[str] = Field(default=None)
    credits: Optional[int] = Field(default=None)
    is_derived: bool = Field(default=False)
    source_text: Optional[str] = Field(default=None, max_length=300)
    updated_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    program: "Program" = Relationship(back_populates="tuition_fee_records")


class ProgramRequirement(SQLModel, table=True):
    __tablename__ = "program_requirement"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "category",
            "subject_name",
            "framework",
            "minimum_value",
            "unit",
            "applicant_scope",
            "requirement_text",
            name="uq_program_requirement_fingerprint",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    category: RequirementCategory = Field(
        default=RequirementCategory.OTHER,
        sa_column=Column(REQUIREMENT_CATEGORY_ENUM, nullable=False, index=True),
    )
    subject_name: Optional[str] = Field(default=None, index=True)
    framework: Optional[str] = Field(default=None, index=True)
    minimum_value: Optional[str] = Field(default=None, index=True)
    unit: Optional[str] = Field(default=None, index=True)
    applicant_scope: str = Field(default="all", index=True)
    requirement_text: str = Field(default="")
    evidence_url: Optional[str] = Field(default=None)
    sort_order: int = Field(default=0)
    updated_at: datetime = Field(default_factory=_utc_now)

    program_id: int = Field(foreign_key="program.id", index=True)
    version_id: Optional[int] = Field(default=None, foreign_key="requirement_version.id", index=True)
    subject_dim_id: Optional[int] = Field(default=None, foreign_key="subject_dim.id", index=True)
    exam_dim_id: Optional[int] = Field(default=None, foreign_key="exam_dim.id", index=True)
    framework_dim_id: Optional[int] = Field(default=None, foreign_key="framework_dim.id", index=True)
    evidence_id: Optional[int] = Field(default=None, foreign_key="requirement_evidence.id", index=True)

    program: "Program" = Relationship(back_populates="requirement_records")
    version: Optional[RequirementVersion] = Relationship(back_populates="requirement_records")
    subject_dim: Optional[SubjectDim] = Relationship(back_populates="requirement_records")
    exam_dim: Optional[ExamDim] = Relationship(back_populates="requirement_records")
    framework_dim: Optional[FrameworkDim] = Relationship(back_populates="requirement_records")
    evidence: Optional[RequirementEvidence] = Relationship(back_populates="requirement_records")
