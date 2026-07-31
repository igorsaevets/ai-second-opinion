#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
orchestrate.py — call external reviewer models in parallel, at full depth, and verify the answers.

Companion to SKILL.md in this directory. Standard library only: no pip install, works anywhere
Python 3.8+ runs. Every non-obvious line carries a comment naming the failure it prevents.

    python orchestrate.py --brief BRIEF.md --system SYSTEM.md --tier deep --out ./reviews
    python orchestrate.py --brief BRIEF.md --tier strategic --only spark
    python orchestrate.py --brief BRIEF.md --tier deep --marker REVIEW-COMPLETE-T42

Environment (see SKILL.md section 1). The key is read here and never printed:
    MODEL_API_KEY   bearer token for the HTTPS reviewer
    MODEL_API_BASE  default https://api.meta.ai/v1
    MODEL_NAME      default muse-spark-1.1
    CODEX_BIN       default "codex"
    AGY_BIN         default %LOCALAPPDATA%\\agy\\bin\\agy.exe
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor

# Windows consoles default to cp1251/cp866 and will crash on the first non-ASCII character
# in a model's answer. Force UTF-8 on stdout before anything can print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- thinking tiers -------------------------------------------------------------------------
# Bare {"type":"adaptive"} silently under-allocates: measured 9,955 output tokens and ZERO web
# searches on a brief where {"type":"enabled","budget_tokens":100000} produced 20,132 tokens and
# 34 searches. The floor is what you assert the answer against afterwards (see verify()).
TIERS = {
    "quick":     {"thinking": {"type": "adaptive"},                              "floor": 0},
    "standard":  {"thinking": {"type": "adaptive", "budget_tokens": 30000},      "floor": 5000},
    "strategic": {"thinking": {"type": "enabled",  "budget_tokens": 60000},      "floor": 15000},
    "deep":      {"thinking": {"type": "enabled",  "budget_tokens": 100000},     "floor": 25000},
}

MAX_TOKENS = 131072          # ceiling; must exceed budget_tokens plus the final answer
STREAM_ABOVE_BUDGET = 32000  # above this, a non-streaming call is a coin flip. See SKILL.md 2.4.
AGY_ARGV_LIMIT = 30000       # Windows command line dies somewhere past ~32K chars


def log(msg):
    print(msg, flush=True)


# =============================================================================================
# HTTPS reviewer (Anthropic Messages shape)
# =============================================================================================

