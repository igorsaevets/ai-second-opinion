# Snapshot classifier (Kit-Б-12, since v1.73.0)

`citecheck.is_snapshot_url(url)` classifies one URL as a historical snapshot, or returns
`None` when the URL points to the current source. Regex-only. No HTTP.

## Why this matters

A legal brief that cites `www.govinfo.gov/content/pkg/CFR-2019-title8-vol1/xml/...` quoted
the CFR that existed in 2019. A brief that cites `www.govinfo.gov/link/uscode/8/1158`
quoted the U.S. Code that governs the case today. Both look equally official; without URL
inspection a reviewer cannot tell them apart. If the case turns on today's text, the first
citation may be quoting an obsolete provision — silently.

Same problem across `web.archive.org/web/YYYYMMDD/...` (frozen), `ecfr.gov/on/YYYY-MM-DD/...`
(frozen), `perma.cc/XXXX-XXXX` (frozen), vs `ecfr.gov/current/...` (live) and
`federalregister.gov/documents/YYYY/...` (current publication, dated but not archived).

## Kinds

Every kind is a value in the exported `SNAPSHOT_KINDS` frozenset.

| kind | pattern | year? | date? | congress? | volume? |
|---|---|---|---|---|---|
| `wayback` | `web.archive.org/web/{timestamp}/URL` | yes | if ≥ 8 digits | — | — |
| `archive-today` | `archive.ph/{timestamp}/URL` and mirrors | yes | if ≥ 8 digits | — | — |
| `archive-today-short` | `archive.ph/{4-10 alphanum}` | — | — | — | — |
| `govinfo-cfr` | `govinfo.gov/.../CFR-YYYY-titleN-...` | yes | — | — | — |
| `govinfo-uscode` | `govinfo.gov/.../USCODE-YYYY-titleN-...` | yes | — | — | — |
| `govinfo-statute` | `govinfo.gov/.../STATUTE-N-...` | — | — | — | yes |
| `govinfo-fr` | `govinfo.gov/.../FR-YYYY-MM-DD-...` | yes | yes | — | — |
| `govinfo-plaw` | `govinfo.gov/.../PLAW-{congress}publ...` | — | — | yes | — |
| `govinfo-bills` | `govinfo.gov/.../BILLS-{congress}...` | — | — | yes | — |
| `ecfr-dated` | `ecfr.gov/on/YYYY-MM-DD/...` | yes | yes | — | — |
| `perma-cc` | `perma.cc/XXXX-XXXX` | — | — | — | — |

*STATUTE-N carries the STATUTES AT LARGE volume number (volume 104 covers 1990's laws).
It is not a year — a downstream reader can map volume to year when needed.*

Archive.today mirror domains all classify equally: `archive.today`, `archive.ph`,
`archive.is`, `archive.li`, `archive.md`, `archive.fo`, `archive.vn`.

## What is deliberately NOT a snapshot

- `govinfo.gov/link/...` — permalink, resolves to current text. Not snapshot.
- `ecfr.gov/current/...` — live view of the eCFR. Not snapshot.
- `federalregister.gov/documents/YYYY/MM/DD/DOCID/...` — the DATE is the publication date
  of a still-current document. Not snapshot. Use `citecheck.resolve_federal_register` for
  those; it checks whether the DOCID resolves to the article the slug claims.
- Random other URLs, GitHub, docs.python.org, etc. — the DEFAULT is «current». A URL that
  the classifier does not recognise is not «unknown»; it is treated as live.

## API

```python
from citecheck import is_snapshot_url, snapshot_urls_in_answer, summarize_snapshots

is_snapshot_url("https://web.archive.org/web/20230515000000/https://example.com/")
# → {"kind": "wayback", "year": 2023, "date": "2023-05-15"}

is_snapshot_url("https://www.ecfr.gov/current/title-8/chapter-I/subchapter-B/part-208")
# → None

snapshot_urls_in_answer(answer_markdown)
# → [{"url": "...", "kind": "wayback", "year": 2023, "date": "2023-05-15"}, ...]
# Deduplicated by (host, path), order preserved.

summarize_snapshots(hits)
# → "3 snapshot(s): govinfo-cfr=2, wayback=1"
```

## Standalone CLI

```powershell
python citecheck.py --answer ANSWER.md --snapshot
```

Composes with `--resolve-urls`. Both flags together print DEAD-URL check first, then
snapshot classification. No event log needed.

## Wire in orchestrate.py

`call_oai_reviewer` runs classification right after `_cite_check`. If any citation is a
snapshot, one advisory line is added to `note` for the channel — visible in `REPORT.md`
and per-channel summary. The classification is wrapped in `try/except`: it cannot affect
a paid call, cannot flip the exit code. Same fail-safe policy as Б-9/Б-10.

## Failure modes it will NOT create

1. **False «fabricated»** — a URL that fails to classify is NOT marked as fabricated. It
   is silently treated as live. Fabrication detection lives in `probe_url` (DEAD 404) and
   in Kit-Б-9 quote verification, not here.
2. **False «snapshot»** — the patterns are anchored to specific host+path shapes. A random
   URL that happens to contain `2023` will not classify.
3. **Exit-code flip** — advisory only. Every path returns None on any exception.

## What this classifier does NOT know

- **Which is the today text.** Even after classifying a URL as snapshot, the current text
  under the same citation could be identical to what the snapshot shows. Only the reviewer
  or a live fetch can decide. Б-12 says «this quote is frozen», not «this quote is stale».
- **What the CONGRESS number maps to as a year.** 116th Congress = 2019-2021, 117th =
  2021-2023, etc. — but a downstream consumer needs the mapping; this file returns the
  raw congress integer, not a year.
- **Whether the snapshot is fresher than the live page.** A `web.archive.org` capture from
  2024 of a page that has since been deleted is more useful than the 404 the live URL now
  returns. The distinction is left to the reviewer.

## Scope split with Kit-Б-10 (grounding classifier)

Both features flag «possibly stale citation», but they measure different things.

| | Kit-Б-10 (`ground_classify.py`) | Kit-Б-12 (`citecheck.is_snapshot_url`) |
|---|---|---|
| Input | body text + quote | URL string |
| Mechanism | TF-IDF cosine + topic overlap + rule matrix | pure regex on host+path |
| Output | V/N/P/W/F/T/D/U/S/X/AMBIGUOUS | dict{kind,year,date[,congress]} or None |
| Failure mode | fuzzy match, thresholds, calibration | deterministic, no thresholds |
| Wire | `call_oai_reviewer` after quote_verify | `call_oai_reviewer` after `_cite_check` |
| Sidecar | `<channel>.quote-verify.md` (superset of Б-9) | `note` line only |

Deliberately separate files because the design pressures point in opposite directions.
Б-10 needs calibration on a growing corpus (see R104/R106 lessons); Б-12 is a static
pattern list that ages only when a vendor invents a new URL shape.
