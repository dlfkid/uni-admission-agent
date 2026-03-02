from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship, Column
from sqlalchemy import Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

# --- Enums ---
class CurrencyCode(str, Enum):
    HKD = "HKD"
    USD = "USD"
    CNY = "CNY"
    GBP = "GBP"
    EUR = "EUR"
    AUD = "AUD"
    CAD = "CAD"
    SGD = "SGD"
    JPY = "JPY"
    OTHER = "OTHER"

class StudyMode(str, Enum):
    FULL_TIME = "FullTime"
    PART_TIME = "PartTime"
    HYBRID = "Hybrid"
    UNKNOWN = "Unknown"

class University(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(index=True, unique=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    programs: List["Program"] = Relationship(back_populates="university")
    program_catalogs: List["ProgramCatalog"] = Relationship(back_populates="university")


class ProgramCatalog(SQLModel, table=True):
    """Canonical program identity across years (stable grouping node)."""

    __tablename__ = "program_catalog"
    __table_args__ = (
        UniqueConstraint("university_id", "catalog_key", name="uq_program_catalog_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    catalog_key: str = Field(index=True)
    program_group_code: Optional[str] = Field(default=None, index=True)
    canonical_name_en: Optional[str] = Field(default=None, index=True)
    canonical_name_zh: Optional[str] = Field(default=None, index=True)
    faculty: Optional[str] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Foreign Keys
    university_id: Optional[int] = Field(default=None, foreign_key="university.id")
    university: Optional[University] = Relationship(back_populates="program_catalogs")
    program_versions: List["Program"] = Relationship(back_populates="program_catalog")


class Program(SQLModel, table=True):
    """Year-specific snapshot record for a program."""

    __table_args__ = (
        UniqueConstraint("program_catalog_id", "academic_year", name="uq_program_version_year"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    academic_year: int = Field(index=True)
    name_zh: Optional[str] = Field(default="", index=True)
    name_en: str = Field(index=True)

    # Lineage and Grouping
    program_group_code: Optional[str] = Field(default=None, index=True)
    program_catalog_id: Optional[int] = Field(default=None, foreign_key="program_catalog.id")

    # Organization
    faculty: Optional[str] = Field(default=None, index=True)

    # Status
    is_active: bool = Field(default=True)
    is_discontinued: bool = Field(default=False)
    
    # Tuition: Structured with Amount + Currency
    # example: HK$ 350,000 -> amount=350000.00, currency=HKD
    tuition_amount: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))
    currency: Optional[CurrencyCode] = Field(default=None)
    
    # Study Options: List[Dict] -> JSONB
    # structure: [{"mode": "FullTime", "duration_months": 12}, ...]
    study_options: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    
    # Deadlines: List[Dict] -> JSONB
    # structure: [{"round": 1, "description": "Main Round", "cutoff_date": "2025-12-31T00:00:00"}]
    # Round number is assigned chronologically (1, 2, 3...)
    deadlines: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    
    # Store any extra columns from Excel as JSON
    extra_metadata: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    source_url: Optional[str] = Field(default=None)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Foreign Keys
    university_id: Optional[int] = Field(default=None, foreign_key="university.id")
    university: Optional[University] = Relationship(back_populates="programs")
    program_catalog: Optional[ProgramCatalog] = Relationship(back_populates="program_versions")

    # Normalized child records
    study_option_records: List["ProgramStudyOption"] = Relationship(back_populates="program")
    deadline_records: List["ProgramDeadline"] = Relationship(back_populates="program")
    requirement_records: List["ProgramRequirement"] = Relationship(back_populates="program")
    requirement_versions: List["RequirementVersion"] = Relationship(back_populates="program")
