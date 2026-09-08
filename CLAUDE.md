# factlayer — working notes

## What this is
Extract facts from PDFs. Ground every fact in a quote. Decide across documents whether facts
corroborate, contradict, or reconcile. Must work on PDFs it has never seen.

## Non-negotiable invariants
1. No hard-coded facts, filenames, entities, or metrics. Anything document-specific is a bug.
2. Every stored fact carries: doc + page + verbatim quote + char offsets.
3. A fact whose quote does not literally appear in its page text is quarantined, never shown as active.
4. Verdicts come from rules in `reconcile.py`. The LLM only writes prose. Never let it decide.
5. Nothing runs against a real LLM in tests. `LLM_PROVIDER_ORDER=fake`.
6. No secrets in the repo. `.env.example` only.

## Code style — Ponytail / YAGNI ladder
Before writing anything: does it need to exist (no → skip) → already in the codebase (reuse) →
stdlib → native feature → installed dependency → one line → only then the minimum that works.
Lazy about the solution, never about reading.
Never trimmed away: input validation at trust boundaries, error handling, grounding checks.

## Layout
`src/factlayer/` one module per pipeline stage:
config → pdf → llm → normalize → extract → reconcile → store → pipeline → api

## Commands
```
pip install -e ".[dev]"
pytest                                  # offline, no key needed
python -m factlayer ingest data/**/*.pdf
uvicorn factlayer.api:app --reload
```
