# Fact Knowledge Layer

Extracts facts from PDFs, ties every fact to the sentence it came from, and works out
whether facts across documents corroborate, contradict, or can be reconciled.

The idea it is built around: **a difference between two numbers is not a contradiction
until the system has tried to explain it and failed.** Period, unit, currency and scope
each get a chance to account for the gap before anything is called a contradiction.
Those verdicts come from rules, not from a language model, so they are reproducible.

## Live Demo

**<!-- LIVE_URL -->** — opens with six documents already processed. The four cases the
assignment asks for are on the front page, each with its source quotes and page
numbers. Nothing to install, nothing to configure, no key to enter.

Those four are chosen by rule at query time rather than picked by hand, so they
re-derive themselves against whatever documents are loaded — including any you upload.

Browsing needs no credentials. Uploading a PDF needs a model key, which is attached to
the deployment as a secret rather than committed; without one the upload endpoint says
so plainly instead of accepting a file and storing nothing from it.

## Setup and Run Instructions

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # add at least one free API key
uvicorn factlayer.api:app --reload
```

Open <http://localhost:8000>, drop a PDF on the page, and watch the facts and
relationships appear. Every upload adds to the existing knowledge layer.

Keys are free. Any one of these is enough; more just means fallback when one is
rate-limited:

| Provider | Get a key |
|---|---|
| Groq | <https://console.groq.com/keys> |
| Google Gemini | <https://aistudio.google.com/apikey> |
| OpenRouter | <https://openrouter.ai/keys> (models ending `:free`) |
| Cerebras | <https://cloud.cerebras.ai> |

Command line, for loading several PDFs at once:

```bash
python -m factlayer ingest data/*/*.pdf
python -m factlayer export --out samples
```

Tests need no key at all — a scripted provider stands in for the model:

```bash
pytest
```

### Deploying it

The whole thing is one container: API, interface, and a knowledge layer already built
from the starter documents, so a deployment opens with something to look at rather than
an empty database. The built corpus is committed at `deploy/factlayer.db.gz` (2.4 MB)
and unpacked at image build time, so a deploy needs no API key and no rebuild.

**Render** reads `render.yaml` and builds the `Dockerfile`, so the only decision is
which repository to point at:

> New → Blueprint → pick this repository → Apply.

The free instance is 512 MB; the server settles at about 77 MB serving this corpus. It
sleeps after inactivity and takes roughly a minute to wake, which is the price of free.

The container reads `PORT` from the environment and falls back to 7860, so the same
image runs unchanged on anything that speaks Docker:

```bash
docker build -t factlayer . && docker run -p 8000:8000 -e PORT=8000 factlayer
```

**Hugging Face Spaces** was the first target and `deploy/publish.sh` still works, but
not on a free account: Docker Spaces are a paid feature, and a free account's Gradio
Spaces are pinned to ZeroGPU hardware, whose runtime already holds the port the server
needs. It needs a PRO subscription, so `app.py`, the publish script and the
**Deploy to Hugging Face Space** action are kept for anyone who has one.

Rebuild the corpus first only if you want to:

```bash
python -m factlayer ingest data/*/*.pdf
gzip -9 -c factlayer.db > deploy/factlayer.db.gz
```

Browsing the corpus needs no credentials. To let a deployment accept new PDFs, set
`GEMINI_API_KEY` (or any provider key from the table above) in its environment.

### API

| | |
|---|---|
| `POST /api/documents` | upload a PDF (multipart `file`) |
| `GET /api/documents` | what has been ingested |
| `GET /api/claims` | facts; `?status=quarantined` shows extraction failures |
| `GET /api/claims/{id}` | one fact, its page text and its relationships |
| `GET /api/relations` | relationships; `?verdict=CONTRADICTS` to filter |
| `GET /health` | which providers are configured |

## Video Demo

_To be added._

## Approach

**Pipeline.** `pdf → extract → ground → store → reconcile`, one module per
stage in `src/factlayer/`. Full design notes in [docs/DESIGN.md](docs/DESIGN.md).

**Two fact shapes.** A *measurement* (entity, metric, value, unit, period, scope) and an
*attribute* (subject, property, value, validity). Both carry `scope` — free key/value
qualifiers the document itself states, such as `consolidation: consolidated` or
`revision: provisional`. There is no fixed vocabulary, so a qualifier this system has
never seen still participates in reconciliation.

**The model reads; the code decides.** The model is asked only for verbatim strings —
it never converts "Rs. 8,142 crore" into a number, because `normalize.py` does that.
A model careless with arithmetic therefore cannot corrupt a stored value.

**Grounding is enforced, not promised.** A claim goes live only if its quote really is
on the page it cites and the number it asserts really is inside that quote. What gets
stored is the *page's* substring, not the model's copy of it. Claims that fail are kept
and marked quarantined with a reason.

**Verdicts are rules.** `reconcile.compare` is a pure function that walks the dimensions
in order and only reaches `CONTRADICTS` when entity, metric, unit, period and scope all
match and the values still disagree. The model rewrites the prose of the explanation and
may not change the verdict; each relation records which happened in `decided_by`.

**Storage.** SQLite. A relations table between claims is a graph, and it needs no server,
so `pip install` and one command is the whole setup. Claim and relation ids are content
hashes, which is what makes ingestion incremental: a new PDF adds rows and is compared
against what is already there, and re-reading the same evidence rewrites the same row.

**No embeddings.** Differently-worded metrics are matched by having the model coin a
canonical `snake_case` name at extraction time, reusing names already in the database.
Blocking is then an exact match. This drops a heavyweight dependency, works offline, and
gives a schema that grows as new kinds of facts appear.

**Free tiers, taken seriously.** Groq, Gemini, OpenRouter and Cerebras all speak the
OpenAI wire format, so a provider is a row of config rather than an integration, and
the list doubles as the retry policy. Quotas are counted per model, so a provider may
name several and the client uses whichever is least busy — more allowance and a
fallback at once. A sliding-window limiter paces requests below the published limit
rather than collecting 429s, and when a provider says how long to wait, that number is
used instead of a guess. Ingestion is resumable: every page is marked read, so a
document stopped by a quota — or by the process being killed — continues where it
stopped when the same file is uploaded again.

**AI tools used.** Built with Claude Code (Opus). The [Ponytail](https://github.com/DietrichGebert/ponytail)
YAGNI ladder was used as the working rule throughout — reuse before writing, standard
library before dependency, minimum that works — which is why the whole system is nine
modules and a command line, with no framework beyond FastAPI and no vector database.

## The Four Cases

All four are taken verbatim from the running system, and all four are chosen by rule
rather than by hand — the front page recomputes them against whatever documents are
loaded, including ones you upload. The corpus here is the six starter PDFs: 511 pages,
4,281 facts, 11,514 relationships.

### 1. A fact corroborated across documents

**CORROBORATES** · `VALUE_AGREEMENT` · decided by rules

Both documents report India · Foreign exchange reserves for the same period as $706 billion (7.06e+11 USD) and USD 704.9 billion (7.049e+11 USD). Once magnitudes and units are normalised these are the same figure.

- **$706 billion** — India · Foreign exchange reserves  
  <sub>`03-imf-india-2025-article-iv-excerpt.pdf` p.12</sub>
  > In 2024Q4 and 2025Q1, the Indian rupee experienced depreciation pressure, and foreign exchange (FX) reserves declined to $668 billion in March 2025, from $706 billion in September 2024 on significant currency intervention.

- **USD 704.9 billion** — India · Foreign exchange reserves  
  <sub>`01-india-economic-survey-2024-25-excerpt.pdf` p.31</sub>
  > As a result of stable capital flows, India’s foreign exchange reserves increased from USD 616.7 billion at the end of January 2024 to USD 704.9 billion in September 2024 before moderating to USD 634.6 billion as on 3 January 2025.

_The IMF and the Economic Survey, written months apart by different institutions, each
state India's September 2024 reserves — one as `$706 billion`, the other as
`USD 704.9 billion`. Normalisation makes them comparable and they land 0.16% apart,
inside tolerance. Neither document mentions the other._

### 2. A genuine contradiction

**CONTRADICTS** · `ATTRIBUTE_VALUE_CONFLICT` · decided by rules

Kapil Bharati's DIN is given as '02227607' and '01432123' for the same time, and both cannot hold.

- **02227607** — Kapil Bharati · DIN  
  <sub>`01-delhivery-prospectus-2022-excerpt.pdf` p.30</sub>
  > Kapil Bharati 02227607 295 DDA Flats, Gulmohar Enclave, Andrewsganj

- **01432123** — Kapil Bharati · DIN  
  <sub>`01-delhivery-prospectus-2022-excerpt.pdf` p.85</sub>
  > DIN: 01432123

_Both pages are in the same prospectus and they cannot both be right. Page 85 suggests
the second number belongs to a different director, so the likeliest cause is a table the
extractor lost its place in — which is precisely what a reader should be told. The system
has no idea what a DIN is; it flagged this because two claims about one subject disagreed._

_Two honest observations about this case. There is **no cross-document contradiction** in
the corpus at all. And of the 492 contradictions found, **478 are between two rows of a
single table on one page** — the signature of an extractor losing its row, not of a
document disagreeing with itself. The selection rule knows this and prefers a
contradiction that spans pages, which is why this one is shown._

### 3. An apparent contradiction, explained by context

**RECONCILABLE** · `SCOPE_MISMATCH` · decided by rules

The documents qualify this differently (unit: ₹ in Million versus ₹ Cr), so 757.86 (757.9) and 76 (76) are not directly comparable and this is not a disagreement.

- **757.86** — Delhivery Limited · Adjusted EBITDA  
  <sub>`02-delhivery-annual-report-fy24-excerpt.pdf` p.37 · FY2024 · unit: ₹ in Million</sub>
  > Adjusted EBITDA 757.86 (4,038.66)

- **76** — Delhivery · Adjusted EBITDA  
  <sub>`03-delhivery-q4-fy24-earnings-presentation.pdf` p.15 · FY2024 · unit: ₹ Cr</sub>
  > Adjusted EBITDA (217) (125) (67) 6 (25) (13) 92 21 (404) 76

_The engine doing its job. Two documents report the same measure for the same year, one
in millions and one in crore, and it declined to call that a disagreement. It is also
honest about its ceiling: 757.86 million and 76 crore are the same amount, and a sharper
version would have converted the units and called it corroboration instead of stopping at
"qualified differently"._

### 4. An extraction failure, and how it is handled

**Quarantined** — The quoted sentence is not on the page it cites.

The model reported `Delhivery Limited · Revenue from operations = 66,586.61` in `02-delhivery-annual-report-fy24-excerpt.pdf` p.22, quoting:

> y The revenue from operations on standalone basis for FY24 stood at ₹ 74,540.82 million as against ₹66,586.61 million for FY23, registering a growth of 11.95%.


That sentence is not on the page. What the page actually holds:

> Corporate Overview Statutory Reports Financial Statements Directors’ Report Dear Members, y Proprietary logistics operating system: In-house logistics of your Company function as managed marketplaces that Delhivery Limited (“Company”/“Delhivery”) technology stack is built by your Company to meet the…
_The sentence reads like a real disclosure and carries real figures, which is what makes it
dangerous. It is not on page 22. Grounding caught it, so it never became a fact — it sits
in quarantine with the reason attached, browsable under **Quarantined** on the site. 743
of 5,024 extracted claims (14.8%) were rejected this way, nearly all of them on
multi-column pages where the text reflows across columns._


## Scale, and Growing the Layer

The assignment's four suggested extensions, each with the measurement behind it. All
figures come from the corpus in `deploy/factlayer.db.gz`.

**Large PDFs without a performance problem.** The unit of work is a page, never a
document. Pages are read, extracted and marked read one at a time, and the model is
never shown more than a single page (capped at 12,000 characters), so cost grows with
page count rather than with document size and nothing has to hold a whole PDF in
working memory. Four pages are read concurrently. The largest starter document is 100
pages; the corpus is 511.

**Many PDFs in one knowledge layer.** A new fact is compared only against facts that
could possibly be about the same thing — comparison is blocked on
`(entity_canonical, metric_canonical)` before any rule runs. Across this corpus that is
**23,568 comparisons instead of the 9,161,340** an all-pairs sweep would need, 389x
fewer, from 4,281 facts falling into 1,672 blocks whose largest holds 104. The ratio
improves as documents accumulate, because blocks grow far more slowly than the corpus.

**A schema that evolves as new kinds of facts appear.** Nothing document-specific is
declared in code. These six PDFs produced **218 entity keys, 1,188 metric keys and 258
distinct scope qualifiers** — `unit`, `consolidation`, `basis`, `sector`, `gender`,
`organisation`, `revision` — every one of them coined at extraction time. Keys already
in the database are shown back to the model on the next page, so later pages reuse them
instead of inventing synonyms, and a qualifier the system has never seen still takes
part in reconciliation. This is also why there are no embeddings: the vocabulary *is*
the index.

**New documents incrementally, without rebuilding.** Documents are identified by the
hash of their bytes, and claims and relations by their content, so re-uploading a file
is a no-op and a new PDF adds rows and is compared against what is already stored.
`tests/test_pipeline_offline.py` holds the system to it: ingesting document B leaves
document A's claim ids untouched. Extraction and reconciliation are separable for the
same reason — `python -m factlayer reconcile` recompares all 4,281 facts in **2.3
seconds** without re-reading a single page, and reproduces all 11,514 relations exactly,
so tuning a verdict rule costs a rebuild instead of a re-extraction.

A fifth extension, not on the list: ingestion is **resumable**. Every page is marked
read as it is finished, so a document interrupted by an exhausted free tier — or by the
process being killed — continues from where it stopped when the same file is uploaded
again, rather than starting over or being silently half-loaded.


## Limitations and Next Steps

**Does not work yet**

- **Units declared in a table header are lost.** This is the biggest one. A column headed
  "per cent" with bare numbers below leaves each fact unitless, and the engine then
  refuses to compare it with a figure that does carry a unit. It is why the Economic
  Survey's `6.4 per cent` GDP growth for FY2025 and the RBI's bare `6.5` for the same
  year never meet: 438 of the 976 cross-document pairs end at `UNIT_UNKNOWN`, and it is
  the main reason no cross-document contradiction surfaced.
- **Multi-column pages interleave.** Text extraction reads across columns, so a page like
  a board-of-directors listing produces jumbled sentences. 743 of 5,024 extracted claims
  (15%) failed grounding and were quarantined, nearly all from such pages — visible and
  explained, but lost.
- **Table rows can be misattributed.** The extractor can carry a value from one row onto
  the subject of another. The quote is genuine and on the page, so grounding cannot catch
  it — and this is the dominant failure mode by volume: 478 of the 492 contradictions
  found are between two rows of one table. The selection rule works around it by
  preferring contradictions that span pages, but the underlying facts are still wrong.
- **Near-identical metric names once merged wrongly.** "water intensity per rupee of
  turnover" and "waste intensity per rupee of turnover" are 97% alike, and folding them
  together reported the two as contradicting each other. Merging now also requires the
  leading word to match, which costs some real merges (`authorised` against `authorized`)
  to avoid inventing conflicts. Under-merging is silent; over-merging accuses.
- **No OCR.** A scanned PDF is refused with a clear message rather than silently
  returning nothing.
- **Cross-currency facts are never compared.** Without an exchange rate for the right
  date, they are marked `RECONCILABLE / CURRENCY_MISMATCH` rather than reconciled.
- **Canonical-name merging can under-merge.** Two genuinely identical metrics worded
  very differently may stay separate, and separate metrics never meet to be compared.
- **A wrong-but-plausible extraction survives.** Grounding catches invented quotes and
  invented numbers, not a number correctly copied from the wrong row of a table.

**Next**

- Read units from table headers and attach them to every cell beneath, which would
  unblock the largest class of cross-document comparisons.
- Column-aware text extraction, which would fix the largest single source of
  quarantined claims.
- A labelled gold set and precision/recall numbers, so claims about accuracy are
  measured rather than asserted.
- Reconcile currencies using an FX rate taken from the documents themselves.
- Show the source page with the quote highlighted, instead of the quote alone.

## Additional Notes

- No credentials in the repository. `.env.example` lists what to set; `.env` is ignored.
- The test suite is the specification. It was written before the implementation, from
  the behaviour the system should have, so it tests what was built rather than
  mirroring how it was built. It runs offline, with no API key, in a few seconds.
- `data/` holds the starter PDFs, split into the two topic sets they came in.
