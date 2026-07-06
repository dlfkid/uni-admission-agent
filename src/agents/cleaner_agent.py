"""
LLM Cleaner Agent for parsing unstructured admission data.

Uses the RouterAgent (multi-provider) to extract structured data
from raw Excel rows or scraped content.
"""

import logging
import re
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


def _normalize_parsed_data(parsed: ParsedProgramData) -> ParsedProgramData:
    """Deduplicate/normalize a program's list fields (page-size independent).

    Runs on BOTH the single-pass and rolling-chunk paths (via ``clean_markdown``),
    so dedup and null-date dropping behave identically regardless of page size.
    Preserves scalar fields (faculty, tuition) untouched.

    - study_options: dedup by (mode, duration).
    - deadlines: dedup by cutoff date; drop entries with no date (they carry no
      actionable info and are almost always a duplicate artifact of a dated round).
    - requirements: dedup by (category, loose-normalized text), then drop any whose
      text is fully contained in a longer requirement (chunk paraphrases split one
      rule across chunks and recombine it elsewhere; the subset is redundant).
    """
    def _norm(text: Optional[str]) -> str:
        return " ".join(str(text or "").lower().split())

    def _loose(text: Optional[str]) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())

    # Study options: key on (mode, duration).
    dedup_options: List[ParsedStudyOption] = []
    seen_options: set = set()
    for opt in parsed.study_options:
        key = (str(getattr(opt, "mode", "")), getattr(opt, "duration_months", None))
        if key not in seen_options:
            seen_options.add(key)
            dedup_options.append(opt)

    # Deadlines: key on cutoff date; drop null-date entries.
    dedup_deadlines: List[ParsedDeadline] = []
    seen_deadlines: set = set()
    for dl in parsed.deadlines:
        cutoff = getattr(dl, "cutoff_date", None)
        if cutoff is None:
            continue
        key = cutoff.date() if hasattr(cutoff, "date") else str(cutoff)
        if key not in seen_deadlines:
            seen_deadlines.add(key)
            dedup_deadlines.append(dl)

    # Requirements: exact (category, loose-text) dedup, then substring containment.
    keyed: List[ParsedRequirement] = []
    seen_requirements: set = set()
    for req in parsed.requirements:
        key = (_norm(getattr(req, "category", "")), _loose(getattr(req, "requirement_text", "")))
        if key not in seen_requirements:
            seen_requirements.add(key)
            keyed.append(req)

    loose_texts = [_loose(getattr(r, "requirement_text", "")) for r in keyed]
    dedup_requirements: List[ParsedRequirement] = []
    for i, req in enumerate(keyed):
        txt_i = loose_texts[i]
        # Drop if fully contained in a longer requirement (cross-category on purpose).
        container = next(
            (loose_texts[j] for j in range(len(loose_texts))
             if j != i and txt_i and txt_i != loose_texts[j] and txt_i in loose_texts[j]),
            None,
        )
        if container is not None:
            # Aggressive + irreversible: log the drop so a missing requirement is
            # auditable (a shorter, more-permissive rule can be a substring of a
            # more specific one).
            logger.debug(
                "Dropping requirement contained in a longer one: %r ⊂ %r",
                getattr(req, "requirement_text", ""), container,
            )
            continue
        dedup_requirements.append(req)

    return ParsedProgramData(
        faculty=parsed.faculty,
        tuition=parsed.tuition,
        study_options=dedup_options,
        deadlines=dedup_deadlines,
        requirements=dedup_requirements,
    )


def _merge_parsed_data(
    existing: ParsedProgramData, new: ParsedProgramData,
) -> ParsedProgramData:
    """Merge two ParsedProgramData objects across chunks. New non-empty scalars win.

    Concatenates list fields then delegates dedup/normalization to
    ``_normalize_parsed_data`` (the single source of dedup truth, also applied to
    single-pass results in ``clean_markdown``).
    """
    combined = ParsedProgramData(
        faculty=new.faculty if new.faculty else existing.faculty,
        tuition=new.tuition if new.tuition else existing.tuition,
        study_options=list(existing.study_options) + list(new.study_options),
        deadlines=list(existing.deadlines) + list(new.deadlines),
        requirements=list(existing.requirements) + list(new.requirements),
    )
    return _normalize_parsed_data(combined)


