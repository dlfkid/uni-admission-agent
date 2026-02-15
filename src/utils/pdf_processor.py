"""
PDF-to-Markdown processor.

Uses pymupdf4llm to convert PDF files (local or remote) into
structured Markdown text, optimized for LLM consumption.
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

import httpx
import pymupdf4llm
from pydantic import BaseModel, Field

from src.core.environment import PDFProcessingError

logger = logging.getLogger(__name__)

# Default cache directory for downloaded PDFs
_PDF_CACHE_DIR = Path(tempfile.gettempdir()) / "uni_admission" / "pdf_cache"

# Safety limit for large PDFs
DEFAULT_MAX_PAGES = 50


class PDFConversionResult(BaseModel):
    """Result of converting a PDF to Markdown."""

    source: str = Field(..., description="Original source path or URL")
    markdown: str = Field(..., description="Extracted Markdown content")
    page_count: int = Field(..., description="Number of pages processed")
    char_count: int = Field(..., description="Length of the Markdown output")


class PDFProcessor:
    """
    Converts PDF files to Markdown for LLM consumption.

    Supports local file paths and remote URLs. Remote PDFs are
    downloaded to a temp cache before processing.
    """

    def __init__(self, max_pages: int = DEFAULT_MAX_PAGES) -> None:
        self.max_pages = max_pages

    def convert_to_markdown(
        self,
        source: Union[Path, str],
        max_pages: Optional[int] = None,
    ) -> PDFConversionResult:
        """
        Convert a PDF to Markdown.

        Args:
            source: Local file path (Path or str) or remote URL (str).
            max_pages: Override the default max pages limit.

        Returns:
            PDFConversionResult with Markdown content and metadata.

        Raises:
            PDFProcessingError: If the PDF cannot be read or converted.
        """
        page_limit = max_pages or self.max_pages
        source_str = str(source)

        # Determine if source is a URL or local file
        if source_str.startswith(("http://", "https://")):
            local_path = self._download_pdf(source_str)
        else:
            local_path = Path(source_str)

        if not local_path.exists():
            raise PDFProcessingError(f"PDF file not found: {local_path}")

        if not local_path.suffix.lower() == ".pdf":
            raise PDFProcessingError(
                f"Not a PDF file: {local_path.name}"
            )

        return self._convert_local(local_path, source_str, page_limit)

    def _download_pdf(self, url: str) -> Path:
        """Download a PDF from a URL to the cache directory."""
        _PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Generate a safe filename from the URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "download.pdf"
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        cache_path = _PDF_CACHE_DIR / filename

        # Skip download if already cached
        if cache_path.exists():
            logger.info("Using cached PDF: %s", cache_path)
            return cache_path

        logger.info("Downloading PDF: %s", url)

        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()

                cache_path.write_bytes(response.content)
                logger.info(
                    "Downloaded %s bytes to %s",
                    f"{len(response.content):,}",
                    cache_path,
                )
                return cache_path

        except httpx.HTTPError as e:
            raise PDFProcessingError(
                f"Failed to download PDF from {url}: {e}"
            ) from e

    def _convert_local(
        self,
        file_path: Path,
        original_source: str,
        page_limit: int,
    ) -> PDFConversionResult:
        """Convert a local PDF file to Markdown."""
        import pymupdf

        try:
            # Open to check page count
            doc = pymupdf.open(str(file_path))
            total_pages = len(doc)
            doc.close()

            # Determine pages to process
            pages_to_process: Optional[list[int]] = None
            if total_pages > page_limit:
                logger.warning(
                    "PDF has %d pages, exceeding limit of %d. "
                    "Only processing first %d pages.",
                    total_pages, page_limit, page_limit,
                )
                pages_to_process = list(range(page_limit))

            actual_page_count = min(total_pages, page_limit)

            # Convert to Markdown
            logger.info(
                "Converting PDF: %s (%d/%d pages)",
                file_path.name, actual_page_count, total_pages,
            )

            raw_output = pymupdf4llm.to_markdown(
                str(file_path),
                pages=pages_to_process,
            )

            # pymupdf4llm.to_markdown() returns str by default,
            # but its type stub declares str | list[dict].
            # We always get str when not using page_chunks=True.
            markdown: str = raw_output if isinstance(raw_output, str) else str(raw_output)

            char_count = len(markdown)
            logger.info(
                "PDF conversion complete: %s chars from %d pages",
                f"{char_count:,}", actual_page_count,
            )

            return PDFConversionResult(
                source=original_source,
                markdown=markdown,
                page_count=actual_page_count,
                char_count=char_count,
            )

        except PDFProcessingError:
            raise
        except Exception as e:
            raise PDFProcessingError(
                f"Failed to convert PDF {file_path.name}: {e}"
            ) from e
