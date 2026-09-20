# quote_verify.py — byte-check quotes against the pages we fetched

Advisory sidecar. Runs after every OR-channel review; never fails a run.

## What it answers

For each quotation in an OR-channel answer, one of:

* `[VERIFIED]` — the quoted text is on the bytes we fetched from the cited URL.
* `[NEAR-MATCH]` — the quoted text is 1–3 characters off. A `diff:` line shows
  what changed, plus a class: `punct-only` (typical of R44 vendor corruption)
  or `digit-change` / `letter-change` / `mixed` (SUSPICIOUS, ask the reader).
* `[UNSEEN-BYTES]` — the URL was opened, but this quote is not on the page we
  hold. The model wrote it from memory, or the page changed under us.
* `[UNSEEN-URL]` — the cited URL was never opened by this channel. Same class
  of failure as citecheck's `UNVERIFIED`, at a different level.
* `[SHORT-UNVERIFIABLE]` — the normalized quote is ≤25 chars; a 3-word phrase
  matches a random 400 KB page too often. We refuse to say more than that.
* `[NO-URL]` — the quote has no URL near it. Nothing to compare against.

Never `[FABRICATED]`, never `[CORRUPT]`. Categorical accusations are the
FP-tolerance line the design forbids to cross: a reader who sees the diff and
the class can weigh it; a reader who sees a verdict starts arguing with the
tool.

## When it runs

Inside `call_oai_reviewer` in `orchestrate.py`, right after `_cite_check` and
before the return dict. Every channel routed to `call_oai_reviewer` — that is
`kind in {"openrouter", "oai"}` — gets a sidecar `<CNAME>.quote-verify.md`
next to its answer, plus a one-line summary in `notes`.

CLI channels (`agy`, `codex`, `grokcli`) do not go through this path — they
fetch through their own infrastructure, we hold no bytes on our disk, and this
tool structurally cannot check them. `citecheck` still runs on the URLs.

## The three layers, and why in that order

1. **URL provenance** — pure set membership on `opened_urls`. Free. If a quote
   cites `https://govinfo.gov/link/uscode/8/1158` and that URL is not in the
   channel's opened set, the quote could not have come from that page. That is
   a full stop — no byte check needed.
2. **Byte match with normalization** — read the persisted cleaned-text from
   `<rundir>/<cname>.fetches/<slug>.txt`, normalize both the quote and the
   body, try exact substring first (99% of the correct-quote case), then a
   local fuzzy match: SequenceMatcher locates the best-matching region, and a
   capped Levenshtein computes the true edit distance in a small window.
3. **Short-quote guard** — quotes ≤ 25 chars after normalization are marked
   `[SHORT-UNVERIFIABLE]` up front. A byte match on a 3-word phrase in a
   400 KB page returns a random position; graded as "matched" it would be
   false positives all the way down.

## Normalization — one function, applied to both sides

The mistake this closes measured 33% wrap-defect and ~5% ligature-defect on
real reviews (from the krokai measurement, R51). The normalizer:

* Unicode NFC (composes precomposed pairs into single code points).
* Collapses any run of whitespace — including `\n`, `\t` — to one space. This
  is the single biggest source of false negatives; `may not\napply` and
  `may not apply` are the same quote.
