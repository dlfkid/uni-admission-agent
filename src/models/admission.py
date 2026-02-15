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
    
    # Relationships
    programs: List["Program"] = Relationship(back_populates="university")


class Program(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("university_id", "academic_year", "name_en", name="uq_program_year"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    academic_year: int = Field(index=True)
    name_zh: Optional[str] = Field(default="", index=True)
    name_en: str = Field(index=True)

    # Lineage and Grouping
    program_group_code: Optional[str] = Field(default=None, index=True)

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
    study_options: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    
    # Deadlines: List[Dict] -> JSONB
    # structure: [{"round": 1, "description": "Main Round", "cutoff_date": "2025-12-31T00:00:00"}]
    # Round number is assigned chronologically (1, 2, 3...)
    deadlines: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    
    # Store any extra columns from Excel as JSON
    extra_metadata: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Foreign Keys
    university_id: Optional[int] = Field(default=None, foreign_key="university.id")
    university: Optional[University] = Relationship(back_populates="programs")
