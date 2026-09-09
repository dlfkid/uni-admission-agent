"""A bare unit name adopts the prefixed form its siblings already use.

CUHK 2027 stored 'Faculty of Social Science' for 27 programmes and a bare
'Social Science' for two others, from the same pages under the same faculty.
Nothing normalises this field — it is whatever the extraction returned — so
grouping or filtering by faculty silently splits one unit into two.

The rule only ever joins a bare name to a prefixed sibling seen in the same
batch. It never invents a prefix, because the correct one is a property of the
university, not of English: this corpus holds 'School of Energy and
Environment' (CityU), 'College of Science and Engineering' (Edinburgh) and
'Graduate School' (EdUHK) alongside CUHK's faculties.
"""
from __future__ import annotations

import pytest

from src.services.faculty_naming import canonicalize_faculty


def test_bare_name_adopts_the_prefixed_sibling() -> None:
    known = {"Faculty of Social Science", "Faculty of Law"}
    assert canonicalize_faculty("Social Science", known) == "Faculty of Social Science"


@pytest.mark.parametrize("prefixed", [
    "School of Energy and Environment",
    "College of Science and Engineering",
    "Academy of Innovation",
    "Division of Cardiology",
    "Institute of Chinese Studies",
])
def test_any_unit_word_counts_not_just_faculty(prefixed: str) -> None:
    bare = prefixed.split(" of ", 1)[1]
    assert canonicalize_faculty(bare, {prefixed}) == prefixed


def test_a_value_that_already_has_a_prefix_is_left_alone() -> None:
    """The guard against HKU's joint programmes.

    'Faculty of Arts' is a substring of 'Faculty of Arts / Faculty of Law',
    and a substring rule would rewrite a single-faculty programme into a
    joint one. Only prefix-less values are candidates.
    """
    known = {"Faculty of Arts / Faculty of Law", "Faculty of Arts"}
    assert canonicalize_faculty("Faculty of Arts", known) == "Faculty of Arts"


def test_ambiguity_is_left_alone() -> None:
    """Two units could claim the bare name; picking one would be a guess."""
    known = {"Faculty of Social Science", "School of Social Science"}
    assert canonicalize_faculty("Social Science", known) == "Social Science"


def test_no_sibling_means_no_change() -> None:
    assert canonicalize_faculty("Arts, Humanities and Cultures", {"Faculty of Social Sciences"}) == (
        "Arts, Humanities and Cultures"
    )


def test_matching_ignores_case_and_spacing_but_keeps_the_sibling_spelling() -> None:
    assert canonicalize_faculty("  social   science ", {"Faculty of Social Science"}) == (
        "Faculty of Social Science"
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_values_pass_through(value) -> None:
    assert canonicalize_faculty(value, {"Faculty of Social Science"}) == value


def test_the_bare_name_is_not_matched_against_itself() -> None:
    """A batch where every programme uses the bare form stays bare."""
    assert canonicalize_faculty("Social Science", {"Social Science"}) == "Social Science"
