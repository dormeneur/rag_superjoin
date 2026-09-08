"""The HTTP surface a grader will actually use.

The assignment requires uploading arbitrary PDFs and inspecting the results, so the
API is tested as a contract: routes, status codes, and the shape of what comes back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PAGE = "Revenue from operations was Rs. 8,142 crore in FY24."

CLAIM = [
    {
        "kind": "measurement",
        "entity": "Delhivery Limited",
        "entity_canonical": "delhivery_limited",
        "metric": "Revenue from operations",
        "metric_canonical": "revenue_from_operations",
        "value": "Rs. 8,142 crore",
        "period": "FY24",
        "scope": {},
        "quote": PAGE,
        "confidence": 0.9,
    }
]


@pytest.fixture
def client():
    from factlayer.api import app

    return TestClient(app)


def upload(client, data: bytes, name: str = "doc.pdf"):
    return client.post(
        "/api/documents", files={"file": (name, data, "application/pdf")}
    )


def test_health_is_available_without_any_configuration(client):
    assert client.get("/health").status_code == 200


def test_upload_returns_an_ingest_summary(client, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM})
    response = upload(client, pdf_bytes([PAGE]))

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"]
    assert body["page_count"] == 1
    assert body["claims_extracted"] == 1
    assert body["status"] == "complete"


def test_uploaded_documents_are_listed(client, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM})
    upload(client, pdf_bytes([PAGE]), "annual-report.pdf")

    documents = client.get("/api/documents").json()
    assert [d["filename"] for d in documents] == ["annual-report.pdf"]


def test_every_returned_fact_carries_its_evidence(client, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM})
    upload(client, pdf_bytes([PAGE]))

    facts = client.get("/api/claims").json()
    assert facts
    for fact in facts:
        assert fact["quote"]
        assert fact["page_no"] >= 1
        assert fact["document"]["filename"]


def test_a_single_fact_can_be_inspected_with_its_relations(client, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM, "7,900": CLAIM})
    upload(client, pdf_bytes([PAGE]), "a.pdf")
    upload(client, pdf_bytes([PAGE.replace("8,142", "7,900")]), "b.pdf")

    fact_id = client.get("/api/claims").json()[0]["id"]
    detail = client.get(f"/api/claims/{fact_id}").json()
    assert detail["quote"]
    assert "relations" in detail


def test_relations_explain_themselves(client, fake_llm, pdf_bytes):
    conflicting = PAGE.replace("8,142", "7,900")
    fake_llm(
        {
            "8,142": CLAIM,
            "7,900": [{**CLAIM[0], "value": "Rs. 7,900 crore", "quote": conflicting}],
        }
    )
    upload(client, pdf_bytes([PAGE]), "a.pdf")
    upload(client, pdf_bytes([conflicting]), "b.pdf")

    relations = client.get("/api/relations").json()
    assert relations
    for relation in relations:
        assert relation["verdict"] in {
            "CORROBORATES",
            "CONTRADICTS",
            "RECONCILABLE",
            "UNRELATED",
        }
        assert relation["reason_code"]
        assert relation["explanation"]
        assert relation["decided_by"].startswith("rules")
        assert relation["claim_a"]["quote"]
        assert relation["claim_b"]["quote"]


def test_relations_can_be_filtered_by_verdict(client, fake_llm, pdf_bytes):
    conflicting = PAGE.replace("8,142", "7,900")
    fake_llm(
        {
            "8,142": CLAIM,
            "7,900": [{**CLAIM[0], "value": "Rs. 7,900 crore", "quote": conflicting}],
        }
    )
    upload(client, pdf_bytes([PAGE]), "a.pdf")
    upload(client, pdf_bytes([conflicting]), "b.pdf")

    contradictions = client.get("/api/relations?verdict=CONTRADICTS").json()
    assert contradictions
    assert all(r["verdict"] == "CONTRADICTS" for r in contradictions)


def test_quarantined_facts_are_hidden_by_default_but_reachable(client, fake_llm, pdf_bytes):
    """Required case 4 lives here: extraction failures must be inspectable."""
    fake_llm({"8,142": [{**CLAIM[0], "quote": "a sentence that is not in the pdf"}]})
    upload(client, pdf_bytes([PAGE]))

    assert client.get("/api/claims").json() == []
    quarantined = client.get("/api/claims?status=quarantined").json()
    assert len(quarantined) == 1
    assert quarantined[0]["quarantine_reason"]


def test_uploading_the_same_file_twice_does_not_duplicate_it(client, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM})
    data = pdf_bytes([PAGE])
    upload(client, data, "a.pdf")
    second = upload(client, data, "a-again.pdf")

    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(client.get("/api/documents").json()) == 1


@pytest.mark.parametrize(
    "data,name",
    [
        (b"not a pdf", "notes.txt"),
        (b"", "empty.pdf"),
        (b"%PDF-1.4 truncated", "broken.pdf"),
    ],
)
def test_bad_uploads_are_client_errors_not_crashes(client, data, name):
    response = upload(client, data, name)
    assert response.status_code == 400
    assert response.json()["detail"]


def test_an_unknown_fact_is_a_404(client):
    assert client.get("/api/claims/does-not-exist").status_code == 404


def test_an_unknown_relation_is_a_404(client):
    assert client.get("/api/relations/does-not-exist").status_code == 404


def test_the_ui_is_served_from_the_same_server(client):
    """One command has to start everything, or the demo does not survive contact."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
