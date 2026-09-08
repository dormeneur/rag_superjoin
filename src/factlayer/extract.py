"""Turning a page of text into grounded claims.

Division of labour: the model reads language and copies strings; this module does
every conversion and every check. A claim only becomes active if its quote is
really on the page it names and the number it asserts is really in that quote.
Claims that fail are kept, marked quarantined, because a wrong answer the system
caught is more informative than one it silently dropped.
"""

from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz, process

from .llm import ProviderOverride, complete_json
from .normalize import (
    normalize_entity,
    normalize_metric,
    parse_period,
    parse_quantity,
)
from .pdf import Page, find_quote
from .store import Claim

MAX_PAGE_CHARS = 12000
MAX_VOCABULARY = 120
MERGE_THRESHOLD = 92

SYSTEM = """\
You read one page of a document and list the facts it states.

A fact is one of two shapes.

measurement — something measured or counted.
  entity            who or what the fact is about, as the page names it
  metric            what is measured, as the page labels it
  value             the amount, copied EXACTLY as written ("Rs. 8,142 crore", "6.4%")
  period            the period it covers, as written ("FY24", "Q4 FY24", "as of March 31, 2024").
                    If a table ROW names its own period — a month, a quarter, a date —
                    use the row's period, not the heading's. A monthly table has twelve
                    different periods, not twelve figures for one year.
  scope             qualifiers the page attaches to it, as free key/value pairs, e.g.
                    {"consolidation": "consolidated"}, {"revision": "provisional"},
                    {"basis": "estimate"}, {"segment": "express parcel"}

attribute — something that is true of a subject at a time.
  entity            the subject, usually a person, organisation or place
  metric            the property, e.g. "role", "registered office", "auditor"
  value_text        the value, copied as written
  period            the period it holds for, if the page states one
  scope             qualifiers, e.g. {"organisation": "Delhivery Limited"}

Every fact also needs:
  kind              "measurement" or "attribute"
  quote             ONE sentence or table row copied verbatim from the page that
                    states this fact. It must appear on the page character for
                    character. Never paraphrase, never join separated lines.
  confidence        0 to 1, how sure you are the page states this

Rules
- Copy. Do not convert units, do not compute totals, do not restate in your own words.
- If the value is not written on the page, do not report the fact.
- A group is not one fact: give each board member, each segment, each year its own entry.
- In a table, what makes a row different from the row above it — its month, its
  segment, its subsidiary — belongs in period or scope. Without it the rows become
  the same fact disagreeing with itself.
- Ignore page furniture: headers, footers, page numbers, tables of contents.
- "entity" and "metric" are what the PAGE calls them, in the page's own words and
  capitalisation ("Revenue from operations", not "revenue_from_operations"). The
  snake_case key belongs only in entity_canonical / metric_canonical.
- entity_canonical and metric_canonical are stable snake_case keys. REUSE a key from
  the known list below whenever it means the same thing, even if this page words it
  differently. Only coin a new key when nothing in the list fits. Keys never contain
  a period, year or unit.

- Name the subject. Pages say "the Company", "we", "our business", "the Bank" and
  "the Group"; those are not entities. Resolve them to the named subject the
  document is about, which is given below. Never use a pronoun, a bare "company",
  or a marketing phrase as an entity.

Reply with a JSON array of objects and nothing else. An empty array is a valid answer.\
"""

METADATA_SYSTEM = """\
Identify the document from its opening pages. Reply with one JSON object:
{"title": string|null, "publisher": string|null, "date": string|null}
"date" is the date the document itself was published or is dated as of, copied as
written. Use null when the pages do not say. Reply with the object and nothing else.\
"""


def document_metadata(
    pages: list[Page], *, provider_override: ProviderOverride | None = None
) -> dict:
    """Publication date matters: it is what lets a later document supersede an
    earlier one instead of contradicting it."""
    opening = "\n\n".join(page.text for page in pages[:3])[:6000]
    try:
        # 600, not 300: a reasoning-capable model spends part of the budget thinking
        # before it writes the answer, and 300 was cutting that answer off.
        data = complete_json(
            METADATA_SYSTEM, opening, max_tokens=600, provider_override=provider_override
        )
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    period = parse_period(str(data.get("date") or ""))
    return {
        "title": _clean(data.get("title")),
        "publisher": _clean(data.get("publisher")),
        "doc_date": period.end if period else None,
    }


def claims_from_page(
    page: Page,
    *,
    doc_id: str,
    doc_date: date | None,
    vocabulary: dict[str, list[str]],
    subject: str = "",
    provider_override: ProviderOverride | None = None,
) -> list[Claim]:
    """Extract and ground the claims on one page. Raises when the model is
    unreachable or unreadable, so the caller can lose one page rather than the
    whole document."""
    reply = complete_json(
        SYSTEM, _prompt(page, vocabulary, subject), max_tokens=8192,
        provider_override=provider_override,
    )
    if not isinstance(reply, list):
        return []

    claims = []
    for item in reply:
        if isinstance(item, dict):
            claim = _to_claim(item, page, doc_id, doc_date, vocabulary)
            if claim is not None:
                claims.append(claim)
    return claims


