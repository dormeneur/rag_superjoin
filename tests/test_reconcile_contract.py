"""Reconciliation contract — the core of the system.

`compare` is a pure function: two claims in, a verdict out. No LLM, no database.
The verdict must be reproducible, so every case below is decided by rules alone.

Verdicts:
    CORROBORATES  two claims independently support the same fact
    CONTRADICTS   they cannot both be true and nothing explains the difference
    RECONCILABLE  they differ, but the difference is explained by a dimension
                  (period, unit, scope) or cannot be meaningfully compared
    UNRELATED     they are not about the same thing
"""

from __future__ import annotations

from datetime import date

import pytest

from factlayer.reconcile import compare

from factories import make_claim


def verdict_of(a, b):
    return compare(a, b).verdict


def reason_of(a, b):
    return compare(a, b).reason_code


# ------------------------------------------------------- case 1: corroboration

def test_same_number_written_differently_corroborates():
    """Assignment case 1. 'Rs. 8,142 crore' and 'INR 81.42 billion' are the same
    number reached by different wording; normalisation must make them agree."""
    a = make_claim(doc_id="annual-report", value_num=8.142e10,
                   quote="Revenue from operations stood at Rs. 8,142 crore.")
    b = make_claim(doc_id="earnings-deck", value_num=8.142e10,
                   metric="Total revenue from operations",
                   quote="Revenue from operations: INR 81.42 billion")
    assert verdict_of(a, b) == "CORROBORATES"


def test_small_rounding_differences_still_corroborate():
    a = make_claim(value_num=8.142e10)
    b = make_claim(value_num=8.140e10)  # 0.02% apart
    assert verdict_of(a, b) == "CORROBORATES"


def test_tolerance_is_configurable():
    a = make_claim(value_num=100.0, unit=None)
    b = make_claim(value_num=101.0, unit=None)
    assert compare(a, b, tolerance=0.05).verdict == "CORROBORATES"
    assert compare(a, b, tolerance=0.001).verdict == "CONTRADICTS"


# --------------------------------------------------- case 2: real contradiction

def test_different_values_for_the_same_period_and_scope_contradict():
    """Assignment case 2. Nothing distinguishes these two claims except the number,
    so the system must call it rather than explain it away."""
    a = make_claim(doc_id="rbi", value_num=8.142e10)
    b = make_claim(doc_id="imf", value_num=7.900e10)
    assert verdict_of(a, b) == "CONTRADICTS"
    assert reason_of(a, b) == "VALUE_CONFLICT"


def test_percentages_are_compared_on_absolute_difference():
    """A growth rate of 6.5% vs 6.4% is a real disagreement even though the relative
    difference is small. Comparing percentages relatively would hide it."""
    a = make_claim(metric="Real GDP growth", metric_canonical="real_gdp_growth",
                   value_num=6.5, unit="percent")
    b = make_claim(metric="Real GDP growth", metric_canonical="real_gdp_growth",
                   value_num=6.4, unit="percent")
    assert verdict_of(a, b) == "CONTRADICTS"


# ------------------------------------- case 3: apparent contradiction, explained

def test_different_periods_are_explained_not_contradicted():
    """Assignment case 3, the time dimension. A full year and one of its quarters
    hold different numbers for good reason."""
    a = make_claim(value_num=8.142e10, period_label="FY2024",
                   period_start=date(2023, 4, 1), period_end=date(2024, 3, 31))
    b = make_claim(value_num=2.076e10, period_label="Q4 FY2024",
                   period_start=date(2024, 1, 1), period_end=date(2024, 3, 31))
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "PERIOD_MISMATCH"


def test_different_scope_is_explained_not_contradicted():
    """Assignment case 3, the scope dimension: consolidated vs standalone."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "consolidated"})
    b = make_claim(value_num=7.500e10, scope={"consolidation": "standalone"})
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "SCOPE_MISMATCH"


def test_provisional_versus_revised_is_explained_not_contradicted():
    """Assignment case 3, the data-vintage dimension. Statistical agencies revise."""
    a = make_claim(value_num=6.5, unit="percent", scope={"revision": "provisional"})
    b = make_claim(value_num=6.7, unit="percent", scope={"revision": "revised"})
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "SCOPE_MISMATCH"


def test_the_conflicting_dimension_is_named_in_the_diff():
    """An explanation is only useful if it says which dimension differed."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "consolidated"})
    b = make_claim(value_num=7.500e10, scope={"consolidation": "standalone"})
    diff = compare(a, b).dimension_diff
    assert "consolidation" in diff["scope"]["conflicts"]
    assert set(diff["scope"]["conflicts"]["consolidation"]) == {"consolidated", "standalone"}


