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
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
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


# Console output is also teed to <out>/run.log and kept in memory for diagnostics.json.
# Appending per line rather than buffering is deliberate: if the run is killed or the machine
# dies mid-review, the log of what happened up to that point is what makes the failure
# diagnosable, and a buffered log is empty in exactly that case.
_LOG = {"path": None, "lines": []}


def log(msg):
    # Scrubbed at the single choke point rather than at each call site. Caught while testing the
    # crash handler: an exception whose MESSAGE contained a key printed it to the console in full,
    # because only the diagnostics FILE was being scrubbed. stdout is read by the orchestrating
    # model and archived to disk, so it is the same exfiltration surface as any other - and the
    # one call site that forgets is the one that matters. scrub() is defined further down; the
    # lookup happens at call time, so ordering is not a problem.
    msg = scrub(str(msg))
    print(msg, flush=True)
    _LOG["lines"].append(msg)
    if _LOG["path"]:
        try:
            with open(_LOG["path"], "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass          # a broken log must never take the run down with it


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
    # Label-driven, because a bare date is far too common to flag. But the label alone is not
    # enough either: `\s*[:\-]?\s*\S` accepts ANY next character, so the sentence "blocks a
    # labelled date of birth unless you pass --allow-pii" matched on the word "unless". Found by
    # running the publish audit over this project's own prose, which is full of such sentences.
    # A false positive here is not cosmetic - it is the failure mode that kills the gate, because
    # a user who sees it fire on clean text learns to pass --allow-pii by reflex, and that flag
    # disables the whole PII class at once. So the value must actually look like a date.
    #
    # NOTE the missing trailing \b after the label. This set has now hit the same trap four times:
    # `d\.?o\.?b\.?\b` can never match in "d.o.b. April 12, 1988", because between the final "."
    # and the space there is no word boundary - both are non-word characters. Ditto "passport no."
    # The date/identifier shape below is the real gate, so the closing \b was only ever a liability.
    ("DATE_OF_BIRTH",  re.compile(r"(?i)\b(?:date\s+of\s+birth|d\.?o\.?b\.?|дата\s+рождения)"
                                  r"[\s:=—-]*(?:is|born)?[\s:=—-]*"
                                  r"(?:\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}"        # 1988-04-12, 4/12/88
                                  r"|\d{1,2}\s+[A-Za-zА-Яа-я]{3,}\.?\s+\d{4}"    # 12 April 1988
                                  r"|[A-Za-zА-Яа-я]{3,}\.?\s+\d{1,2},?\s+\d{4})")),  # April 12, 1988
    # Same failure, same fix: a passport number is an identifier, so require one - six to twelve
    # alphanumerics containing at least one digit - rather than "any next character". The digit is
    # what keeps "the passport number fields are blank" from matching on the word "fields".
    ("PASSPORT_LABEL", re.compile(r"(?i)\b(?:passport\s*(?:no\.?|number|#)|номер\s+паспорта)"
                                  r"[\s:=№-]*"
                                  r"(?=[A-Za-z0-9]{6,12}(?![A-Za-z0-9]))"
                                  r"[A-Za-z0-9]*\d[A-Za-z0-9]*")),
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


def scrub(text):
    """
    Replace every secret- and PII-shaped run with a labelled placeholder.

    This is the one place in the program that REWRITES rather than reports, because
    diagnostics.json is written to be pasted into a chat or attached to a public issue, and
    "safe as long as the author remembered" is not safe.

    re.sub, never a truncation. On 2026-07-31 a real key reached a transcript through a
    "masking" expression that kept the first 60 characters of a 48-character key, i.e. all of
    it. A substitution cannot fail that way: either the pattern matched and the text is gone,
    or it did not match and nothing claimed otherwise.
    """
    if not isinstance(text, str):
        return text
    for kind, rx in SECRET_PATTERNS:
        text = rx.sub("[REDACTED:%s]" % kind, text)
    for kind, rx in PII_PATTERNS:
        text = rx.sub("[REDACTED:%s]" % kind, text)
    return text


def scrub_deep(obj):
    """scrub() over an arbitrary JSON-shaped structure, keys included."""
    if isinstance(obj, dict):
        return {scrub(k): scrub_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_deep(v) for v in obj]
    return scrub(obj)


# Signature -> (plain-language cause, what to actually do). Matched case-insensitively against
# the error text. This is what turns diagnostics.json from a stack trace into something an
# assistant - or a non-technical user pasting the file into a chat - can act on.
KNOWN_FAILURES = [
    ("MODEL_API_KEY not set",
     "The Spark channel has no API key.",
     "Set MODEL_API_KEY, or run with --skip spark to use the other channels only. The harness "
     "is designed to run any subset of channels; a missing key is not a fatal condition."),
    ("binary not found",
     "A command-line reviewer is not installed, or is installed somewhere this program did not "
     "look.",
     # Deliberately names no channel. This advice used to say "--skip codex / --skip gemini" and
     # "CODEX_BIN=... or AGY_BIN=...", which was correct until a fourth channel was added to the
     # registry - after which a user whose KIMI channel failed was told to reconfigure Codex. The
     # channel that failed is already printed on the line above this one; repeating a frozen list
     # here could only ever go stale.
     "Either install it, or exclude that channel with --skip <channel>. If it IS installed, point "
     "the harness at it explicitly with the matching <CHANNEL>_BIN environment variable "
     "(CODEX_BIN / AGY_BIN / HERMES_BIN). `python doctor.py` reports which ones were found."),
    ("END MARKER ABSENT",
     "The model stopped before finishing, or never emitted the agreed end-of-review marker.",
     "The harness appends the marker instruction automatically when the brief does not contain "
     "it. If this still fires, the model most likely hit a length or time limit - re-run that "
     "channel alone, or lower --tier."),
    ("REFUSAL",
     "The model declined the task on policy grounds rather than failing technically.",
     "This is almost always a framing problem, not a subject ban. Rewrite the brief as "
     "verification of sources rather than as strategy or advice, and pass "
     "--system legal-research for regulated subjects."),
    ("status.*401|unauthor|invalid.*api.?key|authentication",
     "The API key was rejected by the vendor.",
     "The key is present but not valid - it was revoked, rotated, or belongs to a different "
     "account. Issue a new one and replace it in the environment."),
    ("status.*429|rate.?limit|quota|weekly limit",
     "A usage or rate limit was hit on that vendor.",
     "Wait, or route the work to another channel with --route/--skip. Do NOT switch that "
     "channel to a metered pay-per-token key to get around a subscription limit unless you "
     "have decided that cost is acceptable."),
    ("timed out|timeout",
     "The channel took longer than its allotted time.",
     "Raise the timeout for that channel, lower --tier, or split the brief into smaller "
     "questions. Deep tiers on large briefs can legitimately run for many minutes."),
    ("EMPTY ANSWER|response.*\"\"",
     "The channel returned nothing at all.",
     "On the Antigravity channel this is the classic symptom of a denied tool permission "
     "discarding the whole run - run patch_agy_permissions.py. Otherwise check the run log for "
     "an error frame."),
    ("SECRETS IN THE PAYLOAD",
     "The brief contains something shaped like a key, token or password.",
     "This is refused with no override, because a credential sent to three external vendors "
     "cannot be recalled. Remove or redact it in the brief."),
]


def diagnose(text):
    """Return (cause, fix) for an error string, or (None, None) if unrecognised."""
    for sig, cause, fix in KNOWN_FAILURES:
        if re.search(sig, text or "", re.I):
            return cause, fix
    return None, None


def write_diagnostics(outdir, payload):
    """
    Write a scrubbed, machine-readable account of the run to <outdir>/diagnostics.json.

    Written on EVERY run, not only on failure: the common support question is "it worked
    yesterday", which needs the successful run's file to compare against. Never raises - a
    diagnostics writer that can break the thing it is diagnosing is worse than none.
    """
    try:
        os.makedirs(outdir, exist_ok=True)
        payload = scrub_deep(payload)
        path = os.path.join(outdir, "diagnostics.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        return path
    except Exception:
        return None


# How many cited URLs to probe per channel. A review normally cites 10-35; a runaway answer can
# cite hundreds, and probing those serially would make the check the slowest part of the run. When
# the cap bites it is REPORTED, never silent - a truncated check that reads as a complete one is
# the same lie as a citation to a page nobody opened.
CITECHECK_MAX_URLS = 60


def citation_audit(results, enabled=True):
    """
    Fetch every URL each channel cited and report which ones do not exist.

    WHY THIS RUNS AUTOMATICALLY. There are two different questions about a citation, and only one
    of them is answerable everywhere:

      "did the model OPEN this page?"   - grounding. Needs the channel's own tool telemetry.
                                          agy exposes it, Spark reports a count, Codex reports
                                          NOTHING. So for a third of the panel it is unanswerable.
      "does this page EXIST?"           - existence. Needs only a fetch, so it works on every
                                          channel including Codex. Weaker, but universal.

    Existence is the check that catches the dangerous failure: a fluent, correct-sounding review
    citing pages that were never opened and sometimes never existed. Measured 2026-07-31: agy cited
    11 URLs of which 3 were 404, while its conclusions were right. That combination survives a
    skim, which is exactly why a human is the wrong instrument for it.

    It used to be a separate command you had to remember to run afterwards. A verification step
    that depends on remembering is one that runs least often when the run is rushed - which is the
    same moment nobody re-reads the citations either. So: on by default, `--no-citecheck` to turn
    it off.

    Deliberately does NOT affect the exit code. A 404 is information for the reader, not a verdict:
    one measured "dead" citation was a GitHub API query for a tag that does not exist, i.e. the 404
    WAS the answer. Failing a run on that would teach people to ignore the check, and a gate that
    gets ignored protects nothing.

    Never raises. Costs nothing at either vendor - it is plain HTTP to the cited hosts.
    """
    out = {}
    if not enabled:
        return {"skipped": "disabled with --no-citecheck"}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from citecheck import URL_RE, probe_url, resolve_all
    except Exception as exc:
        # A partial install is a supported state, so this is a note, not a failure.
        return {"skipped": "citecheck.py unavailable (%s)" % type(exc).__name__}

    for name, r in results.items():
        text = r.get("text") or ""
        if not text:
            continue
        seen, urls = set(), []
        for u in URL_RE.findall(text):
            u = u.rstrip(".,;:")          # prose punctuation is not part of the URL
            if u not in seen:
                seen.add(u)
                urls.append(u)
        if not urls:
            out[name] = {"cited": 0}
            continue
        probed, dropped = urls[:CITECHECK_MAX_URLS], max(0, len(urls) - CITECHECK_MAX_URLS)
        try:
            verdicts = resolve_all(probed)
        except Exception as exc:
            out[name] = {"cited": len(urls), "error": type(exc).__name__}
            continue
        tally, detail = {}, []
        for u, (v, why) in zip(probed, verdicts):
            tally[v] = tally.get(v, 0) + 1
            if v in ("DEAD", "MOVED", "UNKNOWN"):
                detail.append({"verdict": v, "url": u, "note": why})
        entry = {"cited": len(urls), "probed": len(probed), "tally": tally,
                 "dead": tally.get("DEAD", 0), "flagged": detail}
        if dropped:
            entry["not_probed"] = dropped
            entry["not_probed_note"] = ("cap of %d per channel; these were NOT checked and are "
                                        "not counted as live" % CITECHECK_MAX_URLS)
        out[name] = entry
    return out


def log_citation_audit(audit):
    """Print the citation audit. BLOCKED/UNKNOWN are never called fabrication."""
    if not audit or "skipped" in audit:
        if audit.get("skipped"):
            log("\nCitation check skipped: %s" % audit["skipped"])
        return
    log("\nCitation existence check (no vendor cost; fetches the cited pages directly):")
    for name, e in sorted(audit.items()):
        if e.get("error"):
            log("  [%s] could not check (%s) - this is NOT evidence against the citations"
                % (name, e["error"]))
            continue
        if not e.get("cited"):
            log("  [%s] cited no URLs" % name)
            continue
        counts = "  ".join("%s=%d" % (k, v) for k, v in sorted(e.get("tally", {}).items()))
        log("  [%s] %d cited, %d probed  %s" % (name, e["cited"], e.get("probed", 0), counts))
        if e.get("not_probed"):
            log("      %d URL(s) beyond the per-channel cap were NOT checked" % e["not_probed"])
        for d in e.get("flagged", []):
            # The URL is not truncated. It is the one thing on this line the reader has to be able
            # to copy and open, and a shortened URL that looks whole is its own small lie.
            log("      %-8s %s %s" % (d["verdict"], d["url"], (d["note"] or "")[:40]))
        if e.get("dead"):
            log("      %d cited URL(s) return 404/410. A citation to a page that does not exist "
                "was not read - it was constructed. Check what it was supporting." % e["dead"])
    log("  BLOCKED/UNKNOWN mean the check could not be completed, never that a source is fake.")


def environment_report(want=()):
    """Facts about this machine that determine whether a run can work. No secret values."""
    key = os.environ.get("MODEL_API_KEY") or ""
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                key = winreg.QueryValueEx(reg, "MODEL_API_KEY")[0]
        except OSError:
            key = ""
    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "os_name": os.name,
        "cwd": os.getcwd(),
        "skill_dir": os.path.dirname(os.path.abspath(__file__)),
        # Presence and length only. Printing the value is the failure this whole file guards.
        "spark_key_present": bool(key),
        "spark_key_length": len(key),
        "spark_endpoint": os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"),
    }
    for name, resolver in (("codex", codex_bin), ("agy", agy_bin), ("kimi", hermes_bin)):
        try:
            b = resolver()
            found = bool(os.path.isfile(b) or shutil.which(b))
            env[name + "_path"] = b
            env[name + "_installed"] = found
            env[name + "_version"] = _binary_version(b) if found else None
        except Exception as exc:
            env[name + "_installed"] = False
            env[name + "_error"] = repr(exc)
    return env


def _binary_version(binary):
    """Ask the binary what it is. Never assert a version in prose - both CLIs moved inside a week."""
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20)
        return (p.stdout or p.stderr or "").strip().splitlines()[0][:80] or None
    except Exception:
        return None


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


