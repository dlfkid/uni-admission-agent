import json
from pathlib import Path

from src.services.crawl_strategy.json_extractors import (
    _dig, json_is_usable, make_json_api_extractor,
)

_SAMPLE = json.dumps({"returnValue": [
    {"programme": {"Title__c": "Master of Science in Data Science",
                   "Program_Page_Link__c": "https://x.edu/ds"}},
    {"programme": {"Title__c": "Bachelor of L&#39;Arts",
                   "Program_Page_Link__c": "https://x.edu/arts"}},
    {"programme": {"Title__c": "Master of Science in Data Science",
                   "Program_Page_Link__c": "https://x.edu/dup"}},   # dup name
    {"programme": {"Title__c": "",
                   "Program_Page_Link__c": "https://x.edu/empty"}},  # no name
    {"programme": {"Program_Page_Link__c": "https://x.edu/missing"}},  # no title key
]})

_PATHS = ("returnValue", "programme.Title__c", "programme.Program_Page_Link__c")


def test_dig_nested():
    assert _dig({"a": {"b": {"c": 1}}}, "a.b.c") == 1


def test_dig_missing_returns_none():
    assert _dig({"a": {}}, "a.b.c") is None
    assert _dig({"a": 5}, "a.b") is None


def test_json_is_usable():
    assert json_is_usable(_SAMPLE, "returnValue") is True
    assert json_is_usable(json.dumps({"returnValue": []}), "returnValue") is False
    assert json_is_usable("not json at all", "returnValue") is False
    assert json_is_usable("", "returnValue") is False


def test_extractor_names_urls_unescape_dedup_skip():
    ext = make_json_api_extractor(*_PATHS)
    items = ext(_SAMPLE, "https://study.nus.edu.sg/programme")
    names = [i.name_en for i in items]
    # dup name dropped, empty + missing skipped, &#39; unescaped to '
    assert names == ["Master of Science in Data Science", "Bachelor of L'Arts"]
    assert items[0].detail_url == "https://x.edu/ds"


def test_extractor_handles_invalid_json():
    ext = make_json_api_extractor(*_PATHS)
    assert ext("not json", "https://x.edu") == []


_FIXTURE = (Path(__file__).parent.parent.parent
            / "golden_samples" / "cases" / "nus_api" / "response.json")


def test_golden_fixture_real_structure():
    ext = make_json_api_extractor(*_PATHS)
    items = ext(_FIXTURE.read_text(encoding="utf-8"), "https://study.nus.edu.sg/programme")
    assert len(items) >= 200
    assert all(i.name_en for i in items)