def _prompt(page: Page, vocabulary: dict[str, list[str]], subject: str = "") -> str:
    known_entities = ", ".join(vocabulary.get("entities", [])[:MAX_VOCABULARY]) or "(none yet)"
    known_metrics = ", ".join(vocabulary.get("metrics", [])[:MAX_VOCABULARY]) or "(none yet)"
    return (
        f"This document is: {subject or '(unknown)'}\n"
        f"Known entity_canonical keys: {known_entities}\n"
        f"Known metric_canonical keys: {known_metrics}\n\n"
        f"--- page {page.number} ---\n{page.text[:MAX_PAGE_CHARS]}"
    )


# ------------------------------------------------------------------- grounding


def _to_claim(item, page, doc_id, doc_date, vocabulary) -> Claim | None:
    entity = _clean(item.get("entity"))
    metric = _clean(item.get("metric"))
    quote = _clean(item.get("quote"))
    if not (entity and metric and quote):
        return None  # not enough to be a claim at all, let alone a wrong one

    value_written = _clean(item.get("value")) or _clean(item.get("value_text"))
    if not value_written:
        return None

    # The shape follows the value, not the label. Models drop the label, and a
    # "measurement" with no number in it ("credit rating: AAA") is an attribute.
    quantity = parse_quantity(value_written)
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in {"measurement", "attribute"} or (kind == "measurement" and not quantity):
        kind = "measurement" if quantity else "attribute"
    if kind == "attribute":
        quantity = None

    period = parse_period(_clean(item.get("period")) or "")
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}

    entity_canonical = _canonical(
        item.get("entity_canonical"), normalize_metric(normalize_entity(entity)),
        vocabulary.get("entities", []),
    )
    if not entity_canonical:
        # Facts are grouped and compared by entity. One that normalises to nothing
        # can never be grouped, so it would sit in the store looking like a fact.
        return None

    claim = Claim(
        kind=kind,
        entity=entity,
        entity_canonical=entity_canonical,
        metric=metric,
        metric_canonical=_canonical(
            item.get("metric_canonical"), normalize_metric(metric),
            vocabulary.get("metrics", []),
        ),
        value_num=quantity.value if quantity else None,
        unit=quantity.unit if quantity else None,
        value_text=value_written,
        period_start=period.start if period else None,
        period_end=period.end if period else None,
        period_label=period.label if period else None,
        scope={str(k): v for k, v in scope.items()},
        quote=quote,
        page_no=page.number,
        char_start=-1,
        char_end=-1,
        confidence=_confidence(item.get("confidence")),
        doc_id=doc_id,
        doc_date=doc_date,
    )
    return _ground(claim, page)


def _ground(claim: Claim, page: Page) -> Claim:
    """The two checks that make "linked to evidence" mean something."""
    span = find_quote(page.text, claim.quote)
    if span is None:
        claim.status = "quarantined"
        claim.quarantine_reason = "The quoted sentence is not on the page it cites."
        return claim

    claim.char_start, claim.char_end = span
    claim.quote = page.text[span[0] : span[1]]  # store the page's words, not the model's

    if claim.kind == "measurement":
        asserted = _digits(claim.value_text or "")
        if asserted and asserted not in _digits(claim.quote):
            claim.status = "quarantined"
            claim.quarantine_reason = (
                f"The quote does not contain the value {claim.value_text!r}."
            )
    return claim


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


# ---------------------------------------------------------------- canonical names


def _canonical(proposed, fallback: str, known: list[str]) -> str:
    """Keep the vocabulary from fragmenting into near-duplicates.

    The model is asked to reuse an existing key; this catches the cases where it
    coins a spelling variant anyway. The cost is not symmetric: under-merging leaves
    two facts that never meet, while over-merging invents a contradiction between
    facts that were never about the same thing. So the guards err towards apart.

    Similarity alone is not enough. "water_intensity_per_rupee_of_turnover" and
    "waste_intensity_per_rupee_of_turnover" are 97% alike and measure different
    things; merging them reported the two as contradicting each other. The leading
    word carries the meaning, so it has to match exactly, and so do any digits.
    """
    name = normalize_metric(_clean(proposed) or "") or fallback
    if not name or not known:
        return name
    match = process.extractOne(name, known, scorer=fuzz.ratio, score_cutoff=MERGE_THRESHOLD)
    if match and _digits(match[0]) == _digits(name) and _head(match[0]) == _head(name):
        return match[0]
    return name


def _head(name: str) -> str:
    """The first word of a key, which is what it is chiefly about."""
    return name.split("_", 1)[0]


def _clean(value) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5