def hermes_bin():
    return _resolve_bin("HERMES_BIN", "hermes.exe",
                        [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                      "hermes", "hermes-agent", "venv", "Scripts")])


# Toolsets handed to the Kimi channel. `web` and nothing else, on purpose: Hermes ships
# terminal, file, code_execution, browser and computer_use ENABLED by default, and a review
# channel's whole input is an untrusted brief. Granting those would turn a prompt into command
# execution on this machine. Verified against `hermes tools list` on 2026-08-01.
HERMES_TOOLSETS = "web"


def call_hermes(brief, marker, outfile, model=None, toolsets=None, system=None, timeout=2400):
    """
    Kimi K3 through the Hermes CLI. `-z/--oneshot` prints ONLY the final response text to
    stdout - no banner, no spinner, no tool previews - so stdout IS the review.

    Two traps, both measured 2026-08-01 rather than assumed:
      - the model id must be `moonshotai/kimi-k3`. Bare `kimi-k3` resolves to an unconfigured
        `kimi-coding` provider, and `kimi-k3-max` is not a valid id at all ("K3 Max" is a
        marketing variant name, not something the API accepts).
      - the prompt rides on argv, so it hits the same Windows command-line ceiling that bites
        agy. Same limit, same reason, so it reuses AGY_ARGV_LIMIT.
    """
    binary = hermes_bin()
    model = model or os.environ.get("HERMES_MODEL", "moonshotai/kimi-k3")
    payload = _with_system(brief, system)
    if len(payload) > AGY_ARGV_LIMIT:
        return {"channel": "kimi", "ok": False,
                "error": "prompt is %d chars; argv dies past ~%d on Windows"
                         % (len(payload), AGY_ARGV_LIMIT)}
    cmd = [binary, "-z", payload,
           "-m", model,
           "-t", toolsets or HERMES_TOOLSETS,
           # Do not inherit SOUL.md / AGENTS.md / Hermes memory: a reviewer that loads the
           # persona of the setup under review is not independent, and the injection makes the
           # run irreproducible. NEVER add --yolo here.
           "--ignore-rules"]
    try:
        p, secs = _run(cmd, timeout=timeout)
    except FileNotFoundError:
        return {"channel": "kimi", "ok": False, "error": "binary not found: " + binary}
    except subprocess.TimeoutExpired:
        return {"channel": "kimi", "ok": False, "error": "timed out after %ss" % timeout}

    text = p.stdout or ""
    if outfile and text.strip():
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    warn = []
    # Hermes exits 0 on a provider error and prints the error as the "answer", so the exit code
    # proves nothing. These two strings are the observed shapes of that failure.
    low = text.lower()
    if "no usable credentials" in low or "is not a valid model id" in low:
        warn.append("PROVIDER/MODEL ERROR returned as prose: " + text.strip()[:160])
    if marker and not text.strip().endswith(marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip():
        warn.append("EMPTY OUTPUT (exit=%d) %s" % (p.returncode, (p.stderr or "")[:200]))
    refusal = refusal_check(text, marker)
    if refusal:
        warn.append(refusal)
    return {"channel": "kimi", "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode,
            "model": model, "warnings": warn}


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
    for name, resolver in (("codex", codex_bin), ("agy", agy_bin), ("kimi", hermes_bin)):
        if name not in want:
            continue
        b = resolver()
        if not (os.path.isfile(b) or shutil.which(b)):
            yield "%s: binary not found (%s). Install it or exclude the channel." % (name, b)
    if "agy" in want:
        problem = agy_permission_preflight()
        if problem:
            yield "agy: " + problem
        # agy mangles non-ASCII in its own JSON output (observed: a directory with a Cyrillic
        # component came back as "...\\???????????\\..."). Nothing crashed, but a workspace it
        # cannot spell is not a workspace you want a 25-minute run to depend on.
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


# Appended only on the second attempt. Deliberately short and concrete: the first run already had
# the full source-discipline preset and ignored it, so repeating that language louder is not the
# move. This names the specific observed failure and asks for a countable behaviour instead.
AGY_ESCALATION = """

---

ADDITIONAL REQUIREMENT FOR THIS ATTEMPT.

A previous attempt at this exact brief cited sources without opening a single page, and several of
the URLs it produced do not exist. That is the failure to avoid here.

- **Open the pages.** A search-result snippet is not a source; issuing a query is not reading.
- **Cite only URLs you fetched in this run.** If you did not open it, do not list it. Reconstructing
  a plausible-looking documentation URL from memory is worse than having no citation at all,
  because it looks like evidence.
- If a page will not open, say so explicitly, name the URL, and state what you concluded without
  it. "I could not open this" is a completely acceptable answer and is far more useful than a
  citation that turns out to be a 404.
- Prefer fewer claims, each backed by a page you actually read, over broad coverage backed by
  recollection.
"""


def call_agy(brief, marker, workdir, outfile, model=None, effort="high", timeout="25m",
             system=None):
    """
    Run the channel, and re-run it ONCE if it cited sources without opening any.

    Measured 2026-07-31: agy issued 10 searches, opened **zero** pages, and cited 11 URLs of which
    3 return 404 - while getting the substance right. That combination is the dangerous one,
    because it survives casual review: correct conclusions with invented receipts.

    Why a mechanical retry rather than a stronger instruction: this channel has now refused to be
    steered by prose three separate times (a system prompt could not restrict its tool access, an
    agent persona could not, and `--mode` does nothing at all). Re-running is the lever that
    actually exists. It is bounded to exactly one extra attempt, it announces itself and its cost
    before spending, and it keeps BOTH transcripts on disk so the two can be compared.

    The retry is not automatically believed. If the second attempt still opens nothing, the FIRST
    answer is returned - it was produced under cleaner conditions, without an instruction nagging
    it about sources - and both failures are reported. Two ungrounded runs is a much stronger
    signal about the brief than one.
    """
    first = _agy_once(brief, marker, workdir, outfile, model, effort, timeout, system)

    if not first.get("zero_grounding"):
        return first

    log("  [agy] cited %d URL(s) and opened NONE. Re-running once with an explicit instruction "
        "to open sources (this costs a second agy call)." % first.get("n_cited", 0))

    retry_out = os.path.splitext(outfile)[0] + ".retry" + (os.path.splitext(outfile)[1] or ".md")
    second = _agy_once(brief + AGY_ESCALATION, marker,
                       os.path.join(workdir, "retry"), retry_out,
                       model, effort, timeout, system)

    if second.get("n_grounded"):
        second.setdefault("notes", []).append(
            "SECOND ATTEMPT. The first run cited %d URL(s) and opened none of them; it is kept at "
            "%s. This answer grounded %d of %d. Prefer this one, but the first is worth a glance - "
            "a model told to open sources sometimes narrows to what it can easily fetch."
            % (first.get("n_cited", 0), outfile, second.get("n_grounded", 0),
               second.get("n_cited", 0)))
        return second

    first.setdefault("warnings", []).append(
        "RE-RUN ALSO GROUNDED NOTHING (kept at %s). Two independent attempts cited sources and "
        "opened none, so this is not a fluke: treat every URL in this review as unverified and "
        "check them with `citecheck.py --answer <file> --resolve-urls` before repeating any of "
        "them." % retry_out)
    first["ok"] = False
    return first


def _agy_once(brief, marker, workdir, outfile, model=None, effort="high", timeout="25m",
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
            "perm_mode": ev["perm_mode"], "events": ndjson,
            # Machine-readable so the retry wrapper below does not have to re-parse prose.
            # "cited sources and opened none of them" is a different animal from partial
            # grounding: it is memory dressed as research, and it is worth one more attempt.
            "zero_grounding": bool(n_cited and not grounded),
            "n_cited": n_cited, "n_grounded": len(grounded)}


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
    ap.add_argument("--no-log", action="store_true",
                    help="do not write run.log / diagnostics.json into the output directory")
    # Default ON. A verification step you have to remember is one that runs least often when the
    # run was rushed - the same moment nobody re-reads the citations by hand either.
    ap.add_argument("--no-citecheck", action="store_true",
                    help="skip fetching the cited URLs at the end of the run. The check costs "
                         "nothing at any vendor and never changes the exit code; it is on by "
                         "default because it is the only citation check that works on Codex")
    a = ap.parse_args()

    # Start the file log before anything can fail, so a failure in validation is still recorded.
    started = time.time()
    if not a.no_log:
        try:
            os.makedirs(a.out, exist_ok=True)
            _LOG["path"] = os.path.join(a.out, "run.log")
            with open(_LOG["path"], "w", encoding="utf-8") as f:
                f.write("# model-orchestration run log - %s\n"
                        % datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        except OSError as exc:
            log("note: could not open a run log in %s (%s); continuing without one" % (a.out, exc))

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

    # Every channel is VERIFIED against the end marker, but until 2026-07-31 nothing ever asked
    # the model to emit one: the brief's author was silently expected to know. A brief written by
    # anyone who had not read the docs therefore came back PROBLEM on all three channels with a
    # perfectly good review inside - the worst kind of failure, because the harness looked broken
    # while the models had done their job. Measured on a two-line hand-written brief.
    #
    # Appended only when the brief does not already contain the marker, so an author who did
    # write the instruction gets no duplicate, and `--marker ""` still disables the check.
    if a.marker and a.marker not in brief:
        brief += ("\n\n---\nWhen the ENTIRE review is finished, end your reply with this exact "
                  "line and nothing after it:\n%s\n" % a.marker)
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
        want = {"spark": "http", "gemini": "agy", "hermes": "kimi"}.get
        want = {want(c) or c for c in (a.only or ["http", "codex", "agy", "kimi"])}
    if not want:
        log("every channel is disabled - nothing to run")
        return 2
    agy_cfg = (plan or {}).get("agy") or {}
    codex_cfg = (plan or {}).get("codex") or {}
    kimi_cfg = (plan or {}).get("kimi") or {}

    # Everything below is free and answers "will this round survive?" before it is launched.
    # It runs on --dry-run too: a preflight that skips the checks a real run needs is not a
    # preflight. Previously --dry-run verified the route, the brief path and the preset, then
    # said nothing about a missing key or a missing binary - so the first evidence that Codex
    # was not installed arrived after the other two channels had already been paid for.
    preflight = list(channel_preflight(want, a.out))
    for problem in preflight:
        log("  [preflight] " + problem)

    if a.dry_run:
        log("--dry-run: nothing was called")
        return 0

    os.makedirs(a.out, exist_ok=True)

    jobs = {}
    log("brief=%d chars  tier=%s  marker=%s" % (len(brief), a.tier, a.marker))
    # Threads, not asyncio: two of the three channels are blocking subprocesses, and on Windows
    # asyncio subprocess support depends on the event loop policy. Threads just work.
    with ThreadPoolExecutor(max_workers=4) as ex:
        if "kimi" in want:
            jobs["kimi"] = ex.submit(call_hermes, brief, a.marker,
                                     os.path.join(a.out, "KIMI.md"),
                                     model=kimi_cfg.get("model"),
                                     toolsets=kimi_cfg.get("toolsets"),
                                     system=system)
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

    # ---- citation audit ---------------------------------------------------------------------
    # Runs on every review, because the alternative - a separate command afterwards - is a check
    # that gets skipped exactly when the run was rushed. See citation_audit() for why existence
    # rather than grounding, and why it never touches the exit code.
    audit = citation_audit(results, enabled=not a.no_citecheck)
    log_citation_audit(audit)

    # ---- diagnostics ------------------------------------------------------------------------
    # Written on every run, success included: the most common support question is "it worked
    # yesterday", and answering it needs the working run's file to diff against.
    problems = []
    for name, r in results.items():
        for text in ([r.get("error")] if r.get("error") else []) + list(r.get("warnings", [])):
            cause, fix = diagnose(str(text))
            problems.append({"channel": name, "detail": str(text),
                             "likely_cause": cause, "suggested_fix": fix})
    # A missing binary is reported twice - once by the preflight, once by the channel that then
    # failed on it - and printing the same advice four times reads like four separate faults.
    # Keep the channel-level entry, which names the channel, and drop the preflight echo.
    seen = {(p["channel"], p["likely_cause"]) for p in problems}
    causes = {p["likely_cause"] for p in problems}
    for p in preflight:
        cause, fix = diagnose(p)
        if cause and cause not in causes:
            problems.append({"channel": "preflight", "detail": p,
                             "likely_cause": cause, "suggested_fix": fix})
            causes.add(cause)
    deduped, keys = [], set()
    for p in problems:
        k = (p["channel"], p["likely_cause"], p["detail"])
        if k not in keys:
            keys.add(k)
            deduped.append(p)
    problems = deduped

    diag = write_diagnostics(a.out, {
        "schema": "model-orchestration/diagnostics/1",
        "how_to_read_this":
            "A machine-readable account of one review run. It is scrubbed of secrets and "
            "personal identifiers by construction, so it is safe to paste into a chat or attach "
            "to a bug report. `problems` is the part to act on: each entry carries the raw "
            "detail plus a plain-language cause and a suggested fix. `channels` shows what each "
            "reviewer actually did - a channel with ok=false still often produced usable text, "
            "which is saved beside this file. `environment` shows what is installed; a missing "
            "key or a missing CLI is a normal, non-fatal condition and only disables that one "
            "channel.",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "ok_channels": ok_count,
        "total_channels": len(results),
        "invocation": {"tier": a.tier, "marker": a.marker, "system": a.system or "base-depth",
                       "only": a.only, "skip": a.skip, "route": a.route, "sets": a.sets,
                       "brief_chars": len(brief), "allow_pii": a.allow_pii},
        "environment": environment_report(want),
        "plan": plan,
        "preflight": preflight,
        "problems": problems,
        "citations":
            {"how_to_read_this":
                "Per channel: how many URLs the review cited and what happened when each was "
                "fetched. DEAD (404/410) is the one that matters - a citation to a page that "
                "does not exist was constructed, not read. BLOCKED and UNKNOWN mean the check "
                "could not be completed and are never evidence of fabrication. This checks "
                "EXISTENCE, which works on every channel; whether the model actually opened a "
                "page needs that channel's tool telemetry, which Codex does not report at all.",
             "results": audit},
        "channels": {n: {k: v for k, v in r.items() if k != "text"} for n, r in results.items()},
        "console": _LOG["lines"],
    }) if not a.no_log else None

    if problems:
        log("\n%d problem(s) recorded. Plain-language cause and fix for each:" % len(problems))
        for p in problems:
            if p["likely_cause"]:
                log("  [%s] %s" % (p["channel"], p["likely_cause"]))
                log("      -> %s" % p["suggested_fix"])
    if diag:
        log("\nDiagnostics: %s" % diag)
        log("If something went wrong, hand that file to an AI assistant and ask it to fix the "
            "cause - it is scrubbed of keys and personal data and contains everything needed.")

    log("Now report, per channel: accepted / rejected with proof / where they disagreed. "
        "The disagreement is the product.")
    return 0 if ok_count else 1


def _crash_handler():
    """
    Turn an unexpected exception into a diagnostics file instead of a bare traceback.

    A traceback on a terminal is lost the moment the window closes, and it is the one thing a
    user cannot usefully relay ("it says something about line 812"). Writing it to disk, scrubbed,
    means the next question - "send me the file" - has an answer that costs the user nothing.
    """
    try:
        return main()
    except KeyboardInterrupt:
        log("\ninterrupted by user")
        return 130
    except SystemExit:
        raise
    except BaseException as exc:                       # noqa: BLE001 - deliberate catch-all
        tb = traceback.format_exc()
        log("\nUNEXPECTED ERROR: %s: %s" % (type(exc).__name__, exc))
        out = "./reviews"
        for i, arg in enumerate(sys.argv):
            if arg == "--out" and i + 1 < len(sys.argv):
                out = sys.argv[i + 1]
        cause, fix = diagnose(str(exc) + "\n" + tb)
        path = write_diagnostics(out, {
            "schema": "model-orchestration/diagnostics/1",
            "how_to_read_this":
                "This run crashed. `traceback` is the Python failure; it is scrubbed of secrets "
                "and personal identifiers, so it is safe to paste into a chat or a bug report. "
                "Give it to an AI assistant with the request to diagnose and fix the cause.",
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "crashed": True,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": tb,
            "likely_cause": cause,
            "suggested_fix": fix,
            "argv": sys.argv[1:],
            "environment": environment_report(),
            "console": _LOG["lines"],
        })
        if path:
            log("Diagnostics written to %s - hand that file to an AI assistant and ask it to "
                "diagnose the cause. It contains no keys and no personal data." % path)
        else:
            log(tb)
        return 1


if __name__ == "__main__":
    sys.exit(_crash_handler())
