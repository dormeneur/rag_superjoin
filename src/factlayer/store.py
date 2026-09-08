"""The knowledge layer itself: documents, their pages, the claims drawn from them,
and the relations between those claims.

SQLite because it needs no server, ships with Python, and a relations table is a
graph. Claim and relation identifiers are content hashes, so re-running ingestion
over the same evidence writes the same rows instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config
from .normalize import readable, readable_name
from .pdf import Page

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    title        TEXT,
    publisher    TEXT,
    doc_date     TEXT,
    page_count   INTEGER NOT NULL,
    status       TEXT NOT NULL,
    stored_path  TEXT,
    ingested_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id   TEXT NOT NULL REFERENCES documents(id),
    page_no  INTEGER NOT NULL,
    text     TEXT NOT NULL,
    read     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, page_no)
);

CREATE TABLE IF NOT EXISTS claims (
    id                TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL REFERENCES documents(id),
    kind              TEXT NOT NULL,
    entity            TEXT NOT NULL,
    entity_canonical  TEXT NOT NULL,
    metric            TEXT NOT NULL,
    metric_canonical  TEXT NOT NULL,
    value_num         REAL,
    unit              TEXT,
    value_text        TEXT,
    period_start      TEXT,
    period_end        TEXT,
    period_label      TEXT,
    scope             TEXT NOT NULL,
    quote             TEXT NOT NULL,
    page_no           INTEGER NOT NULL,
    char_start        INTEGER NOT NULL,
    char_end          INTEGER NOT NULL,
    confidence        REAL,
    status            TEXT NOT NULL,
    quarantine_reason TEXT,
    doc_date          TEXT
);

CREATE INDEX IF NOT EXISTS claims_by_key
    ON claims (entity_canonical, metric_canonical, status);
CREATE INDEX IF NOT EXISTS claims_by_document ON claims (doc_id, status);

CREATE TABLE IF NOT EXISTS relations (
    id             TEXT PRIMARY KEY,
    claim_a        TEXT NOT NULL REFERENCES claims(id),
    claim_b        TEXT NOT NULL REFERENCES claims(id),
    verdict        TEXT NOT NULL,
    reason_code    TEXT NOT NULL,
    dimension_diff TEXT NOT NULL,
    explanation    TEXT,
    decided_by     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (claim_a, claim_b)
);

CREATE INDEX IF NOT EXISTS relations_by_verdict ON relations (verdict);
"""

CLAIM_COLUMNS = [
    "id", "doc_id", "kind", "entity", "entity_canonical", "metric", "metric_canonical",
    "value_num", "unit", "value_text", "period_start", "period_end", "period_label",
    "scope", "quote", "page_no", "char_start", "char_end", "confidence", "status",
    "quarantine_reason", "doc_date",
]


