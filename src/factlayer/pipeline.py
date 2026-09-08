"""Ingesting one PDF into the knowledge layer.

Incremental by construction: a document is identified by the hash of its bytes, and
claims and relations are identified by content, so ingesting a new PDF adds rows and
compares them against what is already stored. Nothing is rebuilt.
"""

from __future__ import annotations

import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date

from . import config, extract, llm, reconcile
from .pdf import Page, PdfError, looks_factual, read_pages
from .store import Claim, Store


class IngestError(ValueError):
    """The upload could not be turned into a document."""


class NoProviderError(RuntimeError):
    """No model provider is configured, and the caller wants to know now.

    The command line prefers to make what progress it can and leave the document
    resumable. A web request would rather say so immediately than accept a PDF and
    store nothing from it.
    """


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


def ingest(
    data: bytes, filename: str, *, store: Store | None = None,
    require_provider: bool = False,
) -> IngestResult:
    """Add a PDF to the knowledge layer, or carry on with one that stalled.

    A document that was left partial — because a free tier ran out, a provider was
    down, or a page cap was set — is resumed from the pages it never read. A
    document that finished is not read again.
    """
    store = store or Store()

    digest = hashlib.sha256(data).hexdigest()
    existing = store.document_by_hash(digest)

    # Only a finished document is a duplicate. Anything else — left partial by a
    # quota, or stuck on "processing" because the run was killed — is unfinished
    # work to pick up, not work to skip.
    if existing and existing["status"] == "complete":
        return _already_have_it(store, existing)

    if existing:
        _require_provider(require_provider)
        return _read_pending_pages(store, existing["id"], existing["filename"],
                                   existing["page_count"], resumed=True)

    # Whether the file is readable does not depend on how the server is configured,
    # so it is settled before anything else can refuse the request.
    try:
        pages = read_pages(data)
    except PdfError as exc:
        raise IngestError(str(exc)) from exc

    _require_provider(require_provider)

    metadata = extract.document_metadata(pages)
    doc_id = store.add_document(
        filename=filename,
        sha256=digest,
        page_count=len(pages),
        status="processing",
        title=metadata.get("title"),
        publisher=metadata.get("publisher"),
        doc_date=metadata.get("doc_date"),
        stored_path=_keep_a_copy(data, digest, filename),
    )
    store.add_pages(doc_id, pages)
    return _read_pending_pages(store, doc_id, filename, len(pages), resumed=False)


def _read_pending_pages(
    store: Store, doc_id: str, filename: str, page_count: int, *, resumed: bool
) -> IngestResult:
    doc_date = _document_date(store, doc_id)
    subject = _document_subject(store, doc_id, filename)
    pending = store.pages(doc_id, unread_only=True)

    # A page the filter passed over is settled, not pending: mark it read so a later
    # run does not keep reconsidering it.
    store.mark_pages_read(doc_id, [p.number for p in pending if not looks_factual(p.text)])
    worth_reading = [page for page in pending if looks_factual(page.text)]

    cap = config.max_pages_per_document()
    if cap:
        worth_reading = worth_reading[:cap]

    claims, read_pages_numbers = _extract_all(
        store, worth_reading, doc_id, doc_date, subject)
    store.mark_pages_read(doc_id, read_pages_numbers)

    stored = store.add_claims(_consolidate(claims, store.vocabulary()))
    active = [claim for claim in stored if claim.status == "active"]
    relations = _relate(store, active)

    remaining = store.unread_count(doc_id)
    status = "complete" if remaining == 0 else "partial"
    store.set_document_status(doc_id, status)

    return IngestResult(
        document_id=doc_id,
        filename=filename,
        page_count=page_count,
        status=status,
        claims_extracted=len(active),
        claims_quarantined=len(stored) - len(active),
        relations_created=relations,
        pages_read=len(read_pages_numbers),
        note=_note(remaining, resumed),
    )


def _require_provider(required: bool) -> None:
    if required and not llm.available_providers():
        raise NoProviderError(
            "No model provider is configured on this deployment, so new PDFs cannot "
            "be read. The documents already loaded were processed in advance and are "
            "fully browsable."
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


def _document_date(store: Store, doc_id: str) -> date | None:
    """Publication date decides whether a later document supersedes an earlier one,
    so a resume retries it when the first attempt could not reach a provider."""
    document = store.document(doc_id) or {}
    if document.get("doc_date"):
        return date.fromisoformat(document["doc_date"])

    metadata = extract.document_metadata(store.pages(doc_id)[:3])
    if metadata.get("doc_date"):
        store.connection.execute(
            "UPDATE documents SET doc_date = ?, title = COALESCE(title, ?),"
            " publisher = COALESCE(publisher, ?) WHERE id = ?",
            (metadata["doc_date"].isoformat(), metadata.get("title"),
             metadata.get("publisher"), doc_id),
        )
        store.connection.commit()
    return metadata.get("doc_date")


def rebuild_relations(store: Store | None = None) -> int:
    """Recompare every stored fact and replace the relations table.

    Extraction is the expensive half and the rules are the half that changes, so
    they are separable: tuning a verdict rule costs a rebuild, not a re-read.
    """
    store = store or Store()
    store.clear_relations()
    return _relate(store, store.claims(status="active"))


# ------------------------------------------------------------------- extraction


def _document_subject(store: Store, doc_id: str, filename: str) -> str:
    """What the document is about, so a page saying "the Company" can be resolved to
    a name instead of coining an entity called "company"."""
    document = store.document(doc_id) or {}
    parts = [document.get("title"), document.get("publisher")]
    return " — ".join(part for part in parts if part) or filename


def _extract_all(
    store: Store, pages: list[Page], doc_id: str, doc_date: date | None,
    subject: str = "",
) -> tuple[list[Claim], list[int]]:
    """Read pages in parallel and report which ones succeeded. A page that fails
    stays unread, so the next run retries it instead of losing it."""
    if not pages:
        return [], []

    vocabulary = store.vocabulary()

    def read(page: Page):
        return extract.claims_from_page(
            page, doc_id=doc_id, doc_date=doc_date, vocabulary=vocabulary,
            subject=subject,
        )

    claims: list[Claim] = []
    done: list[int] = []
    workers = max(1, min(config.extraction_workers(), len(pages)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for page, result in zip(pages, pool.map(_safely(read), pages)):
            if result is None:
                continue  # this page is lost for now, the document is not
            claims.extend(result)
            done.append(page.number)
    return claims, done


def _safely(function):
    def wrapper(page):
        try:
            return function(page)
        except (llm.LLMUnavailable, llm.LLMOutputError, ValueError) as exc:
            # The page is lost, not the document — but silently is not the same as
            # gone: an operator watching server logs needs the reason, since the
            # note the caller gets back only ever says "a provider was unavailable".
            print(f"[extract] page {page.number} lost: {exc}", file=sys.stderr)
            return None

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


def _note(remaining: int, resumed: bool) -> str | None:
    prefix = "Resumed. " if resumed else ""
    if not remaining:
        return f"{prefix}Every page has been read." if resumed else None
    return (
        f"{prefix}{remaining} page(s) are still unread — a provider was unavailable, "
        "or a page cap is set. Upload the same file again to carry on from here; "
        "nothing already read will be repeated."
    )