* Curly quotes / apostrophes → ASCII (`«»""''´\``` → `"` and `'`).
* Ligatures (`ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ`) → letters (`ff fi fl ffi ffl st st`). Present
  on any web page ingested from PDF.
* Non-breaking and thin/wide space variants → regular space.
* Zero-width characters (`​‌‍`, BOM, WJ) → removed entirely.
* Ellipsis character `…` → three ASCII dots.

**What is NOT normalized, on purpose.** Brackets, word order, case: they carry
meaning. `SHALL` in a statute is not `shall` — case in legal citations is
rhetorical. Bracket balance is what the R44 detector cares about.

## Fuzzy match — thresholds and their justification

For a needle of length *n*:

```
threshold = min(3, max(1, floor(n * 0.02)))
```

* n = 25 → threshold 1 (tight — reflects the short-quote sensitivity)
* n = 100 → threshold 2
* n = 150 → threshold 3
* n = 300 → threshold 3 (capped)

This narrow band catches the R44 case (1 char in a 50–200-char quote) and
excludes real fabrications (`208 → 209` is a digit-change and lands as
`[NEAR-MATCH]` with `kind=digit-change`, which is SUSPICIOUS, not silent).

## FP-tolerance policy («ложное срабатывание хуже пропуска»)

* Exit code is **always 0**. This tool never blocks a review.
* Short quotes never FAIL. They land in `[SHORT-UNVERIFIABLE]`.
* `[NEAR-MATCH]` is a question, not an accusation. The diff-view shows what
  changed; the reader decides whether the change is corruption or fabrication.
* `[MEMORY]`-tagged quotes are skipped entirely. The model has already said
  "not from a page"; we do not check.
* The Ф2 calibration will measure FPR on a honest corpus (`runs/r80-panel-test/*`).
  If FPR > 5%, the fix is to widen the NORMALIZER (add missed classes), NOT to
  loosen the fuzzy threshold. Loose thresholds hide vendor corruption, which
  is the whole point of the tool.

## Prep-step (persistence) — layout and cost

The fetch loop in `call_oai_reviewer` writes the cleaned-text of every
successful fetch to `<rundir>/<cname>.fetches/<slug>.txt`. The slug is a
deterministic filesystem-safe name from the URL (host+path+query, plus a
12-char sha1 suffix so different URLs never collide).

* Layout: **`<cname>.fetches/`** (dot suffix), consistent with existing
  per-channel artefact names (`<cname>.progress.log`, `<cname>.events.ndjson`,
  `<CNAME>.md`). Not the nested `<cname>/fetches/` form the design first
  sketched — one flat per-channel dir is easier to grep and inspect.
* Cost: cleaned-text is capped at ~400 KB per page (FETCH_MAX_BYTES),
  fetches at ~10 per channel per round (fetch budget), so ≤ 4 MB per channel
  per round. `runs/` is already `.gitignore`d.
* PII: bytes are stored as fetched — no scrubbing. A Federal Register letter
  could contain personal names; `runs/` never leaves the machine, so this is
  acceptable for Ф1. Scrubbing at persist would break the byte match itself
  (VERIFIED against scrubbed bytes when the quote has the real name would
  become UNSEEN-BYTES); Ф2 will decide if an opt-in scrub makes sense.
* Failure: any IO error on persist is silent — Б-9 is optional infrastructure
  and a persist failure just means the byte-match layer reports
  `[UNSEEN-BYTES]` for the affected URL, which is honest (we do not have the
  bytes), not a false alarm.

## Reading a sidecar

```
# Б-9 quote-verify report for KIMI

**Summary:** 3 VERIFIED, 1 NEAR-MATCH, 1 UNSEEN-BYTES (5 quote(s) extracted,
                                                         4 fetched page(s) on disk)

Advisory tool: this report never fails the run. Read it, then decide.

## [VERIFIED] (3)
- «safe third country agreement...»
  - source: https://govinfo.gov/link/uscode/8/1158
  - exact match at body offset 4830

## [NEAR-MATCH] (1)
- «208(a)2)(D) may apply»
  - source: https://govinfo.gov/link/uscode/8/1158
  - distance 1, kind=punct-only
  - diff: `208(a){+(+}2)(D) may apply`
```

The `diff:` line uses `[- ... -]` for chars in the QUOTE not in the page
(deletions from body's side) and `{+ ... +}` for chars in the PAGE not in the
quote (things the vendor may have dropped). A `punct-only` change is the R44
signature — usually vendor corruption during streaming. A `digit-change` or
`letter-change` is worth reading the source directly.

## Running standalone

```
python quote_verify.py --answer ANSWER.md \
                       --fetches-dir DIR \
                       --opened-urls-list OPENED.txt \
                       -o sidecar.md \
                       --channel kimi
```

Any missing input degrades honestly: no `--fetches-dir` → quotes with URLs
report `[UNSEEN-BYTES]` (we cannot verify without bytes); no `--opened-urls`
→ every URL-bearing quote reports `[UNSEEN-URL]`. Exit code is always 0.

## Scope and non-goals

* Covers class **(a) fabricated** and class **(c) vendor-corrupts** from the
  known error taxonomy.
* Does **not** cover class **(b) kim-inverted** — quote's words on the page,
  surrounding claim inverts the meaning. That is Б-10 (grounding classifier,
  task #21), and requires semantics.
* Does **not** replace `citecheck` — that checks URL existence and grounding
  (`did we open this URL`); Б-9 checks quoted-text-vs-page-bytes. They compose.
* Does **not** replace `krokai` — that checks quotations against a local legal
  corpus (USC / CFR / Policy Manual). Б-9 checks against arbitrary web URLs
  fetched in the current round. Complementary.
* Does **not** distinguish "page updated since our fetch" from "vendor
  corrupted the text". Both land as `[NEAR-MATCH]`. Our bytes are a snapshot
  at fetch time; we do not re-fetch to compare.

## Ф2 — the calibration this release leaves open

Six questions the retrospective run on `runs/r80-panel-test/*` should answer:

1. Is the fuzzy threshold `2% AND ≤3 chars` universal? R44 is the only
   documented vendor-corruption case; more may show a different pattern.
2. What fraction of `[OPENED]`-tagged claims does the extraction miss?
   `_QUOTED_RE` covers quotation marks and block quotes; it does NOT (yet)
   parse `[OPENED]`-tag semantics.
3. Is 25 chars the right SHORT-UNVERIFIABLE threshold, or is a sentence-anchor
   layer needed between?
4. Are ligatures actually present in web pages? (Their 5% rate was measured on
   PDF; web pages may be lower.)
5. When should `<rundir>/<cname>.fetches/` be cleaned up? Same policy as the
   parent `<rundir>/` — but that policy is not written down.
6. Should persisted bytes be PII-scrubbed on write? Trade-off: scrubbing
   breaks the byte-match VERIFIED path for legitimate quotes containing the
   PII. Ф2 must weigh the disk-exposure risk against the false-negative rate.