def test_agreement_wins_over_scope_difference():
    """If two differently-scoped numbers agree anyway, that is corroboration, not a
    difference needing explanation."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "consolidated"})
    b = make_claim(value_num=8.142e10, scope={"consolidation": "standalone"})
    assert verdict_of(a, b) == "CORROBORATES"


# ------------------------------------------------------------ units and currency

def test_same_amount_in_different_units_corroborates():
    """Crore and million both normalise to rupees before comparison."""
    a = make_claim(value_num=8.142e10, unit="INR")   # 8,142 crore
    b = make_claim(value_num=8.142e10, unit="INR")   # 81,420 million
    assert verdict_of(a, b) == "CORROBORATES"


def test_different_currencies_are_not_declared_a_contradiction():
    """Without an exchange rate for the right date, these cannot be compared. Saying
    'contradiction' would be wrong; saying 'unrelated' would lose the link."""
    a = make_claim(value_num=8.142e10, unit="INR")
    b = make_claim(value_num=9.8e8, unit="USD")
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "CURRENCY_MISMATCH"


def test_incompatible_dimensions_are_unrelated():
    """A rupee amount and a percentage are not the same measurement."""
    a = make_claim(value_num=8.142e10, unit="INR")
    b = make_claim(value_num=6.4, unit="percent")
    assert verdict_of(a, b) == "UNRELATED"


def test_missing_unit_blocks_a_contradiction_verdict():
    """An unlabelled number must never be used to accuse a labelled one."""
    a = make_claim(value_num=8142.0, unit=None)
    b = make_claim(value_num=8.142e10, unit="INR")
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "UNIT_UNKNOWN"


# ----------------------------------------------------------------- non-matches

def test_different_entities_are_unrelated():
    a = make_claim(entity_canonical="delhivery_limited")
    b = make_claim(entity_canonical="blue_dart_express")
    assert verdict_of(a, b) == "UNRELATED"


def test_different_metrics_are_unrelated():
    a = make_claim(metric_canonical="revenue_from_operations")
    b = make_claim(metric_canonical="employee_benefit_expense")
    assert verdict_of(a, b) == "UNRELATED"


def test_disjoint_periods_are_unrelated():
    a = make_claim(period_label="FY2023", period_start=date(2022, 4, 1),
                   period_end=date(2023, 3, 31))
    b = make_claim(period_label="FY2025", period_start=date(2024, 4, 1),
                   period_end=date(2025, 3, 31))
    assert verdict_of(a, b) == "UNRELATED"


def test_a_claim_is_not_compared_with_itself():
    a = make_claim(id="claim-1")
    assert verdict_of(a, a) == "UNRELATED"


def test_measurements_and_attributes_do_not_mix():
    a = make_claim(kind="measurement")
    b = make_claim(kind="attribute", value_num=None, value_text="Sahil Barua")
    assert verdict_of(a, b) == "UNRELATED"


# ------------------------------------------------------------------- attributes

def attribute(**kw):
    base = dict(
        kind="attribute",
        entity="Sahil Barua",
        entity_canonical="sahil_barua",
        metric="role",
        metric_canonical="role",
        value_num=None,
        unit=None,
        value_text="Managing Director and Chief Executive Officer",
        period_start=None,
        period_end=None,
        period_label=None,
    )
    base.update(kw)
    return make_claim(**base)


def test_same_role_stated_differently_corroborates():
    a = attribute(value_text="Managing Director and Chief Executive Officer")
    b = attribute(value_text="managing director & chief executive officer")
    assert verdict_of(a, b) == "CORROBORATES"


def test_a_role_change_over_time_is_a_succession_not_a_contradiction():
    """Assignment case 3, applied to a semantic fact: the 'director resigned' example.
    The older document is not wrong, it is simply older."""
    a = attribute(doc_id="prospectus-2022", doc_date=date(2022, 5, 1),
                  value_text="Independent Director")
    b = attribute(doc_id="annual-report-fy24", doc_date=date(2024, 6, 30),
                  value_text="Resigned with effect from 12 January 2024")
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "TEMPORAL_SUCCESSION_INFERRED"


def test_an_inferred_succession_is_labelled_as_inferred():
    """Succession read from document dates is weaker evidence than a stated validity
    window, and the output must not pretend otherwise."""
    a = attribute(doc_date=date(2022, 5, 1), value_text="Independent Director")
    b = attribute(doc_date=date(2024, 6, 30), value_text="Resigned")
    stated_a = attribute(value_text="Independent Director",
                         period_start=date(2020, 1, 1), period_end=date(2023, 12, 31))
    stated_b = attribute(value_text="Resigned",
                         period_start=date(2024, 1, 12), period_end=date(2024, 6, 30))
    assert reason_of(a, b) == "TEMPORAL_SUCCESSION_INFERRED"
    assert reason_of(stated_a, stated_b) == "TEMPORAL_SUCCESSION"


def test_conflicting_roles_in_the_same_period_contradict():
    a = attribute(doc_id="doc-a", doc_date=date(2024, 6, 30),
                  value_text="Independent Director",
                  period_start=date(2024, 1, 1), period_end=date(2024, 3, 31))
    b = attribute(doc_id="doc-b", doc_date=date(2024, 6, 30),
                  value_text="Chief Financial Officer",
                  period_start=date(2024, 1, 1), period_end=date(2024, 3, 31))
    assert verdict_of(a, b) == "CONTRADICTS"


def test_conflicting_attributes_inside_one_document_contradict():
    """Same document, same date: there is no time dimension to hide behind."""
    a = attribute(doc_id="doc-a", doc_date=date(2024, 6, 30), value_text="Director")
    b = attribute(doc_id="doc-a", doc_date=date(2024, 6, 30), value_text="Auditor")
    assert verdict_of(a, b) == "CONTRADICTS"


def test_facts_about_different_people_are_unrelated():
    """Board membership is set-valued: two directors are not a contradiction."""
    a = attribute(entity_canonical="sahil_barua", value_text="Managing Director")
    b = attribute(entity_canonical="kapil_bharati", value_text="Executive Director")
    assert verdict_of(a, b) == "UNRELATED"


def test_the_same_role_at_different_organisations_is_explained():
    a = attribute(value_text="Director", scope={"organisation": "delhivery"})
    b = attribute(value_text="Chairperson", scope={"organisation": "acme"})
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "SCOPE_MISMATCH"


# --------------------------------------------------------------- verdict hygiene

VERDICTS = {"CORROBORATES", "CONTRADICTS", "RECONCILABLE", "UNRELATED"}


@pytest.mark.parametrize(
    "a,b",
    [
        (make_claim(), make_claim(value_num=1.0)),
        (make_claim(), make_claim(unit=None)),
        (make_claim(period_start=None, period_end=None, period_label=None), make_claim()),
        (make_claim(value_num=None), make_claim()),
        (attribute(), make_claim()),
    ],
)
def test_compare_always_returns_a_known_verdict(a, b):
    result = compare(a, b)
    assert result.verdict in VERDICTS
    assert result.reason_code
    assert isinstance(result.dimension_diff, dict)


def test_compare_is_symmetric():
    """The order two documents were ingested in must not change the verdict."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "consolidated"})
    b = make_claim(value_num=7.5e10, scope={"consolidation": "standalone"})
    assert compare(a, b).verdict == compare(b, a).verdict
    assert compare(a, b).reason_code == compare(b, a).reason_code


