# Design

## Shape

```
PDF ─ pdf.py ─ pages ─ extract.py ─ claims ─ ground ─ store.py
                          │                             │
                        llm.py                      reconcile.py ─ relations
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
values agree?        yes                  → CORROBORATES  VALUE_AGREEMENT
period undeclared    either side silent   → RECONCILABLE  PERIOD_UNDECLARED
scope conflict?      both state, differ   → RECONCILABLE  SCOPE_MISMATCH
scope undeclared?    one states, one not  → RECONCILABLE  SCOPE_UNDECLARED
otherwise                                 → CONTRADICTS   VALUE_CONFLICT
```

**Silence is never read as agreement.** Twice this mattered on real data. A
shareholder's total holding states no period; comparing it against dated individual
purchases produced 77 false contradictions in one document. A figure qualified
"standalone" on one side and unqualified on the other is not a disagreement either.
Both now withhold the accusation and say what was missing. Agreement is still checked
first, so two undated figures that match still corroborate.

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
- a page is skipped only when it has too little text to state anything. An earlier
  filter also required a currency amount, a percentage or a role keyword; measured,
  it skipped 9% of pages while silently dropping facts that carried no digits, so it
  was removed
- sliding-window rate limiter, counted per provider *and* per model. A provider may
  list several models: quotas are per model, so that is both more allowance and a
  fallback, and the client picks the least busy one
- when a provider states how long to wait, that delay is used instead of a guess
- resumable: every page is marked read, so a document stopped by a quota — or by the
  process being killed — continues where it left off
- reconciliation blocks on (entity, metric), so a new document compares against a
  bucket rather than against every fact ever stored

## Known weaknesses

- multi-column pages interleave on extraction. This is the single largest source of
  quarantined claims: in the 2022 prospectus, 158 of 168 quarantined claims quote a
  sentence that the reflowed page text does not contain
- no OCR: a scanned PDF is refused with a clear message rather than silently empty
- cross-currency facts are never compared; no FX rates
- canonical-name merging is fuzzy above 92% with a digit guard — it can still
  under-merge two genuinely identical metrics worded very differently
