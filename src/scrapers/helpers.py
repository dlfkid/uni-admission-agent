"""
Helper functions for the scraping engine.

This module contains utility functions extracted from engine.py to keep
the main module under the pylint line limit.
"""

import logging
import re
from pathlib import Path
from typing import List

from src.core.paths import get_prompts_dir

logger = logging.getLogger(__name__)

# --- Prompt Loading ---

_PROMPTS_DIR = get_prompts_dir()


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = _PROMPTS_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def sanitize_filename(url: str, extension: str = ".md", max_length: int = 200) -> str:
    """Create a safe filename from URL."""
    filename = url.replace("https://", "").replace("http://", "")
    filename = re.sub(r'[^\w\-\_\.]', '_', filename)[:max_length]
    if not filename.endswith(extension):
        filename += extension
    return filename


def save_markdown(export_path: str, url: str, markdown: str) -> None:
    """Save markdown content to disk with a sanitized filename."""
    try:
        filename = sanitize_filename(url, ".md")
        filepath = Path(export_path) / filename
        filepath.write_text(markdown, encoding='utf-8')
        logger.info("Exported markdown to: %s", filepath)
    except Exception as e:
        logger.error("Failed to save markdown for %s: %s", url, e)


def save_html_debug(export_path: str, url: str, html: str) -> None:
    """Save HTML for debugging purposes."""
    filename = sanitize_filename(url, ".html")
    filepath = Path(export_path) / filename
    filepath.write_text(html, encoding='utf-8')
    logger.info("Saved HTML for debugging: %s", filepath)


def split_markdown_chunks(
    markdown: str, max_chars: int, overlap_ratio: float = 0.20,
) -> List[str]:
    """Split Markdown into overlapping chunks that fit within the LLM context window.

    Chunks overlap by `overlap_ratio` (default 20%) to prevent context truncation
    when critical information spans chunk boundaries. Splits on double-newline
    (paragraph) boundaries to avoid cutting mid-link or mid-sentence.

    Args:
        markdown: Full Markdown content.
        max_chars: Maximum characters per chunk.
        overlap_ratio: Fraction of overlap between consecutive chunks (0.0-0.5).

    Returns:
        List of overlapping Markdown chunks.

    Example:
        For max_chars=20000 and overlap_ratio=0.2:
        - Chunk 1: chars 0-20000
        - Chunk 2: chars 16000-36000 (4000 char overlap)
        - Chunk 3: chars 32000-52000 (4000 char overlap)
    """
    if len(markdown) <= max_chars:
        return [markdown]

    # Clamp overlap ratio to reasonable range
    overlap_ratio = max(0.0, min(0.5, overlap_ratio))
    overlap_chars = int(max_chars * overlap_ratio)
    step_size = max_chars - overlap_chars

    chunks: List[str] = []
    start = 0

    while start < len(markdown):
        end = min(start + max_chars, len(markdown))

        # If this is the last chunk, just take the remaining text
        if end == len(markdown):
            chunks.append(markdown[start:])
            break

        # Find a good paragraph break near the end of the chunk
        slice_text = markdown[start:end]
        split_pos = slice_text.rfind("\n\n")

        # If no good paragraph break, try single newline
        if split_pos < len(slice_text) // 2:
            split_pos = slice_text.rfind("\n")

        # If still no newline, do hard split at max_chars
        if split_pos < len(slice_text) // 2:
            split_pos = len(slice_text)

        # Append the chunk
        chunk_end = start + split_pos
        chunks.append(markdown[start:chunk_end])

        # Move start forward by step_size (creating overlap)
        start += step_size

        # Adjust start to a newline boundary if possible (for cleaner overlap)
        if start < len(markdown):
            # Look for a newline within a small window
            window_start = max(start - 50, chunk_end)
            window_end = min(start + 50, len(markdown))
            window_text = markdown[window_start:window_end]
            newline_pos = window_text.find("\n")
            if newline_pos != -1:
                start = window_start + newline_pos + 1

    logger.info(
        "Split %s chars into %d overlapping chunks (overlap: %d chars, %.0f%%)",
        f"{len(markdown):,}", len(chunks), overlap_chars, overlap_ratio * 100,
    )
    return chunks


# --- Program Name Extraction ---

# Degree-type keywords that strongly indicate a program heading
_DEGREE_KEYWORDS_RE = re.compile(
    r"\b(?:MSc|MA|MBA|MPhil|MEng|MRes|MFA|MLitt|MChem|MComp|MMath"
    r"|BSc|BA|BEng|BBA|LLB|LLM|PhD|DPhil|EdD|DBA|PGDip|PGCert"
    r"|Master|Bachelor|Doctor|Diploma|Certificate"
    r"|Masters|Postgraduate|Undergraduate)\b",
    re.IGNORECASE,
)

# Headings that are obviously NOT program names (boilerplate / navigation)
_NOISE_HEADING_RE = re.compile(
    r"(?:cookie|privacy|navigation|menu|search|skip to|accept|"
    r"your .* options|tell us|changes to our|"
    r"related content|course terms|how to apply|"
    r"footer|header|breadcrumb|sidebar)",
    re.IGNORECASE,
)


def _parse_heading(line: str) -> tuple[int, str]:
    """Return (level, text) for a Markdown heading line, or (0, '')."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return 0, ""
    # Count the heading level
    level = 0
    for char in stripped:
        if char == "#":
            level += 1
        else:
            break
    # Must have a space after the '#' characters (standard Markdown)
    if 0 < level < len(stripped) and stripped[level] == " ":
        text = stripped[level:].strip()
        return level, text
    return 0, ""


def extract_program_name(markdown: str) -> str:
    """Extract the most likely program / course name from Markdown.

    Uses a multi-pass strategy:

    1. **Degree-keyword match** — scan all H1-H3 headings for degree
       keywords (MSc, BA, PhD …). Return the first match.
    2. **First clean H1** — if no keyword match, return the first H1
       that is not obvious boilerplate (cookie / privacy banners).
    3. **First clean H2** — same logic for H2.
    4. **Fallback** — return the first heading of any level, or ``""``.
    """
    headings: list[tuple[int, str]] = []
    for line in markdown.split("\n"):
        level, text = _parse_heading(line)
        if level > 0 and text:
            headings.append((level, text))

    if not headings:
        return ""

    # Pass 1: heading with a degree keyword (strongest signal)
    for level, text in headings:
        if level <= 3 and _DEGREE_KEYWORDS_RE.search(text):
            if not _NOISE_HEADING_RE.search(text):
                return text

    # Pass 2: first non-noise H1
    for level, text in headings:
        if level == 1 and not _NOISE_HEADING_RE.search(text):
            return text

    # Pass 3: first non-noise H2
    for level, text in headings:
        if level == 2 and not _NOISE_HEADING_RE.search(text):
            return text

    # Pass 4: absolute fallback — first heading that isn't noise
    for _level, text in headings:
        if not _NOISE_HEADING_RE.search(text):
            return text

    # Everything was noise — return first heading anyway
    return headings[0][1]
