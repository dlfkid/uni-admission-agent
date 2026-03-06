"""
LLM Cleaner Agent for parsing unstructured admission data.

Uses the RouterAgent (multi-provider) to extract structured data
from raw Excel rows or scraped content.
"""

import logging
from typing import Dict, List, Optional, Any

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator

from src.agents.factory import RouterAgent, create_router
from src.core.paths import get_prompts_dir
from src.models.admission import CurrencyCode, StudyMode

logger = logging.getLogger(__name__)

# --- Constants ---

MAX_DETAIL_CHARS = 20000  # Max chars per chunk for detail page parsing
CHUNK_OVERLAP_RATIO = 0.20  # 20% overlap between consecutive chunks to prevent context truncation
PROMPTS_DIR = get_prompts_dir()


# --- Prompt Loading ---


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


# --- Pydantic Schemas for Structured Output ---


class ParsedTuition(BaseModel):
    amount: Decimal = Field(..., description="Tuition amount in numbers, e.g., 350000.00")
    currency: CurrencyCode = Field(..., description="Currency code, e.g., HKD, USD")

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, v: object) -> object:
        """Parse shorthand formats like '14k', '1.5m', '350K' into Decimal.
        
        LLMs sometimes return abbreviated amounts. Convert to full numbers:
        - '14k' or '14K' → 14000
        - '1.5m' or '1.5M' → 1500000
        - '350,000' → 350000 (strip commas)
        """
        if not isinstance(v, str):
            return v
        
        # Remove commas and spaces
        v_clean = v.replace(",", "").replace(" ", "").strip()
        
        # Handle 'k' or 'K' suffix (thousands)
        if v_clean.lower().endswith("k"):
            try:
                base = float(v_clean[:-1])
                return Decimal(str(base * 1000))
            except (ValueError, TypeError):
                pass
        
        # Handle 'm' or 'M' suffix (millions)
        if v_clean.lower().endswith("m"):
            try:
                base = float(v_clean[:-1])
                return Decimal(str(base * 1_000_000))
            except (ValueError, TypeError):
                pass
        
        # Return cleaned string for normal Decimal parsing
        return v_clean


class ParsedStudyOption(BaseModel):
    mode: StudyMode = Field(..., description="Study mode: FullTime, PartTime, Hybrid")
    duration_months: int = Field(..., description="Duration in months. 1 year = 12 months.")


class ParsedDeadline(BaseModel):
    description: Optional[str] = Field(default=None, description="Round description, e.g., 'Early Round', 'Round 1', 'Main'.")
    cutoff_date: Optional[datetime] = Field(default=None, description="ISO 8601 date string. If missing, return null.")


class ParsedRequirement(BaseModel):
    category: str = Field(
        default="academic_subject",
        description="Requirement category (academic_subject, language, standardized_test, portfolio, experience, other).",
    )
    subject_name: Optional[str] = Field(default=None, description="Subject/test name, e.g. Mathematics, IELTS.")
    framework: Optional[str] = Field(default=None, description="Qualification framework, e.g. A-Level, IB, Gaokao.")
    minimum_value: Optional[str] = Field(default=None, description="Minimum threshold, e.g. A, 6.5, 1300.")
    unit: Optional[str] = Field(default=None, description="Unit or score scale, e.g. points, band.")
    applicant_scope: str = Field(default="all", description="Target applicant scope, e.g. all/international/local.")
    requirement_text: str = Field(default="", description="Human-readable requirement statement.")
    evidence_url: Optional[str] = Field(default=None, description="Source evidence URL if available.")


