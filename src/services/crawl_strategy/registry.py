"""Domain-pinned strategy registry for the crawl-strategy subsystem."""
from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urlsplit

from src.services.crawl_strategy.types import (
    ExtractKind, FetchMode, PaginateMode, Strategy,
)

# Domain (host) -> pinned, proven Strategy. Adding a university = add a row
# here + a golden sample + (if needed) a new extractor. Never touch
# orchestration code.
REGISTRY: Dict[str, Strategy] = {
    "courses.leeds.ac.uk": Strategy(
        FetchMode.SERVER, ExtractKind.HEADING_LINK,
        params={"page_param": "page", "page_start": 1},
        paginate=PaginateMode.URL_PAGES),
    "www.ucl.ac.uk": Strategy(
        FetchMode.CLIENT, ExtractKind.INLINE_DEGREE,
        paginate=PaginateMode.NONE),
    "www.manchester.ac.uk": Strategy(
        FetchMode.CLIENT, ExtractKind.MERGED_COLUMNS,
        paginate=PaginateMode.NONE),
    "www.polyu.edu.hk": Strategy(
        FetchMode.CLIENT, ExtractKind.BLOB,
        paginate=PaginateMode.NONE),
    "study.nus.edu.sg": Strategy(
        FetchMode.CLIENT_WAIT, ExtractKind.TEXT_HEADING,
        paginate=PaginateMode.SCROLL),
}


def lookup(index_url: str) -> Optional[Strategy]:
    """Return the pinned Strategy for *index_url*'s host, or None if unknown."""
    host = urlsplit(str(index_url or "").strip()).netloc.lower()
    return REGISTRY.get(host)
