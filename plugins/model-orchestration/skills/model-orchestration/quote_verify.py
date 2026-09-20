#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quote_verify.py - do the quotes in an OR-channel answer actually appear on the pages we
                  fetched for it? Costs nothing; reads bytes from disk.

    python quote_verify.py --answer ANSWER.md --fetches-dir DIR --opened-urls-list LIST
                           [-o SIDECAR.md]

WHY
---
Companion of citecheck.py, one level deeper. Where citecheck answers "was this URL
opened at all", quote_verify answers "does the QUOTED TEXT actually appear on the
bytes we hold from that URL". Three known classes on our own history:

    (a) fabricated       - quote written "from memory"; the URL was never opened,
                           or the quoted text is not on the page.
    (b) kim-inverted     - the URL was opened, quote's words are on the page, but
                           the surrounding claim inverts the meaning ("only if X"
                           written as "unless X"). SEMANTICS - out of scope for
                           this tool; that is B-10 (grounding classifier, task #21).
    (c) vendor-corrupts  - R44: grok deleted 35 open-parens from a 26 KB legal
                           review, `208(a)(2)(D)` -> `208(a)2)(D)`. Marker present,
                           the quote LOOKS right until you compare byte for byte.

This tool covers (a) and (c). Not (b).

THREE LAYERS, cheapest first
----------------------------

    Layer 1  URL provenance (free): if a claimed URL is not in this channel's
             opened_urls set, the quote could not have come from THAT page.
             -> [UNSEEN-URL]

    Layer 2  Normalized byte match. Read the persisted cleaned-text from
             <rundir>/<cname>.fetches/<slug>.txt (prep-step in orchestrate.py's
             fetch loop). Normalize both quote AND body (NFC + whitespace runs +
             curly quotes + ellipsis + ligatures + NBSP + zero-width). Try
             exact substring first, then a fuzzy match: SequenceMatcher locates
             a candidate window, a capped Levenshtein verifies within +-2%
             distance AND <=3 absolute chars.
             -> [VERIFIED] / [NEAR-MATCH] with diff-view / [UNSEEN-BYTES]

    Layer 3  Short-quote guard: <=25 chars after normalization is too permissive
             for byte matching - a 3-word phrase matches a random 400 KB page
             with high probability.
             -> [SHORT-UNVERIFIABLE]

The exit code is ALWAYS 0 - this tool is advisory and MUST NEVER block a review.
Same policy as citecheck.

FP TOLERANCE (Igor: "false alarm worse than a miss")
----------------------------------------------------
  - Exit code always 0.
  - Short quotes never FAIL, only [SHORT-UNVERIFIABLE].
  - Fuzzy thresholds are narrow (2% AND <=3 chars): vendor-corruption is usually
    1-2 chars in a local window; a real `208->209` fabrication in a 12-char
    context is 8% distance and does NOT slip through.
  - [NEAR-MATCH] is a QUESTION, not an accusation - diff-view shows what changed,
    reader decides. Never called "fabricated" without a diff.
  - Ф2 calibration must measure FPR on honest-corpus; if >5%, widen the
    normalization, NEVER loosen the fuzzy threshold.

SCOPE
-----
  In:  OR-channels (kind in {"openrouter", "oai"}), which fetch through OUR
       _safe_fetch_url and land bytes on disk. Runs from inside call_oai_reviewer.
  Out: CLI channels (agy, codex, grokcli) - their fetch happens inside the
       vendor CLI/gateway; we hold no bytes.
       Direct API channels (gemini url_context, xai native tools) - vendor
       does its own grounding; we hold no bytes.
       Semantic inversion - that is B-10.
       Fabricated URLs - that is citecheck's job; quote_verify runs AFTER.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# =============================================================================
# URL slug - ONE implementation, shared with orchestrate.py's prep-step.
# =============================================================================
# If this drifts from the prep-step, quote_verify reads the wrong file (or none)
# and every honest quote looks fabricated. "One rule, one home" applies to a
# filename convention exactly as it applies to a policy: two homes for one
# contract, and the copies rot. orchestrate.py imports slug_for_url from here,
# so the definition lives here and nowhere else.

def slug_for_url(url):
    """Filesystem-safe deterministic slug for a URL. Never > 116 chars.

    Reduces one URL to one stable filename. Uses the same normalization axis as
    orchestrate.py's _fetch_key (scheme dropped for the filename, host lowered,
    www stripped, path rstripped) plus a 12-char sha1 suffix so that two URLs
    which normalize the same visible base cannot collide on disk.
    """
    u = (url or "").strip().rstrip(".,;:")
    try:
        s = urlsplit(u)
        host = s.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = (s.path or "/").rstrip("/") or "/"
        base = host + path
        if s.query:
            base += "?" + s.query
    except ValueError:
        # Malformed URL: fall back to the fragment-stripped string. The FETCH
        # itself will fail on such a URL, but if a caller ever asks us for a
        # slug we still return something stable.
        base = u.split("#", 1)[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "url"
    h = hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:12]
    return safe[:100] + "--" + h + ".txt"


# =============================================================================
# Normalization - see design.md §3. Applied identically to quote AND body.
# =============================================================================

# Curly quotes / apostrophes / prime -> ASCII equivalents. Meaning is preserved;
# rendering differences are erased.
_QUOTE_MAP = {
    "«": '"', "»": '"',      # « »  (Cyrillic, French)
    "“": '"', "”": '"',      # " " (English curly)
    "‘": "'", "’": "'",      # ' ' (English single-curly, and apostrophe)
    "„": '"', "‟": '"',      # „ ‟ (German-style, reversed)
    "′": "'", "″": '"',      # prime, double prime
    "´": "'", "`": "'",      # acute, grave (used as apostrophe in typography)
}

# Ligatures - NFC does NOT decompose these (they are compatibility, not canonical).
# Web pages picked up from PDF conversion often carry them.
_LIGATURE_MAP = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
}

