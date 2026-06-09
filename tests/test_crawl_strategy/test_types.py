from src.services.crawl_strategy.types import (
    FetchMode, ExtractKind, Strategy, ExtractItem, FetchResult, CrawlOutcome,
)


def test_strategy_holds_axes_and_params():
    s = Strategy(fetch=FetchMode.CLIENT_WAIT, extract=ExtractKind.TEXT_HEADING,
                 params={"wait_selector": ".card"})
    assert s.fetch is FetchMode.CLIENT_WAIT
    assert s.extract is ExtractKind.TEXT_HEADING
    assert s.params["wait_selector"] == ".card"


def test_extract_item_name_and_url():
    item = ExtractItem(name_en="AI for Business MSc", detail_url="https://x/y")
    assert item.name_en == "AI for Business MSc"
    assert item.detail_url == "https://x/y"


def test_crawl_outcome_defaults():
    out = CrawlOutcome(status="unsupported", university="nus")
    assert out.status == "unsupported"
    assert out.names == []
    assert out.report_zip is None


def test_json_api_extract_kind_exists():
    from src.services.crawl_strategy.types import ExtractKind
    assert ExtractKind.JSON_API.value == "json_api"
