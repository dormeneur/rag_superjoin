"""Ingesting one PDF into the knowledge layer.

Incremental by construction: a document is identified by the hash of its bytes, and
claims and relations are identified by content, so ingesting a new PDF adds rows and
compares them against what is already stored. Nothing is rebuilt.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date

from . import config, extract, llm, reconcile
from .pdf import Page, PdfError, looks_factual, read_pages
from .store import Claim, Store


class IngestError(ValueError):
    """The upload could not be turned into a document."""


@dataclass
class IngestResult:
    document_id: str
    filename: str
    page_count: int
    status: str  # complete | partial | duplicate
    claims_extracted: int
    claims_quarantined: int
    relations_created: int
    pages_read: int = 0
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def ingest(data: bytes, filename: str, *, store: Store | None = None) -> IngestResult:
    store = store or Store()

    digest = hashlib.sha256(data).hexdigest()
    if existing := store.document_by_hash(digest):
        return _already_have_it(store, existing)

    try:
        pages = read_pages(data)
    except PdfError as exc:
        raise IngestError(str(exc)) from exc

    metadata = extract.document_metadata(pages)
    doc_date = metadata.get("doc_date")
    doc_id = store.add_document(
        filename=filename,
        sha256=digest,
        page_count=len(pages),
        status="processing",
        title=metadata.get("title"),
        publisher=metadata.get("publisher"),
        doc_date=doc_date,
        stored_path=_keep_a_copy(data, digest, filename),
    )
    store.add_pages(doc_id, pages)

    claims, failures = _extract_all(store, pages, doc_id, doc_date)
    claims = _consolidate(claims, store.vocabulary())
    stored = store.add_claims(claims)

    active = [claim for claim in stored if claim.status == "active"]
    relations = _relate(store, active)

    status = "partial" if failures else "complete"
    store.set_document_status(doc_id, status)
    return IngestResult(
        document_id=doc_id,
        filename=filename,
        page_count=len(pages),
        status=status,
        claims_extracted=len(active),
        claims_quarantined=len(stored) - len(active),
        relations_created=relations,
        pages_read=len(pages),
        note=_note(failures),
    )


def _already_have_it(store: Store, existing: dict) -> IngestResult:
    doc_id = existing["id"]
    active = store.claims(doc_id=doc_id, status="active")
    return IngestResult(
        document_id=doc_id,
        filename=existing["filename"],
        page_count=existing["page_count"],
        status="duplicate",
        claims_extracted=len(active),
        claims_quarantined=len(store.claims(doc_id=doc_id, status="quarantined")),
        relations_created=0,
        note="This document is already in the knowledge layer; nothing was reprocessed.",
    )


# ------------------------------------------------------------------- extraction


def _extract_all(
    store: Store, pages: list[Page], doc_id: str, doc_date: date | None
) -> tuple[list[Claim], int]:
    """Only pages that state something are sent to a model, and they go in parallel.
    One page failing costs that page, not the document."""
    vocabulary = store.vocabulary()
    worth_reading = [page for page in pages if looks_factual(page.text)]
    cap = config.max_pages_per_document()
    if cap:
        worth_reading = worth_reading[:cap]

    if not worth_reading:
        return [], 0

    def read(page: Page):
        return extract.claims_from_page(
            page, doc_id=doc_id, doc_date=doc_date, vocabulary=vocabulary
        )

    claims: list[Claim] = []
    failures = 0
    workers = max(1, min(config.extraction_workers(), len(worth_reading)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_safely(read), worth_reading):
            if result is None:
                failures += 1
            else:
                claims.extend(result)
    return claims, failures


def _safely(function):
    def wrapper(page):
        try:
            return function(page)
        except (llm.LLMUnavailable, llm.LLMOutputError, ValueError):
            return None  # this page is lost, the document is not

    return wrapper


def _consolidate(claims: list[Claim], known: dict[str, list[str]]) -> list[Claim]:
    """Pages are read in parallel, so two of them can coin different names for the
    same thing. Walk the results in order and fold near-duplicates together before
    anything is stored."""
    entities = list(known.get("entities", []))
    metrics = list(known.get("metrics", []))
    for claim in claims:
        claim.entity_canonical = extract._canonical(
            claim.entity_canonical, claim.entity_canonical, entities
        )
        claim.metric_canonical = extract._canonical(
            claim.metric_canonical, claim.metric_canonical, metrics
        )
        if claim.entity_canonical not in entities:
            entities.append(claim.entity_canonical)
        if claim.metric_canonical not in metrics:
            metrics.append(claim.metric_canonical)
    return claims


# ----------------------------------------------------------------- reconciliation


def _relate(store: Store, new_claims: list[Claim]) -> int:
    """Compare each new claim against everything already stored that shares its
    entity and metric. Blocking on those two keys is what keeps this linear-ish as
    documents pile up, instead of comparing every fact with every other fact."""
    rows = []
    seen: set[tuple[str, str]] = set()
    for claim in new_claims:
        candidates = store.claims(
            status="active",
            entity_canonical=claim.entity_canonical,
            metric_canonical=claim.metric_canonical,
        )
        for other in candidates:
            if other.id == claim.id:
                continue
            pair = tuple(sorted([claim.id, other.id]))
            if pair in seen:
                continue
            seen.add(pair)

            verdict = reconcile.compare(claim, other)
            if verdict.verdict == reconcile.UNRELATED:
                continue
            rows.append({
                "claim_a": claim.id,
                "claim_b": other.id,
                "verdict": verdict.verdict,
                "reason_code": verdict.reason_code,
                "dimension_diff": verdict.dimension_diff,
                "explanation": reconcile.describe(claim, other, verdict),
                "decided_by": "rules",
            })

    if not rows:
        return 0
    _polish(rows)
    store.add_relations(rows)
    return len(rows)


def _polish(rows: list[dict]) -> None:
    """Let a model rewrite the rule-written reasons into better prose. It is given
    the verdict and may not change it; if no provider answers, the rule-written text
    stands and `decided_by` keeps saying so."""
    budget = int(os.getenv("FACTLAYER_MAX_EXPLANATIONS", "40"))
    interesting = [row for row in rows if row["verdict"] != reconcile.CORROBORATES][:budget]
    if not interesting:
        return

    numbered = {str(index): row for index, row in enumerate(interesting)}
    payload = [
        {"id": key, "verdict": row["verdict"], "reason": row["reason_code"],
         "draft": row["explanation"]}
        for key, row in numbered.items()
    ]
    system = (
        "You rewrite drafted explanations so they read clearly for someone comparing "
        "two documents. Keep every fact, figure and date exactly as drafted. Do not "
        "change the verdict, do not add information, do not speculate. Two sentences "
        "at most each. Reply with a JSON array of {\"id\": string, \"explanation\": string}."
    )
    try:
        reply = llm.complete_json(system, str(payload))
    except Exception:
        return
    if not isinstance(reply, list):
        return

    for item in reply:
        if not isinstance(item, dict):
            continue
        row = numbered.get(str(item.get("id")))
        text = item.get("explanation")
        if row is not None and isinstance(text, str) and text.strip():
            row["explanation"] = text.strip()
            row["decided_by"] = "rules+llm_explanation"


# ------------------------------------------------------------------------ odds


def _keep_a_copy(data: bytes, digest: str, filename: str) -> str:
    """Keep the original so a quote can still be checked against its page later."""
    path = config.uploads_dir() / f"{digest[:16]}-{os.path.basename(filename)}"
    path.write_bytes(data)
    return str(path)


def _note(failures: int) -> str | None:
    if not failures:
        return None
    return (
        f"{failures} page(s) could not be read by any configured model provider. "
        "Facts on those pages are missing; re-uploading later will fill them in."
    )