# Zero-width characters - removed entirely (they are invisible but affect matching).
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿⁠]")

# Non-breaking and thin/wide space variants -> regular space.
_SPACE_LIKE_RE = re.compile(r"[      　]")

# Ellipsis character -> three ASCII dots.
_ELLIPSIS_CH = "…"


def normalize(text):
    """Canonical form. Applied identically to needle and haystack."""
    if not text:
        return ""
    # NFC first: composes e+acute -> é (one code point), so we compare only one shape.
    t = unicodedata.normalize("NFC", text)
    t = _ZERO_WIDTH_RE.sub("", t)
    t = _SPACE_LIKE_RE.sub(" ", t)
    t = t.replace(_ELLIPSIS_CH, "...")
    for src, dst in _QUOTE_MAP.items():
        t = t.replace(src, dst)
    for src, dst in _LIGATURE_MAP.items():
        t = t.replace(src, dst)
    # Whitespace runs (including \n \t) -> single space. This closes the R51-class
    # "wrap defect": `may not\napply` normalizes to `may not apply`.
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# =============================================================================
# Fuzzy match - locate an approximate occurrence of `needle` in `haystack`.
# =============================================================================

def _levenshtein_capped(a, b, cap):
    """Levenshtein distance with an early cap. Returns None if > cap.

    Standard DP, but if the whole row min exceeds cap we exit early - the
    distance can only grow from here. For our sizes (needle <= few hundred
    chars, capped window a bit larger) this is fast.
    """
    if abs(len(a) - len(b)) > cap:
        return None
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        row_min = cur[0]
        for j, cb in enumerate(b):
            cur.append(min(cur[j] + 1,
                           prev[j + 1] + 1,
                           prev[j] + (0 if ca == cb else 1)))
            if cur[-1] < row_min:
                row_min = cur[-1]
        prev = cur
        if row_min > cap:
            return None
    return prev[-1] if prev[-1] <= cap else None


