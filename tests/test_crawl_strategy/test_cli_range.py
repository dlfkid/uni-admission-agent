import pytest

from src.cmd.cli import _resolve_crawl_range


def test_resolve_default_when_neither_given():
    r = _resolve_crawl_range(limit=None, all_=False)
    assert r.limit == 30 and r.paginate is False


def test_resolve_limit():
    r = _resolve_crawl_range(limit=200, all_=False)
    assert r.limit == 200 and r.paginate is True


def test_resolve_all():
    r = _resolve_crawl_range(limit=None, all_=True)
    assert r.limit is None and r.paginate is True


def test_resolve_rejects_both():
    with pytest.raises(ValueError):
        _resolve_crawl_range(limit=10, all_=True)
