"""End-to-end pipeline behaviour, with no network and no API key.

The pipeline contract:
  * ingesting the same bytes twice must not produce a second document
  * ingesting a new document must not disturb claims already stored
  * a claim whose quote is not in the source page must never end up active
  * unreadable input must fail with a clear error, not a traceback
"""

from __future__ import annotations

import pytest

from factlayer.pipeline import IngestError, ingest
from factlayer.store import Store

REVENUE_PAGE = "Revenue from operations was Rs. 8,142 crore in FY24."
GROWTH_PAGE = "Real GDP growth for FY24 is estimated at 6.5 per cent."


def revenue_claim(quote=REVENUE_PAGE, value="Rs. 8,142 crore"):
    return [
        {
            "kind": "measurement",
            "entity": "Delhivery Limited",
            "entity_canonical": "delhivery_limited",
            "metric": "Revenue from operations",
            "metric_canonical": "revenue_from_operations",
            "value": value,
            "period": "FY24",
            "scope": {},
            "quote": quote,
            "confidence": 0.9,
        }
    ]


def test_ingest_extracts_grounded_claims(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": revenue_claim()})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "annual-report.pdf")

    assert result.status == "complete"
    assert result.claims_extracted == 1
    assert result.claims_quarantined == 0

    (claim,) = Store().claims(doc_id=result.document_id)
    assert claim.value_num == pytest.approx(8.142e10)
    assert claim.unit == "INR"
    assert claim.period_label == "FY2024"
    assert claim.page_no == 1


def test_the_llm_supplies_language_and_the_code_supplies_arithmetic(fake_llm, pdf_bytes):
    """The model is asked for the verbatim string, never for a converted number, so a
    model that is bad at arithmetic cannot corrupt a value."""
    fake_llm({"8,142 crore": revenue_claim()})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    (claim,) = Store().claims(doc_id=result.document_id)
    assert claim.value_num == pytest.approx(8.142e10)


def test_reingesting_the_same_file_is_a_no_op(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": revenue_claim()})
    data = pdf_bytes([REVENUE_PAGE])

    first = ingest(data, "annual-report.pdf")
    second = ingest(data, "annual-report-copy.pdf")

    assert second.status == "duplicate"
    assert second.document_id == first.document_id
    store = Store()
    assert len(store.documents()) == 1
    assert len(store.claims()) == 1


def test_a_new_document_does_not_disturb_existing_claims(fake_llm, pdf_bytes):
    """Incremental ingestion: the knowledge layer grows, it is never rebuilt."""
    fake_llm(
        {
            "8,142 crore": revenue_claim(),
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
                    "quote": GROWTH_PAGE,
                    "confidence": 0.9,
                }
            ],
        }
    )
    first = ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    before = {c.id: c.value_num for c in Store().claims(doc_id=first.document_id)}

    ingest(pdf_bytes([GROWTH_PAGE]), "b.pdf")
    after = {c.id: c.value_num for c in Store().claims(doc_id=first.document_id)}

    assert before == after


def test_cross_document_relations_are_created_on_ingest(fake_llm, pdf_bytes):
    conflicting = "Revenue from operations was Rs. 7,900 crore in FY24."
    fake_llm(
        {
            "8,142 crore": revenue_claim(),
            "7,900 crore": revenue_claim(quote=conflicting, value="Rs. 7,900 crore"),
        }
    )
    ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    result = ingest(pdf_bytes([conflicting]), "b.pdf")

    assert result.relations_created >= 1
    verdicts = [r["verdict"] for r in Store().relations()]
    assert "CONTRADICTS" in verdicts


def test_relations_are_not_duplicated_when_a_third_document_arrives(fake_llm, pdf_bytes):
    conflicting = "Revenue from operations was Rs. 7,900 crore in FY24."
    third = "Revenue from operations totalled Rs. 8,142 crore in FY24."
    fake_llm(
        {
            "totalled": revenue_claim(quote=third),
            "7,900": revenue_claim(quote=conflicting, value="Rs. 7,900 crore"),
            "was Rs. 8,142": revenue_claim(),
        }
    )
    ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    ingest(pdf_bytes([conflicting]), "b.pdf")
    pairs = {(r["claim_a"], r["claim_b"]) for r in Store().relations()}

    ingest(pdf_bytes([third]), "c.pdf")
    new_pairs = {(r["claim_a"], r["claim_b"]) for r in Store().relations()}

    assert pairs <= new_pairs, "existing relations must survive a later ingest"
    assert len(new_pairs) == len(Store().relations()), "no duplicate rows"


