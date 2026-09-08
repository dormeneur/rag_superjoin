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
python -m factlayer ingest data/delhivery/*.pdf
python -m factlayer export --out samples
```

Tests need no key at all — a scripted provider stands in for the model:

```bash
pytest
```

### Deploying it

The whole thing is one container: API, interface, and a knowledge layer already built
from the starter documents, so a deployment opens with something to look at rather than
an empty database.

```bash
python -m factlayer ingest data/*/*.pdf          # build the corpus
gzip -9 -c factlayer.db > deploy/factlayer.db.gz # 11 MB becomes 1.7 MB
deploy/publish.sh <hf-username> <space-name>     # push to a Hugging Face Space
```

It runs anywhere that takes a Dockerfile. Hugging Face Spaces is the default because it
is free, needs no card, and keeps a permanent URL. To enable uploads on the deployment,
add `GEMINI_API_KEY` under the Space's *Variables and secrets*.

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
library before dependency, minimum that works — which is why there are nine source
modules and no framework beyond FastAPI.

## The Four Cases

_To be filled in from real output once the full dataset has been processed._

1. **Corroborated across documents** — _pending_
2. **Genuine contradiction** — _pending_
3. **Apparent contradiction explained by context** — _pending_
4. **An extraction failure and how it is handled** — _pending_

## Limitations and Next Steps

**Does not work yet**

- **Multi-column pages interleave.** Text extraction reads across columns, so a page
  like a board-of-directors listing produces jumbled sentences. Claims from such pages
  usually fail grounding and land in quarantine — visible, but lost.
- **No OCR.** A scanned PDF is refused with a clear message rather than silently
  returning nothing.
- **Cross-currency facts are never compared.** Without an exchange rate for the right
  date, they are marked `RECONCILABLE / CURRENCY_MISMATCH` rather than reconciled.
- **Canonical-name merging can under-merge.** Two genuinely identical metrics worded
  very differently may stay separate, and separate metrics never meet to be compared.
- **A wrong-but-plausible extraction survives.** Grounding catches invented quotes and
  invented numbers, not a number correctly copied from the wrong row of a table.

**Next**

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
