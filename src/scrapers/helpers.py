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


def extract_program_name(markdown: str) -> str:
    """
    Best-effort extraction of program name from Markdown.

    Looks for the first H1 or H2 heading as a heuristic.
    """
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
        if stripped.startswith("## "):
            return stripped.lstrip("# ").strip()
    return ""