def fuzzy_find(needle, haystack, max_ratio=0.02, max_abs=3):
    """Find the best occurrence of `needle` in `haystack` up to a small edit distance.

    Returns (offset, distance, matched_snippet) or None if nothing within threshold.

    Two-tier strategy:
      1. Exact substring first (the common case, O(n+m)).
      2. Fuzzy: SequenceMatcher finds the longest common substring cheaply, we
         anchor on it, then a capped Levenshtein sweeps a small window around
         the anchor to compute the true edit distance.

    Threshold = min(max_abs, max(1, floor(len(needle) * max_ratio))).
    For needle=100 chars, max_ratio=0.02 -> 2, cap min(2,3)=2.
    For needle=12 chars, max_ratio=0.02 -> 0 -> raised to 1.
    """
    if not needle or len(haystack) < len(needle):
        return None
    n = len(needle)
    threshold = min(max_abs, max(1, int(n * max_ratio)))

    # Layer 2a: exact substring
    idx = haystack.find(needle)
    if idx >= 0:
        return (idx, 0, needle)

    # Layer 2b: fuzzy via SequenceMatcher-anchored local Levenshtein.
    # Note: a single middle-position edit can drop the longest common substring
    # to as little as needle/2, so the gate below is deliberately loose - we
    # cheaply anchor on any match >= 3 chars and let the capped Levenshtein
    # decide whether the alignment holds.
    sm = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    lm = sm.find_longest_match(0, n, 0, len(haystack))
    if lm.size < 3:
        return None

    # Anchor: where `needle` would start in haystack if the longest match aligns.
    anchor = max(0, lm.b - lm.a)
    # Sweep a small window around the anchor. Offsets +/- threshold+1 account
    # for a leading char that was inserted/deleted (which shifts the alignment).
    best = None
    lo = max(0, anchor - threshold - 1)
    hi = min(len(haystack) - n + threshold + 1, anchor + threshold + 2)
    for offset in range(lo, hi):
        # Compare against three window widths: shorter (deletion), same length,
        # and longer (insertion). One of them must contain the aligned match.
        for wlen in (max(1, n - threshold), n, n + threshold):
            if offset + wlen > len(haystack):
                continue
            window = haystack[offset:offset + wlen]
            d = _levenshtein_capped(needle, window, threshold)
            if d is not None and (best is None or d < best[1]):
                best = (offset, d, haystack[offset:offset + n + threshold])
                if d == 0:
                    return best
    if best and best[1] <= threshold:
        return best
    return None


# =============================================================================
# Diff view - classify what CHANGED between the near-match snippet and the quote.
# =============================================================================

def classify_diff(quote_norm, matched_snippet):
    """Return a short string classifying the character changes.

    Categories, chosen so a reader can weigh a NEAR-MATCH by kind:
      "punct-only"     - only punctuation/whitespace differs (typical of R44
                         vendor-corruption: `208(a)(2)(D)` -> `208(a)2)(D)`)
      "digit-change"   - a digit was substituted (SUSPICIOUS: often fabrication)
      "letter-change"  - a letter was substituted (SUSPICIOUS: possible either)
      "mixed"          - combinations
      "unknown"        - nothing measurable (empty diff or all identical)

    Uses SequenceMatcher's opcodes to find the exact edited spans.
    """
    if not quote_norm or not matched_snippet:
        return "unknown"
    a = quote_norm
    b = matched_snippet[:len(a) + 3]  # bounded so we do not scan a whole page
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    changed_from_a = []
    changed_from_b = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        changed_from_a.append(a[i1:i2])
        changed_from_b.append(b[j1:j2])
    if not changed_from_a and not changed_from_b:
        return "unknown"
    all_chars = "".join(changed_from_a) + "".join(changed_from_b)
    has_letter = any(c.isalpha() for c in all_chars)
    has_digit = any(c.isdigit() for c in all_chars)
    has_punct = any((not c.isalnum()) and not c.isspace() for c in all_chars)
    has_space = any(c.isspace() for c in all_chars)
    if has_letter and has_digit:
        return "mixed"
    if has_letter:
        return "letter-change"
    if has_digit:
        return "digit-change"
    if has_punct or has_space:
        return "punct-only"
    return "unknown"


def build_diff_view(quote_norm, matched_snippet, max_chars=120):
    """Human-readable inline diff. `abc` -> `a[-b-]{+B+}c` style, bounded length."""
    if not quote_norm or not matched_snippet:
        return ""
    a = quote_norm[:max_chars]
    b = matched_snippet[:max_chars + 3]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(a[i1:i2])
        elif tag == "delete":
            out.append("[-" + a[i1:i2] + "-]")
        elif tag == "insert":
            out.append("{+" + b[j1:j2] + "+}")
        elif tag == "replace":
            out.append("[-" + a[i1:i2] + "-]{+" + b[j1:j2] + "+}")
    return "".join(out)


# =============================================================================
# Quote extraction - see design.md §6. Four rules.
# =============================================================================

# 1. Anything inside a paired quotation mark. All common shapes. NOT single ' - that
#    catches apostrophes as quotes and produces a fountain of false candidates.
_QUOTED_RE = re.compile(
    r'"([^"\n]{4,400})"'                                 # "..."
    r"|«([^»\n]{4,400})»"                 # «...»
    r"|“([^”\n]{4,400})”"                 # "..."
    r"|„([^“”\n]{4,400})[“”]"   # „..."
)

# 2. Block quotes (markdown `> `). Multi-line, so the whole run is one candidate.
_BLOCKQUOTE_RE = re.compile(r"(?m)^>\s*(.{4,400})$")

