import os
import logging
from typing import Optional, List, Any, Dict
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from src.models.admission import CurrencyCode, StudyMode, RoundType
from src.core.token_tracker import tracker

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
    Agent that uses Google GenAI to parse and clean unstructured admission data
    into strictly structured formats.
    """
    
    def __init__(self, api_key: Optional[str] = None, model_id: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GOOGLE_API_KEY/GEMINI_API_KEY not found. Agent functionality will be disabled.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)
            
        self.model_id = model_id or os.environ.get("GEMINI_MODEL_NAME") or "gemini-2.0-flash-exp"

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=90))
    def clean_row(self, raw_row: Dict[str, Any]) -> Optional[ParsedProgramData]:
        """
        Parses a dictionary of raw row data into structured ParsedProgramData.
        
        Args:
            raw_row: Dict like {"Tuition Fee": "HK$ 350,000", "Duration": "1 year"}
            
        Returns:
            ParsedProgramData object or None if failure/no client.
        """
        if not self.client:
            return None

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
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ParsedProgramData
                )
            )
            
            if not response.text:
                logger.warning("Empty response from GenAI")
                return None
            
            # Track Usage
            if response.usage_metadata:
                tracker.track_usage(
                    input_tokens=response.usage_metadata.prompt_token_count or 0,
                    output_tokens=response.usage_metadata.candidates_token_count or 0,
                    model=self.model_id
                )

            # Parse using Pydantic validation
            parsed_data = ParsedProgramData.model_validate_json(response.text)
            return parsed_data

        except Exception as e:
            logger.error(f"GenAI Parsing Failed: {e}")
            raise # Retry will catch this

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=90))
    def clean_batch(self, raw_rows: List[Dict[str, Any]]) -> List[ParsedProgramData]:
        """
        Parses a batch of raw rows (max 5 recommended) into a list of ParsedProgramData.
        
        Args:
            raw_rows: List of dicts, each representing a row.
            
        Returns:
            List of ParsedProgramData objects. Returns None/Empty list elements if parsing fails for specific items but generally tries to return all.
            If the API call fails completely, it raises exception (triggering retry).
        """
        if not self.client or not raw_rows:
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
            "Input Data Rows:"
        ]
        
        for i, row in enumerate(raw_rows):
            prompt_lines.append(f"Row {i}: {row}")
            
        prompt = "\n".join(prompt_lines)

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ParsedProgramBatch
                )
            )
            
            if not response.text:
                logger.warning("Empty response from GenAI for batch")
                return [ParsedProgramData() for _ in raw_rows] # Return empty objects
            
            # Track Usage
            if response.usage_metadata:
                tracker.track_usage(
                    input_tokens=response.usage_metadata.prompt_token_count or 0,
                    output_tokens=response.usage_metadata.candidates_token_count or 0,
                    model=self.model_id
                )

            # Parse
            parsed_batch = ParsedProgramBatch.model_validate_json(response.text)
            
            # Validation: Ensure output length matches input
            if len(parsed_batch.programs) != len(raw_rows):
                 logger.warning(f"Batch size mismatch! Input: {len(raw_rows)}, Output: {len(parsed_batch.programs)}")
                 # We might want to pad or truncate, or just return what we have. 
                 # For safety, let's return what we have, importer needs to handle mapping if possible
                 # But since we asked for order preservation, we assume index matching.
            
            return parsed_batch.programs

        except Exception as e:
            logger.error(f"GenAI Batch Parsing Failed: {e}")
            raise
