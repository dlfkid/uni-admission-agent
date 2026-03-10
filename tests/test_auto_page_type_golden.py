import json
from pathlib import Path

import pytest

from src.models.scraper_models import PageType
from src.scrapers.link_parser import detect_page_type, extract_links_with_text


def _load_manifest() -> dict:
    return json.loads(Path("golden_samples/manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_manifest()["cases"], ids=lambda c: c["case_id"])
def test_auto_detects_golden_index_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "index.md").read_text(encoding="utf-8")
    link_count = len(extract_links_with_text(markdown, case["index_url"]))
    detected = detect_page_type(markdown=markdown, link_count=link_count, page_url=case["index_url"])
    assert detected == PageType.INDEX, f"{case['case_id']} index misclassified as {detected}"


@pytest.mark.parametrize("case", _load_manifest()["cases"], ids=lambda c: c["case_id"])
def test_auto_detects_golden_detail_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "detail.md").read_text(encoding="utf-8")
    link_count = len(extract_links_with_text(markdown, case["detail_url"]))
    detected = detect_page_type(markdown=markdown, link_count=link_count, page_url=case["detail_url"])
    assert detected == PageType.DETAIL, f"{case['case_id']} detail misclassified as {detected}"
