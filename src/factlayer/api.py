"""HTTP surface: upload PDFs, browse the facts, inspect why two facts relate."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import llm
from .pipeline import IngestError, ingest
from .store import Claim, Store

WEB = Path(__file__).parent / "web"

app = FastAPI(
    title="Fact Knowledge Layer",
    description="Extract facts from PDFs, keep them tied to their evidence, and "
                "explain how facts across documents relate.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "providers_ready": llm.available_providers()}


@app.get("/api/stats")
def stats() -> dict:
    return Store().counts()


# ------------------------------------------------------------------- documents


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        result = ingest(data, file.filename or "document.pdf")
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.as_dict()


@app.get("/api/documents")
def documents() -> list[dict]:
    return Store().documents()


# ----------------------------------------------------------------------- facts


@app.get("/api/claims")
def claims(
    doc_id: str | None = None,
    status: str = "active",
    entity: str | None = None,
    metric: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    store = Store()
    found = store.claims(
        doc_id=doc_id, status=status, entity_canonical=entity,
        metric_canonical=metric, search=search, limit=limit, offset=offset,
    )
    return [_claim(store, claim) for claim in found]


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str) -> dict:
    store = Store()
    found = store.claim(claim_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such fact.")
    detail = _claim(store, found)
    detail["relations"] = [
        _relation(store, row) for row in store.relations(claim_id=claim_id)
    ]
    detail["page_text"] = store.page_text(found.doc_id, found.page_no)
    return detail


# ------------------------------------------------------------------- relations


@app.get("/api/relations")
def relations(verdict: str | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    store = Store()
    return [
        _relation(store, row)
        for row in store.relations(verdict=verdict, limit=limit, offset=offset)
    ]


@app.get("/api/relations/{relation_id}")
def relation_detail(relation_id: str) -> dict:
    store = Store()
    row = store.relation(relation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such relation.")
    return _relation(store, row)


# ------------------------------------------------------------------- showcase


@app.get("/api/highlights")
def highlights() -> dict:
    """One real example of each of the four cases the assignment asks to see.

    Chosen by rule from whatever is stored, so this works on an uploaded corpus as
    well as on the starter documents. Nothing here is hard-coded.
    """
    store = Store()
    picks = store.showcase()
    hydrated: dict = {}
    for name in ("corroborated", "contradiction", "explained"):
        row = picks[name]
        hydrated[name] = _relation(store, row) if row else None

    failure = picks["failure"]
    if failure:
        claim = store.claim(failure["id"])
        hydrated["failure"] = _claim(store, claim) if claim else None
        if hydrated["failure"]:
            hydrated["failure"]["page_text"] = store.page_text(
                claim.doc_id, claim.page_no
            )
    else:
        hydrated["failure"] = None
    return hydrated


# ---------------------------------------------------------------------- the ui


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html", media_type="text/html")


# ------------------------------------------------------------- serialisation


def _claim(store: Store, claim: Claim) -> dict:
    data = claim.as_dict()
    document = store.document(claim.doc_id) or {}
    data["document"] = {
        "id": document.get("id"),
        "filename": document.get("filename", ""),
        "title": document.get("title"),
        "publisher": document.get("publisher"),
        "doc_date": document.get("doc_date"),
    }
    return data


def _relation(store: Store, row: dict) -> dict:
    data = dict(row)
    for side in ("claim_a", "claim_b"):
        claim = store.claim(row[side])
        data[side] = _claim(store, claim) if claim else None
    return data
