"""Choosing what to put on the front page.

The assignment asks for one example of each of four cases. Picking them by hand
would be hard-coding facts, and would break the moment someone uploads their own
PDFs. So they are chosen by rule from whatever is stored, and the rules prefer the
examples that make the strongest evidence: across two documents rather than one,
and explained by a real dimension rather than a bare difference in period.
"""

from __future__ import annotations

from datetime import date

import pytest

from factlayer.store import Store

from factories import make_claim


def seeded(*, documents=("doc-a", "doc-b")):
    store = Store()
    for index, doc_id in enumerate(documents):
        store.add_document(
            filename=f"{doc_id}.pdf", sha256=f"hash-{doc_id}", page_count=1,
            status="complete", title=doc_id.title(),
            doc_date=date(2022 + index, 6, 30),
        )
    return store


def doc_ids(store):
    return [d["id"] for d in store.documents()]


def stored(store, *claims):
    return store.add_claims(claims)


def link(store, a, b, verdict, reason, explanation="because"):
    store.add_relations([{
        "claim_a": a.id, "claim_b": b.id, "verdict": verdict,
        "reason_code": reason, "dimension_diff": {}, "explanation": explanation,
        "decided_by": "rules",
    }])


def test_an_empty_layer_offers_nothing_and_does_not_fail():
    """A fresh deployment has no documents yet. The front page must still render."""
    picks = Store().showcase()
    assert set(picks) == {"corroborated", "contradiction", "explained", "failure"}
    assert all(value is None for value in picks.values())


def test_each_case_is_picked_from_stored_relations():
    store = seeded()
    a_id, b_id = doc_ids(store)
    agree_a, agree_b, clash_a, clash_b, scoped_a, scoped_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="revenue", value_num=100.0),
        make_claim(doc_id=b_id, metric_canonical="revenue", value_num=100.0, page_no=2),
        make_claim(doc_id=a_id, metric_canonical="headcount", value_num=10.0),
        make_claim(doc_id=b_id, metric_canonical="headcount", value_num=20.0, page_no=2),
        make_claim(doc_id=a_id, metric_canonical="margin", value_num=1.0),
        make_claim(doc_id=b_id, metric_canonical="margin", value_num=2.0, page_no=2),
    )
    link(store, agree_a, agree_b, "CORROBORATES", "VALUE_AGREEMENT")
    link(store, clash_a, clash_b, "CONTRADICTS", "VALUE_CONFLICT")
    link(store, scoped_a, scoped_b, "RECONCILABLE", "SCOPE_MISMATCH")

    picks = store.showcase()
    assert picks["corroborated"]["reason_code"] == "VALUE_AGREEMENT"
    assert picks["contradiction"]["reason_code"] == "VALUE_CONFLICT"
    assert picks["explained"]["reason_code"] == "SCOPE_MISMATCH"


def test_a_cross_document_example_beats_a_within_document_one():
    """Two documents agreeing is the interesting case; one document agreeing with
    itself is not what the assignment is asking to see."""
    store = seeded()
    a_id, b_id = doc_ids(store)
    same_a, same_b, cross_a, cross_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=1),
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=2),
        make_claim(doc_id=a_id, metric_canonical="beta", value_num=1.0, page_no=3),
        make_claim(doc_id=b_id, metric_canonical="beta", value_num=1.0, page_no=4),
    )
    link(store, same_a, same_b, "CORROBORATES", "VALUE_AGREEMENT")
    link(store, cross_a, cross_b, "CORROBORATES", "VALUE_AGREEMENT")

    picked = store.showcase()["corroborated"]
    assert {picked["claim_a"], picked["claim_b"]} == {cross_a.id, cross_b.id}


def test_a_within_document_example_is_used_when_there_is_nothing_better():
    """One PDF uploaded on its own should still show something."""
    store = seeded(documents=("only",))
    (doc,) = doc_ids(store)
    a, b = stored(
        store,
        make_claim(doc_id=doc, metric_canonical="alpha", value_num=1.0, page_no=1),
        make_claim(doc_id=doc, metric_canonical="alpha", value_num=1.0, page_no=2),
    )
    link(store, a, b, "CORROBORATES", "VALUE_AGREEMENT")
    assert store.showcase()["corroborated"] is not None


def test_a_difference_explained_by_scope_beats_one_explained_by_period():
    """Both are reconcilable, but 'consolidated versus standalone' shows the engine
    reasoning, where 'a year versus its own fourth quarter' is nearly arithmetic."""
    store = seeded()
    a_id, b_id = doc_ids(store)
    period_a, period_b, scope_a, scope_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=1),
        make_claim(doc_id=b_id, metric_canonical="alpha", value_num=2.0, page_no=2),
        make_claim(doc_id=a_id, metric_canonical="beta", value_num=1.0, page_no=3),
        make_claim(doc_id=b_id, metric_canonical="beta", value_num=2.0, page_no=4),
    )
    link(store, period_a, period_b, "RECONCILABLE", "PERIOD_MISMATCH")
    link(store, scope_a, scope_b, "RECONCILABLE", "SCOPE_MISMATCH")

    assert store.showcase()["explained"]["reason_code"] == "SCOPE_MISMATCH"