# ------------------------------------------------------- grounding is enforced

def test_a_quote_that_is_not_in_the_document_is_quarantined(fake_llm, pdf_bytes):
    """The model inventing a sentence is the failure mode that matters most."""
    fake_llm({"8,142 crore": revenue_claim(quote="Revenue was Rs. 9,999 crore.")})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")

    assert result.claims_extracted == 0
    assert result.claims_quarantined == 1
    assert Store().claims(status="active") == []
    (quarantined,) = Store().claims(status="quarantined")
    assert quarantined.quarantine_reason


def test_a_value_missing_from_its_own_quote_is_quarantined(fake_llm, pdf_bytes):
    """The quote is real but does not contain the number attributed to it."""
    fake_llm({"8,142 crore": revenue_claim(value="Rs. 9,999 crore")})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")

    assert result.claims_quarantined == 1
    assert Store().claims(status="active") == []


def test_quarantined_claims_are_kept_for_inspection(fake_llm, pdf_bytes):
    """Failures are evidence about the system, so they are stored, not dropped."""
    fake_llm({"8,142 crore": revenue_claim(quote="invented sentence")})
    ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    assert len(Store().claims(status="quarantined")) == 1


def test_quarantined_claims_never_form_relations(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": revenue_claim(quote="invented sentence")})
    ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    ingest(pdf_bytes([REVENUE_PAGE.replace("8,142", "7,900")]), "b.pdf")
    assert Store().relations() == []


# ------------------------------------------------------------ malformed input

def test_bytes_that_are_not_a_pdf_are_rejected_clearly():
    with pytest.raises(IngestError):
        ingest(b"this is not a pdf", "notes.txt")


def test_a_truncated_pdf_is_rejected_clearly(pdf_bytes):
    data = pdf_bytes([REVENUE_PAGE])
    with pytest.raises(IngestError):
        ingest(data[: len(data) // 3], "truncated.pdf")


def test_a_pdf_with_no_text_layer_is_rejected_clearly(pdf_bytes):
    """A scanned PDF needs OCR, which this system does not do. It must say so rather
    than silently returning zero facts."""
    with pytest.raises(IngestError, match="text"):
        ingest(pdf_bytes([" "]), "scan.pdf")


def test_an_empty_upload_is_rejected_clearly():
    with pytest.raises(IngestError):
        ingest(b"", "empty.pdf")


# ------------------------------------------------------------ llm misbehaviour

def test_unparseable_model_output_does_not_abort_the_document(fake_llm, pdf_bytes):
    """One bad batch must cost one batch, not the whole document."""
    fake_llm({REVENUE_PAGE[:20]: "this is not json at all"})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    assert result.status in {"complete", "partial"}


def test_a_claim_missing_required_fields_is_skipped(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": [{"kind": "measurement", "entity": "Delhivery Limited"}]})
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    assert result.claims_extracted == 0


def test_no_provider_available_leaves_the_document_partial(monkeypatch, pdf_bytes):
    """All free tiers rate-limited at once is a normal Tuesday. The document is
    recorded so ingestion can be resumed, and the failure is visible."""
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "")
    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    assert result.status == "partial"
    assert Store().documents()[0]["status"] == "partial"


# ------------------------------------------------------------------- resuming

def test_a_partial_document_is_resumed_on_re_upload(fake_llm, pdf_bytes, monkeypatch):
    """A free tier that ran out mid-document must not cost the whole document. The
    pages already read stay read, and a re-upload picks up the rest."""
    data = pdf_bytes([REVENUE_PAGE])

    monkeypatch.setenv("LLM_PROVIDER_ORDER", "")
    first = ingest(data, "annual-report.pdf")
    assert first.status == "partial"
    assert first.claims_extracted == 0

    fake_llm({"8,142 crore": revenue_claim()})
    second = ingest(data, "annual-report.pdf")

    assert second.document_id == first.document_id
    assert second.status == "complete"
    assert second.claims_extracted == 1
    assert len(Store().documents()) == 1


def test_resuming_reads_only_the_pages_that_were_missed(fake_llm, pdf_bytes, monkeypatch):
    """Ingestion can be capped to stay inside a daily quota; running it again
    continues rather than starting over."""
    fake_llm(
        {
            "8,142": revenue_claim(),
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
                    "quote": GROWTH_PAGE,
                    "confidence": 0.9,
                }
            ],
        }
    )
    monkeypatch.setenv("FACTLAYER_MAX_PAGES", "1")
    data = pdf_bytes([REVENUE_PAGE, GROWTH_PAGE])

    first = ingest(data, "report.pdf")
    assert first.status == "partial"
    assert first.claims_extracted == 1

    second = ingest(data, "report.pdf")
    assert second.status == "complete"
    assert len(Store().claims(status="active")) == 2


def test_a_completed_document_is_never_reprocessed(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": revenue_claim()})
    data = pdf_bytes([REVENUE_PAGE])
    ingest(data, "a.pdf")
    assert ingest(data, "a.pdf").status == "duplicate"


def test_pages_with_nothing_to_extract_are_not_retried_forever(fake_llm, pdf_bytes):
    """A page the filter skipped is a decision, not an outage: it must not leave the
    document permanently 'partial'."""
    fake_llm({"8,142 crore": revenue_claim()})
    result = ingest(pdf_bytes([REVENUE_PAGE, "Contents", "  "]), "a.pdf")
    assert result.status == "complete"


# ---------------------------------------------------- tolerating model omissions

def test_a_numeric_claim_without_an_explicit_kind_is_a_measurement(fake_llm, pdf_bytes):
    """Models drop optional fields. Losing a well-grounded fact over a missing label
    would be throwing away good evidence for a formatting slip."""
    item = revenue_claim()[0]
    del item["kind"]
    fake_llm({"8,142 crore": [item]})

    result = ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf")
    assert result.claims_extracted == 1
    assert Store().claims()[0].kind == "measurement"


def test_a_textual_claim_without_an_explicit_kind_is_an_attribute(fake_llm, pdf_bytes):
    page = "Sahil Barua is the Managing Director and Chief Executive Officer."
    fake_llm({
        "Sahil Barua": [{
            "entity": "Sahil Barua",
            "entity_canonical": "sahil_barua",
            "metric": "role",
            "metric_canonical": "role",
            "value_text": "Managing Director and Chief Executive Officer",
            "quote": page,
            "confidence": 0.9,
        }]
    })
    result = ingest(pdf_bytes([page]), "doc.pdf")
    assert result.claims_extracted == 1
    assert Store().claims()[0].kind == "attribute"


def test_a_measurement_with_no_readable_number_becomes_an_attribute(fake_llm, pdf_bytes):
    """'Credit rating: AAA' is a fact worth keeping, just not a numeric one."""
    page = "The Company's long-term credit rating is AAA (Stable) as of March 31, 2024."
    fake_llm({
        "credit rating": [{
            "kind": "measurement",
            "entity": "Delhivery Limited",
            "entity_canonical": "delhivery_limited",
            "metric": "long-term credit rating",
            "metric_canonical": "long_term_credit_rating",
            "value": "AAA (Stable)",
            "period": "as of March 31, 2024",
            "quote": page,
            "confidence": 0.9,
        }]
    })
    result = ingest(pdf_bytes([page]), "doc.pdf")
    assert result.claims_extracted == 1
    claim = Store().claims()[0]
    assert claim.kind == "attribute"
    assert claim.value_text == "AAA (Stable)"


def test_a_page_whose_only_fact_carries_no_digits_is_still_read(fake_llm, pdf_bytes):
    """Skipping pages by looking for numbers loses semantic facts. The page filter
    only skips pages with too little text to state anything."""
    page = "The Company's long-term credit rating is AAA (Stable)."
    fake_llm({
        "credit rating": [{
            "kind": "attribute",
            "entity": "Delhivery Limited",
            "entity_canonical": "delhivery_limited",
            "metric": "long-term credit rating",
            "metric_canonical": "long_term_credit_rating",
            "value_text": "AAA (Stable)",
            "quote": page,
            "confidence": 0.9,
        }]
    })
    assert ingest(pdf_bytes([page]), "doc.pdf").claims_extracted == 1


# ------------------------------------------------- reconciliation is separable

def test_relations_can_be_rebuilt_without_re_reading_the_pdfs(fake_llm, pdf_bytes):
    """Extraction is expensive; the rules are cheap and change often. Rebuilding must
    not need the documents again, and must reproduce the same verdicts."""
    from factlayer.pipeline import rebuild_relations

    conflicting = REVENUE_PAGE.replace("8,142", "7,900")
    fake_llm({
        "was Rs. 8,142": revenue_claim(),
        "7,900": revenue_claim(quote=conflicting, value="Rs. 7,900 crore"),
    })
    ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    ingest(pdf_bytes([conflicting]), "b.pdf")
    before = {(r["claim_a"], r["claim_b"], r["verdict"], r["reason_code"])
              for r in Store().relations()}
    assert before

    rebuilt = rebuild_relations()
    after = {(r["claim_a"], r["claim_b"], r["verdict"], r["reason_code"])
             for r in Store().relations()}

    assert rebuilt == len(after)
    assert after == before
    assert len(Store().claims()) == 2, "rebuilding must not touch the facts"


def test_rebuilding_drops_relations_the_rules_no_longer_produce(fake_llm, pdf_bytes):
    from factlayer.pipeline import rebuild_relations
    from factlayer.store import Store as S

    fake_llm({"8,142 crore": revenue_claim()})
    ingest(pdf_bytes([REVENUE_PAGE]), "a.pdf")
    store = S()
    claim = store.claims()[0]
    store.add_relations([{
        "claim_a": claim.id, "claim_b": claim.id, "verdict": "CONTRADICTS",
        "reason_code": "STALE", "dimension_diff": {}, "explanation": "x",
        "decided_by": "rules",
    }])
    assert store.relations()

    rebuild_relations()
    assert [r for r in S().relations() if r["reason_code"] == "STALE"] == []


def test_a_document_interrupted_mid_run_is_resumed_not_stranded(fake_llm, pdf_bytes):
    """A killed process leaves a document marked 'processing'. Treating anything
    that is not finished as already-present strands it: never completed, and never
    retried because it looks like a duplicate."""
    data = pdf_bytes([REVENUE_PAGE])

    fake_llm({"8,142 crore": revenue_claim()})
    first = ingest(data, "annual-report.pdf")
    Store().set_document_status(first.document_id, "processing")  # as a crash leaves it

    second = ingest(data, "annual-report.pdf")

    assert second.status == "complete"
    assert second.document_id == first.document_id
    assert len(Store().documents()) == 1


def test_only_a_finished_document_counts_as_a_duplicate(fake_llm, pdf_bytes):
    fake_llm({"8,142 crore": revenue_claim()})
    data = pdf_bytes([REVENUE_PAGE])
    assert ingest(data, "a.pdf").status == "complete"
    assert ingest(data, "a.pdf").status == "duplicate"


# ------------------------------------------------- entities must be identifiable

def test_a_claim_with_no_usable_entity_is_skipped(fake_llm, pdf_bytes):
    """Entity is the key facts are grouped and compared by. A claim whose subject
    normalises to nothing cannot be grouped with anything, and would sit in the
    store looking like a fact while being unusable."""
    item = revenue_claim()[0]
    item["entity"], item["entity_canonical"] = "—", ""
    fake_llm({"8,142 crore": [item]})
    assert ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf").claims_extracted == 0


def test_the_document_subject_is_offered_to_the_extractor(fake_llm, pdf_bytes):
    """Pages say "the Company" and "our business". The extractor is told what the
    document is about so it can resolve those to a name instead of coining an
    entity called "company"."""
    # The first mapping can only match a prompt that already carries the subject, so
    # it fires for the page call and not for the metadata call that produced it.
    fake_llm({
        "This document is: Delhivery Limited": revenue_claim(),
        "Revenue from operations": {"title": "Delhivery Limited",
                                    "publisher": None, "date": None},
    })
    assert ingest(pdf_bytes([REVENUE_PAGE]), "doc.pdf").claims_extracted == 1
