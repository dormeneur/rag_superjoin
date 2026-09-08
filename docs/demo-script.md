# Demo video — shot list (target 2:30, hard limit 3:00)

Record the deployed site. Nothing to install on camera, nothing to type.

**0:00–0:20 · What it is**
Front page. One line: "Extracts facts from PDFs, ties each to the sentence it came
from, and decides whether facts across documents agree." Point at the ledger:
6 documents, 4,281 facts, 11,514 relationships.

**0:20–0:45 · A PDF being processed** *(the assignment asks to show this)*
Upload panel → drop any PDF → the counts move. Say the two things that make it safe:
a fact is stored only if its quote is really on the page it cites, and re-uploading the
same file changes nothing.

**0:45–1:15 · Case 1, corroboration**
Exhibit 1. IMF says `$706 billion`, the Economic Survey says `USD 704.9 billion`, for
the same September 2024 reserves. Different institutions, months apart, neither cites
the other. Normalised they land 0.16% apart. Show both quotes and page numbers.

**1:15–1:40 · Case 2, contradiction**
Exhibit 2. One director, two different DINs on two pages of one prospectus. Both
cannot hold. Say the system has no idea what a DIN is — it flagged this because two
claims about one subject disagreed.

**1:40–2:10 · Case 3, explained by context** *(the interesting one — linger here)*
Exhibit 3. Adjusted EBITDA `757.86` in one document, `76` in the other. Not called a
contradiction: one is `₹ in Million`, the other `₹ Cr`. Say the rule out loud —
**a difference is not a contradiction until period, unit, currency and scope have each
had a chance to explain it.** Point at `decided by rules`: the model wrote the prose,
it did not choose the verdict.

**2:10–2:30 · Case 4, an honest failure**
Quarantined tab. A quote that reads like a real disclosure and is not on page 22.
Grounding caught it, so it never became a fact. 743 of 5,024 claims (14.8%) were
rejected this way, mostly multi-column pages.

**2:30–2:40 · Close**
"Verdicts are rules, so they are reproducible; the four cases on the front page are
chosen at query time, not hand-picked, so they re-derive against whatever you upload."

Do not show: the code, the tests, the terminal. They are in the README.
