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
from .normalize import (
    is_currency, normalize_value_text, readable, readable_metric, readable_name,
)
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
    if period["relation"] in {"a_contains_b", "b_contains_a", "overlapping"}:
        # One period sits inside the other, so the numbers are measuring different
        # spans of time and are expected to differ.
        return Verdict(RECONCILABLE, "PERIOD_MISMATCH", diff)

    # Agreement is checked before the period is used to withhold judgement: two
    # figures that match are corroboration whether or not they are dated.
    diff["value"] = _compare_values(a, b, tolerance, percent_tolerance)
    if diff["value"]["agrees"]:
        return Verdict(CORROBORATES, "VALUE_AGREEMENT", diff)

    if _mirrored(a.value_num, b.value_num, tolerance):
        # Financial statements bracket a figure to show it is being deducted, so the
        # same amount reaches the layer as 3,032.19 on one page and (3,032.19) on
        # another. That is one figure presented two ways, not two claims in conflict.
        return Verdict(RECONCILABLE, "SIGN_CONVENTION", diff)

    if period["relation"] == "undeclared":
        # A running total states no period; an individual transaction states a date.
        # Comparing them as though they covered the same span invents a conflict.
        return Verdict(RECONCILABLE, "PERIOD_UNDECLARED", diff)

    scope = _compare_scopes(a, b)
    diff["scope"] = scope
    if scope["conflicts"]:
        # Consolidated vs standalone, provisional vs revised, estimate vs actual:
        # the documents are not talking about quite the same thing.
        return Verdict(RECONCILABLE, "SCOPE_MISMATCH", diff)
    if scope["only_a"] or scope["only_b"]:
        # One document qualifies the figure and the other is simply silent. Silence
        # is not agreement, and calling this a contradiction claims more than the
        # evidence supports.
        return Verdict(RECONCILABLE, "SCOPE_UNDECLARED", diff)

    return Verdict(CONTRADICTS, "VALUE_CONFLICT", diff)


def _mirrored(left: float, right: float, tolerance: float) -> bool:
    """The same magnitude carrying opposite signs."""
    if left == 0 or right == 0 or (left > 0) == (right > 0):
        return False
    scale = max(abs(left), abs(right))
    return abs(abs(left) - abs(right)) / scale <= tolerance


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
        # At least one side never said what span it covers, so there is no basis for
        # claiming they cover the same one.
        result["relation"] = "undeclared"
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

    if scope["only_a"] or scope["only_b"]:
        return Verdict(RECONCILABLE, "SCOPE_UNDECLARED", diff)

    return Verdict(CONTRADICTS, "ATTRIBUTE_VALUE_CONFLICT", diff)


# ------------------------------------------------------------------ explanations


def describe(a: Claim, b: Claim, verdict: Verdict) -> str:
    """A plain-English reason built from the rules alone.

    Always available, even with every provider rate-limited, so a relation is never
    shown without a reason. A model may rewrite this later; it may not change it.
    """
    subject = f"{readable_name(a.entity)} · {readable(a.metric)}"
    left, right = _render(a), _render(b)
    reason = verdict.reason_code

    if reason == "VALUE_AGREEMENT":
        return (f"Both documents report {subject} for {a.period_label or 'the same period'} "
                f"as {left} and {right}. Once magnitudes and units are normalised these "
                f"are the same figure.")
    if reason == "VALUE_CONFLICT":
        return (f"{subject} for {a.period_label or 'the same period'} is reported as "
                f"{left} in one document and {right} in the other. Entity, metric, period, "
                f"unit and stated scope all match, so nothing explains the difference.")
    if reason == "PERIOD_MISMATCH":
        return (f"These cover different periods — {a.period_label} versus {b.period_label} — "
                f"so {left} and {right} are not expected to match.")
    if reason == "SCOPE_MISMATCH":
        pairs = "; ".join(
            f"{key}: {values[0]} versus {values[1]}"
            for key, values in verdict.dimension_diff["scope"]["conflicts"].items()
        )
        return (f"The documents qualify this differently ({pairs}), so {left} and "
                f"{right} are not directly comparable and this is not a disagreement.")
    if reason == "SIGN_CONVENTION":
        return (f"{left} and {right} are the same amount with opposite signs. In a "
                f"financial statement a bracketed figure is one being deducted, so "
                f"this is one number presented two ways rather than a disagreement.")
    if reason == "PERIOD_UNDECLARED":
        stated = a.period_label or b.period_label
        return (f"{left} and {right} are both reported for {subject}, but "
                + (f"only one of them states a period ({stated}). "
                   if stated else "neither states a period. ")
                + "Without a shared period there is no basis for calling this a "
                  "disagreement.")
    if reason == "SCOPE_UNDECLARED":
        stated = {**verdict.dimension_diff["scope"]["only_a"],
                  **verdict.dimension_diff["scope"]["only_b"]}
        named = "; ".join(f"{key}: {value}" for key, value in stated.items())
        return (f"One document qualifies this ({named}) and the other does not. "
                f"{left} and {right} may be measuring different things, so this is "
                f"not treated as a disagreement.")
    if reason == "CURRENCY_MISMATCH":
        return (f"Reported in different currencies ({a.unit} and {b.unit}). Comparing "
                f"{left} with {right} needs an exchange rate for the period, which the "
                f"documents do not give.")
    if reason == "UNIT_UNKNOWN":
        return (f"One of these figures has no stated unit ({left} versus {right}), so they "
                f"cannot safely be compared.")
    if reason == "ATTRIBUTE_AGREEMENT":
        return (f"Both documents state {readable_name(a.entity)}'s "
                f"{readable_metric(a.metric)} as {a.value_text}.")
    if reason == "TEMPORAL_SUCCESSION":
        return (f"{readable_name(a.entity)}'s {readable_metric(a.metric)} is stated as "
                f"'{a.value_text}' for {a.period_label} and '{b.value_text}' for "
                f"{b.period_label}. The value changed over time.")
    if reason == "TEMPORAL_SUCCESSION_INFERRED":
        older, newer = (a, b) if (a.doc_date and b.doc_date and a.doc_date < b.doc_date) else (b, a)
        return (f"{readable_name(a.entity)}'s {readable_metric(a.metric)} is "
                f"'{older.value_text}' in the older document "
                f"({older.doc_date}) and '{newer.value_text}' in the newer one "
                f"({newer.doc_date}). Read as a change over time rather than a "
                f"disagreement — neither document states validity dates, so this is "
                f"inferred from publication dates.")
    if reason == "ATTRIBUTE_VALUE_CONFLICT":
        return (f"{readable_name(a.entity)}'s {readable_metric(a.metric)} is given as "
                f"'{a.value_text}' and '{b.value_text}' for the same time, and both "
                f"cannot hold.")
    return f"{subject}: {reason.lower().replace('_', ' ')}."


def _render(claim: Claim) -> str:
    if claim.value_num is None:
        return f"'{claim.value_text}'"
    written = f"{claim.value_text}" if claim.value_text else ""
    unit = "" if claim.unit == "percent" else f" {claim.unit or ''}".rstrip()
    figure = f"{claim.value_num:,.4g}{unit}" if claim.unit != "percent" else f"{claim.value_num:g}%"
    return f"{written} ({figure})" if written else figure
