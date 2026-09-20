# Grounding classifier (Б-10) — reference

Kit-Б-10 SEMANTIC shipped v1.71.0 (2026-09-20). Read this when a
`.quote-verify.md` sidecar shows a class you have not seen, or when you are
about to write a fixture / calibrate a threshold.

**What this tool answers.** For every quote that Б-9 marks, Б-10 assigns one
of **11 grounding classes**, so a reviewer sees the *action* instead of the
*mechanism*: `[P]` "ask model for the exact quote" instead of raw
`[UNSEEN-BYTES] not found in body`.

**What this tool does NOT answer.** Whether the quote is *true*. That is
krokai-law's job, and even it needs external corpora. Б-10 only classifies
the *relationship* between quote and page. And it only runs on OR-channels
(quote_verify's scope): CLI channels (agy, codex, grokcli) fetch inside
their own gateway, so this project holds no bytes to check.

---

## The 11 classes

| id | Meaning | Reviewer action | Signal |
|---|---|---|---|
| **V** | verified verbatim | trust | Б-9 = VERIFIED (exact byte match) |
| **N** | near-verbatim | trust, check the diff | Б-9 = NEAR-MATCH (fuzzy ≤2 %, ≤3 chars) |
| **P** | paraphrase legitimate | ask model for exact quote | max_sim ≥ 0.5 AND containment ≥ 0.4 |
| **W** | wrong-URL attribution | fix URL | containment < 0.15 (quote's words absent) |
| **F** | fabricated content | reject | max_sim < 0.2 AND containment < 0.4 |
| **T** | truncated URL (R44) | recover intended URL | URL fails truncation heuristic |
| **D** | dead / fabricated URL | reject | UNSEEN-URL + cite_check DEAD |
| **U** | unfetched-but-live URL | investigate | UNSEEN-URL + cite_check LIVE |
| **S** | short-unverifiable | pass | Б-9 = SHORT-UNVERIFIABLE (≤25 chars) |
| **X** | no-URL near quote | ask model for URL | Б-9 = NO-URL |
| **?** | AMBIGUOUS | human review | middle-range signals (safety net) |

**8 living classes + 3 pass-through** (S, X, and AMBIGUOUS). AMBIGUOUS exists
by design — it is the FP-tolerance safety net when signals do not clearly
distinguish paraphrase from wrong-URL from fabrication.

## Decision matrix (default, stdlib TF-IDF)

Applied only to UNSEEN-BYTES from Б-9. The other Б-9 statuses map directly
(VERIFIED → V, NEAR-MATCH → N, SHORT-UNVERIFIABLE → S, NO-URL → X).

```
                        max_sim ≥ 0.5     max_sim < 0.2     max_sim 0.2-0.5
  topic ≥ 0.4               P              AMBIGUOUS          AMBIGUOUS
  topic 0.15-0.4         AMBIGUOUS               F             AMBIGUOUS
  topic < 0.15                          W (all cases)
```

**Order matters.** W is checked first (explicit topic mismatch is decisive).
Then P (both signals strong). Then F (both signals weak, but topic ≥ TOPIC_LOW —
if topic < TOPIC_LOW, W already fired). Everything else falls to AMBIGUOUS.

For UNSEEN-URL:

```
  is_url_truncated(url) → T
  cite_check(url) == DEAD → D
  cite_check(url) == LIVE → U
  cite_check(url) == BLOCKED / not run → AMBIGUOUS
```

## Signal definitions

**max_sim** — TF-IDF cosine similarity between the quote and every body
sentence, take the max. Sentence tokenizer preserves ~30 abbreviations
(`U.S.C.`, `Fed.R.Civ.P.`, `Fig.`, `No.`, etc.) so `Case No. 22-15432` does
NOT split. Ф2 will measure false-split rate.

**topic_overlap** — ASYMMETRIC containment:
`|quote_content_words ∩ body_content_words| / |quote_content_words|`.

Why NOT Jaccard (which the R102 design proposed): measured on R101 gold set
2026-09-20 — Jaccard(quote, cpython/subprocess.py-body-198K) = **0.001**
even when the quote's own content-words like `terminate` sit clearly in the
page. Jaccard's denominator `|q ∪ b|` is dominated by the 800+ content-words
of the page, so any quote (short by construction) drowns. Containment asks
the RIGHT question — "are what the quote SAYS on this page" — and is
body-size-independent.

Content-word extractor drops:
* Stop words (basic bilingual: English `the/of/to/...` + Russian `и/в/не/...`)
* Digits (a 10-digit case number is not a topic word)
* Tokens < 3 chars (`io`, `os`, `up`)

**is_url_truncated** — heuristic for R44-class vendor-corrupts. Returns True
for:
* Bare host + suspect `.co` in a small list of well-known `.com` brands
  (`github.co`, `learn.microsoft.co`, etc.) with no path
* Host with no dot at all (`https://example`)
* URLs failing `urlsplit` parse

Deliberately narrow — a false T ("we called your intact URL corrupted") is
worse than a missed one. Ф2 measures FPR.

## Thresholds — hypotheses, Ф2 will calibrate

```python
_SIM_HIGH  = 0.5
_SIM_LOW   = 0.2
_TOPIC_HIGH = 0.4
_TOPIC_LOW  = 0.15
```

Not measured on a real corpus — they came from design intuition and the
smoke run on R101 gold set. Ф2 calibration will:
1. Run Б-10 on R101 + R80 corpora, classify each quote by hand.
2. Grid-search thresholds for max F precision @ ≥60 % recall.
3. If F precision < 90 % (the operator: "FP worse than miss"), widen AMBIGUOUS, do
   NOT tighten thresholds against the rule.

## Opt-in modules (v1.71.0: recognised, deferred; Ф3 will wire)

Set any of these env vars and Б-10 announces it in the sidecar approach line,
then falls back to default. Ф2 decides which are worth the code.

* `GROUND_EMBEDDINGS=1` — replace TF-IDF cosine with sentence-transformers
  MiniLM. Requires `torch` + `sentence-transformers` (~90 MB model).
  Catches word-swap paraphrase that TF-IDF misses. **Never default** —
  breaks stdlib-only kit invariant.

* `GROUND_LLM_VERIFY=<channel>` — for AMBIGUOUS quotes only, send a
  mini-prompt to a cheap channel: "Given this body and quote, classify
  as verbatim / paraphrase / wrong-URL / fabricated. Answer 1 letter."
  Cost per AMBIGUOUS quote: ~$0.0005 on orspark13cont. Vendor dep.

* `GROUND_WAYBACK=1` — for F candidates, look up the URL on
  web.archive.org near the fetch date. Rescues F → PAGE-UPDATED for
  legitimate cases where the page changed since fetch. Rate-limited.

## Integration point

Called from `orchestrate.py:call_oai_reviewer` right after `quote_verify.check`:

```python
try:
    import ground_classify as _b10
    _b10_report = _b10.classify(_b9_report, fetches_dir=_b9_fetches_dir,
                                cite_check_map=None)
    _sidecar_md = _b10.format_sidecar(_b10_report, channel_name=name)
except ImportError:
    _sidecar_md = _b9.format_sidecar(_b9_report, channel_name=name)
except Exception:
    _sidecar_md = _b9.format_sidecar(_b9_report, channel_name=name)
```

`cite_check_map=None` in v1.71.0 — UNSEEN-URL classification uses only the
truncation heuristic. Ф3 will wire cite_check_map from `_cite_check` output
so D/U classes activate.

## Standalone CLI

```powershell
python "<SKILL_DIR>\ground_classify.py" `
  --answer runs\r101-live-verify\ORMIMO25PRO.md `
  --fetches-dir runs\r101-live-verify\ormimo25pro.fetches `
  --opened-urls-json opened.json `
  --cite-check-json cite_check.json `
  --channel ormimo25pro `
  -o r101.grounded.md
```

Exit code always 0.

## Known limitations (Ф1)

* **Paraphrase with word substitution** (`terminate` ↔ `kill`) reads as
  AMBIGUOUS with TF-IDF. Sentence embeddings (opt-in Ф3) would catch this.
* **Long bodies** (>500 KB) load only 500 KB before normalize (Б-9's
  BODY_READ_CAP). If the quote sits after that, containment sees a
  truncated view.
* **Quote extraction** is by Б-9's `extract_quotes` rules — anything not
  wrapped in quotation marks or a block quote is left alone (design.md §6).
* **Bilingual stop-word list is minimal.** Ф2 will measure whether it
  needs expansion (technical or legal corpora with different word freq).
* **Truncation heuristic covers ~7 suspect `.co` hosts.** A wider list
  would trade precision for recall; Ф2 will measure honest URL FPR before
  expanding.
* **No cross-quote consistency** — if a model quotes the same passage twice
  with different wording, Б-10 classifies each independently. Cross-quote
  analysis is Б-14 (not scheduled).
* **No semantic negation.** "only if X" written as "unless X" reads as a
  legitimate P (word overlap high, sentence match good). Semantic inversion
  is Б-13 (not scheduled).

## Ф1 was here — the design bugs the smoke exposed

Design (R102) proposed Jaccard for topic_overlap. First smoke on R101 gold
set showed Jaccard(quote, 198K body) = 0.001 — every UNSEEN-BYTES scored as
W (100 % false positive) because the denominator scaled with body size.
Fix: switch to asymmetric containment before shipping. Thresholds moved
accordingly (Jaccard-scale 0.05/0.15 → containment 0.15/0.4).

R103 also attempted a stricter F rule (`max_sim < LOW AND topic < LOW`)
under the misreading "F needs both LOW". That made F unreachable because W
fires first on `topic < LOW`. Reverted to the design condition
`max_sim < LOW AND topic < TOPIC_HIGH`, which puts F in the middle-topic +
low-sim niche where invented-phrasing-on-topic quotes actually live.