def _post(url, payload, key, timeout, stream):
    """One HTTP attempt. Returns (parsed_or_raw, http_status). Raises on transport failure."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,   # some hosts want x-api-key instead; try bearer first
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if not stream:
            return json.loads(resp.read().decode("utf-8")), resp.status
        return _read_sse(resp), resp.status


def _read_sse(resp):
    """
    Collapse an Anthropic SSE stream into the same dict shape a non-streaming call returns.

    Streaming is MANDATORY for big thinking budgets: idle connections get dropped by
    intermediaries, and a client may refuse a non-streaming request expected to run past ~10 min.
    """
    out = {"content": [], "usage": {}, "stop_reason": None, "sse_error": None}
    text_parts, blocks = [], []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue                      # skip "event:" lines and keep-alive blanks
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            ev = json.loads(body)
        except json.JSONDecodeError:
            continue                      # a partial frame; the next one will carry it
        t = ev.get("type")
        if t == "error":
            # The endpoint answers HTTP 200 and THEN streams a single error frame. Without
            # this branch the frame fell through the elif chain, the stream ended with no
            # text, and the run was reported as "EMPTY ANSWER" with no cause - which cost
            # two whole review rounds on 2026-07-29 before the payload was bisected by hand.
            out["sse_error"] = (ev.get("error", {}) or {}).get("message") or json.dumps(ev)[:300]
        elif t == "message_start":
            out["usage"].update(ev.get("message", {}).get("usage", {}) or {})
        elif t == "content_block_start":
            # Keep the whole block, not just its type. A `web_search_tool_result` block carries
            # the URLs the model actually fetched, and throwing them away was why the
            # cited-vs-opened check existed for agy and not for this channel - the one place
            # fabricated citations were proved, checked on one channel out of three.
            cb = ev.get("content_block", {}) or {}
            if cb.get("type") and cb.get("type") != "text":
                blocks.append(cb)
        elif t == "content_block_delta":
            d = ev.get("delta", {})
            if d.get("type") == "text_delta":
                text_parts.append(d.get("text", ""))
        elif t == "message_delta":
            out["stop_reason"] = ev.get("delta", {}).get("stop_reason") or out["stop_reason"]
            # output_tokens only becomes final here; message_start carries a placeholder
            out["usage"].update(ev.get("usage", {}) or {})
    out["content"] = blocks
    out["content"].append({"type": "text", "text": "".join(text_parts)})
    return out


def call_http_reviewer(brief, system, tier, marker, timeout=2400):
    """Probe, then the real call, with retries that distinguish network blips from refusals."""
    key = os.environ.get("MODEL_API_KEY")
    if not key and os.name == "nt":
        try:                                  # PowerShell setx writes here; the process env may lag
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                key = winreg.QueryValueEx(reg, "MODEL_API_KEY")[0]
        except OSError:
            pass
    if not key:
        return {"channel": "http", "ok": False, "error": "MODEL_API_KEY not set"}

    url = os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1").rstrip("/") + "/messages"
    model = os.environ.get("MODEL_NAME", "muse-spark-1.1")
    cfg = TIERS[tier]
    base = {"model": model, "system": system, "messages": [{"role": "user", "content": brief}]}

    # --- probe: real system + real message, tiny max_tokens, no thinking, no tools.
    # Content filters are CUMULATIVE over a long payload, so a packet that passes in pieces can
    # fail whole. Better to learn that for 64 tokens than for 100,000.
    probe = dict(base, max_tokens=64)
    t0 = time.time()
    try:
        _post(url, probe, key, 240, stream=False)
        log("  [http] probe OK in %.0fs" % (time.time() - t0))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:400]
        if "content management policy" in msg or e.code == 403:
            return {"channel": "http", "ok": False,
                    "error": "CONTENT FILTER on probe - neutralise wording in the SENT copy "
                             "only, strip appendices, re-probe. Do NOT retry unchanged. " + msg}
        return {"channel": "http", "ok": False, "error": "probe HTTP %d: %s" % (e.code, msg)}
    except Exception as e:
        return {"channel": "http", "ok": False, "error": "probe transport failure: %r" % (e,)}

    # --- the real call
    budget = cfg["thinking"].get("budget_tokens", 0)
    stream = budget > STREAM_ABOVE_BUDGET
    # output_config is sent OPTIMISTICALLY and dropped on the first api_error.
    #
    # Measured 2026-07-29 by bisecting the payload one field at a time: this endpoint now
    # answers `thinking.enabled + tools + output_config` with HTTP 200 followed by an SSE
    # frame {"error":{"message":"Internal server error.","type":"api_error"}}, in 0.5s. The
    # identical payload WITHOUT output_config returned 17 902 chars and ~50 web-search
    # blocks in 152s. Since it is a 200 and not a 400, none of the HTTPError branches below
    # can see it - hence the explicit sse_error retry.
    payload = dict(base,
                   max_tokens=MAX_TOKENS,
                   thinking=cfg["thinking"],
                   output_config={"effort": "xhigh"},
                   tools=[{"type": "web_search_20250305", "name": "web_search"}])
    if stream:
        payload["stream"] = True
    log("  [http] tier=%s budget=%s stream=%s" % (tier, budget or "adaptive", stream))

    t0 = time.time()
    last = None
    for attempt in range(4):
        try:
            data, _ = _post(url, payload, key, timeout, stream)
            sse_err = data.get("sse_error") if isinstance(data, dict) else None
            if sse_err and "output_config" in payload:
                log("  [http] 200+SSE error (%s) - dropping output_config and retrying"
                    % sse_err[:80])
                payload.pop("output_config")
                last = "sse_error: %s" % sse_err
                continue
            if sse_err:
                return {"channel": "http", "ok": False,
                        "error": "endpoint streamed an error frame: %s" % sse_err}
            return _verify_http(data, marker, cfg["floor"], time.time() - t0, tier)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            last = "HTTP %d: %s" % (e.code, body)
            if e.code == 400 and "thinking" in body:
                # Provider divergence: some hosts reject enabled+budget, others reject adaptive.
                # Flip the form once and try again rather than failing the whole round.
                payload["thinking"] = ({"type": "adaptive", "display": "summarized"}
                                       if cfg["thinking"]["type"] == "enabled"
                                       else {"type": "enabled", "budget_tokens": 60000})
                log("  [http] 400 on thinking shape - flipping form and retrying")
                continue
            if e.code == 401 or "content management policy" in body:
                break                                  # never retry auth or filter unchanged
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            break
        except Exception as e:
            # gaierror / getaddrinfo failed / connection reset. A real run died here once and
            # took the whole orchestration with it, because urllib has no retry of its own.
            last = "transport: %r" % (e,)
            log("  [http] %s - retry %d/3 in %ds" % (last, attempt + 1, 2 ** attempt))
            time.sleep(2 ** attempt)
    return {"channel": "http", "ok": False, "error": last}


def _verify_http(data, marker, floor, secs, tier):
    """The four mandatory checks from SKILL.md 2.7. A call that ran is not a review that happened."""
    blocks = data.get("content", []) or []
    text = "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    usage = data.get("usage", {}) or {}
    out_tok = usage.get("output_tokens") or 0

    # tools: is PERMISSION, not instruction. A model handed web_search may never call it and then
    # answer dated questions from training data.
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests")
    if searches is None:
        searches = sum(1 for b in blocks
                       if b.get("type") in ("server_tool_use", "web_search_tool_result"))

    # HARD failures: the answer is unusable. Never report one of these as a review.
    fail = []
    if data.get("stop_reason") not in (None, "end_turn"):
        fail.append("stop_reason=%s (TRUNCATED - the tail of the analysis is missing)"
                    % data.get("stop_reason"))
    if marker and marker not in text:
        fail.append("END MARKER ABSENT - output is incomplete, do not parse it as a finished review")
    if not text.strip():
        fail.append("EMPTY ANSWER despite a successful HTTP call")
    refusal = refusal_check(text, marker)
    if refusal:
        fail.append(refusal)

    # SOFT signals: the answer exists and may be fine; judge it yourself.
    # Kept out of `ok` because a false alarm here trains you to ignore the real ones.
    note = []
    if not searches:
        note.append("ZERO tool invocations - every dated fact in this answer is from training data, "
                    "not from the web. Treat dated claims as unverified.")
    # Same cited-vs-opened check the agy channel gets. This channel reports a search COUNT, which
    # proves activity and not grounding - the distinction that caught four fabricated Federal
    # Register document numbers on the other channel. `web_search_tool_result` blocks list the
    # pages actually fetched, so the comparison is free once the blocks are kept.
    opened = set()
    for b in blocks:
        if b.get("type") == "web_search_tool_result":
            for item in (b.get("content") or []):
                if isinstance(item, dict) and item.get("url"):
                    opened.add(_norm_url(item["url"]))
    n_cited, grounded, ungrounded = _cite_check(text, opened)
    # Only speak when there IS a record of what was opened. With an empty `opened` set every
    # citation looks ungrounded, which would be inferring a failure from missing evidence - the
    # exact move this harness forbids the models themselves to make.
    if opened and ungrounded:
        msg = ("CITATIONS: only %d of %d cited URLs appear among the %d pages this run actually "
               "fetched. Unopened: %s. Those came from the model's memory, not from a page it "
               "read - verify before repeating them."
               % (len(grounded), n_cited, len(opened), ", ".join(ungrounded[:5])))
        # Zero grounding with citations present is memory dressed as research: a hard failure.
        # Partial grounding stays a note, because such a review can still be worth reading.
        (fail if n_cited and not grounded else note).append(msg)

    if floor and out_tok and out_tok < floor:
        # Caught by running it: a brief that says "answer in under 250 words" makes a short reply
        # CORRECT, and this check then fires on a perfectly good answer. The floor only means
        # something when the brief did not cap the length.
        note.append("output_tokens=%d is below the %d floor for tier '%s'. If the brief asked for a "
                    "short answer this is expected and fine. If it asked for a full review, the "
                    "model under-allocated: raise budget_tokens or split into more sub-questions."
                    % (out_tok, floor, tier))

    return {"channel": "http", "ok": not fail, "text": text, "seconds": round(secs, 1),
            "in_tokens": usage.get("input_tokens"), "out_tokens": out_tok,
            "tool_calls": searches, "stop_reason": data.get("stop_reason"),
            "block_types": sorted({b.get("type") for b in blocks if b.get("type")}),
            "warnings": fail, "notes": note}


# =============================================================================================
# CLI channels
# =============================================================================================

# A model that declines the task still follows the formatting instruction, so it appends the end
# marker and sails through every mechanical check. Measured 2026-07-31: Codex returned 162 bytes
# - "I can't provide the requested review because I'm not allowed to give legal advice or analyze
# an individual immigration filing strategy." - followed by a clean "AOS-REVIEW-COMPLETE", and
# the harness reported the channel OK. A marker proves the model reached the end of ITS turn, not
# that it did the work.
REFUSAL_TELLS = [
    "i can't provide", "i cannot provide", "i can’t provide",
    "i can't help", "i cannot help", "i can’t help",
    "i'm not allowed", "i am not allowed", "i’m not allowed",
    "i'm unable to", "i am unable to", "i’m unable to",
    "i won't be able", "i will not be able",
    "not able to assist", "can't assist with", "cannot assist with",
    "i must decline", "i have to decline",
]


def refusal_check(text, marker=None, min_chars=800):
    """
    Return a warning string if this looks like a decline rather than a review, else None.

    Two independent signals, deliberately both required for the phrase branch: a refusal tell
    AND a short body. A long review may legitimately contain "I cannot provide a date for X"
    somewhere in the middle; a 200-character answer that opens with one is a refusal.
    """
    body = (text or "").strip()
    if marker and body.endswith(marker):
        body = body[:-len(marker)].strip()
    if not body:
        return None                      # emptiness is caught by the per-channel checks
    head = body[:400].lower()
    if len(body) < min_chars and any(t in head for t in REFUSAL_TELLS):
        return ("REFUSAL, NOT A REVIEW: the channel declined the task and then appended the end "
                "marker, which passes every mechanical check. Verbatim: %r. Do not count this "
                "channel; re-scope the brief for it or drop it from this round."
                % body[:200])
    if len(body) < min_chars:
        return ("SUSPICIOUSLY SHORT (%d chars excluding the marker) - a review this size is "
                "usually a decline, a truncation, or a misread brief. Read it before counting it."
                % len(body))
    return None


def _run(cmd, stdin_text=None, timeout=3000, cwd=None, stdout_path=None):
    """
    stdout_path streams stdout straight to a file instead of buffering it. agy's stream-json log
    is tens of thousands of lines; more importantly, a run that is killed or times out still
    leaves a partial log on disk, which is the difference between "we know it searched 22 times
    before dying" and "no evidence at all".
    """
    t0 = time.time()
    if stdout_path:
        with open(stdout_path, "w", encoding="utf-8") as out:
            p = subprocess.run(cmd, input=stdin_text, stdout=out, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=cwd)
    else:
        p = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    return p, time.time() - t0


# Firecrawl tools Codex must not be able to call. Costs verified on docs.firecrawl.dev
# 2026-07-26. Kept available: firecrawl_scrape (1 credit) and firecrawl_map (1 credit flat,
# any number of URLs), plus the read-only status/list tools.
#   search family  - free equivalents exist (built-in search, tavily, exa, jina)
#   crawl / agent  - unbounded: crawl bills 1 credit PER PAGE, agent caps at 2500 per job
#   monitor_*      - recurring and autonomous; the docs' own example is 2880 credits/month
#                    for a single URL on a 30-minute cron, spent with nobody watching
#   extract/parse  - token-metered or undocumented cost
#   interact       - 2 credits per browser-MINUTE; playwright and cloakbrowser are free
FIRECRAWL_DENY = [
    "firecrawl_search",
    "firecrawl_research_search_papers", "firecrawl_research_search_github",
    "firecrawl_research_related_papers", "firecrawl_research_read_paper",
    "firecrawl_research_inspect_paper",
    "firecrawl_crawl", "firecrawl_agent", "firecrawl_extract", "firecrawl_parse",
    "firecrawl_monitor_create", "firecrawl_monitor_update", "firecrawl_monitor_run",
    "firecrawl_interact", "firecrawl_interact_stop",
]


SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


# =============================================================================================
# Pre-send safety net: secrets and PII
# =============================================================================================
#
# Every document in this project says "tokenize PII in the sent copy, for every channel, before
# the first call", and references/briefs.md admits in as many words: "The harness does NOT do
# this - it is on you." That is exactly the arrangement this session disproved in another area:
# prose failed 5/5 at restricting agy's tool access, and only a permission rule worked. A rule
# that depends on someone remembering it under time pressure is not a control.
#
# The asymmetry here is worse than with tools. A denied tool costs a re-run. A sent payload
# cannot be recalled - it is at three separate vendors, possibly logged, possibly retained, and
# the material in this workspace is immigration-case material.
#
# Two classes, and they are NOT the same:
#   SECRET  - a key, token or private key. Never overridable. There is no review that needs one.
#   PII     - identifiers about a person. Blocked by default, overridable with --allow-pii,
#             because reviewing e.g. a public contact page legitimately contains an address.
#
# Findings are reported as KIND + LINE NUMBER + length only. Printing the match would defeat the
# purpose: this console output is read by the orchestrating model and lands in its transcript,
# which is the very place the identifier must not reach. A line number is enough to fix it.

SECRET_PATTERNS = [
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("ANTHROPIC_KEY",     re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OPENAI_KEY",        re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("AWS_ACCESS_KEY",    re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN",      re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|"
                                     r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("SLACK_TOKEN",       re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("GOOGLE_API_KEY",    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # Labelled assignment: catches .env lines and `Authorization: <token>` pasted into a brief.
    # `(?<![A-Za-z])` and not `\b`, because underscore counts as a word character: `\bapi_key`
    # never matches inside FIRECRAWL_API_KEY, which is the exact shape a real .env line has.
    ("LABELLED_SECRET",   re.compile(r"(?i)(?<![A-Za-z])(?:api[_-]?key|secret|token|password|"
                                     r"passwd|client[_-]?secret|authorization)(?![A-Za-z])"
                                     r"\s*[:=]\s*['\"]?[^\s'\"]{12,}")),
    # `bearer` needs its own rule and used to be an alternative in the branch above, where it
    # could never fire: that branch demands a ':' or '=' AFTER the label, and a real header is
    # `Authorization: Bearer <token>` - the delimiter precedes the word. So the one shape the
    # alternative was added for was the one shape it could not match. Found 2026-07-31 by the
    # PostToolUse hook's self-test; the third pattern in this file to read correct and match
    # nothing, which is why both gates now ship with tests instead of confidence.
    #
    # The digit lookahead is a deliberate trade: without it, `Bearer authentication` (14 chars of
    # ordinary prose) trips the no-override class. Requiring one digit inside a 20+ char token
    # costs the all-alphabetic token, which is rare, and buys silence on English.
    ("BEARER_TOKEN",      re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._\-]*\d)"
                                     r"[A-Za-z0-9._\-]{20,}")),
]

PII_PATTERNS = [
    # USCIS alien registration number. Word-bounded so it cannot match inside a hash.
    ("A_NUMBER",       re.compile(r"\bA-?\d{8,9}\b")),
    # USCIS receipt number: a fixed service-centre prefix plus 10 digits.
    ("USCIS_RECEIPT",  re.compile(r"\b(?:EAC|WAC|LIN|SRC|NBC|MSC|IOE|YSC|NSC|TSC|VSC|CSC)"
                                  r"\d{10}\b")),
    ("SSN",            re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("EMAIL",          re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # No leading \b on the parenthesised form: between a space and "(" there is no word
    # boundary, so "\b\(" never matches and "phone (415) 555-0142" sailed straight through the
    # first version of this gate. Caught by testing it on a payload built to be caught.
    ("US_PHONE",       re.compile(r"(?<!\d)(?:\+1[ .\-]?)?\(\d{3}\)[ .\-]?\d{3}[ .\-]?\d{4}\b"
                                  r"|\b\d{3}-\d{3}-\d{4}\b")),
    # Label-driven, because a bare date is far too common to flag.
    ("DATE_OF_BIRTH",  re.compile(r"(?i)\b(?:date of birth|d\.?o\.?b\.?|дата рождения)\b"
                                  r"\s*[:\-]?\s*\S")),
    ("PASSPORT_LABEL", re.compile(r"(?i)\b(?:passport (?:no\.?|number|#)|номер паспорта)\b"
                                  r"\s*[:\-]?\s*\S")),
]


def scan_payload(text, label):
    """
    Return (secrets, pii): lists of "KIND at LABEL line N (len M)" strings, deduplicated.

    No match text is ever included. See the block comment above for why that is deliberate.
    """
    secrets, pii = [], []
    for lineno, line in enumerate((text or "").splitlines(), 1):
        for bucket, patterns in ((secrets, SECRET_PATTERNS), (pii, PII_PATTERNS)):
            for kind, rx in patterns:
                for m in rx.finditer(line):
                    hit = "%s at %s line %d (%d chars)" % (kind, label, lineno,
                                                           len(m.group()))
                    if hit not in bucket:
                        bucket.append(hit)
    return secrets, pii


def pii_gate(parts, allow_pii):
    """
    parts: list of (label, text). Returns an exit code to propagate, or 0 to continue.

    Runs before --dry-run returns, so --dry-run is a complete preflight: the whole point of a
    free check is that it tells you everything a paid run would have told you.
    """
    secrets, pii = [], []
    for label, text in parts:
        s, p = scan_payload(text, label)
        secrets += s
        pii += p

    if secrets:
        log("\n*** SECRETS IN THE PAYLOAD - REFUSING TO SEND ***")
        for h in secrets:
            log("    " + h)
        log("    A key or token would go to three external vendors and cannot be recalled. There\n"
            "    is no --allow flag for this: remove it from the brief. If it is a false positive\n"
            "    (a placeholder, a documented example), rename the variable or redact the value.")
        return 3
    if pii and not allow_pii:
        log("\n*** PERSONAL IDENTIFIERS IN THE PAYLOAD - NOT SENT ***")
        for h in pii:
            log("    " + h)
        log("    Tokenize these in the SENT copy only - never edit the source of record. Replace\n"
            "    with APPLICANT_1 / [A-NUMBER] / [RECEIPT] and tell the model the placeholders\n"
            "    are expected; a reviewer never needs the real identifiers to review reasoning.\n"
            "    Line numbers are given without the values on purpose - this console output is\n"
            "    read by the orchestrating model, and printing them here would leak them into the\n"
            "    transcript, which is the same mistake one step earlier.\n"
            "    If they genuinely belong in the brief, re-run with --allow-pii.")
        return 3
    if pii and allow_pii:
        log("  [preflight] --allow-pii: sending %d personal identifier(s) to every enabled "
            "channel. This cannot be undone." % len(pii))
    return 0


def _resolve_system(name):
    """
    Find a system-prompt file no matter which project you are standing in.

    This script is invoked by absolute path from any chat, in any project, so a bare
    `--system systems/legal-research.md` resolved against the CURRENT directory - which is
    almost never the skill directory - and died with FileNotFoundError everywhere except here.
    Try the literal path first (so an ad-hoc file still works), then the skill's own presets,
    and accept a bare preset name: `--system legal-research`.
    """
    cands = [name,
             os.path.join(SKILL_DIR, name),
             os.path.join(SKILL_DIR, "systems", name),
             os.path.join(SKILL_DIR, "systems", name + ".md")]
    for c in cands:
        if os.path.isfile(c):
            return c
    presets = []
    sysdir = os.path.join(SKILL_DIR, "systems")
    if os.path.isdir(sysdir):
        presets = [os.path.splitext(f)[0] for f in sorted(os.listdir(sysdir)) if f.endswith(".md")]
    raise SystemExit("--system %r not found. Built-in presets: %s (a path is also accepted)"
                     % (name, ", ".join(presets) or "(none)"))


def _with_system(brief, system):
    """
    Only the HTTPS channel has a real `system` slot. Codex takes one prompt on stdin and agy
    takes one on argv, so for them the system layer has to ride in front of the brief.

    This is not cosmetic. A brief framed as "review this filing strategy" gets REFUSED by Codex
    on policy grounds (measured 2026-07-31), and the refusal still ends with the end marker, so
    it passes every mechanical check. The same underlying question, framed as source-verification
    for attorney review, is answered. The framing therefore has to reach every channel that can
    refuse - which was all of them except http.
    """
    if not system:
        return brief
    return system.rstrip() + "\n\n---\n\n" + brief


def call_codex(brief, marker, workdir, outfile, model=None, effort=None, system=None):
    """
    Prompt goes on STDIN and argv ends with "-". A positional prompt while something else pipes
    stdin makes it hang on 'Reading additional input from stdin...'.
    """
    binary = codex_bin()
    os.makedirs(workdir, exist_ok=True)
    # Injected per invocation instead of written into ~/.codex/config.toml, which is the user's
    # file and off limits. Verified 2026-07-26: the 15-element TOML array survives the Windows
    # command line intact and `codex mcp get firecrawl --json` reports all 15 back.
    deny = "mcp_servers.firecrawl.disabled_tools=[%s]" % ",".join(
        '"%s"' % t for t in FIRECRAWL_DENY)
    # Model override. 2026-07-26: off gpt-5.6-sol/max to gpt-5.4/xhigh (5.6 credits low).
    # 2026-07-30: Igor moved the channel to gpt-5.5/xhigh; probe confirmed the model exists
    # (low-effort "OK", 16,574 tok). Injected with -m / -c so ~/.codex/config.toml is never
    # edited: that file is his. Probe any unfamiliar model name cheaply before a real run.
    # Precedence: the resolved plan (registry + --route) beats the env var beats the default.
    # The env vars stay supported because existing project scripts set them inline.
    model = model or os.environ.get("CODEX_MODEL", "gpt-5.5")
    effort = effort or os.environ.get("CODEX_EFFORT", "xhigh")
    cmd = [binary, "exec",
           "--sandbox", "read-only",
           "--skip-git-repo-check",          # -C often points outside a git repo
           "-C", workdir,
           "--color", "never",
           "-m", model,
           "-c", 'model_reasoning_effort="%s"' % effort,
           "-c", "tools.web_search=true",    # NOT --search: that flag does not exist and kills the launch
           "-c", deny,                       # Firecrawl credit policy; see FIRECRAWL_DENY above
           "-o", outfile,                    # read-only sandbox still writes the report through -o
           "-"]
    try:
        p, secs = _run(cmd, stdin_text=_with_system(brief, system))
    except FileNotFoundError:
        return {"channel": "codex", "ok": False, "error": "binary not found: " + binary}
    except subprocess.TimeoutExpired:
        return {"channel": "codex", "ok": False, "error": "timed out; deep reviews run 25-35 min"}

    text = ""
    if os.path.exists(outfile):
        with open(outfile, encoding="utf-8", errors="replace") as f:
            text = f.read()
    warn = []
    # Codex emits partial output and keeps thinking. Exit code 0 does not mean finished.
    if marker and not text.strip().endswith(marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip():
        warn.append("EMPTY OUTPUT (exit=%d) %s" % (p.returncode, (p.stderr or "")[:200]))
    refusal = refusal_check(text, marker)
    if refusal:
        warn.append(refusal)
    return {"channel": "codex", "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode, "warnings": warn}


AGY_AGENT = "deep-researcher"   # written into the run's own workspace; see _write_agy_agent

# The persona this channel runs under. It exists because the CLI's own product framing is
# "near-zero overhead, fast local iterations" - correct for coding, wrong for a review, and it
# shows up as a short confident answer with two sources. Naming that default and overriding it
# explicitly measurably raises depth (thinking_tokens 1.5k -> 5.9k on the same brief).
#
# What is NOT in here, deliberately: an instruction to avoid MCP tools. That was tried and
# failed 5/5 - the MCP servers' own tool descriptions ("WHEN TO USE THIS SERVER: URL/Webpage
# Reading...") outrank an agent system prompt, and the model reached for jina every time.
# Permission rules are the only thing that decides tool access. Prose does not.
AGY_AGENT_MD = """---
name: deep-researcher
description: Source-verifying research reviewer. Never answers dated questions from memory.
---

