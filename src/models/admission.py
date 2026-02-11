from typing import Optional, List, Dict, Any
from datetime import datetime
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

class RoundType(str, Enum):
    EARLY = "Early"
    MAIN = "Main"
    EXTENDED = "Extended"
    UNKNOWN = "Unknown"

class University(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(index=True, unique=True)
    
    # Relationships
    programs: List["Program"] = Relationship(back_populates="university")


class Program(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("university_id", "academic_year", "name_en", name="uq_program_univ_year_name"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    academic_year: int = Field(index=True)
    name_zh: str = Field(index=True)
    name_en: str = Field(index=True)
    
    # Tuition: Structured with Amount + Currency
    # example: HK$ 350,000 -> amount=350000.00, currency=HKD
    tuition_amount: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))
    currency: Optional[CurrencyCode] = Field(default=None)
    
    # Study Options: List[Dict] -> JSONB
    # structure: [{"mode": "FullTime", "duration_months": 12}, ...]
    study_options: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    
    # Deadlines: List[Dict] -> JSONB
    # structure: [{"round": "Main", "cutoff_date": "2025-12-31T00:00:00"}]
    deadlines: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSONB))
    
    # Legacy/Raw field fallback
    tuition_fee_raw: Optional[str] = None
    duration_raw: Optional[str] = None
    deadline_raw: Optional[str] = None
    
    # Store any extra columns from Excel as JSON
    extra_metadata: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Foreign Keys
    university_id: Optional[int] = Field(default=None, foreign_key="university.id")
    university: Optional[University] = Relationship(back_populates="programs")
