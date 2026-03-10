import json
from pathlib import Path

import pytest

from src.services.page_type_resolution import classify_page_type_auto
from src.scrapers.link_parser import extract_links_with_text


def _load_manifest() -> dict:
    return json.loads(Path("golden_samples/manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_manifest()["cases"], ids=lambda c: c["case_id"])
def test_auto_detects_golden_index_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "index.md").read_text(encoding="utf-8")
    link_count = len(extract_links_with_text(markdown, case["index_url"]))
    decision = classify_page_type_auto(
        url=case["index_url"],
        markdown=markdown,
        html="",
        link_count=link_count,
        router=None,
    )
    assert decision.page_type == "index", f"{case['case_id']} index misclassified as {decision.page_type}"


@pytest.mark.parametrize("case", _load_manifest()["cases"], ids=lambda c: c["case_id"])
def test_auto_detects_golden_detail_pages(case: dict) -> None:
    case_dir = Path("golden_samples/cases") / case["case_id"]
    markdown = (case_dir / "detail.md").read_text(encoding="utf-8")
    link_count = len(extract_links_with_text(markdown, case["detail_url"]))
    decision = classify_page_type_auto(
        url=case["detail_url"],
        markdown=markdown,
        html="",
        link_count=link_count,
        router=None,
    )
    assert decision.page_type == "detail", f"{case['case_id']} detail misclassified as {decision.page_type}"