# 3. Provenance tag inline: [OPENED] / [SNIPPET] / [MEMORY] plus optional URL.
#    We SKIP [MEMORY] entirely per design.md §5 rule 4: model has already told us
#    this is not from an opened source.
_TAG_RE = re.compile(
    r"\[(?P<tag>OPENED|SNIPPET|MEMORY)\]\s*"
    r"(?:[^\[\n]{0,200}?)?"
    r"(?:\((?P<url>https?://[^\s)\]>]+)\))?",
    re.IGNORECASE,
)

# 4. `per <URL>, ...` / `according to <URL>: "..."` / `Section X: "..."`. The
#    quote here has already been caught by rule 1 (they wrap in quotes); this
#    rule just associates a URL that appears NEAR a quoted candidate.
_URL_RE = re.compile(r"https?://[^\s)\]>\"'`|]+")


def _find_nearby_url(text, quote_end, radius=200):
    """Return the closest URL following `quote_end` within `radius` chars, or None."""
    window = text[quote_end:quote_end + radius]
    m = _URL_RE.search(window)
    return m.group(0).rstrip(".,;:") if m else None


def extract_quotes(answer_text):
    """Yield candidate quotes as dicts.

    Each dict has: quote (raw text), source_url (str or None), kind (str).
    Duplicates are collapsed on (quote, source_url).

    We deliberately do NOT try to be exhaustive here. The FP-tolerance policy
    says a missed quote is better than a false alarm on a paraphrase - so any
    text NOT wrapped in an unambiguous quotation shape is left alone.
    """
    if not answer_text:
        return []
    text = answer_text
    seen = set()
    out = []

    # Rule 3 first: find [MEMORY]-tagged spans so we can SKIP them in rule 1.
    # A [MEMORY] tag annotates the PRECEDING quote as "not from an opened
    # source". Skip quotes whose END sits within `LOOKBACK` chars BEFORE the
    # tag - not after (a quote after the tag has nothing to do with it).
    # 60 chars covers `"quote text" [MEMORY]` and `"quote" (source: memory) [MEMORY]`
    # but is short enough not to swallow the previous paragraph's honest quote.
    _MEMORY_LOOKBACK = 60
    memory_tag_positions = []
    for m in _TAG_RE.finditer(text):
        if (m.group("tag") or "").upper() == "MEMORY":
            memory_tag_positions.append(m.start())

    def _preceded_by_memory(quote_end):
        return any(tag_start - _MEMORY_LOOKBACK <= quote_end <= tag_start
                   for tag_start in memory_tag_positions)

    # Rule 1: quoted text
    for m in _QUOTED_RE.finditer(text):
        if _preceded_by_memory(m.end()):
            continue
        quote = next((g for g in m.groups() if g), "").strip()
        if not quote:
            continue
        # Skip if the "quote" is really a URL or a filename
        if _URL_RE.match(quote) or quote.startswith(("http", "www.")):
            continue
        url = _find_nearby_url(text, m.end())
        key = (quote, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"quote": quote, "source_url": url, "kind": "quoted"})

    # Rule 2: block quotes
    for m in _BLOCKQUOTE_RE.finditer(text):
        if _preceded_by_memory(m.end()):
            continue
        quote = m.group(1).strip()
        if not quote or _URL_RE.match(quote):
            continue
        url = _find_nearby_url(text, m.end())
        key = (quote, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"quote": quote, "source_url": url, "kind": "block"})

    return out


# =============================================================================
# Main check - runs the three layers over one answer + its fetches directory.
# =============================================================================

# Guard: never punish a short quote with a byte match - see design.md §3 Layer 3.
SHORT_QUOTE_MIN = 25

# Cap on how much of a page we normalize per quote check. A 400 KB body is fine.
BODY_READ_CAP = 500_000  # bytes


def _load_body(fetches_dir, url):
    """Read the persisted cleaned-text for a URL, or None if not present.

    Never raises - a missing file, a bad decode, or an OS error all resolve to
    None (which the caller treats as "we have no bytes for this URL"). This
    runs after every OR-channel review; it must not fail the review.
    """
    if not url or not fetches_dir:
        return None
    slug = slug_for_url(url)
    path = os.path.join(fetches_dir, slug)
    try:
        with open(path, "rb") as f:
            raw = f.read(BODY_READ_CAP)
        return raw.decode("utf-8", "replace")
    except OSError:
        return None


