"""Deciding what two claims mean for each other.

The rule this module exists to enforce: a difference between two numbers is not a
contradiction until the system has looked for an explanation and failed to find one.
So `compare` walks the dimensions a fact actually has — who, what, in what unit, over
what period, under what scope — and only reaches VALUE_CONFLICT when every one of
them matches and the values still disagree.

`compare` is pure and never calls a model. Verdicts have to be reproducible, and a
verdict that changes because a free tier was busy is not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config
from .normalize import is_currency, normalize_value_text
from .store import Claim

CORROBORATES = "CORROBORATES"
CONTRADICTS = "CONTRADICTS"
RECONCILABLE = "RECONCILABLE"
UNRELATED = "UNRELATED"


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason_code: str
    dimension_diff: dict[str, Any]


def compare(
    a: Claim,
    b: Claim,
    *,
    tolerance: float | None = None,
    percent_tolerance: float | None = None,
) -> Verdict:
    tolerance = config.value_tolerance() if tolerance is None else tolerance
    if percent_tolerance is None:
        percent_tolerance = config.percent_tolerance()

    diff: dict[str, Any] = {
        "entity": {"a": a.entity, "b": b.entity},
        "metric": {"a": a.metric, "b": b.metric},
    }

    if a.id is not None and a.id == b.id:
        return Verdict(UNRELATED, "SAME_CLAIM", diff)
    if a.kind != b.kind:
        return Verdict(UNRELATED, "KIND_MISMATCH", diff)
    if a.entity_canonical != b.entity_canonical:
        return Verdict(UNRELATED, "ENTITY_MISMATCH", diff)
    if a.metric_canonical != b.metric_canonical:
        return Verdict(UNRELATED, "METRIC_MISMATCH", diff)

    if a.kind == "attribute":
        return _compare_attributes(a, b, diff)
    return _compare_measurements(a, b, diff, tolerance, percent_tolerance)


# ------------------------------------------------------------------ measurements


def _compare_measurements(a, b, diff, tolerance, percent_tolerance) -> Verdict:
    diff["unit"] = {"a": a.unit, "b": b.unit}

    if a.value_num is None or b.value_num is None:
        return Verdict(UNRELATED, "INSUFFICIENT_DATA", diff)

    if a.unit != b.unit:
        if is_currency(a.unit) and is_currency(b.unit):
            # Comparing across currencies needs the exchange rate of the right day.
            # Calling this a contradiction would be a fabrication.
            return Verdict(RECONCILABLE, "CURRENCY_MISMATCH", diff)
        if a.unit is None or b.unit is None:
            # An unlabelled number must never be used to accuse a labelled one.
            return Verdict(RECONCILABLE, "UNIT_UNKNOWN", diff)
        return Verdict(UNRELATED, "UNIT_DIMENSION_MISMATCH", diff)

    period = _compare_periods(a, b)
    diff["period"] = period
    if period["relation"] == "disjoint":
        return Verdict(UNRELATED, "PERIOD_DISJOINT", diff)
    if period["relation"] != "same":
        # One period sits inside the other, so the numbers are measuring different
        # spans of time and are expected to differ.
        return Verdict(RECONCILABLE, "PERIOD_MISMATCH", diff)

    diff["value"] = _compare_values(a, b, tolerance, percent_tolerance)
    if diff["value"]["agrees"]:
        return Verdict(CORROBORATES, "VALUE_AGREEMENT", diff)

    scope = _compare_scopes(a, b)
    diff["scope"] = scope
    if scope["conflicts"]:
        # Consolidated vs standalone, provisional vs revised, estimate vs actual:
        # the documents are not talking about quite the same thing.
        return Verdict(RECONCILABLE, "SCOPE_MISMATCH", diff)

    return Verdict(CONTRADICTS, "VALUE_CONFLICT", diff)


def _compare_values(a, b, tolerance, percent_tolerance) -> dict[str, Any]:
    difference = abs(a.value_num - b.value_num)
    if a.unit == "percent":
        # Percentages are compared in percentage points. Comparing 6.4% and 6.5%
        # relatively would call a real disagreement a rounding difference.
        return {
            "a": a.value_num, "b": b.value_num,
            "absolute_difference": difference,
            "agrees": difference <= percent_tolerance,
        }
    scale = max(abs(a.value_num), abs(b.value_num))
    relative = difference / scale if scale else 0.0
    return {
        "a": a.value_num, "b": b.value_num,
        "relative_difference": relative,
        "agrees": relative <= tolerance,
    }


def _compare_periods(a, b) -> dict[str, Any]:
    result = {"a": a.period_label, "b": b.period_label}
    if a.period_start is None or b.period_start is None:
        result["relation"] = "same" if a.period_label == b.period_label else "unknown"
        if result["relation"] == "unknown":
            result["relation"] = "same"  # nothing to distinguish them by
        return result

    if (a.period_start, a.period_end) == (b.period_start, b.period_end):
        result["relation"] = "same"
    elif _contains(a, b):
        result["relation"] = "a_contains_b"
    elif _contains(b, a):
        result["relation"] = "b_contains_a"
    elif _overlaps(a, b):
        result["relation"] = "overlapping"
    else:
        result["relation"] = "disjoint"
    return result


def _contains(outer, inner) -> bool:
    return outer.period_start <= inner.period_start and inner.period_end <= outer.period_end


def _overlaps(a, b) -> bool:
    return a.period_start <= b.period_end and b.period_start <= a.period_end


def _compare_scopes(a, b) -> dict[str, Any]:
    """Scope is whatever qualifiers the documents themselves attach to a fact. It is
    compared generically: any key both sides state, with different values, is a
    difference in what is being measured. No fixed vocabulary, so an unfamiliar
    qualifier in an unfamiliar document still works."""
    conflicts: dict[str, list[str]] = {}
    for key in set(a.scope) & set(b.scope):
        left, right = str(a.scope[key]), str(b.scope[key])
        if normalize_value_text(left) != normalize_value_text(right):
            conflicts[key] = [left, right]
    return {
        "conflicts": conflicts,
        "only_a": {k: a.scope[k] for k in set(a.scope) - set(b.scope)},
        "only_b": {k: b.scope[k] for k in set(b.scope) - set(a.scope)},
    }


# -------------------------------------------------------------------- attributes


def _compare_attributes(a, b, diff) -> Verdict:
    scope = _compare_scopes(a, b)
    diff["scope"] = scope
    if scope["conflicts"]:
        # The same role at two different organisations, for instance.
        return Verdict(RECONCILABLE, "SCOPE_MISMATCH", diff)

    diff["value"] = {"a": a.value_text, "b": b.value_text}
    if normalize_value_text(a.value_text or "") == normalize_value_text(b.value_text or ""):
        return Verdict(CORROBORATES, "ATTRIBUTE_AGREEMENT", diff)

    stated = a.period_start is not None and b.period_start is not None
    if stated:
        diff["period"] = {"a": a.period_label, "b": b.period_label}
        if _overlaps(a, b):
            return Verdict(CONTRADICTS, "ATTRIBUTE_VALUE_CONFLICT", diff)
        return Verdict(RECONCILABLE, "TEMPORAL_SUCCESSION", diff)

    if a.doc_date and b.doc_date and a.doc_date != b.doc_date:
        # Attributes change: a director resigns, an address moves. The older document
        # is not wrong, it is older. Marked as inferred because the documents did not
        # actually state validity dates.
        diff["doc_date"] = {"a": a.doc_date.isoformat(), "b": b.doc_date.isoformat()}
        return Verdict(RECONCILABLE, "TEMPORAL_SUCCESSION_INFERRED", diff)

    return Verdict(CONTRADICTS, "ATTRIBUTE_VALUE_CONFLICT", diff)
