"""The command line used to build a corpus.

A free tier refusing the occasional page is routine, so the documented one-line
command has to finish the job rather than leave a document partial and expect the
operator to notice.
"""

from __future__ import annotations

import json

from factlayer.__main__ import main
from factlayer.store import Store

PAGE = "Revenue from operations was Rs. 8,142 crore in FY24."

CLAIM = [{
    "kind": "measurement",
    "entity": "Delhivery Limited", "entity_canonical": "delhivery_limited",
    "metric": "Revenue from operations", "metric_canonical": "revenue_from_operations",
    "value": "Rs. 8,142 crore", "period": "FY24", "scope": {},
    "quote": PAGE, "confidence": 0.9,
}]


def test_ingest_reads_a_pdf_and_reports_it(tmp_path, fake_llm, pdf_bytes, capsys):
    fake_llm({"8,142": CLAIM})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_bytes([PAGE]))

    assert main(["ingest", str(pdf)]) == 0
    assert "report.pdf" in capsys.readouterr().out
    assert len(Store().claims()) == 1


def test_a_document_left_unfinished_is_retried_a_bounded_number_of_times(
    tmp_path, monkeypatch, pdf_bytes, capsys
):
    """With the provider down throughout, the sweeps still run and still stop. A
    retry loop that cannot give up is worse than no retry loop."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_bytes([PAGE]))
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "")

    main(["ingest", str(pdf), "--sweeps", "2"])

    out = capsys.readouterr().out
    assert "sweep 1" in out and "sweep 2" in out and "sweep 3" not in out
    assert Store().documents()[0]["status"] == "partial"


def test_a_resumed_document_reaches_complete(tmp_path, monkeypatch, fake_llm, pdf_bytes):
    """The pages a busy provider refused are picked up on the next run, and the
    document finishes without re-reading what it already read."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_bytes([PAGE]))

    monkeypatch.setenv("LLM_PROVIDER_ORDER", "")
    main(["ingest", str(pdf), "--sweeps", "0"])
    assert Store().documents()[0]["status"] == "partial"

    fake_llm({"8,142": CLAIM})
    main(["ingest", str(pdf)])
    assert Store().documents()[0]["status"] == "complete"
    assert len(Store().claims()) == 1


def test_sweeping_stops_once_everything_is_finished(tmp_path, fake_llm, pdf_bytes, capsys):
    fake_llm({"8,142": CLAIM})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_bytes([PAGE]))

    main(["ingest", str(pdf), "--sweeps", "5"])
    assert "sweep" not in capsys.readouterr().out


def test_an_unreadable_file_is_reported_and_fails_the_run(tmp_path, capsys):
    bad = tmp_path / "notes.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert main(["ingest", str(bad)]) == 1
    assert "notes.pdf" in capsys.readouterr().err


def test_export_writes_the_layer_as_json(tmp_path, fake_llm, pdf_bytes):
    fake_llm({"8,142": CLAIM})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_bytes([PAGE]))
    main(["ingest", str(pdf)])

    out = tmp_path / "samples"
    assert main(["export", "--out", str(out)]) == 0
    facts = json.loads((out / "facts.json").read_text())
    assert facts and facts[0]["quote"]
    assert json.loads((out / "summary.json").read_text())["facts"] == 1


def test_reconcile_rebuilds_relations_from_stored_facts(tmp_path, fake_llm, pdf_bytes):
    conflicting = PAGE.replace("8,142", "7,900")
    fake_llm({
        "was Rs. 8,142": CLAIM,
        "7,900": [{**CLAIM[0], "value": "Rs. 7,900 crore", "quote": conflicting}],
    })
    for name, text in (("a.pdf", PAGE), ("b.pdf", conflicting)):
        path = tmp_path / name
        path.write_bytes(pdf_bytes([text]))
        main(["ingest", str(path)])

    before = len(Store().relations())
    assert before
    assert main(["reconcile"]) == 0
    assert len(Store().relations()) == before
