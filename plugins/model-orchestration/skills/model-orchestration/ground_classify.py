#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ground_classify.py - Kit-Б-10 grounding classifier: semantic layer on Б-9 byte-check.

    python ground_classify.py --answer ANSWER.md --fetches-dir DIR --opened-urls-list LIST
                              [--cite-check-json MAP.json] [-o SIDECAR.md] [--channel NAME]

WHY
---
Б-9 (quote_verify.py) answers "does this quote appear in the bytes we hold for the claimed
URL". Two answers of shape UNSEEN-BYTES can mean fundamentally different things:

    (P) paraphrase       - model paraphrased a true statement of the source page. Legitimate.
    (W) wrong-URL        - model attributed a real quote to the wrong page (topic mismatch).
    (F) fabricated       - quote is not on the page AND not on-topic. Training-data source.

Byte-check cannot distinguish these; a reviewer needs the distinction to act on them.

Same for UNSEEN-URL:

    (T) truncated URL    - R44-class vendor-corrupts (`github.co` vs `github.com/...`).
    (D) dead URL         - URL fetched but 404, or fabricated URL that never existed.
    (U) unfetched-live   - URL exists and is live, but we did not open it in this run.

The 8 GROUNDING CLASSES this file emits, plus 3 pass-through (S/X/AMBIGUOUS):

    V  verified verbatim         (from Б-9 VERIFIED)                trust
    N  near-verbatim             (from Б-9 NEAR-MATCH)              trust (compare diff first)
    P  paraphrase legitimate     (NEW)                              ask model for exact quote
    W  wrong-URL attribution     (NEW)                              fix URL
    F  fabricated content        (NEW)                              reject
    T  truncated URL             (NEW)                              recover intended URL
    D  dead / fabricated URL     (NEW)                              reject
    U  unfetched-but-live URL    (NEW)                              flag: why not opened?
    S  short-unverifiable        (Б-9 pass-through)                 pass
    X  no-URL                    (Б-9 pass-through)                 pass
    ?  AMBIGUOUS (safety-net)    (NEW)                              flag: human review

FP TOLERANCE (Igor: "false alarm worse than a miss")
----------------------------------------------------
Same policy as Б-9. Ф1 rules that matter:
  - Exit code always 0 (advisory).
  - F requires TWO independent LOW signals - never one.
  - W requires an EXPLICIT topic mismatch (topic_overlap < _TOPIC_LOW).
  - When signals are middle-range, AMBIGUOUS. Never a false F or W.
  - Ф2 calibration must measure F FPR on honest-corpus; if >3%, widen AMBIGUOUS - do NOT
    tighten thresholds against the rule.
  - T (truncated URL) heuristic stays narrow - only clearly malformed URLs. A short but
    real URL must not be flagged.

APPROACH F HYBRID (v1.71.0: default only; opt-in modules on roadmap)
--------------------------------------------------------------------
Default execution: stdlib TF-IDF cosine (quote vs body sentences) + Jaccard topic overlap
(quote content-words vs body content-words) + URL truncation heuristic + rule matrix.
$0, employees, no deps.

Opt-in env vars (v1.71.0 recognises them but reports "not yet integrated" and falls back
to default; roadmap for Ф3 after Ф2 calibration decides which are worth wiring):

    GROUND_EMBEDDINGS=1        - replace TF-IDF with sentence-transformers (torch dep)
    GROUND_LLM_VERIFY=<chan>   - paid mini-prompt for AMBIGUOUS bucket only
    GROUND_WAYBACK=1           - rescue F -> PAGE-UPDATED via web.archive.org

STDLIB-ONLY invariant: this file imports only collections, math, re, unicodedata, os,
urllib.parse, argparse, json, sys, pathlib.  No sentence-transformers, no numpy, no torch.
Kit ships to employees under any Python 3.8+.

