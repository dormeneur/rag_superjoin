"""Grounding is a property of the whole system, not of one function.

Whatever the extractor does, however the prompt changes, and whichever provider
answers, these must hold for every claim the system is willing to show:

  * the quote is a literal substring of the page it is attributed to
  * the character offsets select exactly that quote
  * the number being asserted actually appears inside the quote

These are the checks that make "linked to evidence" a fact rather than a claim.
"""

from __future__ import annotations

import re

from factlayer.pipeline import ingest
from factlayer.store import Store

PAGES = [
    "Revenue from operations was Rs. 8,142 crore in FY24.",
    "Real GDP growth for FY24 is estimated at 6.5 per cent.",
    "Sahil Barua is the Managing Director and Chief Executive Officer.",
]

RESPONSES = {
    "8,142 crore": [
        {
            "kind": "measurement",
            "entity": "Delhivery Limited",
            "entity_canonical": "delhivery_limited",
            "metric": "Revenue from operations",
            "metric_canonical": "revenue_from_operations",
            "value": "Rs. 8,142 crore",
            "period": "FY24",
            "scope": {},
            "quote": PAGES[0],
            "confidence": 0.92,
        }
    ],
    "6.5 per cent": [
        {
            "kind": "measurement",
            "entity": "India",
            "entity_canonical": "india",
            "metric": "Real GDP growth",
            "metric_canonical": "real_gdp_growth",
            "value": "6.5 per cent",
            "period": "FY24",
            "scope": {},
            "quote": PAGES[1],
            "confidence": 0.88,
        }
    ],
    "Sahil Barua": [
        {
            "kind": "attribute",
            "entity": "Sahil Barua",
            "entity_canonical": "sahil_barua",
            "metric": "role",
            "metric_canonical": "role",
            "value_text": "Managing Director and Chief Executive Officer",
            "scope": {"organisation": "Delhivery Limited"},
            "quote": PAGES[2],
            "confidence": 0.9,
        }
    ],
}


def digits(text: str) -> list[str]:
    return re.findall(r"\d", text.replace(",", ""))


def test_every_active_claim_is_grounded_in_its_page(fake_llm, pdf_bytes):
    fake_llm(RESPONSES)
    result = ingest(pdf_bytes(PAGES), "mixed.pdf")
    store = Store()
    claims = store.claims(doc_id=result.document_id, status="active")

    assert claims, "the fixture should produce claims to check"
    for claim in claims:
        page_text = store.page_text(claim.doc_id, claim.page_no)
        assert claim.quote in page_text, f"claim {claim.id} quotes text not on its page"
        assert page_text[claim.char_start : claim.char_end] == claim.quote


def test_every_numeric_claim_has_its_digits_in_its_quote(fake_llm, pdf_bytes):
    fake_llm(RESPONSES)
    result = ingest(pdf_bytes(PAGES), "mixed.pdf")
    for claim in Store().claims(doc_id=result.document_id, status="active"):
        if claim.value_num is None:
            continue
        quote_digits = "".join(digits(claim.quote))
        significant = "".join(digits(claim.value_text or ""))[:3]
        assert significant and significant in quote_digits


def test_every_active_claim_names_a_real_page(fake_llm, pdf_bytes):
    fake_llm(RESPONSES)
    result = ingest(pdf_bytes(PAGES), "mixed.pdf")
    store = Store()
    (document,) = store.documents()
    for claim in store.claims(doc_id=result.document_id, status="active"):
        assert 1 <= claim.page_no <= document["page_count"]


def test_every_relation_points_at_stored_claims(fake_llm, pdf_bytes):
    fake_llm(RESPONSES)
    ingest(pdf_bytes(PAGES), "a.pdf")
    ingest(pdf_bytes([p.replace("8,142", "7,900") for p in PAGES]), "b.pdf")
    store = Store()
    known = {c.id for c in store.claims(status="active")}
    for relation in store.relations():
        assert relation["claim_a"] in known
        assert relation["claim_b"] in known


def test_evidence_survives_a_restart(fake_llm, pdf_bytes):
    """Evidence must be re-checkable later, so page text is persisted alongside the
    claims rather than held in memory during ingestion."""
    fake_llm(RESPONSES)
    result = ingest(pdf_bytes(PAGES), "mixed.pdf")
    fresh = Store()  # a new connection, as a later request would open
    for claim in fresh.claims(doc_id=result.document_id, status="active"):
        assert claim.quote in fresh.page_text(claim.doc_id, claim.page_no)
