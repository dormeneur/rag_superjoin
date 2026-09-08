# Design

## Shape

```
PDF ─ pdf.py ─ pages ─ filter ─ extract.py ─ claims ─ ground ─ store.py
                                    │                            │
                                  llm.py                     reconcile.py ─ relations
```

One module per stage. No stage knows about another's internals.

## Two fact shapes, nothing else

| | measurement | attribute |
|---|---|---|
| about | something counted | something true of a subject |
| value | `value_num` + `unit` (from code) | `value_text` |
| time | period covered | period it holds for |
| example | Delhivery, revenue from operations, FY2024 | Sahil Barua, role, MD & CEO |

Both carry `scope`: free key/value qualifiers the document itself states
(`consolidation: consolidated`, `revision: provisional`, `organisation: …`).
No fixed vocabulary, so an unfamiliar qualifier in an unfamiliar PDF still works.

## Who does what

**Model:** reads language, copies strings verbatim, coins/reuses canonical names.
**Code:** all arithmetic, all dates, all verdicts.

The model is never asked to convert "Rs. 8,142 crore" into a number. It returns the
string; `normalize.py` converts it. A model bad at arithmetic cannot corrupt a value.

## Grounding

Two checks, both cheap, both required before a claim goes live:

1. the quote is on the page it cites (exact, else whitespace-insensitive)
2. the number asserted is inside that quote

Stored quote = the **page's** substring, not the model's copy. Failures are kept and
marked `quarantined` with a reason — a caught error is evidence, a dropped one is not.

## Verdicts (rules, not the model)

Order matters. Each step is a chance to explain the difference before accusing.

```
same claim / kind / entity / metric?      no  → UNRELATED
units compatible?    different currency   → RECONCILABLE  CURRENCY_MISMATCH
                     one unit unknown     → RECONCILABLE  UNIT_UNKNOWN
                     different dimension  → UNRELATED
periods              disjoint             → UNRELATED
                     one inside the other → RECONCILABLE  PERIOD_MISMATCH
values agree?        yes                  → CORROBORATES
scope conflict?      yes                  → RECONCILABLE  SCOPE_MISMATCH
otherwise                                 → CONTRADICTS   VALUE_CONFLICT
```

Attributes: scope conflict → RECONCILABLE; same value → CORROBORATES; stated validity
windows disjoint → `TEMPORAL_SUCCESSION`; only publication dates differ →
`TEMPORAL_SUCCESSION_INFERRED` (labelled inferred, because it is); otherwise CONTRADICTS.

Percentages compare in **percentage points** (6.4 vs 6.5 is a real disagreement);
everything else compares relatively.

The LLM rewrites the prose of an explanation. It may not change a verdict.
`decided_by` records which happened.

## Decisions and trade-offs

| Chose | Over | Why |
|---|---|---|
| SQLite | Neo4j / Postgres | no server, relations table *is* the graph, grader runs one command |
| No embeddings | vector search | LLM coins canonical names at extraction, so blocking is an exact match — no torch, works offline |
| Rules decide verdicts | LLM decides | reproducible, auditable, survives every provider being rate-limited |
| Verbatim strings from the model | model returns parsed numbers | code does the maths; also makes grounding checkable |
| Whole page per call | sentence chunks | a table row means nothing without its header |
| Content-hash ids | autoincrement | re-reading the same evidence rewrites the same row; incremental by construction |

## Scale

- pages read one at a time, cache flushed — flat memory on a 500-page report
- page filter keeps ~91% of the starter set; these reports really are that dense
- sliding-window rate limiter per provider, four providers as fallback
- resumable: every page is marked read, so a stalled document continues on re-upload
- reconciliation blocks on (entity, metric), so a new document compares against a
  bucket rather than against every fact ever stored

## Known weaknesses

- multi-column pages interleave on extraction (Board of Directors pages do this)
- no OCR: a scanned PDF is refused with a clear message rather than silently empty
- cross-currency facts are never compared; no FX rates
- canonical-name merging is fuzzy above 92% with a digit guard — it can still
  under-merge two genuinely identical metrics worded very differently
