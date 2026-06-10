import pytest

from src.api.schemas import CrawlRequest


def _base(**kw):
    return {"url": "https://x.edu/p", "univ_slug": "xuni", "year": 2026, **kw}


def test_crawl_request_accepts_limit():
    assert CrawlRequest(**_base(limit=50)).limit == 50


def test_crawl_request_accepts_crawl_all():
    assert CrawlRequest(**_base(crawl_all=True)).crawl_all is True


def test_crawl_request_defaults():
    req = CrawlRequest(**_base())
    assert req.limit is None and req.crawl_all is False


def test_crawl_request_rejects_both():
    with pytest.raises(ValueError):
        CrawlRequest(**_base(limit=5, crawl_all=True))
