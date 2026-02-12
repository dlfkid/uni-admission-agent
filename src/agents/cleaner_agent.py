"""
LLM Cleaner Agent for parsing unstructured admission data.

Uses the RouterAgent (multi-provider) to extract structured data
from raw Excel rows or scraped content.
"""

import logging
from typing import Dict, List, Optional

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from src.agents.factory import RouterAgent, create_router
from src.models.admission import CurrencyCode, StudyMode, RoundType

logger = logging.getLogger(__name__)

# --- Pydantic Schemas for Structured Output ---


class ParsedTuition(BaseModel):
    amount: Decimal = Field(..., description="Tuition amount in numbers, e.g., 350000.00")
    currency: CurrencyCode = Field(..., description="Currency code, e.g., HKD, USD")


class ParsedStudyOption(BaseModel):
    mode: StudyMode = Field(..., description="Study mode: FullTime, PartTime, Hybrid")
    duration_months: int = Field(..., description="Duration in months. 1 year = 12 months.")


class ParsedDeadline(BaseModel):
    round: RoundType = Field(..., description="Round type: Early, Main, Extended. Infer if not explicit.")
    cutoff_date: Optional[datetime] = Field(default=None, description="ISO 8601 date string. If missing, return null.")


class ParsedProgramData(BaseModel):
    tuition: Optional[ParsedTuition] = Field(default=None, description="Tuition fee structure")
    study_options: List[ParsedStudyOption] = Field(default_factory=list, description="List of study options")
    deadlines: List[ParsedDeadline] = Field(default_factory=list, description="List of application deadlines")


class ParsedProgramBatch(BaseModel):
    programs: List[ParsedProgramData] = Field(..., description="List of parsed programs matching the input order")


# --- Agent Class ---


class LLMCleanerAgent:
    """
    Agent that uses multi-provider LLM routing to parse and clean
    unstructured admission data into strictly structured formats.
    """

    def __init__(self, router: Optional[RouterAgent] = None) -> None:
        """
        Initialize the cleaner agent.

        Args:
            router: RouterAgent instance. If None, creates one from env config.
        """
        if router is not None:
            self.router = router
        else:
            self.router = create_router()

    def clean_row(self, raw_row: Dict[str, str]) -> Optional[ParsedProgramData]:
        """
        Parse a dictionary of raw row data into structured ParsedProgramData.

        Args:
            raw_row: Dict like {"Tuition Fee": "HK$ 350,000", "Duration": "1 year"}

        Returns:
            ParsedProgramData object or None if parsing fails.
        """
        prompt = f"""
        You are an expert data parsing assistant. 
        Your task is to extract structured admission data from the following raw Excel row dictionary.
        
        Raw Data:
        {raw_row}
        
        Requirements:
        1. **Tuition**: Extract numeric amount and currency. Handle "per year" or total logic if implied.
        2. **Study Options**: Convert descriptions like "1 year FT / 2 years PT" into a list of options with mode and months.
        3. **Deadlines**: Extract dates and infer round type (Early, Main, Extended). If strictly valid date is found, use it.
           - If date is ambiguous, try best effort. 
           - Round inference: "Round 1" -> Early, "Round 2"/Normal -> Main, "Clearing" -> Extended.
        
        Output strictly valid JSON matching the schema.
        """

        try:
            response = self.router.generate(prompt, ParsedProgramData)

            if not response.text:
                logger.warning("Empty response from LLM")
                return None

            parsed_data = ParsedProgramData.model_validate_json(response.text)
            return parsed_data

        except Exception as e:
            logger.error(f"LLM Parsing Failed: {e}")
            raise

    def clean_batch(self, raw_rows: List[Dict[str, str]]) -> List[ParsedProgramData]:
        """
        Parse a batch of raw rows into a list of ParsedProgramData.

        Args:
            raw_rows: List of dicts, each representing a row.

        Returns:
            List of ParsedProgramData objects.
        """
        if not raw_rows:
            return []

        # Construct Batch Prompt
        prompt_lines = [
            "You are an expert data parsing assistant.",
            "Your task is to extract structured admission data from the following list of raw Excel rows.",
            "Return a JSON object with a key 'programs' containing a list of parsed objects, strictly preserving the order.",
            "",
            "Requirements:",
            "1. **Tuition**: Extract numeric amount and currency. Handle 'per year' or total logic if implied.",
            "2. **Study Options**: Convert descriptions like '1 year FT / 2 years PT' into a list of options with mode and months.",
            "3. **Deadlines**: Extract dates and infer round type (Early, Main, Extended).",
            "   - 'Round 1' -> Early, 'Round 2'/Normal -> Main, 'Clearing' -> Extended.",
            "4. Missing Data: If a field cannot be extracted, set it to null/empty list as per schema.",
            "",
            "Input Data Rows:",
        ]

        for i, row in enumerate(raw_rows):
            prompt_lines.append(f"Row {i}: {row}")

        prompt = "\n".join(prompt_lines)

        try:
            response = self.router.generate(prompt, ParsedProgramBatch)

            if not response.text:
                logger.warning("Empty response from LLM for batch")
                return [ParsedProgramData() for _ in raw_rows]

            parsed_batch = ParsedProgramBatch.model_validate_json(response.text)

            if len(parsed_batch.programs) != len(raw_rows):
                logger.warning(
                    f"Batch size mismatch! Input: {len(raw_rows)}, "
                    f"Output: {len(parsed_batch.programs)}"
                )

            return parsed_batch.programs

        except Exception as e:
            logger.error(f"LLM Batch Parsing Failed: {e}")
            raise