class ParsedProgramData(BaseModel):
    faculty: Optional[str] = Field(default=None, description="Top-level academic unit (Faculty, School, or College). e.g., 'Faculty of Engineering'.")
    tuition: Optional[ParsedTuition] = Field(default=None, description="Tuition fee structure")
    study_options: List[ParsedStudyOption] = Field(default_factory=list, description="List of study options")
    deadlines: List[ParsedDeadline] = Field(default_factory=list, description="List of application deadlines")
    requirements: List[ParsedRequirement] = Field(default_factory=list, description="Subject-level admission requirements")

    @field_validator("study_options", "deadlines", "requirements", mode="before")
    @classmethod
    def _none_to_list(cls, v: object) -> object:
        """LLMs sometimes return ``null`` for list fields; coerce to ``[]``."""
        return v if v is not None else []


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
    merged_faculty = new.faculty if new.faculty else existing.faculty

    # Accumulate study options and deadlines (dedup by content)
    merged_options = list(existing.study_options)
    for opt in new.study_options:
        if opt not in merged_options:
            merged_options.append(opt)

    merged_deadlines = list(existing.deadlines)
    for dl in new.deadlines:
        if dl not in merged_deadlines:
            merged_deadlines.append(dl)

    merged_requirements = list(existing.requirements)
    for req in new.requirements:
        if req not in merged_requirements:
            merged_requirements.append(req)

    return ParsedProgramData(
        faculty=merged_faculty,
        tuition=merged_tuition,
        study_options=merged_options,
        deadlines=merged_deadlines,
        requirements=merged_requirements,
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

    def clean_row(
        self,
        raw_row: Dict[str, str],
        name_hints: Optional[List[str]] = None,
    ) -> Optional[ParsedProgramData]:
        """
        Parse a dictionary of raw row data into structured ParsedProgramData.

        Args:
            raw_row: Dict like {"Tuition Fee": "HK$ 350,000", "Duration": "1 year"}

        Returns:
            ParsedProgramData object or None if parsing fails.
        """
        hints = self._normalize_name_hints(name_hints)
        hints_block = ""
        if hints:
            lines = "\n".join(f"- {item}" for item in hints)
            hints_block = (
                "Program Name Hints (canonical_name|score):\n"
                f"{lines}\n"
                "Use these only as guidance for program identity; do not invent fields.\n"
            )

        prompt = f"""
        You are an expert data parsing assistant.
        Your task is to extract structured admission data from the following raw data.

        Raw Data:
        {raw_row}
        {hints_block}

        Requirements:
        1. **Faculty**: Identify the top-level academic unit (Faculty, School, or College).
           - Look for text containing 'Faculty of...', 'School of...', 'College of...'.
           - If a program is in 'Department of Computer Science' under 'Faculty of Engineering', return 'Faculty of Engineering'.
           - If not explicitly mentioned, infer from context or set to null.
        2. **Tuition**: Extract numeric amount and currency. Handle "per year" or total logic if implied.
        3. **Study Options**: Convert descriptions like "1 year FT / 2 years PT" into a list of options with mode and months.
        4. **Deadlines**: Extract dates and descriptions.
           - Output ALL valid deadlines found, sorted chronologically.
        5. **Requirements**: Extract subject-level admission requirements.
           - Include score/grade thresholds when present (e.g., Math A, IELTS 6.5).
           - Capture framework when available (A-Level/IB/Gaokao/SAT/ACT).
        6. Missing Data: If a field cannot be extracted, set it to null/empty list as per schema.

        IMPORTANT: Only return necessary structured data. Do NOT include any raw HTML snippets or duplicate original text in the JSON output.

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
        name_hints: Optional[List[str]] = None,
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
            return self._parse_single_pass(markdown, source_url, name_hints)

        return self._parse_rolling_chunks(markdown, source_url, name_hints)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _parse_single_pass(
        self,
        markdown: str,
        source_url: str,
        name_hints: Optional[List[str]],
    ) -> Optional[ParsedProgramData]:
        """Parse a small detail page in one LLM call."""
        raw_row: Dict[str, str] = {
            "source_url": source_url,
            "raw_content": markdown,
        }
        return self.clean_row(raw_row, name_hints=name_hints)

    def _parse_rolling_chunks(
        self,
        markdown: str,
        source_url: str,
        name_hints: Optional[List[str]],
    ) -> Optional[ParsedProgramData]:
        """Parse a large detail page using rolling-window sequential chunks.

        Each chunk receives a context summary from the previous chunk so
        split information (e.g., a label in one chunk and its value in the
        next) can be reconstructed by the LLM.
        """
        chunks = self._split_chunks(markdown, MAX_DETAIL_CHARS)
        total_chunks = len(chunks)
        overlap_chars = int(MAX_DETAIL_CHARS * CHUNK_OVERLAP_RATIO)
        logger.info(
            "Large detail page (%s chars) split into %d overlapping chunks (overlap: %d chars, %.0f%%): %s",
            f"{len(markdown):,}", total_chunks, overlap_chars, CHUNK_OVERLAP_RATIO * 100, source_url,
        )

        prompt_template = _load_prompt("clean_chunk.txt")
        accumulated = ParsedProgramData()
        context_summary = "No previous context. This is the first chunk."

        hints = self._normalize_name_hints(name_hints)

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
            if hints:
                hints_lines = "\n".join(f"- {item}" for item in hints)
                prompt = (
                    "Program Name Hints (canonical_name|score):\n"
                    f"{hints_lines}\n\n"
                    f"{prompt}"
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
        if (
            not accumulated.tuition
            and not accumulated.study_options
            and not accumulated.deadlines
            and not accumulated.requirements
        ):
            logger.warning("No data extracted from any chunk: %s", source_url)
            return None

        return accumulated

    @staticmethod
    def _normalize_name_hints(name_hints: Optional[List[str]]) -> List[str]:
        if not name_hints:
            return []
        normalized: List[str] = []
        for item in name_hints:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized[:5]

    @staticmethod
    def _split_chunks(text: str, max_chars: int, overlap_ratio: float = CHUNK_OVERLAP_RATIO) -> List[str]:
        """Split text into overlapping chunks on paragraph boundaries.

        Chunks overlap by `overlap_ratio` (default 20%) to prevent context truncation
        when critical information spans chunk boundaries. The deduplication logic
        in `_merge_parsed_data` automatically handles duplicate data from overlaps.

        Args:
            text: Full text to split.
            max_chars: Maximum characters per chunk.
            overlap_ratio: Fraction of overlap between consecutive chunks (0.0-0.5).

        Returns:
            List of overlapping text chunks.

        Example:
            For max_chars=20000 and overlap_ratio=0.2:
            - Chunk 1: chars 0-20000
            - Chunk 2: chars 16000-36000 (4000 char overlap)
            - Chunk 3: chars 32000-52000 (4000 char overlap)
        """
        if len(text) <= max_chars:
            return [text]

        # Clamp overlap ratio to reasonable range
        overlap_ratio = max(0.0, min(0.5, overlap_ratio))
        overlap_chars = int(max_chars * overlap_ratio)
        step_size = max_chars - overlap_chars

        chunks: List[str] = []
        start = 0

        while start < len(text):
            end = min(start + max_chars, len(text))

            # If this is the last chunk, just take the remaining text
            if end == len(text):
                chunks.append(text[start:])
                break

            # Find a good paragraph break near the end of the chunk
            slice_text = text[start:end]
            split_pos = slice_text.rfind("\n\n")

            # If no good paragraph break, try single newline
            if split_pos < len(slice_text) // 2:
                split_pos = slice_text.rfind("\n")

            # If still no newline, do hard split at max_chars
            if split_pos < len(slice_text) // 2:
                split_pos = len(slice_text)

            # Append the chunk
            chunk_end = start + split_pos
            chunks.append(text[start:chunk_end])

            # Move start forward by step_size (creating overlap)
            start += step_size

            # Adjust start to a newline boundary if possible (for cleaner overlap)
            if start < len(text):
                # Look for a newline within a small window
                window_start = max(start - 50, chunk_end)
                window_end = min(start + 50, len(text))
                window_text = text[window_start:window_end]
                newline_pos = window_text.find("\n")
                if newline_pos != -1:
                    start = window_start + newline_pos + 1

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
            "1. **Faculty**: Identify the top-level academic unit (Faculty, School, or College).",
            "   - Look for text containing 'Faculty of...', 'School of...', 'College of...'.",
            "   - If a program is in a department, identify its parent Faculty. If not available, set to null.",
            "2. **Tuition**: Extract numeric amount and currency. Handle 'per year' or total logic if implied.",
            "3. **Study Options**: Convert descriptions like '1 year FT / 2 years PT' into a list of options with mode and months.",
            "4. **Deadlines**: Extract dates and descriptions.",
            "   - Output ALL valid deadlines found, sorted chronologically.",
            "5. **Requirements**: Extract subject-level admission requirements.",
            "   - Include score/grade thresholds when available (Math A, IELTS 6.5).",
            "6. Missing Data: If a field cannot be extracted, set it to null/empty list as per schema.",
            "",
            "IMPORTANT: Only return necessary structured data. Do NOT include any raw HTML snippets or duplicate original text in the JSON output.",
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
