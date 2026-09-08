"""Folding two names into one.

Models coin a slightly different key for the same thing on different pages, so
near-duplicates are merged. Merging is not symmetric in cost: under-merging leaves
two facts that never meet, while over-merging invents a contradiction between facts
that were never about the same thing. So the guard errs towards leaving them apart.
"""

from __future__ import annotations

import pytest

from factlayer.extract import _canonical


def merge(proposed, known):
    return _canonical(proposed, proposed, known)


@pytest.mark.parametrize(
    "proposed,known,expected",
    [
        ("revenue_from_operation", ["revenue_from_operations"], "revenue_from_operations"),
        ("total_incomes", ["total_income"], "total_income"),
        ("ebitda_margins", ["ebitda_margin"], "ebitda_margin"),
    ],
)
def test_a_spelling_variant_folds_into_the_name_already_used(proposed, known, expected):
    assert merge(proposed, known) == expected


@pytest.mark.parametrize(
    "proposed,known",
    [
        # Found in the real corpus: one letter apart, 97% similar, and completely
        # different measures. Merging them reported a contradiction between the two.
        ("waste_intensity_per_rupee_of_turnover", ["water_intensity_per_rupee_of_turnover"]),
        ("net_profit", ["net_profits_after_tax"]),
        ("import_duty", ["export_duty"]),
        ("current_assets", ["current_liabilities"]),
    ],
)
def test_two_different_measures_are_never_folded_together(proposed, known):
    assert merge(proposed, known) == proposed


def test_names_whose_digits_differ_stay_apart():
    """A key carrying a year is a key for a different thing."""
    assert merge("revenue_fy24", ["revenue_fy25"]) == "revenue_fy24"


def test_an_unseen_name_is_kept_as_it_is():
    assert merge("brand_new_metric", ["revenue_from_operations"]) == "brand_new_metric"
    assert merge("brand_new_metric", []) == "brand_new_metric"