def _norm_url_for_set(url):
    """Same host+path comparison as orchestrate.py's _norm_url. Kept LOCAL so
    that quote_verify can be imported without dragging orchestrate.py's whole
    module graph. Deliberately narrower: no ValueError-handling for degenerate
    IPv6 - the URL comes from an opened_urls list that already passed the fetch
    fence upstream, so it is well-formed by construction.
    """
    if not url:
        return ("", "")
    u = url.rstrip(".,;:")
    try:
        s = urlsplit(u)
        return (s.netloc.lower().replace("www.", ""),
                (s.path or "/").rstrip("/").lower())
    except ValueError:
        return (u.lower(), "")


def check(answer_text, fetches_dir=None, opened_urls=None):
    """Run the three layers over one answer. Returns a dict.

    Returned dict:
        {
          "summary_line": "3 VERIFIED, 1 UNSEEN-BYTES, 0 NEAR-MATCH"  (advisory string),
          "lines": [ {status, quote, source_url, detail}, ... ],
          "counts": {VERIFIED, UNSEEN_URL, UNSEEN_BYTES, NEAR_MATCH, SHORT_UNVERIFIABLE, NO_URL},
          "n_quotes": int,
          "n_fetches_available": int,
        }

    Never raises. If fetches_dir is None or missing, every quote-with-URL that
    is in opened_urls still gets [UNSEEN-BYTES] (bytes absent), which is
    honest: we cannot verify without the persisted bytes.
    """
    quotes = extract_quotes(answer_text or "")
    opened_norm = {_norm_url_for_set(u) for u in (opened_urls or ())}

    lines = []
    counts = {"VERIFIED": 0, "UNSEEN-URL": 0, "UNSEEN-BYTES": 0,
              "NEAR-MATCH": 0, "SHORT-UNVERIFIABLE": 0, "NO-URL": 0}

    # Which fetched files actually exist on disk? For the summary line.
    n_fetches_avail = 0
    if fetches_dir and os.path.isdir(fetches_dir):
        try:
            n_fetches_avail = sum(1 for f in os.listdir(fetches_dir)
                                  if f.endswith(".txt"))
        except OSError:
            n_fetches_avail = 0

    for q in quotes:
        raw_quote = q["quote"]
        src_url = q.get("source_url")
        quote_norm = normalize(raw_quote)

        # Layer 3: short-quote guard (checked first because it is the cheapest
        # and it applies regardless of URL provenance).
        if len(quote_norm) < SHORT_QUOTE_MIN:
            counts["SHORT-UNVERIFIABLE"] += 1
            lines.append({
                "status": "SHORT-UNVERIFIABLE",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "%d chars < %d threshold" % (len(quote_norm), SHORT_QUOTE_MIN),
            })
            continue

        # No URL claimed - we cannot check this quote (design.md §6: not our
        # job to fix the prompt).
        if not src_url:
            counts["NO-URL"] += 1
            lines.append({
                "status": "NO-URL",
                "quote": raw_quote,
                "source_url": None,
                "detail": "no source URL near quote (nothing to compare against)",
            })
            continue

        # Layer 1: URL provenance
        if _norm_url_for_set(src_url) not in opened_norm:
            counts["UNSEEN-URL"] += 1
            lines.append({
                "status": "UNSEEN-URL",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "URL never opened in this run",
            })
            continue

        # Layer 2: byte match
        body = _load_body(fetches_dir, src_url)
        if body is None:
            counts["UNSEEN-BYTES"] += 1
            lines.append({
                "status": "UNSEEN-BYTES",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "no persisted bytes on disk for this URL "
                          "(prep-step did not write it, or it was pruned)",
            })
            continue

        body_norm = normalize(body)
        result = fuzzy_find(quote_norm, body_norm)
        if result is None:
            counts["UNSEEN-BYTES"] += 1
            lines.append({
                "status": "UNSEEN-BYTES",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "not found in %d chars of body (normalized)" % len(body_norm),
            })
            continue

        offset, distance, snippet = result
        if distance == 0:
            counts["VERIFIED"] += 1
            lines.append({
                "status": "VERIFIED",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "exact match at body offset %d" % offset,
            })
        else:
            kind = classify_diff(quote_norm, snippet[:len(quote_norm) + 3])
            diff = build_diff_view(quote_norm, snippet[:len(quote_norm) + 3])
            counts["NEAR-MATCH"] += 1
            lines.append({
                "status": "NEAR-MATCH",
                "quote": raw_quote,
                "source_url": src_url,
                "detail": "distance %d, kind=%s" % (distance, kind),
                "diff": diff,
            })

    # Summary line - each present count listed, absent counts omitted for brevity.
    parts = []
    for k in ("VERIFIED", "NEAR-MATCH", "UNSEEN-BYTES", "UNSEEN-URL",
              "SHORT-UNVERIFIABLE", "NO-URL"):
        if counts[k]:
            parts.append("%d %s" % (counts[k], k))
    summary = ", ".join(parts) if parts else "0 quotes extracted"

    return {
        "summary_line": summary,
        "lines": lines,
        "counts": counts,
        "n_quotes": len(quotes),
        "n_fetches_available": n_fetches_avail,
    }