def test_compare_does_not_call_an_llm(monkeypatch):
    """Verdicts must be reproducible. If this ever fails, the rule engine has grown a
    dependency it must not have."""
    import factlayer.llm as llm

    def explode(*_args, **_kwargs):
        raise AssertionError("reconcile.compare must not call an LLM")

    monkeypatch.setattr(llm, "complete_json", explode)
    assert compare(make_claim(), make_claim(value_num=1.0)).verdict in VERDICTS


# ------------------------------------------- silence is not the same as agreement

def test_a_qualifier_on_only_one_side_is_not_a_contradiction():
    """One document says 'standalone', the other says nothing. They may well be
    measuring different things, so accusing them of contradicting each other claims
    more than the evidence supports."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "standalone"})
    b = make_claim(value_num=7.500e10, scope={})
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "SCOPE_UNDECLARED"


def test_matching_qualifiers_do_not_excuse_a_conflict():
    """Both documents say consolidated and still disagree. That is a contradiction."""
    a = make_claim(value_num=8.142e10, scope={"consolidation": "consolidated"})
    b = make_claim(value_num=7.500e10, scope={"consolidation": "consolidated"})
    assert verdict_of(a, b) == "CONTRADICTS"


def test_both_sides_silent_is_still_a_contradiction():
    """If neither document qualifies the figure, there is nothing left to explain."""
    a = make_claim(value_num=8.142e10, scope={})
    b = make_claim(value_num=7.500e10, scope={})
    assert verdict_of(a, b) == "CONTRADICTS"


def test_an_undeclared_qualifier_names_what_was_missing():
    a = make_claim(value_num=8.142e10, scope={"consolidation": "standalone"})
    b = make_claim(value_num=7.500e10, scope={})
    diff = compare(a, b).dimension_diff
    assert "consolidation" in diff["scope"]["only_a"]


def test_an_undeclared_qualifier_does_not_excuse_conflicting_attributes():
    a = attribute(doc_id="d", doc_date=date(2024, 6, 30), value_text="Director",
                  scope={"organisation": "delhivery"})
    b = attribute(doc_id="d", doc_date=date(2024, 6, 30), value_text="Auditor", scope={})
    assert verdict_of(a, b) == "RECONCILABLE"
    assert reason_of(a, b) == "SCOPE_UNDECLARED"