# Deep researcher

You are an independent research and verification agent. You are NOT the author of the material
you are given, and your value comes from what you find wrong in it.

## Why the usual terminal defaults do not apply here

This CLI is tuned for fast local coding iterations: short answers, few tool calls, stop as soon
as the answer looks plausible. That tuning is wrong for this task and does not apply. Here, an
answer that arrives quickly and cites nothing is a failed answer. Length, source count and
explicit uncertainty are the deliverable.

## Protocol - follow in order, do not skip ahead

1. Before searching, write out the list of factual claims that need verification. Dated facts,
   version numbers, prices, legal rules, "current" anything - all need verification.
2. For each claim, search, then OPEN the page. A search snippet is not a source: a claim is
   verified only once you have opened the page it lives on.
3. Do not stop at the first plausible answer. For every important claim look for a second,
   independent confirmation, and deliberately look for a source that contradicts it.
4. Prefer primary sources: the vendor's own documentation, the regulator's own page, the
   official changelog, the statute or rule text, the API reference. Use news, blogs and forums
   only as evidence that a dispute exists, never as proof of a fact.
5. Check recency explicitly. For anything that can change, find the effective date or last
   revision date of the source and say what it is. Distinguish the date a rule was PUBLISHED
   from the date it takes EFFECT - they are different facts and conflating them is a real error.