SCOPE
-----
  In:  Б-9 report (from quote_verify.check) - OR-channels only. Runs inside
       call_oai_reviewer, right after quote_verify.check().
  Out: CLI channels (agy, codex, grokcli) - Б-9 doesn't run there, so Б-10 has nothing
       to enrich. Direct API channels with vendor grounding - same.
       Semantic negation ("only if X" written as "unless X") - out of scope; that is Б-13.
       URL snapshot / historical CFR / wayback URL classification (task #21) - that is
       Б-12, a different tool with a URL-string mechanism rather than body-content.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

# Import parent Б-9 for reuse of normalize, _load_body, and slug_for_url.
# quote_verify.py imports must succeed for Б-10 to be useful.
import quote_verify

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# =============================================================================
# Stop words - English + Russian minimal set. Ф2 will measure whether the set
# needs expansion (very long stop-list catches more junk but drops real content).
# =============================================================================

# Kept small on purpose: a stop-word list is a moving target and the same phrase
# can be "signal" in one context (a legal brief that turns on the word "shall")
# and "noise" in another. Aggressive lists cause topic_overlap to drop for
# on-topic quotes with mostly-functional words. Ф2 will re-measure.
_STOP_WORDS = frozenset({
    # English (basic function words)
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "for", "on", "at", "by", "with", "from", "as",
    "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "it", "its", "they", "them", "their", "there", "here", "which", "who", "whom",
    "what", "when", "where", "how", "why", "not", "no", "do", "does", "did",
    "have", "has", "had", "will", "would", "could", "should", "may", "might",
    "can", "one", "two", "so", "any", "all", "some", "many", "much", "more",
    "most", "other", "such", "own", "same", "our", "out", "up", "over", "under",
    "into", "onto", "upon", "about", "above", "below", "before", "after",
    "each", "every", "few", "both", "also", "just", "only", "than", "very",
    # Russian (basic function words - legal/tech corpus is bilingual)
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
    "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
    "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
    "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну",
    "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до",
    "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом", "себя",
    "ничего", "ей", "может", "они", "тут", "где", "есть", "надо", "ней",
    "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без", "будто",
})


# Content-word extractor: unicode alpha only, min length, lowered, not stop-word.
# Uses `[^\W\d_]` to match ANY unicode letter (Latin, Cyrillic, etc.) but no
# digits/underscores/punct. min_len=3 filters "of/to/in" without a stop-list.
_CONTENT_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _content_words(text):
    """Extract content-word SET for topic-overlap. Lowered, stop-word-filtered."""
    if not text:
        return set()
    return {t for t in _CONTENT_WORD_RE.findall(text.lower()) if t not in _STOP_WORDS}


def topic_overlap(quote, body):
    """ASYMMETRIC containment: fraction of the quote's content-words present in body.

    Returns `|quote_words ∩ body_words| / |quote_words|`, in [0.0, 1.0].

    Why NOT Jaccard (which the R102 design proposed). Measured on R101 gold set,
    2026-09-20: Jaccard(quote, cpython/subprocess.py-body-198K) = 0.001 EVEN when
    the quote's own content-words `terminate` sit clearly in the page - the
    denominator |quote_words ∪ body_words| is dominated by the 800+ content-words
    of the page, and any quote (short by construction) drowns. That reads as
    W (wrong-URL) for every long-body case, false positive rate 100%. Asymmetric
    containment asks the RIGHT question - "is what the quote SAYS on this page" -
    and is symmetric-independent of body size.

    HIGH score = quote's words are on this page (paraphrase/verbatim signal).
    LOW  score = quote's words are absent (wrong-URL signal - real page elsewhere).
    """
    q = _content_words(quote)
    b = _content_words(body)
    if not q:
        return 0.0
    return len(q & b) / len(q)


# =============================================================================
# Sentence tokenizer - regex + abbreviation list to prevent false splits on
# common tokens: `No. 22-15432`, `U.S.C.`, `e.g.`, `Fig. 3`, `p. 42`, etc.
# =============================================================================

# Abbreviations that end in "." but do NOT terminate a sentence. Longest-first
# so `Fed.R.Civ.P.` matches before `Fed.R.` and `R.`. Not a legal-only list -
# includes common scholarly and gov citation forms.
_ABBREVIATIONS = (
    # Long forms (must come first - longest match wins)
    "Fed.R.Civ.P.", "Fed.R.Crim.P.", "Fed.R.Evid.", "Fed.R.App.P.",
    "U.S.C.", "C.F.R.", "F.Supp.", "F.Cas.", "Adm.R.", "Fed.R.",
    "L.L.C.", "P.C.",
    # 3-4 letter
    "Inc.", "Ltd.", "Corp.", "Co.", "L.P.",
    "Ave.", "Blvd.", "Rd.", "St.", "Sq.",
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Rev.", "Sr.", "Jr.",
    "Sept.", "Oct.", "Nov.", "Dec.", "Jan.", "Feb.", "Mar.", "Apr.",
    "Fig.", "Figs.", "Vol.", "Vols.", "Ch.", "Ed.", "Eds.", "No.", "Nos.",
    "vs.", "v.", "e.g.", "i.e.", "cf.", "ibid.", "op.cit.", "et.al.",
    # Short forms with dots (order matters within short forms too)
    "ff.", "n.", "p.", "a.",
)

# Placeholder for dots inside abbreviations - must be a byte sequence that
# cannot appear in real text. \x00 is NUL, which never appears in prose.
_ABBR_PLACEHOLDER = "\x00ABBR\x00"

# Sentence boundary: `.!?` followed by whitespace + capital letter, digit, or
# opening quote. Uses lookbehind/lookahead so the split points don't consume
# either the terminator or the next-sentence start.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-Я0-9\"«\(])", re.UNICODE)


def tokenize_sentences(text):
    """Split text into sentences, preserving abbreviations.

    Strategy: replace abbreviation dots with a placeholder, split on terminal
    punctuation, restore placeholders. This is a heuristic - Ф2 will measure
    false-split rate on real corpora.
    """
    if not text:
        return []
    t = text
    for abbr in _ABBREVIATIONS:
        if abbr in t:
            t = t.replace(abbr, abbr.replace(".", _ABBR_PLACEHOLDER))
    parts = _SENTENCE_SPLIT_RE.split(t)
    out = []
    for p in parts:
        s = p.replace(_ABBR_PLACEHOLDER, ".").strip()
        if s:
            out.append(s)
    return out


# =============================================================================
# TF-IDF cosine similarity - stdlib. Not the fanciest similarity, but it does
# catch word-swap paraphrase reasonably well and it's employee-portable.
# =============================================================================

# Term extractor - min length 2 (loose enough to catch short technical words like
# `os`, `io`), same alpha-only + unicode as content_words but lower threshold.
_TERM_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _tf(text):
    """Term frequency: Counter of word -> count. Stop-word filtered."""
    return Counter(w for w in _TERM_RE.findall(text.lower()) if w not in _STOP_WORDS)


def _idf(corpus_texts):
    """Inverse document frequency: dict of word -> smoothed idf score.

    corpus_texts: list of str (each treated as one "document").
    Uses smoothed IDF: log((N+1) / (df+1)) + 1. Avoids division-by-zero and
    keeps rare-word scores > 1.
    """
    N = len(corpus_texts)
    if N == 0:
        return {}
    df = Counter()
    for text in corpus_texts:
        seen = set()
        for w in _TERM_RE.findall(text.lower()):
            if w in _STOP_WORDS:
                continue
            seen.add(w)
        for w in seen:
            df[w] += 1
    return {w: math.log((N + 1) / (c + 1)) + 1 for w, c in df.items()}


def _tfidf_vector(text, idf):
    """TF-IDF vector: dict of word -> tf * idf. Only words in idf are kept."""
    tf = _tf(text)
    return {w: c * idf[w] for w, c in tf.items() if w in idf and idf[w] > 0}


def cosine(v1, v2):
    """Cosine similarity between two sparse dict-vectors. Returns [0.0, 1.0]."""
    if not v1 or not v2:
        return 0.0
    # Dot product - iterate the smaller vector for speed.
    if len(v1) > len(v2):
        v1, v2 = v2, v1
    dot = sum(v1[w] * v2[w] for w in v1 if w in v2)
    n1 = math.sqrt(sum(v * v for v in v1.values()))
    n2 = math.sqrt(sum(v * v for v in v2.values()))
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (n1 * n2)


def max_sentence_similarity(quote, body):
    """Max TF-IDF cosine between quote and any body sentence.

    Returns (max_sim, best_sentence_or_None). If body has no sentences or quote
    has no content words, returns (0.0, None).
    """
    sentences = tokenize_sentences(body)
    if not sentences:
        return (0.0, None)
    # IDF corpus = body sentences + the quote itself. Adding the quote prevents
    # quote-only words from having zero IDF.
    corpus = sentences + [quote]
    idf = _idf(corpus)
    q_vec = _tfidf_vector(quote, idf)
    if not q_vec:
        return (0.0, None)
    best = (0.0, None)
    for sent in sentences:
        s_vec = _tfidf_vector(sent, idf)
        sim = cosine(q_vec, s_vec)
        if sim > best[0]:
            best = (sim, sent)
    return best


# =============================================================================
# URL truncation heuristic - detect R44-class vendor-corrupts.
#
# R101 gave the concrete fixture: `https://github.co` in place of a real
# `https://github.com/python/cpython/...`. That is R44 - the vendor delivered
# an answer with characters missing. A reviewer needs to KNOW "this URL is
# likely intact damaged" vs "this URL is likely a fabrication".
# =============================================================================

# Well-known brands that live on `.com` but whose visible name minus `.com`
# equals a legitimate `.co` domain. `github.co` looks like GitHub but is not;
# `google.co` alone (no country code) is not google.com either. This list is
# narrow on purpose - the FP cost of flagging a legit URL is high.
_SUSPECT_CO_HOSTS = frozenset({
    "github.co", "learn.microsoft.co", "docs.python.co", "docs.microsoft.co",
    "stackoverflow.co", "wikipedia.co", "google.co", "python.co",
    "developer.mozilla.co", "reactjs.co", "kubernetes.co", "docker.co",
})


def is_url_truncated(url):
    """Heuristic: does this URL look TRUNCATED (mid-domain / TLD without path)?

    True examples:
        https://github.co               -> yes (suspect `.co` after a known `.com` brand)
        https://learn.microsoft.co      -> yes (same reason)
        https://example                 -> yes (no TLD at all)

    False examples:
        https://github.com/x/y          -> no (has real path)
        https://ecfr.gov/current/x      -> no
        https://openai.co               -> no (not in suspect list; genuine `.co` exists)
        https://short.link/abc          -> no (shortener, legit)

    NARROW on purpose - a false T ("we called your intact URL corrupted") is worse than
    a missed one. Ф2 will measure FPR against honest URL corpus.
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    try:
        s = urlsplit(url)
    except ValueError:
        return True  # Malformed URL is truncated by construction.
    host = s.netloc.lower()
    path = s.path or ""

    if not host:
        return True
    # No TLD at all (bare word after scheme)
    if "." not in host:
        return True
    # Suspect `.co` after known `.com` brand, no meaningful path
    if host in _SUSPECT_CO_HOSTS and (not path or path == "/"):
        return True
    return False


# =============================================================================
# Classification - main rule matrix. See design.md §4 Approach F default.
#
# Threshold constants below are HYPOTHESES from design.md - Ф2 calibration will
# measure and adjust them. Grouped and named so a future edit is a config touch.
# =============================================================================

# For UNSEEN-BYTES classification into {P, W, F, AMBIGUOUS}.
# R102 design proposed Jaccard-based thresholds (0.15/0.05); R103 smoke on R101
# gold set forced a switch to asymmetric containment for `topic_overlap` (see
# its docstring), and thresholds moved accordingly - Jaccard-scale numbers on a
# containment metric would classify everything as W.
_SIM_HIGH = 0.5      # max_sim >= this: strong sentence-level match (paraphrase signal)
_SIM_LOW = 0.2       # max_sim < this: no sentence-level match (F candidate, needs topic_low too)
_TOPIC_HIGH = 0.4    # containment >= this: most of the quote's words are on-topic for the page
_TOPIC_LOW = 0.15    # containment < this: quote's words are LARGELY ABSENT from the page (W)


def _classify_unseen_bytes(quote, body):
    """UNSEEN-BYTES -> {P, W, F, AMBIGUOUS} with evidence.

    Rule matrix (from design.md §4 F-default):
        topic_overlap < _TOPIC_LOW           -> W (independent of sim)
        max_sim >= _SIM_HIGH AND topic_high  -> P
        max_sim < _SIM_LOW AND topic < HIGH  -> F (TWO independent LOW signals)
        otherwise                            -> AMBIGUOUS (safety net)

    FP-tolerance: F requires BOTH max_sim < _SIM_LOW AND topic_overlap < _TOPIC_HIGH.
    W requires an EXPLICIT topic mismatch. Middle range is AMBIGUOUS by design.
    """
    max_sim, best_sent = max_sentence_similarity(quote, body)
    tov = topic_overlap(quote, body)

    # W first: explicit topic mismatch is decisive regardless of similarity
    # (a quote sharing few content words with the WHOLE body is on wrong page).
    if tov < _TOPIC_LOW:
        return {
            "class": "W",
            "evidence": {"max_sim": round(max_sim, 3), "topic_overlap": round(tov, 3)},
            "detail": ("wrong-URL: topic_overlap=%.3f < %.2f (page and quote share few "
                       "content words - quote likely belongs to a different page)"
                       % (tov, _TOPIC_LOW)),
        }
    # P: strong similarity AND on-topic
    if max_sim >= _SIM_HIGH and tov >= _TOPIC_HIGH:
        ev = {"max_sim": round(max_sim, 3), "topic_overlap": round(tov, 3)}
        if best_sent:
            ev["best_sentence"] = best_sent[:200]
        return {
            "class": "P",
            "evidence": ev,
            "detail": ("paraphrase: max_similarity=%.3f to a page sentence, "
                       "topic_overlap=%.3f (model reformulated a real page statement)"
                       % (max_sim, tov)),
        }
    # F: on-topic-PARTIAL (some quote words on page) BUT no sentence-level match.
    # topic >= TOPIC_LOW is implied - if it were < TOPIC_LOW, W already fired above.
    # So F occupies "quote words are on the page but the phrase is invented" -
    # different from W (quote-words NOT on the page at all = wrong-URL).
    # After the R103 smoke on R101 gold set: the containment fix moved real
    # wrong-URL cases into AMBIGUOUS (containment computed against 54K subprocess
    # docs is HIGH for "default/output/data" words even when the SENTENCE is not
    # there). That is the correct answer - AMBIGUOUS beats false F.
    if max_sim < _SIM_LOW and tov < _TOPIC_HIGH:
        return {
            "class": "F",
            "evidence": {"max_sim": round(max_sim, 3), "topic_overlap": round(tov, 3)},
            "detail": ("fabricated: max_similarity=%.3f < %.2f AND topic_overlap=%.3f < %.2f "
                       "(low sentence-level match despite partial word containment - "
                       "quote phrasing likely invented, not from this source page)"
                       % (max_sim, _SIM_LOW, tov, _TOPIC_HIGH)),
        }
    # Otherwise: middle range - AMBIGUOUS (§7 rule 1 safety-net)
    return {
        "class": "AMBIGUOUS",
        "evidence": {"max_sim": round(max_sim, 3), "topic_overlap": round(tov, 3)},
        "detail": ("ambiguous: max_similarity=%.3f, topic_overlap=%.3f - signals do not "
                   "clearly distinguish paraphrase from wrong-URL or fabrication"
                   % (max_sim, tov)),
    }


def _classify_unseen_url(url, cite_check_status=None):
    """UNSEEN-URL -> {T, D, U, AMBIGUOUS} with evidence.

    cite_check_status: "LIVE" / "DEAD" / "BLOCKED" / None (if cite_check not run).
    """
    if is_url_truncated(url):
        return {
            "class": "T",
            "evidence": {"url": url, "reason": "truncation heuristic"},
            "detail": ("truncated URL (R44-class vendor-corrupts): host/path looks damaged "
                       "in transit - likely a real page whose URL was cut mid-string"),
        }
    if cite_check_status == "DEAD":
        return {
            "class": "D",
            "evidence": {"url": url, "cite_check": "DEAD"},
            "detail": ("dead / fabricated URL: cite_check reports 4xx - either the URL is "
                       "fabricated (never existed) or the page was removed"),
        }
    if cite_check_status == "LIVE":
        return {
            "class": "U",
            "evidence": {"url": url, "cite_check": "LIVE"},
            "detail": ("unfetched-but-live URL: URL is reachable but was NOT opened in "
                       "this run - quote could not have been fetched by this session"),
        }
    # BLOCKED / UNKNOWN / no cite_check run at all
    return {
        "class": "AMBIGUOUS",
        "evidence": {"url": url, "cite_check": cite_check_status or "not run"},
        "detail": ("ambiguous URL status: cite_check=%s - cannot decide between dead, "
                   "unfetched-but-live, or truncated" % (cite_check_status or "not run")),
    }


def classify_quote(q_line, body=None, cite_check_status=None):
    """Assign a grounding_class to one Б-9 line.

    Args:
        q_line: dict from quote_verify.check().lines[] - has "status", "quote",
                "source_url", "detail" (and "diff" for NEAR-MATCH).
        body: normalised body of the source URL, only used for UNSEEN-BYTES.
              None if unavailable (Б-10 falls back to AMBIGUOUS).
        cite_check_status: "LIVE" / "DEAD" / "BLOCKED" / None; used for UNSEEN-URL.

    Returns: new dict with all q_line keys preserved plus:
        - grounding_class      (str, one of V/N/P/W/F/T/D/U/S/X/AMBIGUOUS)
        - grounding_detail     (str, human-readable)
        - grounding_evidence   (dict, structured)
    """
    status = q_line.get("status")
    result = dict(q_line)  # shallow copy - preserve original fields

    if status == "VERIFIED":
        result["grounding_class"] = "V"
        result["grounding_detail"] = "verified verbatim (exact byte match on source page)"
        result["grounding_evidence"] = {"b9_status": "VERIFIED"}
    elif status == "NEAR-MATCH":
        # Preserve Б-9 kind - punct-only is the safest R44-class pattern; letter/digit
        # changes are more suspicious and deserve a diff-check by the reviewer.
        b9_detail = (q_line.get("detail") or "").lower()
        if "punct-only" in b9_detail:
            result["grounding_class"] = "N"
            result["grounding_detail"] = ("near-verbatim (punctuation-only difference from "
                                          "source page - likely a rendering artefact or R44 "
                                          "vendor-corrupts pattern)")
        else:
            result["grounding_class"] = "N"
            result["grounding_detail"] = ("near-verbatim (small difference: %s - reviewer "
                                          "should compare diff before trusting)"
                                          % q_line.get("detail", "unknown"))
        result["grounding_evidence"] = {"b9_status": "NEAR-MATCH",
                                         "b9_detail": q_line.get("detail")}
    elif status == "UNSEEN-BYTES":
        if body is None:
            # No body available (file missing / empty / OS error) - Б-10 cannot classify
            # beyond AMBIGUOUS. This is the RIGHT answer, not a false-safe: preserving
            # Б-9's ambiguity is exactly what F-6 established for the parent tool.
            result["grounding_class"] = "AMBIGUOUS"
            result["grounding_detail"] = ("cannot classify: body bytes not available for "
                                          "this URL (see Б-9 detail for reason)")
            result["grounding_evidence"] = {"b9_status": "UNSEEN-BYTES", "body": "unavailable",
                                            "b9_detail": q_line.get("detail")}
        else:
            sub = _classify_unseen_bytes(q_line.get("quote", ""), body)
            result["grounding_class"] = sub["class"]
            result["grounding_detail"] = sub["detail"]
            result["grounding_evidence"] = sub["evidence"]
    elif status == "UNSEEN-URL":
        sub = _classify_unseen_url(q_line.get("source_url", ""), cite_check_status)
        result["grounding_class"] = sub["class"]
        result["grounding_detail"] = sub["detail"]
        result["grounding_evidence"] = sub["evidence"]
    elif status == "SHORT-UNVERIFIABLE":
        result["grounding_class"] = "S"
        result["grounding_detail"] = ("too short to verify (Б-9 short-quote guard - a "
                                      "3-word phrase matches almost any body by chance)")
        result["grounding_evidence"] = {"b9_status": "SHORT-UNVERIFIABLE"}
    elif status == "NO-URL":
        result["grounding_class"] = "X"
        result["grounding_detail"] = ("no source URL near the quote - reviewer should ask "
                                      "model to provide the source")
        result["grounding_evidence"] = {"b9_status": "NO-URL"}
    else:
        # Unknown Б-9 status - flag for review, don't crash.
        result["grounding_class"] = "AMBIGUOUS"
        result["grounding_detail"] = ("unknown Б-9 status: %r - Б-10 cannot classify"
                                      % status)
        result["grounding_evidence"] = {"b9_status": status}

    return result


# =============================================================================
# classify() - orchestration: enrich a Б-9 report with grounding_class per line.
# =============================================================================

def _resolve_approach():
    """Print the approach line for the sidecar. In v1.71.0 default only;
    opt-in env vars are RECOGNISED but announce 'not yet integrated' and fall
    back to default (Ф3 will wire them after Ф2 calibration decides which are
    worth the code)."""
    approach = "stdlib-tfidf"
    notes = []
    if os.environ.get("GROUND_EMBEDDINGS"):
        notes.append("GROUND_EMBEDDINGS requested but not yet integrated in v1.71.0")
    if os.environ.get("GROUND_LLM_VERIFY"):
        notes.append("GROUND_LLM_VERIFY requested but not yet integrated in v1.71.0")
    if os.environ.get("GROUND_WAYBACK"):
        notes.append("GROUND_WAYBACK requested but not yet integrated in v1.71.0")
    if notes:
        approach += " (" + "; ".join(notes) + ")"
    return approach


def classify(b9_report, fetches_dir=None, cite_check_map=None):
    """Enrich a Б-9 report with grounding_class per line.

    Args:
        b9_report: dict from quote_verify.check().
        fetches_dir: directory of persisted cleaned-text files (for UNSEEN-BYTES body).
        cite_check_map: dict URL -> status ("LIVE"/"DEAD"/"BLOCKED"), or None if not run.

    Returns dict:
        grounded_lines: list of enriched line dicts
        grounding_counts: dict class -> count
        grounding_summary: one-line string
        approach: string (which classifier was used, for the sidecar header)
        n_quotes: passed through from b9_report
        n_fetches_available: passed through from b9_report
    """
    approach = _resolve_approach()
    grounded = []
    counts = {k: 0 for k in ("V", "N", "P", "W", "F", "T", "D", "U", "S", "X", "AMBIGUOUS")}
    cite_check_map = cite_check_map or {}

    for line in b9_report.get("lines", []):
        # For UNSEEN-BYTES: load body from fetches_dir (Б-9 doesn't keep bodies).
        # Wrap in try/except - a body load failure is NOT a Б-10 crash cause;
        # it means we degrade to AMBIGUOUS for that line only.
        body = None
        if line.get("status") == "UNSEEN-BYTES" and fetches_dir and line.get("source_url"):
            try:
                body_raw = quote_verify._load_body(fetches_dir, line["source_url"])
                if body_raw is not None:
                    body = quote_verify.normalize(body_raw)
            except Exception:  # noqa: BLE001 - advisory, never crash Б-10
                body = None
        cite_status = cite_check_map.get(line.get("source_url"))
        enriched = classify_quote(line, body=body, cite_check_status=cite_status)
        cls = enriched["grounding_class"]
        counts[cls] = counts.get(cls, 0) + 1
        grounded.append(enriched)

    # Summary line - each present count listed in a fixed order.
    _ORDER = ("V", "N", "P", "W", "F", "T", "D", "U", "S", "X", "AMBIGUOUS")
    parts = []
    for c in _ORDER:
        if counts[c]:
            parts.append("%d %s" % (counts[c], c))
    summary = ", ".join(parts) if parts else "0 quotes"

    return {
        "grounded_lines": grounded,
        "grounding_counts": counts,
        "grounding_summary": summary,
        "approach": approach,
        "n_quotes": b9_report.get("n_quotes", 0),
        "n_fetches_available": b9_report.get("n_fetches_available", 0),
    }


# =============================================================================
# Sidecar writer - Markdown with grounding class as the primary axis.
# Real alarms (F, D, W, T) at the top; soft signals (P, U, AMBIGUOUS) in middle;
# info (N, V, S, X) at the bottom.
# =============================================================================

_CLASS_INFO = {
    "V":  ("verified verbatim",
           "trust: exact byte match on source page"),
    "N":  ("near-verbatim",
           "trust with caution: check the diff before quoting"),
    "P":  ("paraphrase (legitimate)",
           "reviewer action: ask model for the exact quote from the page, "
           "or replace with the closest verbatim sentence"),
    "W":  ("wrong-URL attribution",
           "reviewer action: correct the URL - quote does not match this "
           "page's topic"),
    "F":  ("fabricated content",
           "reviewer action: reject the claim, or find the real source"),
    "T":  ("truncated URL (R44-class vendor-corrupts)",
           "reviewer action: recover intended URL from surrounding context"),
    "D":  ("dead / fabricated URL",
           "reviewer action: reject the claim, or find the real source"),
    "U":  ("unfetched-but-live URL",
           "reviewer action: consider why this URL was not opened - the "
           "quote could not have been fetched by this run"),
    "S":  ("short-unverifiable",
           "pass-through: too short to verify (Б-9 short-quote guard)"),
    "X":  ("no source URL",
           "reviewer action: ask model to provide the source URL"),
    "AMBIGUOUS": ("ambiguous (signals unclear)",
                  "reviewer action: human review - signals do not clearly "
                  "distinguish paraphrase from wrong-URL or fabrication"),
}

# Section order in the sidecar: real alarms first, info last. Puts what needs
# action at the top of the reader's screen.
_SIDECAR_ORDER = ("F", "D", "W", "T", "P", "U", "AMBIGUOUS", "N", "V", "S", "X")


def format_sidecar(g_report, channel_name=None):
    """Turn a classify() result into a Markdown sidecar string."""
    header = "# Б-10 grounding report"
    if channel_name:
        header += " for " + channel_name.upper()
    lines = [
        header,
        "",
        "**Summary:** %s (%d quote(s) extracted, %d fetched page(s) on disk, approach=%s)"
        % (g_report.get("grounding_summary", ""), g_report.get("n_quotes", 0),
           g_report.get("n_fetches_available", 0), g_report.get("approach", "unknown")),
        "",
        "Advisory tool: this report never fails the run. Read it, then decide.",
        "",
    ]
    # Group by class.
    by_class = {c: [] for c in _CLASS_INFO}
    for row in g_report.get("grounded_lines", []):
        c = row.get("grounding_class", "AMBIGUOUS")
        by_class.setdefault(c, []).append(row)

    for cls in _SIDECAR_ORDER:
        rows = by_class.get(cls) or []
        if not rows:
            continue
        label, action = _CLASS_INFO.get(cls, (cls, ""))
        lines.append("## [%s] %s (%d)" % (cls, label, len(rows)))
        lines.append("")
        if action:
            lines.append("%s" % action)
            lines.append("")
        for r in rows:
            q = (r.get("quote") or "").strip()
            if len(q) > 200:
                q = q[:197] + "..."
            url = r.get("source_url") or "(no URL)"
            lines.append("- «%s»" % q)
            lines.append("  - source: %s" % url)
            lines.append("  - %s" % (r.get("grounding_detail")
                                     or r.get("detail", "")))
            ev = r.get("grounding_evidence") or {}
            if ("max_sim" in ev) or ("topic_overlap" in ev):
                lines.append("  - evidence: max_sim=%s, topic_overlap=%s"
                             % (ev.get("max_sim"), ev.get("topic_overlap")))
            if ev.get("best_sentence"):
                bs = ev["best_sentence"]
                if len(bs) > 200:
                    bs = bs[:197] + "..."
                lines.append("  - best matching sentence: «%s»" % bs)
            if r.get("diff"):
                lines.append("  - diff: `%s`" % r["diff"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# =============================================================================
# CLI - standalone, so this file can be exercised without orchestrate.py.
# =============================================================================

def _read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [ln.strip() for ln in f if ln.strip()]


def main():
    ap = argparse.ArgumentParser(
        description="Grounding classifier - semantic layer on Б-9 quote_verify.")
    ap.add_argument("--answer", required=True,
                    help="path to the answer file (channel's *.md)")
    ap.add_argument("--fetches-dir",
                    help="directory of persisted cleaned-text files "
                         "(for UNSEEN-BYTES body lookup)")
    ap.add_argument("--opened-urls-list",
                    help="path to a file with one opened URL per line")
    ap.add_argument("--opened-urls-json",
                    help="alternative: JSON list of opened URLs")
    ap.add_argument("--cite-check-json",
                    help="optional JSON: {url: 'LIVE'|'DEAD'|'BLOCKED', ...} - "
                         "improves URL classification (D vs U vs T)")
    ap.add_argument("-o", "--out",
                    help="write sidecar markdown here (default: stdout)")
    ap.add_argument("--channel", help="channel name (for sidecar header)")
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

    cite_check_map = None
    if a.cite_check_json:
        try:
            with open(a.cite_check_json, encoding="utf-8") as f:
                cite_check_map = json.load(f)
        except (OSError, ValueError) as e:
            print("could not read --cite-check-json: %s" % e, file=sys.stderr)
            return 2

    b9_report = quote_verify.check(answer, fetches_dir=a.fetches_dir,
                                    opened_urls=opened)
    g_report = classify(b9_report, fetches_dir=a.fetches_dir,
                        cite_check_map=cite_check_map)
    md = format_sidecar(g_report, channel_name=a.channel)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print("wrote %s (%s)" % (a.out, g_report["grounding_summary"]))
    else:
        sys.stdout.write(md)
    # Exit code is ALWAYS 0 - advisory tool, same policy as Б-9.
    return 0


if __name__ == "__main__":
    sys.exit(main())
