"""Scope normalisation and headline derivation for tuition rows.

The page's wording for who pays what varies (UK / Home / International /
Overseas / EU / 本地); the filterable column has three values. The mapping
lives here, not in the LLM.
"""

import pytest

from src.models.admission import TuitionScope
from src.agents.tuition_headline import normalize_applicant_scope


@pytest.mark.parametrize("label", [
    "Local", "local students", "Home", "UK", "UK/EU", "UK and EU", "Home/EU", "Domestic", "本地", "本地学生",
])
def test_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.LOCAL


@pytest.mark.parametrize("label", [
    "Non-local", "Non-local Students", "International", "International, including EU",
    "Overseas", "EU", "非本地", "國際學生",
])
def test_non_local_wordings(label: str) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.NON_LOCAL


@pytest.mark.parametrize("label", [None, "", "  ", "for local and non-local students", "all applicants"])
def test_undistinguished_is_all(label) -> None:
    assert normalize_applicant_scope(label) is TuitionScope.ALL


def test_unknown_wording_maps_to_all_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert normalize_applicant_scope("Martian residents") is TuitionScope.ALL
    assert "Martian residents" in caplog.text


def test_uk_eu_before_brexit_is_local_but_bare_eu_is_non_local() -> None:
    """UK pages price EU with International today; the older 'UK/EU' pairing
    was one home band."""
    assert normalize_applicant_scope("UK/EU") is TuitionScope.LOCAL
    assert normalize_applicant_scope("EU") is TuitionScope.NON_LOCAL


from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

from src.models.admission import CurrencyCode, StudyMode, TuitionBasis
from src.agents.tuition_headline import derive_headline_tuition


@dataclass
class _Fee:
    amount: Decimal
    basis: TuitionBasis
    study_mode: StudyMode = StudyMode.UNKNOWN
    applicant_scope: TuitionScope = TuitionScope.ALL
    credits: Optional[int] = None
    currency: CurrencyCode = CurrencyCode.HKD


FT, PT, ANY = StudyMode.FULL_TIME, StudyMode.PART_TIME, StudyMode.UNKNOWN
ALL, LOCAL, NON_LOCAL = TuitionScope.ALL, TuitionScope.LOCAL, TuitionScope.NON_LOCAL
PROG, ANNUM, SEM, CREDIT = (TuitionBasis.PER_PROGRAMME, TuitionBasis.PER_ANNUM,
                            TuitionBasis.PER_SEMESTER, TuitionBasis.PER_CREDIT)


def _amt(result) -> Optional[int]:
    return int(result.amount) if result.amount is not None else None


# ── scope priority: non_local › all › local ───────────────────────────

def test_non_local_beats_local_when_both_present() -> None:            # EdUHK
    fees = [_Fee(Decimal(47000), ANNUM, ANY, LOCAL), _Fee(Decimal(198000), ANNUM, ANY, NON_LOCAL)]
    assert _amt(derive_headline_tuition(fees, [(FT, 12)])) == 198000


def test_all_is_used_when_no_non_local_row() -> None:                   # PolyU wording
    fees = [_Fee(Decimal(495000), PROG, ANY, ALL), _Fee(Decimal(1), PROG, ANY, LOCAL)]
    assert _amt(derive_headline_tuition(fees, [])) == 495000


def test_local_only_page_still_yields_a_headline() -> None:
    assert _amt(derive_headline_tuition([_Fee(Decimal(9790), ANNUM, ANY, LOCAL)], [])) == 9790


# ── mode priority: FullTime › Unknown › PartTime › Hybrid ─────────────

def test_full_time_beats_part_time() -> None:                            # CUHK
    fees = [_Fee(Decimal(198000), ANNUM, FT), _Fee(Decimal(99000), ANNUM, PT)]
    r = derive_headline_tuition(fees, [(FT, 12), (PT, 24)])
    assert _amt(r) == 198000


def test_undistinguished_mode_beats_part_time() -> None:
    fees = [_Fee(Decimal(300000), PROG, ANY), _Fee(Decimal(150000), PROG, PT)]
    assert _amt(derive_headline_tuition(fees, [])) == 300000


def test_part_time_only_page_yields_the_part_time_figure() -> None:
    assert _amt(derive_headline_tuition([_Fee(Decimal(99000), ANNUM, PT)], [(PT, 24)])) == 198000


# ── basis: programme total, else per annum × years, else per credit × credits ──

def test_programme_total_is_used_as_is_and_nothing_is_derived() -> None:  # HKBU
    r = derive_headline_tuition([_Fee(Decimal(180000), PROG)], [(FT, 12)])
    assert _amt(r) == 180000 and r.derived == ()