6. Before concluding, review your findings for contradictions - between sources, and between a
   source and the material under review. Contradictions are the most valuable output; never
   smooth them over.

## Hard rules

- Never answer a dated or checkable question from memory. If you did not open a source for it,
  it is not verified, and you must label it as such.
- If your search finds nothing, write exactly: `my search found no confirmation`. Do NOT
  conclude the thing does not exist. Asserting non-existence requires positive evidence of
  absence, with that source's URL.
- Quote the URL you actually opened. Do not reconstruct a citation from memory afterwards: a
  document number that drifts by one digit is a fabricated citation, and it looks exactly like
  a real one.
- If a tool you need is unavailable or denied, say so explicitly and name it. Never quietly
  fall back to answering from memory.

## Required output shape

End with an evidence table, one row per checkable claim:

| claim | source URL | source date | verdict (confirmed / contradicted / not found) | what it changes |

Then a short section "What I could not verify and why".
Then the literal end marker you were given. If you were given none, end with RESEARCH-COMPLETE.
"""


def agy_permission_preflight():
    """
    Answer "will this channel be able to finish?" in milliseconds, instead of finding out after
    a 25-minute run comes back empty. Checks that the allow-rules patch is still in place -
    settings.json is a user file and can be reverted, rewritten by the TUI, or lost on reinstall.
    Returns a warning string, or None if it looks healthy.
    """
    path = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return "cannot read %s (%r) - cannot tell whether headless runs will survive" % (path, e)
    allow = (cfg.get("permissions") or {}).get("allow") or []
    if not any(str(r).startswith("mcp(") for r in allow):
        return ("no mcp() allow-rules in %s - in headless mode the first MCP tool the model "
                "reaches for will be auto-denied and the ENTIRE run discarded (empty answer, "
                "status SUCCESS, exit 0). Run: python patch_agy_permissions.py" % path)
    deny = (cfg.get("permissions") or {}).get("deny") or []
    if not any("firecrawl_crawl" in str(r) for r in deny):
        return ("allow-rules are present but firecrawl_crawl is NOT denied in %s - this channel "
                "can spend Firecrawl credits per page with no ceiling. Run: python "
                "patch_agy_permissions.py" % path)
    return None


def _resolve_bin(env_var, exe, extra_paths):
    """
    Find a CLI without assuming this is Igor's machine.

    The old defaults were `codex` (PATH) and a literal %LOCALAPPDATA%\\agy\\bin\\agy.exe, which
    is correct here and wrong everywhere else: on macOS and Linux LOCALAPPDATA is unset, so the
    default collapsed to the relative path "agy/bin/agy.exe" and the channel died with a
    FileNotFoundError that named a path nobody has. Env var, then PATH, then the known install
    locations for this platform.
    """
    v = os.environ.get(env_var)
    if v:
        return v
    found = shutil.which(exe)
    if found:
        return found
    for p in extra_paths:
        if p and os.path.isfile(p):
            return p
    return exe          # let the caller's FileNotFoundError name it


def codex_bin():
    home = os.path.expanduser("~")
    return _resolve_bin("CODEX_BIN", "codex", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "OpenAI", "Codex", "bin",
                     "codex.exe"),
        os.path.join(home, ".local", "bin", "codex"),
        "/usr/local/bin/codex", "/opt/homebrew/bin/codex",
    ])


def agy_bin():
    home = os.path.expanduser("~")
    return _resolve_bin("AGY_BIN", "agy", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"),
        os.path.join(home, ".agy", "bin", "agy"),
        os.path.join(home, ".local", "bin", "agy"),
        "/usr/local/bin/agy", "/opt/homebrew/bin/agy",
    ])


def channel_preflight(want, outdir):
    """
    Yield human-readable problems for the channels about to run. Never raises, never blocks:
    a channel that fails here still gets its turn, because a stale check that vetoes a working
    channel is worse than a warning. Never prints a key value - only whether one is present.
    """
    if "http" in want:
        key = os.environ.get("MODEL_API_KEY")
        if not key and os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                    key = winreg.QueryValueEx(reg, "MODEL_API_KEY")[0]
            except OSError:
                pass
        if not key:
            yield ("http: MODEL_API_KEY is not set (process env or HKCU\\Environment). This "
                   "channel will fail. Set it, or run with --skip spark.")
        else:
            yield "http: key present (len %d), endpoint %s" % (
                len(key), os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"))
    for name, resolver in (("codex", codex_bin), ("agy", agy_bin)):
        if name not in want:
            continue
        b = resolver()
        if not (os.path.isfile(b) or shutil.which(b)):
            yield "%s: binary not found (%s). Install it or exclude the channel." % (name, b)
    if "agy" in want:
        problem = agy_permission_preflight()
        if problem:
            yield "agy: " + problem
        # agy mangles non-ASCII in its own JSON output (observed: a Cyrillic directory came back
        # as "D:\\Claude Code\\???????????\\Gemini"). Nothing crashed, but a workspace it cannot
        # spell is not a workspace you want a 25-minute run to depend on.
        p = os.path.abspath(outdir)
        try:
            p.encode("ascii")
        except UnicodeEncodeError:
            yield ("agy: the output path contains non-ASCII characters (%s). agy corrupts them "
                   "in its own logs; prefer an ASCII path such as %%TEMP%%\\reviews." % p)


def _write_agy_agent(workdir):
    """
    Ship the reviewer persona with the code instead of installing it in ~/.gemini.

    A workspace-scoped agent at <workspace>/.agents/agents/<name>/agent.md IS discovered and
    loaded (verified 2026-07-31: the stream-json `init` event came back with
    agent="deep-researcher"), while the same directory's settings.json and mcp_config.json are
    NOT read by this build. So the persona travels with the run and nothing global is touched -
    but permissions cannot be set this way, which is why patch_agy_permissions.py exists.
    """
    d = os.path.join(workdir, ".agents", "agents", AGY_AGENT)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "agent.md"), "w", encoding="utf-8") as f:
        f.write(AGY_AGENT_MD)
    return d


_URL_RE = re.compile(r"https?://[^\s)\]>\"'`|]+")


def _norm_url(u):
    """host+path, lowercased, no www and no trailing slash: tracking params are not differences."""
    s = urlsplit(u.rstrip(".,;:"))
    return (s.netloc.lower().replace("www.", ""), (s.path or "/").rstrip("/").lower())


def _cite_check(text, opened):
    """
    Compare the URLs the answer CITES against the URLs the run actually OPENED.

    Tool counts prove activity, not grounding. Measured 2026-07-31 on a real review: the model
    issued 24 search queries, opened exactly ONE page, and cited six URLs. Five had never been
    fetched, and two of those were fabricated - real-looking Federal Register document numbers
    glued to the correct article slug:

        cited  .../2022/09/09/2022-19422/public-charge-ground-of-inadmissibility
        actual  2022-19422 = "Proposed Collection; Comment Request", 87 FR 54985
        cited  .../2026/07/20/2026-15432/public-charge-ground-of-inadmissibility
        actual  2026-15432 = "Agency Information Collection Activities...", 91 FR 48060

    Both look exactly like a citation. Only this check tells them apart cheaply.
    """
    cited, seen = [], set()
    for m in _URL_RE.finditer(text or ""):
        n = _norm_url(m.group())
        if n not in seen:
            seen.add(n)
            cited.append((m.group(), n))
    grounded = [u for u, n in cited if n in opened]
    ungrounded = [u for u, n in cited if n not in opened]
    return len(cited), grounded, ungrounded


def _parse_agy_stream(path):
    """
    Turn the NDJSON event log into the only numbers that separate a real review from a fluent
    guess: successful tool calls, denied tool calls, and thinking_tokens.

    The schema is NOT what the changelog implies. The discriminator is `event`, not `type`, and
    each payload is nested under a key named after the event: {"event":"result","result":{...}}.
    Indexing ev["type"] silently matches nothing on every line, which is indistinguishable from
    a model that did no work at all.
    """
    out = {"tools": {}, "denied": {}, "errors": [], "text": "", "status": None,
           "thinking": None, "out_tokens": None, "turns": None, "seconds": None,
           "perm_mode": None, "opened": set(), "queries": 0}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            body = ev.get(kind) if isinstance(ev.get(kind), dict) else {}
            if kind == "init":
                out["perm_mode"] = body.get("permission_mode")
                continue
            if kind == "result":
                out["text"] = body.get("response") or ""
                out["status"] = body.get("status")
                u = body.get("usage") or {}
                out["thinking"] = u.get("thinking_tokens")
                out["out_tokens"] = u.get("output_tokens")
                out["turns"] = body.get("num_turns")
                out["seconds"] = body.get("duration_seconds")
                continue
            ti = body.get("tool_info") or {}
            name = ti.get("name")
            if not name:
                continue
            p = ti.get("parameters") or {}
            if name == "call_mcp_tool" and p.get("ToolName"):
                name = "mcp:%s/%s" % (p.get("ServerName", "?"), p.get("ToolName"))
                p = p.get("Arguments") or p
            err = (ti.get("error") or {}).get("message", "")
            if not err and body.get("state") != "ERROR":
                for k in ("url", "Url", "URL"):
                    if p.get(k):
                        out["opened"].add(_norm_url(str(p[k])))
                if p.get("query") or p.get("Query") or p.get("search_term"):
                    out["queries"] += 1
            if body.get("state") == "ERROR" or err:
                out["denied"][name] = out["denied"].get(name, 0) + 1
                if err and err not in out["errors"]:
                    out["errors"].append(err)
            else:
                out["tools"][name] = out["tools"].get(name, 0) + 1
    return out


def call_agy(brief, marker, workdir, outfile, model=None, effort="high", timeout="25m",
             system=None):
    """
    Two ways in, and they fail differently. Inline -p avoids the file-reading tool (and its
    permission error) but is capped by the Windows argv limit. --add-dir has no size cap but needs
    the tool. Pick by size.
    """
    binary = agy_bin()
    os.makedirs(workdir, exist_ok=True)
    _write_agy_agent(workdir)
    ndjson = os.path.splitext(outfile)[0] + ".events.ndjson"
    brief = _with_system(brief, system)

    if len(brief) <= AGY_ARGV_LIMIT:
        # Pass as a single argv element; let the runtime quote it, never hand-quote a 14KB string.
        cmd = [binary, "-p", brief]
    else:
        bpath = os.path.join(workdir, "BRIEF.md")
        with open(bpath, "w", encoding="utf-8") as f:
            f.write(brief)
        cmd = [binary, "-p",
               "Read BRIEF.md in the working directory and carry out the task described in it.",
               "--add-dir", workdir]
    # --effort IS accepted (low|medium|high) as of 1.1.5 - the old comment here claimed it was
    # rejected, which cost the tier system its only depth lever on this channel. But it is
    # rejected in one specific case, and rejected HARD (exit 1, empty result, 3 seconds):
    #
    #   --model gemini-3.1-pro-high --effort low
    #   -> "invalid model selection: --model gemini-3.1-pro-high conflicts with --effort=low"
    #
    # Effort is baked into the suffixed slug, so passing a contradicting --effort is a conflict.
    # Passing an AGREEING one is fine, and the bare base slug plus --effort is fine. Rather than
    # depend on the registry always holding a bare slug, strip a suffix we recognise and let
    # --effort be the single source of truth. Measured 2026-07-31 across all three combinations.
    mdl = model or os.environ.get("AGY_MODEL", "gemini-3.1-pro")
    for suffix in ("-high", "-medium", "-low"):
        if mdl.endswith(suffix):
            mdl = mdl[:-len(suffix)]
            break
    # --mode is kept because it is the documented intent, but it must NOT be read as the thing
    # that makes this run read-only. Measured 2026-07-31 on 1.1.9, headless `-p`:
    #
    #   --mode plan | default | accept-edits | definitely-not-a-mode
    #     -> every one exits 0, and every one reports permission_mode="request-review" in init.
    #
    # So the flag is (a) unvalidated - a typo like `--mode paln` is accepted in silence, with no
    # warning and no non-zero exit - and (b) not observable in the telemetry, so there is no way
    # to confirm from the event log that it took effect at all. Anything that depends on it is
    # depending on something unverifiable.
    #
    # What actually constrains this run is the permission configuration written by
    # patch_agy_permissions.py, which is checked by doctor.py and is visible as the granted tool
    # list in the init event. That is Igor's own rule holding for a third time in this channel:
    # prose does not restrict tool access, an agent prompt does not, and now a CLI flag does not
    # either - only permission rules do. Do not "harden" this by tightening the --mode value.
    cmd += ["--model", mdl,
            "--effort", effort,
            "--agent", AGY_AGENT,
            "--mode", "plan", "--sandbox",
            "--output-format", "stream-json",   # the ONLY way this channel reports tool use
            "--print-timeout", timeout]         # default truncates at 5m
    try:
        # cwd must be the workspace or the workspace-scoped agent is never discovered.
        p, secs = _run(cmd, timeout=3600, cwd=workdir, stdout_path=ndjson)
    except FileNotFoundError:
        return {"channel": "agy", "ok": False,
                "error": "binary not found (it is NOT on PATH): " + binary}
    except subprocess.TimeoutExpired:
        return {"channel": "agy", "ok": False, "error": "timed out"}

    ev = _parse_agy_stream(ndjson)
    text = ev["text"]
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(text or (p.stderr or ""))

    warn = []
    # THE failure mode of this channel, measured 5/5 times on 2026-07-31: one tool left at the
    # default "ask" is auto-denied in headless mode (no prompt is possible), and that single
    # denial DISCARDS THE WHOLE RUN - 29 successful tool calls thrown away, response "",
    # status "SUCCESS", exit code 0. Nothing but the response length reveals it.
    denial = [e for e in ev["errors"] if "denied permission" in e]
    if denial or ("permission that headless mode cannot prompt for" in (p.stderr or "")):
        warn.append("PERMISSION DENIAL KILLED THE RUN: %s. Fix it once by running "
                    "patch_agy_permissions.py (adds the allow-rules for the free read-only web "
                    "tools and deny-rules for the metered Firecrawl ones). Do NOT reach for "
                    "--dangerously-skip-permissions: that also unlocks firecrawl_crawl."
                    % (denial[0] if denial else "see stderr"))
    if marker and marker not in text:
        warn.append("END MARKER ABSENT - output is incomplete, do not parse it as a review")
    refusal = refusal_check(text, marker, min_chars=500)
    if refusal:
        warn.append(refusal + " Never validate this channel on the exit code or the status field.")
    note = []
    # status lies in BOTH directions, both observed on 2026-07-31: "SUCCESS" on an empty answer
    # after a permission denial, and "ERROR" on a complete, marker-terminated 1,417-char answer
    # because one late MCP call got a transient HTTP 503. The marker is the only honest gate.
    if ev["status"] == "ERROR" and marker and marker in text:
        note.append("status=ERROR but the end marker is present and the answer is complete - a "
                    "late transient tool error. Judge the text, not the status.")
    searches = sum(v for k, v in ev["tools"].items()
                   if any(s in k for s in ("search", "read_url", "crawl", "scrape", "browser")))
    if not searches:
        note.append("ZERO successful search/fetch calls - every dated fact here is from training "
                    "data. Treat dated claims as unverified.")
    n_cited, grounded, ungrounded = _cite_check(text, ev["opened"])
    if ungrounded:
        msg = ("CITATIONS: only %d of %d cited URLs were actually opened in this run "
               "(%d searches issued, %d pages fetched). Unopened: %s. These came from the "
               "model's knowledge, not from a page it read - this channel has been observed "
               "fabricating plausible document numbers. Verify them before repeating them."
               % (len(grounded), n_cited, ev["queries"], len(ev["opened"]),
                  ", ".join(ungrounded[:5])))
        # Zero grounding is a different animal from partial grounding: the model cited sources
        # and opened NONE of them, which is memory dressed as research. Partial grounding stays
        # a note, because a review can be worth reading even when some citations are weak.
        (warn if n_cited and not grounded else note).append(msg)
    return {"channel": "agy", "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode, "warnings": warn,
            "notes": note, "tool_calls": sum(ev["tools"].values()), "searches": searches,
            "denied": sum(ev["denied"].values()), "thinking": ev["thinking"],
            "out_tokens": ev["out_tokens"], "agy_status": ev["status"],
            "perm_mode": ev["perm_mode"], "events": ndjson}


# =============================================================================================

def main():
    ap = argparse.ArgumentParser(description="Run one brief past several reviewer models at once.")
    ap.add_argument("--brief", required=True, help="path to the brief sent to every channel")
    ap.add_argument("--system", help="path to the system prompt for the HTTPS channel")
    ap.add_argument("--tier", default="strategic", choices=list(TIERS))
    ap.add_argument("--marker", default="REVIEW-COMPLETE",
                    help="literal string the model must end with; absence means incomplete")
    ap.add_argument("--out", default="./reviews")
    # No argparse `choices` here on purpose. It used to list spark/http/codex/agy, which let
    # `--only http` through the parser and straight into a literal dict lookup in apply_flags
    # that only knew the registry names - so a documented flag died with "unknown channel 'http'".
    # routing.canon_channel now resolves any alias in channels.json (http, gemini, кодекс...) and
    # raises with the full list on a miss, which is a better error than argparse's anyway.
    ap.add_argument("--only", nargs="*",
                    help="restrict to some channels; any alias in channels.json works "
                         "(spark/http, codex, agy/gemini). Default: all three")
    # Channel and model selection without touching code: the registry holds every model name,
    # and --route takes whatever Igor typed in chat, verbatim, in Russian or English.
    ap.add_argument("--route", help='free text, e.g. "не используй 5.6 Sol, вместо нее 5.5"')
    ap.add_argument("--skip", nargs="*", help="channels to exclude")
    ap.add_argument("--set", dest="sets", nargs="*", help="channel=model, e.g. codex=gpt-5.4")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the resolved plan and exit without spending anything")
    ap.add_argument("--allow-pii", action="store_true",
                    help="send personal identifiers anyway. Secrets are never sendable")
    a = ap.parse_args()

    # Validate every input BEFORE the dry-run exit, so --dry-run is a real preflight. A mistyped
    # --system used to surface only after the expensive channels had already been launched.
    _resolve_system(a.system or "base-depth")
    if not os.path.isfile(a.brief):
        log("brief not found: %s" % a.brief)
        return 2

    # Resolve WHO runs and WITH WHAT before reading the brief, so a bad route costs nothing.
    plan = None
    routing = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import routing
    except Exception as e:
        # The registry is an optimisation, not a dependency: without it the old env-var defaults
        # still run. Only the selection flags genuinely need it, so only they are fatal.
        # This import must be its own try block - catching routing.RouteError below when the
        # import itself failed would raise NameError on the exception clause.
        log("routing unavailable (%r) - falling back to env defaults" % (e,))
        if a.route or a.skip or a.sets:
            return 2
    if routing is not None:
        try:
            reg = routing.load_registry()
            plan = routing.resolve(reg, route=a.route, only=a.only, skip=a.skip,
                                   sets=a.sets, tier=a.tier)
            log(routing.format_plan(plan, reg))
        except routing.RouteError as e:          # ambiguity must stop the run, never guess
            log("ROUTE ERROR: %s" % e)
            return 2

    with open(a.brief, encoding="utf-8") as f:
        brief = f.read()
    # The default used to be a single sentence, and it reached only the HTTPS channel. Depth,
    # source discipline and output language were therefore left to whatever each model defaults
    # to - which for a terminal-tuned CLI is "short, fast, few tool calls". base-depth.md is the
    # amplifier that used to be pasted by hand into briefs; it now ships and applies everywhere.
    system = "You are an independent reviewer. You are NOT the author. Find what is wrong."
    try:
        with open(_resolve_system(a.system or "base-depth"), encoding="utf-8") as f:
            system = f.read()
    except SystemExit:
        if a.system:                      # an explicitly named preset that does not exist is fatal
            raise
        # a missing default is not: fall back to the one-liner rather than refusing to run
    # The rule that stops a failed search from becoming a false "this does not exist".
    # SKILL.md calls this "the rule that goes in every system prompt" - but until 2026-07-31 it
    # reached only the HTTPS channel, because Codex and agy had no system slot wired at all.
    # They do now (_with_system), and this is always passed, with or without --system.
    system += ('\n\nIf your search finds nothing, write exactly "my search found no confirmation" '
               "and do NOT conclude the thing does not exist. A non-existence claim is permitted "
               "only with positive evidence of absence, with that source's URL.")

    # Last point at which anything can still be stopped for free. Both files are scanned: a
    # hand-written --system file is just as capable of carrying a name or a key as the brief.
    gate = pii_gate([("brief", brief), ("system", system)], a.allow_pii)
    if gate:
        return gate

    if plan:
        want = {c for c, p in plan.items() if p["enabled"]}
        if "spark" in want:                      # "spark" is the registry name for the HTTPS channel
            want.add("http")
    else:
        # Registry unavailable: no alias table, so accept both spellings by hand rather than
        # letting the degraded path reintroduce the `--only http` failure the router just fixed.
        want = {"spark": "http", "gemini": "agy"}.get
        want = {want(c) or c for c in (a.only or ["http", "codex", "agy"])}
    if not want:
        log("every channel is disabled - nothing to run")
        return 2
    agy_cfg = (plan or {}).get("agy") or {}
    codex_cfg = (plan or {}).get("codex") or {}

    # Everything below is free and answers "will this round survive?" before it is launched.
    # It runs on --dry-run too: a preflight that skips the checks a real run needs is not a
    # preflight. Previously --dry-run verified the route, the brief path and the preset, then
    # said nothing about a missing key or a missing binary - so the first evidence that Codex
    # was not installed arrived after the other two channels had already been paid for.
    for problem in channel_preflight(want, a.out):
        log("  [preflight] " + problem)

    if a.dry_run:
        log("--dry-run: nothing was called")
        return 0

    os.makedirs(a.out, exist_ok=True)

    jobs = {}
    log("brief=%d chars  tier=%s  marker=%s" % (len(brief), a.tier, a.marker))
    # Threads, not asyncio: two of the three channels are blocking subprocesses, and on Windows
    # asyncio subprocess support depends on the event loop policy. Threads just work.
    with ThreadPoolExecutor(max_workers=3) as ex:
        if "http" in want:
            jobs["http"] = ex.submit(call_http_reviewer, brief, system, a.tier, a.marker)
        if "codex" in want:
            jobs["codex"] = ex.submit(call_codex, brief, a.marker,
                                      os.path.join(a.out, "codex-ws"),
                                      os.path.join(a.out, "CODEX.md"),
                                      model=codex_cfg.get("model"),
                                      effort=codex_cfg.get("effort"),
                                      system=system)
        if "agy" in want:
            jobs["agy"] = ex.submit(call_agy, brief, a.marker,
                                    os.path.join(a.out, "agy-ws"),
                                    os.path.join(a.out, "AGY.md"),
                                    model=agy_cfg.get("model"),
                                    effort=agy_cfg.get("effort") or "high",
                                    timeout=agy_cfg.get("timeout") or "25m",
                                    system=system)
        results = {k: f.result() for k, f in jobs.items()}

    log("\n" + "=" * 78)
    ok_count = 0
    for name, r in results.items():
        if r.get("text"):
            with open(os.path.join(a.out, name.upper() + ".md"), "w", encoding="utf-8") as f:
                f.write(r["text"])
        status = "OK" if r.get("ok") else "PROBLEM"
        log("[%s] %s  %ss" % (name, status, r.get("seconds", "?")))
        if r.get("error"):
            log("    error: " + str(r["error"]))
        if name == "http" and r.get("out_tokens") is not None:
            log("    tokens in=%s out=%s | tool_calls=%s | stop=%s | blocks=%s"
                % (r.get("in_tokens"), r.get("out_tokens"), r.get("tool_calls"),
                   r.get("stop_reason"), r.get("block_types")))
        if name == "agy" and r.get("tool_calls") is not None:
            # This channel used to report nothing at all about its own work, so a fluent answer
            # written entirely from training data was indistinguishable from a researched one.
            log("    thinking=%s out=%s | tools=%s (search/fetch=%s) denied=%s | "
                "agy_status=%s perms=%s"
                % (r.get("thinking"), r.get("out_tokens"), r.get("tool_calls"),
                   r.get("searches"), r.get("denied"), r.get("agy_status"),
                   r.get("perm_mode")))
            if r.get("events"):
                log("    event log: %s" % r["events"])
        if r.get("bytes"):
            log("    bytes=%s exit=%s" % (r["bytes"], r.get("exit")))
        for w in r.get("warnings", []):
            log("    FAIL: " + w)
        for n in r.get("notes", []):
            log("    note: " + n)
        ok_count += bool(r.get("ok"))

    log("=" * 78)
    log("%d/%d channels returned a verified review. Outputs in %s"
        % (ok_count, len(results), os.path.abspath(a.out)))
    log("Now report, per channel: accepted / rejected with proof / where they disagreed. "
        "The disagreement is the product.")
    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