@dataclass
class Claim:
    """One fact, and the evidence it came from.

    `value_text` is always what the document said; `value_num` and `unit` are the
    machine-readable form of that string when it is a number.
    """

    kind: str
    entity: str
    entity_canonical: str
    metric: str
    metric_canonical: str
    quote: str
    page_no: int
    char_start: int
    char_end: int
    value_num: float | None = None
    unit: str | None = None
    value_text: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    period_label: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    doc_id: str | None = None
    doc_date: date | None = None
    id: str | None = None
    status: str = "active"
    quarantine_reason: str | None = None

    def with_identity(self) -> "Claim":
        """A content hash over the evidence and the assertion, so the same fact read
        from the same place twice is the same row."""
        material = "|".join(
            str(part)
            for part in (
                self.doc_id, self.page_no, self.char_start, self.char_end,
                self.kind, self.entity_canonical, self.metric_canonical,
                self.value_num, self.unit, self.value_text, self.period_label,
            )
        )
        return replace(self, id=hashlib.sha256(material.encode()).hexdigest()[:16])

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "kind": self.kind,
            "entity": self.entity,
            "entity_label": readable_name(self.entity),
            "entity_canonical": self.entity_canonical,
            "metric": self.metric,
            "metric_label": readable(self.metric),
            "metric_canonical": self.metric_canonical,
            "value_num": self.value_num,
            "unit": self.unit,
            "value_text": self.value_text,
            "period_label": self.period_label,
            "period_start": _iso(self.period_start),
            "period_end": _iso(self.period_end),
            "scope": self.scope,
            "quote": self.quote,
            "page_no": self.page_no,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "confidence": self.confidence,
            "status": self.status,
            "quarantine_reason": self.quarantine_reason,
            "doc_date": _iso(self.doc_date),
        }


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config.db_path()
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        try:  # databases created before resume existed
            self.connection.execute(
                "ALTER TABLE pages ADD COLUMN read INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        self.connection.commit()

    # ------------------------------------------------------------------ documents

    def add_document(
        self, *, filename: str, sha256: str, page_count: int, status: str,
        title: str | None = None, publisher: str | None = None,
        doc_date: date | None = None, stored_path: str | None = None,
    ) -> str:
        doc_id = hashlib.sha256(sha256.encode()).hexdigest()[:16]
        self.connection.execute(
            "INSERT OR REPLACE INTO documents "
            "(id, filename, sha256, title, publisher, doc_date, page_count, status,"
            " stored_path, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, filename, sha256, title, publisher, _iso(doc_date), page_count,
             status, stored_path, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return doc_id

    def document_by_hash(self, sha256: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return dict(row) if row else None

    def document(self, doc_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None

    def documents(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM documents ORDER BY ingested_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def set_document_status(self, doc_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE documents SET status = ? WHERE id = ?", (status, doc_id)
        )
        self.connection.commit()

    # ---------------------------------------------------------------------- pages

    def add_pages(self, doc_id: str, pages: Iterable) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO pages (doc_id, page_no, text) VALUES (?,?,?)",
            [(doc_id, page.number, page.text) for page in pages],
        )
        self.connection.commit()

    def pages(self, doc_id: str, *, unread_only: bool = False) -> list[Page]:
        sql = "SELECT page_no, text FROM pages WHERE doc_id = ?"
        if unread_only:
            sql += " AND read = 0"
        rows = self.connection.execute(sql + " ORDER BY page_no", (doc_id,))
        return [Page(row["page_no"], row["text"]) for row in rows]

    def mark_pages_read(self, doc_id: str, page_numbers: Iterable[int]) -> None:
        """A page is read once it has been extracted, or once the filter decided not
        to. Either way it is settled, so resuming never revisits it."""
        self.connection.executemany(
            "UPDATE pages SET read = 1 WHERE doc_id = ? AND page_no = ?",
            [(doc_id, number) for number in page_numbers],
        )
        self.connection.commit()

    def unread_count(self, doc_id: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM pages WHERE doc_id = ? AND read = 0", (doc_id,)
        ).fetchone()[0]

    def page_text(self, doc_id: str, page_no: int) -> str:
        row = self.connection.execute(
            "SELECT text FROM pages WHERE doc_id = ? AND page_no = ?", (doc_id, page_no)
        ).fetchone()
        return row["text"] if row else ""

    # --------------------------------------------------------------------- claims

    def add_claims(self, claims: Iterable[Claim]) -> list[Claim]:
        identified = [claim if claim.id else claim.with_identity() for claim in claims]
        self.connection.executemany(
            f"INSERT OR REPLACE INTO claims ({','.join(CLAIM_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(CLAIM_COLUMNS))})",
            [_claim_row(claim) for claim in identified],
        )
        self.connection.commit()
        return identified

    def claims(
        self, doc_id: str | None = None, status: str | None = "active",
        entity_canonical: str | None = None, metric_canonical: str | None = None,
        search: str | None = None, limit: int | None = None, offset: int = 0,
    ) -> list[Claim]:
        clauses, params = [], []
        for column, value in (
            ("doc_id", doc_id), ("status", status),
            ("entity_canonical", entity_canonical),
            ("metric_canonical", metric_canonical),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if search:
            clauses.append("(entity LIKE ? OR metric LIKE ? OR quote LIKE ?)")
            params.extend([f"%{search}%"] * 3)

        sql = "SELECT * FROM claims"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY doc_id, page_no, char_start"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [_row_to_claim(row) for row in self.connection.execute(sql, params)]

    def claim(self, claim_id: str) -> Claim | None:
        row = self.connection.execute(
            "SELECT * FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return _row_to_claim(row) if row else None

    def vocabulary(self) -> dict[str, list[str]]:
        """The canonical names coined so far. Shown to the model at extraction time so
        it reuses an existing name rather than inventing a synonym — this is how the
        schema grows without fragmenting."""
        entities = self.connection.execute(
            "SELECT DISTINCT entity_canonical FROM claims ORDER BY entity_canonical"
        ).fetchall()
        metrics = self.connection.execute(
            "SELECT DISTINCT metric_canonical FROM claims ORDER BY metric_canonical"
        ).fetchall()
        return {
            "entities": [row[0] for row in entities],
            "metrics": [row[0] for row in metrics],
        }

    # ------------------------------------------------------------------ relations

    def add_relations(self, rows: Iterable[dict[str, Any]]) -> int:
        prepared = []
        for row in rows:
            claim_a, claim_b = sorted([row["claim_a"], row["claim_b"]])
            prepared.append((
                hashlib.sha256(f"{claim_a}|{claim_b}".encode()).hexdigest()[:16],
                claim_a, claim_b, row["verdict"], row["reason_code"],
                json.dumps(row.get("dimension_diff", {})), row.get("explanation"),
                row.get("decided_by", "rules"),
                datetime.now(timezone.utc).isoformat(),
            ))
        cursor = self.connection.executemany(
            "INSERT OR REPLACE INTO relations (id, claim_a, claim_b, verdict,"
            " reason_code, dimension_diff, explanation, decided_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            prepared,
        )
        self.connection.commit()
        return cursor.rowcount

    def clear_relations(self) -> None:
        self.connection.execute("DELETE FROM relations")
        self.connection.commit()

    def relations(
        self, verdict: str | None = None, claim_id: str | None = None,
        limit: int | None = None, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)
        if claim_id:
            clauses.append("(claim_a = ? OR claim_b = ?)")
            params.extend([claim_id, claim_id])

        sql = "SELECT * FROM relations"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [_relation_row(row) for row in self.connection.execute(sql, params)]

    def relation(self, relation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM relations WHERE id = ?", (relation_id,)
        ).fetchone()
        return _relation_row(row) if row else None

    # ------------------------------------------------------------------ showcase

    # Reconcilable differences, best first. A difference explained by scope or by a
    # role changing over time shows the engine reasoning; a year against its own
    # fourth quarter is closer to arithmetic.
    EXPLAINED_PREFERENCE = [
        "SCOPE_MISMATCH",
        "TEMPORAL_SUCCESSION",
        "TEMPORAL_SUCCESSION_INFERRED",
        "CURRENCY_MISMATCH",
        "SCOPE_UNDECLARED",
        "PERIOD_UNDECLARED",
        "UNIT_UNKNOWN",
        "PERIOD_MISMATCH",
        "ATTRIBUTE_AGREEMENT",
    ]

    def showcase(self) -> dict[str, Any | None]:
        """One example of each case the assignment asks to see, chosen by rule.

        Picking these by hand would be hard-coding facts and would break the moment
        someone uploads their own PDFs. Chosen this way, the front page works on any
        corpus. Ordering is deterministic so the page does not reshuffle on reload.
        """
        return {
            "corroborated": self._best_relation("CORROBORATES"),
            "contradiction": self._best_relation("CONTRADICTS"),
            "explained": self._best_relation("RECONCILABLE"),
            "failure": self._best_failure(),
        }

    def _best_relation(self, verdict: str) -> dict[str, Any] | None:
        """Prefer an example spanning two documents: one document agreeing with
        itself is not what the assignment is asking to see."""
        preference = {
            reason: rank for rank, reason in enumerate(self.EXPLAINED_PREFERENCE)
        }
        row = self.connection.execute(
            """
            SELECT r.*,
                   (a.doc_id != b.doc_id) AS cross_document,
                   COALESCE(a.confidence, 0) + COALESCE(b.confidence, 0) AS strength
            FROM relations r
            JOIN claims a ON a.id = r.claim_a
            JOIN claims b ON b.id = r.claim_b
            WHERE r.verdict = ?
              AND a.status = 'active' AND b.status = 'active'
            ORDER BY cross_document DESC,
                     CASE r.reason_code {cases} ELSE ? END ASC,
                     strength DESC,
                     r.id ASC
            LIMIT 1
            """.format(
                cases=" ".join(
                    f"WHEN '{reason}' THEN {rank}" for reason in preference
                    for rank in [preference[reason]]
                )
            ),
            (verdict, len(preference)),
        ).fetchone()
        return _relation_row(row) if row else None

    def _best_failure(self) -> dict[str, Any] | None:
        """Case four is an extraction failure the system caught, so it comes from
        the claims grounding rejected. Without a stated reason there is nothing to
        show, so those are skipped."""
        row = self.connection.execute(
            """
            SELECT * FROM claims
            WHERE status = 'quarantined'
              AND quarantine_reason IS NOT NULL AND quarantine_reason != ''
            ORDER BY LENGTH(quote) DESC, id ASC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_claim(row).as_dict() if row else None

    def counts(self) -> dict[str, int]:
        totals = {
            "documents": "SELECT COUNT(*) FROM documents",
            "facts": "SELECT COUNT(*) FROM claims WHERE status = 'active'",
            "quarantined": "SELECT COUNT(*) FROM claims WHERE status = 'quarantined'",
            "relations": "SELECT COUNT(*) FROM relations",
        }
        return {name: self.connection.execute(sql).fetchone()[0]
                for name, sql in totals.items()}


# --------------------------------------------------------------------- conversion


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _claim_row(claim: Claim) -> tuple:
    return (
        claim.id, claim.doc_id, claim.kind, claim.entity, claim.entity_canonical,
        claim.metric, claim.metric_canonical, claim.value_num, claim.unit,
        claim.value_text, _iso(claim.period_start), _iso(claim.period_end),
        claim.period_label, json.dumps(claim.scope), claim.quote, claim.page_no,
        claim.char_start, claim.char_end, claim.confidence, claim.status,
        claim.quarantine_reason, _iso(claim.doc_date),
    )


def _row_to_claim(row: sqlite3.Row) -> Claim:
    return Claim(
        id=row["id"], doc_id=row["doc_id"], kind=row["kind"], entity=row["entity"],
        entity_canonical=row["entity_canonical"], metric=row["metric"],
        metric_canonical=row["metric_canonical"], value_num=row["value_num"],
        unit=row["unit"], value_text=row["value_text"],
        period_start=_parse_date(row["period_start"]),
        period_end=_parse_date(row["period_end"]), period_label=row["period_label"],
        scope=json.loads(row["scope"]), quote=row["quote"], page_no=row["page_no"],
        char_start=row["char_start"], char_end=row["char_end"],
        confidence=row["confidence"], status=row["status"],
        quarantine_reason=row["quarantine_reason"],
        doc_date=_parse_date(row["doc_date"]),
    )


def _relation_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["dimension_diff"] = json.loads(data["dimension_diff"])
    return data
