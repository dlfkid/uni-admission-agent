"""Tests for the institution label derived from an index URL.

The label names phenomenon reports and the ``crawl-index`` output. It is not a
storage key — the crawl path takes the slug from ``--name`` — but it must be
recognisable and, above all, must not collide between institutions.
"""

import pytest

from src.services.crawl_strategy.orchestrator import _university_slug


# ── every host already pinned or captured as a golden sample ──────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://courses.leeds.ac.uk/course-search/masters-courses", "leeds"),
        ("https://www.ucl.ac.uk/prospective-students/undergraduate/degrees", "ucl"),
        ("https://www.manchester.ac.uk/study/masters/courses/list/", "manchester"),
        ("https://www.polyu.edu.hk/study/pg/taught-postgraduate", "polyu"),
        ("https://www.cityu.edu.hk/pg/taught-postgraduate-programmes/list", "cityu"),
        ("https://study.nus.edu.sg/programmes", "nus"),
        ("https://www.gs.cuhk.edu.hk/programme-filter", "cuhk"),
        ("https://www.eduhk.hk/acadprog/postgrad/index.html", "eduhk"),
        ("https://www.ln.edu.hk/sgs/programmes-on-offer", "ln"),
    ],
)
def test_known_hosts_resolve_to_the_institution(url: str, expected: str) -> None:
    assert _university_slug(url) == expected


# ── the 2026-27 HK batch ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://portal.hku.hk/tpg-admissions/programme-listing", "hku"),
        ("https://prog-crs.hkust.edu.hk/pgprog/2025-26", "hkust"),
        ("https://ar.hkbu.edu.hk/tpg-admissions/programmes", "hkbu"),
        ("https://admissions.hkmu.edu.hk/tpg/programmes/", "hkmu"),
        ("https://gs.hksyu.edu/en/Programmes/TaughtMasterProgramme/x", "hksyu"),
        ("https://gs.hsu.edu.hk/hk/programmes", "hsu"),
        ("https://www.speed-polyu.edu.hk/programme/polyuspeedawards", "speed-polyu"),
        ("https://thei.edu.hk/admission/postgraduate/", "thei"),
    ],
)
def test_new_targets_resolve_to_the_institution(url: str, expected: str) -> None:
    assert _university_slug(url) == expected


def test_speed_polyu_is_not_confused_with_polyu() -> None:
    """Different institutions that merely share a substring must stay distinct."""
    assert _university_slug("https://www.speed-polyu.edu.hk/x") != _university_slug(
        "https://www.polyu.edu.hk/x"
    )


# ── the collision this replaced ───────────────────────────────────────


def test_hosts_sharing_a_subdomain_no_longer_collide() -> None:
    """CUHK, HSU and HKSYU all sit behind a ``gs.`` subdomain. Deriving the
    label from the left collapsed all three onto ``gs``, so three different
    universities shared one report name."""
    slugs = {
        _university_slug("https://www.gs.cuhk.edu.hk/programme-filter"),
        _university_slug("https://gs.hsu.edu.hk/hk/programmes"),
        _university_slug("https://gs.hksyu.edu/en/Programmes/x"),
    }
    assert slugs == {"cuhk", "hsu", "hksyu"}
    assert "gs" not in slugs


# ── degenerate input ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://hkbu.edu.hk/x", "hkbu"),          # no subdomain at all
        ("https://ln.hk/x", "ln"),                   # two-letter name, would be
        ("https://localhost:8910/x", "localhost"),   # ...stripped if unguarded
        ("https://EXAMPLE.EDU.HK/x", "example"),     # case-insensitive
    ],
)
def test_degenerate_hosts_still_yield_something_usable(url: str, expected: str) -> None:
    assert _university_slug(url) == expected


def test_an_unparseable_url_does_not_raise() -> None:
    assert _university_slug("not-a-url") == "not-a-url" or _university_slug("not-a-url") == ""
