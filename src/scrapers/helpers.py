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
    markdown: str, max_chars: int,
) -> List[str]:
    """Split Markdown into chunks that fit within the LLM context window.

    Splits on double-newline (paragraph) boundaries to avoid cutting
    mid-link or mid-sentence. Falls back to hard split if no paragraph
    break is found within the chunk.

    Args:
        markdown: Full Markdown content.
        max_chars: Maximum characters per chunk.

    Returns:
        List of Markdown chunks, each ≤ max_chars.
    """
    if len(markdown) <= max_chars:
        return [markdown]

    chunks: List[str] = []
    remaining = markdown

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        # Find a paragraph break near the end of the chunk
        slice_end = remaining[:max_chars]
        split_pos = slice_end.rfind("\n\n")

        if split_pos < max_chars // 2:
            # No good paragraph break found — try single newline
            split_pos = slice_end.rfind("\n")

        if split_pos < max_chars // 2:
            # No newline at all — hard split
            split_pos = max_chars

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    logger.info(
        "Split %s chars into %d chunks",
        f"{len(markdown):,}", len(chunks),
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
