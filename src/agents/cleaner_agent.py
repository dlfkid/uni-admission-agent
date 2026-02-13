"""
LLM Cleaner Agent for parsing unstructured admission data.

Uses the RouterAgent (multi-provider) to extract structured data
from raw Excel rows or scraped content.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from src.agents.factory import RouterAgent, create_router
from src.models.admission import CurrencyCode, StudyMode

logger = logging.getLogger(__name__)

# --- Constants ---

MAX_DETAIL_CHARS = 20000  # Max chars per chunk for detail page parsing
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


# --- Prompt Loading ---


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


# --- Pydantic Schemas for Structured Output ---


class ParsedTuition(BaseModel):
    amount: Decimal = Field(..., description="Tuition amount in numbers, e.g., 350000.00")
    currency: CurrencyCode = Field(..., description="Currency code, e.g., HKD, USD")


class ParsedStudyOption(BaseModel):
    mode: StudyMode = Field(..., description="Study mode: FullTime, PartTime, Hybrid")
    duration_months: int = Field(..., description="Duration in months. 1 year = 12 months.")


class ParsedDeadline(BaseModel):
    description: Optional[str] = Field(default=None, description="Round description, e.g., 'Early Round', 'Round 1', 'Main'.")
    cutoff_date: Optional[datetime] = Field(default=None, description="ISO 8601 date string. If missing, return null.")


class ParsedProgramData(BaseModel):
    tuition: Optional[ParsedTuition] = Field(default=None, description="Tuition fee structure")
    study_options: List[ParsedStudyOption] = Field(default_factory=list, description="List of study options")
    deadlines: List[ParsedDeadline] = Field(default_factory=list, description="List of application deadlines")


class ParsedProgramBatch(BaseModel):
    programs: List[ParsedProgramData] = Field(..., description="List of parsed programs matching the input order")


class ChunkParseResult(BaseModel):
    """Result from parsing a single chunk with rolling context."""
    data: ParsedProgramData = Field(
        ..., description="Extracted admission data from this chunk",
    )
    context_summary: str = Field(
        default="",
        description="Brief summary of key information for the next chunk (max 200 chars)",
    )


# --- Merge Helper ---


def _merge_parsed_data(
    existing: ParsedProgramData, new: ParsedProgramData,
) -> ParsedProgramData:
    """Merge two ParsedProgramData objects. New non-empty fields win.

    Args:
        existing: Previously accumulated data.
        new: New data from the latest chunk.

    Returns:
        Merged ParsedProgramData.
    """
    merged_tuition = new.tuition if new.tuition else existing.tuition

    # Accumulate study options and deadlines (dedup by content)
    merged_options = list(existing.study_options)
    for opt in new.study_options:
        if opt not in merged_options:
            merged_options.append(opt)

    merged_deadlines = list(existing.deadlines)
    for dl in new.deadlines:
        if dl not in merged_deadlines:
            merged_deadlines.append(dl)

    return ParsedProgramData(
        tuition=merged_tuition,
        study_options=merged_options,
        deadlines=merged_deadlines,
    )


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
        Your task is to extract structured admission data from the following raw data.

        Raw Data:
        {raw_row}

        Requirements:
        1. **Tuition**: Extract numeric amount and currency. Handle "per year" or total logic if implied.
        2. **Study Options**: Convert descriptions like "1 year FT / 2 years PT" into a list of options with mode and months.
        3. **Deadlines**: Extract dates and descriptions.
           - Output ALL valid deadlines found, sorted chronologically.
        4. Missing Data: If a field cannot be extracted, set it to null/empty list as per schema.

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
            logger.error("LLM Parsing Failed: %s", e)
            raise

    def clean_markdown(
        self,
        markdown: str,
        source_url: str = "",
    ) -> Optional[ParsedProgramData]:
        """Parse Markdown content from a detail page into structured data.

        For small pages (≤ MAX_DETAIL_CHARS), processes in a single LLM call.
        For large pages, splits into chunks and processes **sequentially**
        with a rolling context summary to preserve cross-chunk context.

        Args:
            markdown: Full Markdown content of the detail page.
            source_url: Source URL for logging.

        Returns:
            ParsedProgramData or None if parsing fails entirely.
        """
        if len(markdown) <= MAX_DETAIL_CHARS:
            return self._parse_single_pass(markdown, source_url)

        return self._parse_rolling_chunks(markdown, source_url)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _parse_single_pass(
        self,
        markdown: str,
        source_url: str,
    ) -> Optional[ParsedProgramData]:
        """Parse a small detail page in one LLM call."""
        raw_row: Dict[str, str] = {
            "source_url": source_url,
            "raw_content": markdown,
        }
        return self.clean_row(raw_row)

    def _parse_rolling_chunks(
        self,
        markdown: str,
        source_url: str,
    ) -> Optional[ParsedProgramData]:
        """Parse a large detail page using rolling-window sequential chunks.

        Each chunk receives a context summary from the previous chunk so
        split information (e.g., a label in one chunk and its value in the
        next) can be reconstructed by the LLM.
        """
        chunks = self._split_chunks(markdown, MAX_DETAIL_CHARS)
        total_chunks = len(chunks)
        logger.info(
            "Large detail page (%s chars) split into %d chunks: %s",
            f"{len(markdown):,}", total_chunks, source_url,
        )

        prompt_template = _load_prompt("clean_chunk.txt")
        accumulated = ParsedProgramData()
        context_summary = "No previous context. This is the first chunk."

        for i, chunk in enumerate(chunks):
            chunk_num = i + 1
            logger.info(
                "Parsing chunk %d/%d (%s chars)...",
                chunk_num, total_chunks, f"{len(chunk):,}",
            )

            prompt = prompt_template.format(
                context_summary=context_summary,
                chunk_number=chunk_num,
                total_chunks=total_chunks,
                chunk_content=chunk,
            )

            try:
                response = self.router.generate(prompt, ChunkParseResult)

                if not response.text:
                    logger.warning("Empty LLM response for chunk %d", chunk_num)
                    continue

                result = ChunkParseResult.model_validate_json(response.text)
                accumulated = _merge_parsed_data(accumulated, result.data)
                context_summary = result.context_summary or context_summary

                logger.info(
                    "Chunk %d/%d parsed. Context: %s",
                    chunk_num, total_chunks,
                    context_summary[:80] + "..." if len(context_summary) > 80 else context_summary,
                )

            except Exception as e:
                logger.error("Chunk %d parsing failed: %s", chunk_num, e)
                continue

        # Check if we got any meaningful data
        if not accumulated.tuition and not accumulated.study_options and not accumulated.deadlines:
            logger.warning("No data extracted from any chunk: %s", source_url)
            return None

        return accumulated

    @staticmethod
    def _split_chunks(text: str, max_chars: int) -> List[str]:
        """Split text into chunks on paragraph boundaries.

        Args:
            text: Full text to split.
            max_chars: Maximum characters per chunk.

        Returns:
            List of text chunks.
        """
        if len(text) <= max_chars:
            return [text]

        chunks: List[str] = []
        remaining = text

        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break

            slice_end = remaining[:max_chars]
            split_pos = slice_end.rfind("\n\n")

            if split_pos < max_chars // 2:
                split_pos = slice_end.rfind("\n")

            if split_pos < max_chars // 2:
                split_pos = max_chars

            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos:].lstrip("\n")

        return chunks

    def clean_batch(
        self, raw_rows: List[Dict[str, Any]]
    ) -> List[ParsedProgramData]:
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
            "3. **Deadlines**: Extract dates and descriptions.",
            "   - Output ALL valid deadlines found, sorted chronologically.",
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
                    "Batch size mismatch! Input: %d, Output: %d",
                    len(raw_rows), len(parsed_batch.programs),
                )

            return parsed_batch.programs

        except Exception as e:
            logger.error("LLM Batch Parsing Failed: %s", e)
            raise
