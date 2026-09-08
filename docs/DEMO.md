# Recording the demo video

The assignment asks for one thing: a video, 3 minutes or under, showing a PDF being
processed and the four required cases, each with its evidence. This is the exact
sequence to record, against the deployed site, in order. No editing needed if you
follow it straight through — just trim the front and back.

## Before you press record

**1. Turn uploads on.** Browsing works with no key, but uploading a new PDF needs a
model reading it, and that needs a key on the deployment:

- Render dashboard → your service → **Environment** → **Add Environment Variable**
- Key: `GEMINI_API_KEY`, value: a free key from <https://aistudio.google.com/apikey>
- Save. Render redeploys — wait for it to go live again (a minute or two).

**2. Pick a PDF the system has never seen.** The six starter documents in `data/` are
already in the corpus, so re-uploading one just says "already in the knowledge layer"
— correct behaviour, but nothing to watch. Grab any other real PDF you have lying
around: a report, a filing, anything with sentences and numbers. Keep it short —
2 to 6 pages — so it finishes in well under a minute on camera. This also happens to
be the point: the assignment says the system "should not rely on ... document-specific
rules," and using something it has never indexed is how you show that's true rather
than claim it.

**3. Open <https://fact-knowledge-layer-mei7.onrender.com>** — that is the confirmed
deployment, showing 6 documents / 4,281 facts / 11,514 relationships / 743 quarantined
on the paper-coloured design. (Render gave it that suffixed name because
`fact-knowledge-layer` — the assignment's own title — was already taken by an
unrelated deployment. If the site ever needs redeploying and the URL changes again,
re-check it against these numbers before recording.)

## Shot list (target 2:30, hard limit 3:00)

**0:00–0:15 — What it is**
Front page. Read the one-liner under the masthead. Point at the ledger:
6 documents, ~4,281 facts, ~11,514 relationships already computed.

**0:15–0:50 — A PDF being processed** *(the assignment requires this on screen)*
Scroll to **"Upload your own PDF"**. Drop the new file. Narrate what's on screen while
it reads: "Reading *filename*… Ns elapsed." When it finishes, the note line reports the
page count, facts extracted, quarantined count, and new relationships — read that line
aloud, then say: "it just became part of the same knowledge layer, compared against
everything already there."

**0:50–1:15 — Case 1, corroboration** *(Exhibits, card 1)*
Two different documents state India's FX reserves for the same period — one as
$706 billion, the other as USD 704.9 billion. Point at the two quotes, the two page
numbers, the two source files. Say: normalised, they land 0.16% apart.

**1:15–1:35 — Case 2, contradiction** *(Exhibits, card 2)*
One director, two different DINs, two pages of the same prospectus. Both cannot hold.
Say: the system has no idea what a DIN is — it flagged this because two claims about
one subject disagreed.

**1:35–2:05 — Case 3, explained by context** *(Exhibits, card 3 — linger here, it's the
point of the whole project)*
Adjusted EBITDA is 757.86 in one filing and 76 in another. Not flagged as a
contradiction: one is stated in ₹ Million, the other in ₹ Cr. Say the rule out loud:
**"a difference between two numbers is not a contradiction until the system has tried
to explain it — by period, unit, currency, or scope — and failed."** Point at
"decided by rules" on the card: the model only wrote the sentence, it never chose the
verdict.

**2:05–2:25 — Case 4, an honest failure** *(Exhibits, card 4, or the Explore →
Quarantined tab)*
A quote that reads like a real disclosure and carries real numbers — and is not on the
page it claims. Say: grounding caught it before it became a fact, so it sits in
quarantine with the reason attached rather than silently corrupting the data.

**2:25–2:30 — Close**
"The four cases update live against whatever is uploaded — they are not hand-picked."

## What not to show

Code, the terminal, the test suite. None of it is required and it eats the clock.
The README covers architecture for anyone who wants it; the video is for the four
cases and the one live upload.

## After recording

1. Upload the video (YouTube "unlisted" or a shared Drive link both work).
2. Put that link in `README.md` under **## Video Demo**, replacing "_To be added._".
3. Put the confirmed live URL in `README.md` line 13, replacing `<!-- LIVE_URL -->`.
4. Submit both links through the form in the assignment PDF.