def test_programme_total_beats_a_per_credit_rate_on_the_same_page() -> None:  # PolyU
    fees = [_Fee(Decimal(495000), PROG), _Fee(Decimal(16500), CREDIT, credits=30)]
    assert _amt(derive_headline_tuition(fees, [])) == 495000


def test_per_annum_is_multiplied_by_whole_years_of_the_same_mode() -> None:   # CUHK 1yr FT / Manchester 2yr PT
    r = derive_headline_tuition([_Fee(Decimal(15800), ANNUM, FT)], [(FT, 12), (PT, 24)])
    assert _amt(r) == 15800
    # I2: a 1-year per-annum fee also yields a derived per_programme row, same
    # as the multi-year case — every headline is traceable to a per_programme
    # row, which keeps a 1-year programme visible to the default filter.
    assert len(r.derived) == 1 and r.derived[0].basis is PROG and r.derived[0].study_mode is FT
    assert r.derived[0].source_text == "derived: 15800 per annum × 1 year"

    r = derive_headline_tuition([_Fee(Decimal(99000), ANNUM, PT)], [(FT, 12), (PT, 24)])
    assert _amt(r) == 198000
    assert len(r.derived) == 1 and r.derived[0].basis is PROG and r.derived[0].study_mode is PT
    assert "per annum" in r.derived[0].source_text


def test_per_annum_rounds_duration_up_to_whole_years() -> None:
    r = derive_headline_tuition([_Fee(Decimal(10000), ANNUM, FT)], [(FT, 18)])
    assert _amt(r) == 20000


def test_per_annum_with_undistinguished_mode_uses_the_full_time_duration() -> None:  # UCL
    r = derive_headline_tuition([_Fee(Decimal(39200), ANNUM, ANY, NON_LOCAL)], [(FT, 36)])
    assert _amt(r) == 117600


def test_per_annum_without_any_duration_is_used_unconverted() -> None:
    r = derive_headline_tuition([_Fee(Decimal(198000), ANNUM, FT)], [])
    assert _amt(r) == 198000 and r.derived == ()


def test_per_credit_times_credits_when_no_total() -> None:
    r = derive_headline_tuition([_Fee(Decimal(9500), CREDIT, credits=30)], [])
    assert _amt(r) == 285000
    assert r.derived[0].basis is PROG and "per credit" in r.derived[0].source_text


def test_per_credit_without_a_credit_count_is_skipped() -> None:
    assert derive_headline_tuition([_Fee(Decimal(9500), CREDIT)], []).amount is None


def test_per_semester_never_feeds_the_headline() -> None:
    assert derive_headline_tuition([_Fee(Decimal(50000), SEM)], [(FT, 12)]).amount is None


def test_no_rows_gives_no_headline() -> None:
    r = derive_headline_tuition([], [])
    assert r.amount is None and r.currency is None and r.derived == ()


def test_currency_travels_with_the_chosen_row() -> None:
    r = derive_headline_tuition([_Fee(Decimal(17500), PROG, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [])
    assert r.currency is CurrencyCode.GBP


# ── the nine golden pages, as rows a correct extraction would produce ──

GOLDEN = {
    "hkbu": ([_Fee(Decimal(180000), PROG)], [(FT, 12)], 180000),
    "cuhk": ([_Fee(Decimal(180000), ANNUM, FT), _Fee(Decimal(90000), ANNUM, PT)], [(FT, 12), (PT, 24)], 180000),
    "eduhk": ([_Fee(Decimal(47000), ANNUM, ANY, LOCAL), _Fee(Decimal(198000), ANNUM, ANY, NON_LOCAL)], [(FT, 12)], 198000),
    "leeds": ([_Fee(Decimal(17500), PROG, ANY, LOCAL, currency=CurrencyCode.GBP),
               _Fee(Decimal(33000), PROG, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 12)], 33000),
    "manchester": ([_Fee(Decimal(15800), ANNUM, ANY, LOCAL, currency=CurrencyCode.GBP),
                    _Fee(Decimal(31000), ANNUM, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 12)], 31000),
    "ucl": ([_Fee(Decimal(9790), ANNUM, ANY, LOCAL, currency=CurrencyCode.GBP),
             _Fee(Decimal(39200), ANNUM, ANY, NON_LOCAL, currency=CurrencyCode.GBP)], [(FT, 36)], 117600),
    "polyu": ([_Fee(Decimal(495000), PROG), _Fee(Decimal(16500), CREDIT, credits=30)], [(FT, 12), (PT, 24)], 495000),
    "cityu_link_only": ([], [(FT, 12)], None),
    "edinburgh_living_costs_excluded": ([], [(FT, 48)], None),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_pages_produce_the_expected_headline(name: str) -> None:
    fees, options, expected = GOLDEN[name]
    assert _amt(derive_headline_tuition(fees, options)) == expected