# =============================================================================
# Sidecar writer - human-readable Markdown next to the channel's answer.
# =============================================================================

_STATUS_ORDER = ("VERIFIED", "NEAR-MATCH", "UNSEEN-BYTES", "UNSEEN-URL",
                 "SHORT-UNVERIFIABLE", "NO-URL")


def format_sidecar(report, channel_name=None):
    """Turn a check() report into a Markdown string suitable for a .quote-verify.md file."""
    header = "# Б-9 quote-verify report"
    if channel_name:
        header += " for " + channel_name.upper()
    lines = [
        header,
        "",
        "**Summary:** %s (%d quote(s) extracted, %d fetched page(s) on disk)"
        % (report.get("summary_line", ""), report.get("n_quotes", 0),
           report.get("n_fetches_available", 0)),
        "",
        "Advisory tool: this report never fails the run. Read it, then decide.",
        "",
    ]
    # Group by status in a fixed order so the reader always sees the same layout.
    by_status = {s: [] for s in _STATUS_ORDER}
    for row in report.get("lines", []):
        s = row["status"]
        by_status.setdefault(s, []).append(row)
    for status in _STATUS_ORDER:
        rows = by_status.get(status) or []
        if not rows:
            continue
        lines.append("## [%s] (%d)" % (status, len(rows)))
        lines.append("")
        for r in rows:
            q = (r.get("quote") or "").strip()
            if len(q) > 200:
                q = q[:197] + "..."
            url = r.get("source_url") or "(no URL)"
            lines.append("- «%s»" % q)
            lines.append("  - source: %s" % url)
            lines.append("  - %s" % r.get("detail", ""))
            if r.get("diff"):
                lines.append("  - diff: `%s`" % r["diff"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# =============================================================================
# CLI - so this file can be run standalone against any answer/fetches pair.
# =============================================================================

def _read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [ln.strip() for ln in f if ln.strip()]


def main():
    ap = argparse.ArgumentParser(
        description="Verify quotes in an answer against the bytes we fetched.")
    ap.add_argument("--answer", required=True,
                    help="path to the answer file (channel's *.md)")
    ap.add_argument("--fetches-dir",
                    help="directory of persisted cleaned-text files "
                         "(<rundir>/<cname>.fetches/). If absent, quote-with-URL "
                         "in opened_urls still reports [UNSEEN-BYTES].")
    ap.add_argument("--opened-urls-list",
                    help="path to a file with one opened URL per line. If absent, "
                         "no quote can be [VERIFIED] - every quote-with-URL lands "
                         "in [UNSEEN-URL].")
    ap.add_argument("--opened-urls-json",
                    help="alternative: JSON list of opened URLs (used by "
                         "diagnostics.json integration).")
    ap.add_argument("-o", "--out",
                    help="write sidecar markdown here (default: stdout).")
    ap.add_argument("--channel", help="channel name (for the sidecar header).")
    a = ap.parse_args()

    try:
        answer = open(a.answer, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print("could not read --answer: %s" % e, file=sys.stderr)
        return 2

    opened = []
    if a.opened_urls_list:
        opened = _read_lines(a.opened_urls_list)
    elif a.opened_urls_json:
        try:
            with open(a.opened_urls_json, encoding="utf-8") as f:
                opened = json.load(f)
        except (OSError, ValueError) as e:
            print("could not read --opened-urls-json: %s" % e, file=sys.stderr)
            return 2

    report = check(answer, fetches_dir=a.fetches_dir, opened_urls=opened)
    md = format_sidecar(report, channel_name=a.channel)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print("wrote %s (%s)" % (a.out, report["summary_line"]))
    else:
        sys.stdout.write(md)
    # Exit code is ALWAYS 0 - advisory tool.
    return 0


if __name__ == "__main__":
    sys.exit(main())
