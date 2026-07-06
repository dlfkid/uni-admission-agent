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
    # CityU TPG list: one page, table rows with /programme/program-list/ URLs.
    # Tuition lives on a per-programme sub-page; detail crawl handles it.
    "www.cityu.edu.hk": Strategy(
        FetchMode.CLIENT, ExtractKind.CITYU_TABLE,
        paginate=PaginateMode.NONE),
    # NUS serves its full catalogue from a guest Salesforce Apex endpoint in one
    # POST (searchProgrammes, empty filters) — fetchable server-side, no browser.
    # The classname carries an internal Salesforce ID that may change on a NUS
    # redeploy; if it does, the api fetch yields unusable content and the normal
    # "known strategy failed" report fires so a developer can update it here.
    "study.nus.edu.sg": Strategy(
        FetchMode.API, ExtractKind.JSON_API,
        params={
            "endpoint": "https://study.nus.edu.sg/webruntime/api/apex/execute"
                        "?language=en-US&asGuest=true&htmlEncode=false",
            "body": {"namespace": "", "classname": "@udd/01pIW000000Rkpx",
                     "method": "searchProgrammes", "isContinuation": False,
                     "params": {"programmeType": "", "interestArea": "[]",
                                "keyword": "", "modeOfStudy": "", "facultyIds": "",
                                "intakePeriod": ""},
                     "cacheable": False},
            "items_path": "returnValue",
            "name_path": "programme.Title__c",
            "detail_url_path": "programme.Program_Page_Link__c",
        },
        paginate=PaginateMode.NONE),
}


def lookup(index_url: str) -> Optional[Strategy]:
    """Return the pinned Strategy for *index_url*'s host, or None if unknown."""
    host = urlsplit(str(index_url or "").strip()).netloc.lower()
    return REGISTRY.get(host)