_PER_CREDIT_RE = re.compile(r"HK\$?\s*([\d,]+(?:\.\d+)?)\s*per\s*credit", re.IGNORECASE)
_PER_PROGRAMME_RE = re.compile(r"HK\$?\s*([\d,]+(?:\.\d+)?)\s*per\s*programme", re.IGNORECASE)
_CREDIT_COUNT_RE = re.compile(
    r"(?:Minimum\s+No\.?\s+of\s+)?[Cc]redits?\s+[Rr]equired\s*[:\-]?\s*\n?\s*(\d{1,3})",
    re.IGNORECASE,
)


def _reconcile_per_credit_tuition(
    parsed: Optional["ParsedProgramData"], markdown: str, source_url: str = "",
) -> None:
    """Fix tuition that is actually a per-credit rate stored as the programme total.

    Some pages list only "HK$X per credit" with no per-programme total; the LLM then
    puts the per-credit rate into ``tuition.amount``, which reads as an absurdly cheap
    whole-programme fee (e.g. HK$8,200 for an MSc). When the page gives a per-credit
    rate, NO per-programme total, and a credit count, compute the total in code
    (per_credit x credits) — deterministic arithmetic the LLM does unreliably.

    Mutates ``parsed.tuition.amount`` in place. No-op when a per-programme total is
    present (already correct) or the signals are missing.
    """
    if parsed is None or not parsed.tuition or parsed.tuition.amount is None:
        return
    if _PER_PROGRAMME_RE.search(markdown):
        return  # a real programme total exists; trust the extracted amount

    per_credit_matches = [
        Decimal(m.group(1).replace(",", "")) for m in _PER_CREDIT_RE.finditer(markdown)
    ]
    if not per_credit_matches:
        return

    amount = Decimal(parsed.tuition.amount)
    # Only act when the stored amount IS one of the per-credit rates on the page.
    if not any(abs(amount - pc) < 1 for pc in per_credit_matches):
        return

    credit_match = _CREDIT_COUNT_RE.search(markdown)
    if not credit_match:
        logger.warning(
            "Per-credit tuition %s found for %s but no credit count — leaving as-is",
            amount, source_url,
        )
        return

    credits = int(credit_match.group(1))
    total = amount * credits
    logger.info(
        "Reconciled per-credit tuition for %s: HK$%s/credit x %d credits = %s",
        source_url, amount, credits, total,
    )
    parsed.tuition.amount = total


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

        # Extract academic year for deadline inference
        year = int(raw_row.get("academic_year") or 0)
        year_minus_1 = year - 1 if year else 0
        deadline_year_hint = ""
        if year:
            deadline_year_hint = (
                f"   - When dates lack a year (e.g., '15 December', '31 March'), infer the year\n"
                f"             from the academic year context. For entry year {year}, application deadlines\n"
                f"             before September are typically in {year}, and deadlines\n"
                f"             in October-December are typically in {year_minus_1}.\n"
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
{deadline_year_hint}\
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
        academic_year: int = 0,
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
            parsed = self._parse_single_pass(markdown, source_url, name_hints, academic_year)
        else:
            parsed = self._parse_rolling_chunks(markdown, source_url, name_hints, academic_year)

        _reconcile_per_credit_tuition(parsed, markdown, source_url)
        # Dedup/null-date normalization runs on BOTH paths here (not just inside the
        # chunk merge) so behavior is uniform regardless of page size.
        if parsed is not None:
            parsed = _normalize_parsed_data(parsed)
        return parsed

    # ------------------------------------------------------------------ #
    #  Self-critique retry                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parsed_has_content(parsed: Optional[ParsedProgramData]) -> bool:
        """Cleaner-level "did we extract anything useful?" check.

        Faculty alone doesn't count — a page that returns only a faculty
        with no tuition/deadlines/requirements is the empty-shell failure
        mode the gate exists to catch.
        """
        if parsed is None:
            return False
        return (
            parsed.tuition is not None
            or bool(parsed.deadlines)
            or bool(parsed.requirements)
        )

    def _build_critique_block(
        self, previous: Optional[ParsedProgramData]
    ) -> str:
        """Build a critique preamble to prepend before re-running extraction.

        Frames the previous output as DATA (not as the LLM's prior message)
        to reduce cognitive-consistency bias — LLMs are less likely to
        double down on a wrong answer when it's presented as external input.
        """
        if previous is None:
            previous_block = "(no fields extracted — extraction returned nothing)"
        else:
            previous_block = previous.model_dump_json()
        return (
            "PREVIOUS EXTRACTION ATTEMPT FAILED QUALITY CHECK\n"
            "================================================\n"
            "A previous extraction returned the following data:\n"
            f"{previous_block}\n\n"
            "This is incomplete: no tuition amount, no deadlines, and no "
            "requirements were found. The page below likely DOES contain "
            "at least one of these — look more carefully for:\n"
            "  - Any monetary amount + currency (tuition / fee / cost)\n"
            "  - Any date phrased as deadline / closing / cutoff / round X\n"
            "  - Any score / GPA / qualification mentioned for admission\n"
            "\n"
            "If after a careful re-read these fields are GENUINELY absent "
            "(e.g. this is an overview / navigation / placeholder page), "
            "return null fields rather than fabricating values. Do not "
            "invent data to satisfy this critique.\n"
            "================================================\n\n"
        )

    def clean_markdown_with_critique(
        self,
        markdown: str,
        source_url: str = "",
        name_hints: Optional[List[str]] = None,
        academic_year: int = 0,
    ) -> Optional[ParsedProgramData]:
        """Parse markdown with one self-critique retry on poor results.

        If the first attempt returns no content (None or empty shell),
        re-call the LLM with a critique preamble that embeds the previous
        output and asks for a more careful re-read. Returns whichever
        attempt produced content; if both attempts fail, returns the
        original (still-empty) result so downstream can quarantine.

        Caps at one retry — no critique chains, to bound cost and avoid
        the LLM hallucinating data to satisfy escalating critiques.
        """
        first = self.clean_markdown(
            markdown=markdown,
            source_url=source_url,
            name_hints=name_hints,
            academic_year=academic_year,
        )
        if self._parsed_has_content(first):
            return first

        # Retry with critique. Same provider, augmented prompt embedded
        # as a preamble to the original markdown so the LLM sees the
        # critique BEFORE the source content.
        critique_prefix = self._build_critique_block(first)
        retry_markdown = critique_prefix + markdown
        logger.info(
            "Self-critique retry for %s (first attempt had no content)",
            source_url,
        )
        retry = self.clean_markdown(
            markdown=retry_markdown,
            source_url=source_url,
            name_hints=name_hints,
            academic_year=academic_year,
        )

        if self._parsed_has_content(retry):
            return retry
        # Both empty — return the original so downstream sees the original
        # failure signal (not the retry which may be different but equally
        # useless).
        return first

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _parse_single_pass(
        self,
        markdown: str,
        source_url: str,
        name_hints: Optional[List[str]],
        academic_year: int = 0,
    ) -> Optional[ParsedProgramData]:
        """Parse a small detail page in one LLM call."""
        raw_row: Dict[str, str] = {
            "source_url": source_url,
            "raw_content": markdown,
        }
        if academic_year:
            raw_row["academic_year"] = str(academic_year)
        return self.clean_row(raw_row, name_hints=name_hints)

    def _parse_rolling_chunks(
        self,
        markdown: str,
        source_url: str,
        name_hints: Optional[List[str]],
        academic_year: int = 0,
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
        year_context = ""
        if academic_year:
            year_context = (
                f"\nAcademic year context: entry year is {academic_year}. "
                f"When dates lack a year, deadlines before September = {academic_year}, "
                f"October-December = {academic_year - 1}.\n"
            )

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
            if year_context:
                prompt = year_context + prompt
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
            For max_chars=20000 and overlap_ratio=0.2 (overlap_chars=4000): each
            chunk ends at a paragraph break at or before start+max_chars, and the
            NEXT chunk starts `overlap_chars` before that actual end — so starts
            depend on where the break landed, not a fixed step. Only in the
            no-break (hard-split) case does this reduce to the regular pattern
            0-20000, 16000-36000, 32000-52000. Advancing relative to the real end
            (rather than a fixed step from the original start) guarantees the
            chunks overlap and never leave a gap that could drop content.
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

            # Advance relative to where THIS chunk actually ended, re-including the
            # last `overlap_chars` of it. Advancing by a fixed step from the original
            # `start` instead would skip the gap between an early paragraph-break
            # cutoff (chunk_end) and start+step_size — silently dropping any value in
            # that gap (e.g. a tuition figure sitting just past the break).
            next_start = chunk_end - overlap_chars

            # Guarantee forward progress even if the chunk was very short.
            if next_start <= start:
                next_start = chunk_end
            start = next_start

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