def test_a_role_change_over_time_is_preferred_over_a_bare_period_difference():
    store = seeded()
    a_id, b_id = doc_ids(store)
    period_a, period_b, role_a, role_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=1),
        make_claim(doc_id=b_id, metric_canonical="alpha", value_num=2.0, page_no=2),
        make_claim(doc_id=a_id, kind="attribute", metric_canonical="role",
                   value_num=None, value_text="Director", page_no=3),
        make_claim(doc_id=b_id, kind="attribute", metric_canonical="role",
                   value_num=None, value_text="Resigned", page_no=4),
    )
    link(store, period_a, period_b, "RECONCILABLE", "PERIOD_MISMATCH")
    link(store, role_a, role_b, "RECONCILABLE", "TEMPORAL_SUCCESSION_INFERRED")

    assert store.showcase()["explained"]["reason_code"] == "TEMPORAL_SUCCESSION_INFERRED"


def test_the_failure_case_comes_from_quarantine():
    """Case four is an extraction failure the system caught, so it is drawn from the
    claims grounding rejected rather than from the relations."""
    store = seeded()
    a_id, _ = doc_ids(store)
    stored(store, make_claim(doc_id=a_id, status="quarantined",
                             quarantine_reason="The quoted sentence is not on the page it cites."))
    failure = store.showcase()["failure"]
    assert failure["status"] == "quarantined"
    assert failure["quarantine_reason"]


def test_a_quarantined_claim_without_a_reason_is_not_shown():
    """The point of the failure card is the explanation, not the failure."""
    store = seeded()
    a_id, _ = doc_ids(store)
    stored(store, make_claim(doc_id=a_id, status="quarantined", quarantine_reason=None))
    assert store.showcase()["failure"] is None


def test_picks_are_stable_between_calls():
    """The front page must not shuffle on every reload."""
    store = seeded()
    a_id, b_id = doc_ids(store)
    for index in range(5):
        a, b = stored(
            store,
            make_claim(doc_id=a_id, metric_canonical=f"m{index}", value_num=1.0, page_no=index + 1),
            make_claim(doc_id=b_id, metric_canonical=f"m{index}", value_num=1.0, page_no=index + 1),
        )
        link(store, a, b, "CORROBORATES", "VALUE_AGREEMENT")

    first = Store().showcase()["corroborated"]
    second = Store().showcase()["corroborated"]
    assert first["id"] == second["id"]


def test_a_verdict_with_no_examples_is_reported_as_missing():
    store = seeded()
    a_id, b_id = doc_ids(store)
    a, b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=1),
        make_claim(doc_id=b_id, metric_canonical="alpha", value_num=1.0, page_no=2),
    )
    link(store, a, b, "CORROBORATES", "VALUE_AGREEMENT")

    picks = store.showcase()
    assert picks["corroborated"] is not None
    assert picks["contradiction"] is None
    assert picks["explained"] is None


def test_a_contradiction_across_pages_beats_one_inside_a_single_table():
    """Two rows of one table disagreeing is usually the extractor losing which row
    it was on. The same claim made differently on two separate pages is far more
    likely to be a real disagreement, so that is what gets shown."""
    store = seeded()
    a_id, _ = doc_ids(store)
    row_a, row_b, page_a, page_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=46),
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=2.0, page_no=46,
                   char_start=200, char_end=260),
        make_claim(doc_id=a_id, metric_canonical="beta", value_num=1.0, page_no=30),
        make_claim(doc_id=a_id, metric_canonical="beta", value_num=2.0, page_no=85),
    )
    link(store, row_a, row_b, "CONTRADICTS", "VALUE_CONFLICT")
    link(store, page_a, page_b, "CONTRADICTS", "VALUE_CONFLICT")

    picked = store.showcase()["contradiction"]
    assert {picked["claim_a"], picked["claim_b"]} == {page_a.id, page_b.id}


def test_crossing_documents_still_outranks_crossing_pages():
    store = seeded()
    a_id, b_id = doc_ids(store)
    same_a, same_b, cross_a, cross_b = stored(
        store,
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=1.0, page_no=30),
        make_claim(doc_id=a_id, metric_canonical="alpha", value_num=2.0, page_no=85),
        make_claim(doc_id=a_id, metric_canonical="beta", value_num=1.0, page_no=7),
        make_claim(doc_id=b_id, metric_canonical="beta", value_num=2.0, page_no=7),
    )
    link(store, same_a, same_b, "CONTRADICTS", "VALUE_CONFLICT")
    link(store, cross_a, cross_b, "CONTRADICTS", "VALUE_CONFLICT")

    picked = store.showcase()["contradiction"]
    assert {picked["claim_a"], picked["claim_b"]} == {cross_a.id, cross_b.id}
