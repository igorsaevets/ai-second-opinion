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
import threading
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
# 🔴 ONE TIER SINCE R43 (2026-08-15). Igor: «мозги у всех режимов должны быть на максимум» - the
# panel decides who is in the room, and depth is no longer a choice at all. `strategic` and `deep`
# survive as ALIASES of `max` so that nothing anyone typed before breaks; the aliases live in the
# registry beside the tier, and routing.canon_tier resolves them.
_TIERS_FALLBACK = {
    "max": {"http_thinking_budget": 100000, "http_floor": 15000, "http_effort": "xhigh",
            "aliases": ["strategic", "deep", "максимум", "глубокий"]},
}


def canon_tier_name(name):
    """Resolve a tier word against the registry's tier aliases; unknown words fall through.

    Kept separate from routing.canon_tier because this module must survive an unreadable
    registry - the fallback above is a degraded run, and a degraded run beats an exception.
    """
    tiers = load_tiers()
    if not name or name in tiers:
        return name
    key = str(name).lower().replace("ё", "е")
    for tname, t in tiers.items():
        if tname.startswith("_") or not isinstance(t, dict):
            continue
        if key == tname.lower() or key in [str(a).lower() for a in t.get("aliases") or []]:
            return tname
    return name


def load_tiers():
    """Tier definitions from channels.json, falling back to the literal above."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
        with open(p, encoding="utf-8") as fh:
            t = json.load(fh).get("tiers")
        return t if isinstance(t, dict) and t else dict(_TIERS_FALLBACK)
    except Exception:                                     # noqa: BLE001
        return dict(_TIERS_FALLBACK)


def _tier_choices():
    """Every word `--tier` accepts: the tier names AND their aliases.

    🔴 argparse validates `choices` before any of this module's code runs, so leaving the aliases
    out would reject `--tier deep` with a bare usage error - in the other project's stored scripts,
    on the first run after the R43 collapse, with no explanation. The aliases are what make a
    breaking rename non-breaking.
    """
    out = []
    for tname, t in load_tiers().items():
        if tname.startswith("_") or not isinstance(t, dict):
            continue
        out.append(tname)
        out.extend(str(a) for a in t.get("aliases") or [])
    return sorted(set(out))


def _default_tier():
    """The default tier, declared in the registry so the flag and the file cannot disagree."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh).get("default_tier")
        if d:
            return d
    except Exception:                                     # noqa: BLE001
        pass
    return next(iter(_TIERS_FALLBACK))


def load_panels():
    """
    Panel names from channels.json, for `--panel`'s choices. NO literal fallback on purpose.

    The tier list has one, because a tier resolves to depth values the dispatcher can supply
    from its own defaults - a degraded run beats an exception. A PANEL resolves to a set of
    channels, and there is no honest default for that without the registry: guessing here would
    mean guessing who gets sent the document. An unreadable registry therefore yields an empty
    choice list, `--panel` becomes unusable, and the round runs the full set - which is exactly
    what happened before panels existed, so the degraded behaviour is the old behaviour rather
    than an invented one.
    """
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh).get("panels")
        return sorted(k for k in d if not k.startswith("_")) if isinstance(d, dict) else []
    except Exception:                                     # noqa: BLE001
        return []


def tier_config(tier):
    """The Spark-shaped view of a tier: the thinking block and the output-token floor.

    `budget_tokens` is sent but does NOT set depth on this endpoint - Meta documents it as
    "accepted for compatibility but not translated into an effort value", and depth is
    output_config.effort alone. It is kept because it does two real things: it decides whether
    the call streams (see STREAM_ABOVE_BUDGET), and the floor derived beside it is what
    _verify_http asserts the answer against.
    """
    tier = canon_tier_name(tier)
    t = (load_tiers().get(tier) or _TIERS_FALLBACK.get(tier)
         or next(iter(_TIERS_FALLBACK.values())))
    budget = int(t.get("http_thinking_budget") or 100000)
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


_LOG_LOCK = threading.Lock()


def log(msg):
    # Scrubbed at the single choke point rather than at each call site. Caught while testing the
    # crash handler: an exception whose MESSAGE contained a key printed it to the console in full,
    # because only the diagnostics FILE was being scrubbed. stdout is read by the orchestrating
    # model and archived to disk, so it is the same exfiltration surface as any other - and the
    # one call site that forgets is the one that matters. scrub() is defined further down; the
    # lookup happens at call time, so ordering is not a problem.
    # Locked (R74): every channel worker calls this from the ThreadPoolExecutor, and on Windows
    # two concurrent opens of the same file in append mode can raise a sharing-violation OSError
    # that the except below then SWALLOWS - a silently thinner run.log. Four R73 reviewers
    # converged on the class; the lock costs nothing and keeps print/list/file in one order.
    msg = scrub(str(msg))
    with _LOG_LOCK:
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
    out = {"content": [], "usage": {}, "stop_reason": None, "sse_error": None,
           "reasoning_chars": 0}
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
            elif d.get("type") == "thinking_delta":
                # 🔴 THE ONLY DEPTH METER THIS CHANNEL HAS, AND IT WAS BEING DROPPED ON THE FLOOR.
                # Until 2026-08-08 `thinking_delta` frames fell through this chain unread, so a
                # Spark result carried no reasoning figure of any kind - while the run summary and
                # report.py both print a `reasoning_tokens` column, which was therefore blank for
                # this channel forever, indistinguishable from a model that did not think. That
                # made every claim about depth on Spark - including the tier ladder's whole reason
                # for existing - structurally unverifiable. Characters, not tokens, because
                # characters are what the wire gives us; the unit is reported alongside the number
                # rather than quietly compared against somebody else's tokens.
                out["reasoning_chars"] += len(d.get("thinking", "") or "")
        elif t == "message_delta":
            out["stop_reason"] = ev.get("delta", {}).get("stop_reason") or out["stop_reason"]
            # output_tokens only becomes final here; message_start carries a placeholder
            out["usage"].update(ev.get("usage", {}) or {})
    out["content"] = blocks
    out["content"].append({"type": "text", "text": "".join(text_parts)})
    return out


def call_http_reviewer(brief, system, tier, marker, timeout=2400, model=None, name="spark",
                       effort=None, fallback_model=None, answer_cap=None):
    """Probe, then the real call, with retries that distinguish network blips from refusals.

    `model` comes from the routing plan, i.e. from channels.json. 🔴 Until 2026-08-06 it did not
    exist and the model was read straight out of MODEL_NAME with a hard-coded fallback, so
    `channels.spark.model` was decorative - the SAME defect codex carried until 2026-08-02, in
    the same file, discovered four days later because the earlier fix was applied to the one
    instance rather than to the class. MODEL_NAME still works, because a documented escape hatch
    that stops working is its own kind of trap, but it can no longer win in silence.

    `name` exists because two channels now share this function (spark 1.1 and spark 1.2 run in
    parallel) and a log line reading `[http]` twice describes neither of them.

    `fallback_model` (added R62): if the primary model fails for a reason OTHER than a content
    filter, retry the whole call with this model. Same endpoint, same key, same brief — only the
    model changes. The content-filter exclusion is deliberate: same payload + same filter = same
    result, and a wasted retry on the paid model costs $1.25/M instead of $0.10/M. The use case
    is spark12cont (Contributor, 100 RPM) falling back to muse-spark-1.2 (Standard, 3000 RPM)
    when the Contributor tier is rate-limited or returns a server error. Implemented as a
    recursive call with fallback_model=None to avoid duplicating the retry logic.
    """
    # _env_key, not an inline winreg copy: the inline form predated the helper, and R47 taught
    # the helper a divergence warning the copies would silently lack (a rotated key masked by a
    # stale process env) - two readers of one variable is the two-homes rot, key-shaped.
    key = _env_key("MODEL_API_KEY")
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
            res = _verify_http(data, marker, cfg["floor"], time.time() - t0, tier,
                               answer_cap=answer_cap)
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
                retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                if e.code == 429:
                    ra_msg = ("Retry-After: %s" % retry_after) if retry_after else "no Retry-After header"
                    log("  [%s] 429 RATE LIMITED (%s) - attempt %d/3"
                        % (name, ra_msg, attempt + 1))
                    last = "HTTP 429 rate limited (%s): %s" % (ra_msg, body[:200])
                wait = 2 ** attempt
                if retry_after:
                    try:
                        # Cap raised 30 -> 60 in R69 (backlog A3): a vendor answering
                        # Retry-After between 31 and 60 was being retried early against a
                        # window it had just declared, guaranteeing a second 429. 60 still
                        # bounds a pathological header (3600 cannot hold a 3-attempt loop
                        # for an hour); the retry itself is free - a 429 bills nothing.
                        wait = max(wait, min(int(retry_after), 60))
                    except (ValueError, TypeError):
                        pass
                time.sleep(wait)
                continue
            break
        except Exception as e:
            # gaierror / getaddrinfo failed / connection reset. A real run died here once and
            # took the whole orchestration with it, because urllib has no retry of its own.
            last = "transport: %r" % (e,)
            log("  [%s] %s - retry %d/3 in %ds" % (name, last, attempt + 1, 2 ** attempt))
            time.sleep(2 ** attempt)
    # --- fallback: if the primary model failed and a fallback is configured, retry with it.
    # Content filter is excluded: same payload to the same vendor's filter = same result.
    # Rate limits (429) and server errors (500+) ARE retried: the Contributor tier has 30×
    # lower RPM (100 vs 3000), so a 429 on Contributor can succeed on Standard.
    if fallback_model and fallback_model != model:
        is_filter = last and ("content management policy" in last or "CONTENT FILTER" in last)
        if not is_filter:
            log("  [%s] FALLBACK: primary %s failed (%s), retrying with %s (Standard tier)"
                % (name, model, (last or "unknown")[:80], fallback_model))
            # answer_cap rides along (R74, four R73 reviewers): without it the fallback's
            # below-floor note phrased a capped short answer as under-allocation.
            res = call_http_reviewer(brief, system, tier, marker, timeout=timeout,
                                     model=fallback_model, name=name, effort=effort,
                                     fallback_model=None, answer_cap=answer_cap)
            if res.get("ok"):
                res["fallback_used"] = True
                res["primary_model"] = model
                log("  [%s] FALLBACK SERVED: %s answered after primary %s failed"
                    % (name, fallback_model, model))
            else:
                log("  [%s] FALLBACK ALSO FAILED: %s -> %s"
                    % (name, fallback_model, res.get("error", "")[:80]))
                res["primary_error"] = last
            return res
        else:
            log("  [%s] fallback skipped: content filter blocks both tiers equally" % name)
    return {"channel": "http", "ok": False, "error": last}


def transport_damage(text):
    """Characters a vendor's transport DROPPED, caught by structural imbalance.

    🔴🔴 A VENDOR CAN CORRUPT THE ANSWER AND EVERY OTHER CHECK STILL PASSES. Measured
    2026-08-15 on the asylum round and reproduced 2026-08-16 with a three-arm probe: MiMo
    delivered a 26 KB review in which **35 '(' characters were simply absent**. The marker was
    on the last line, the byte count was healthy, the citation audit was happy, `ok` was True.
    Nothing looked wrong, and `INA § 208(a)(2)(D)` had become `INA § 208(a)2)(D)` - a legal
    citation silently rewritten into a different one.

    Proved to be the VENDOR, not us: the probe reconstructed the answer from the raw SSE frames
    in a program sharing no code with this file, and the gap was already there on the wire,
    sitting exactly on a frame boundary ('  INA 2' | '08(a)' | '2)(D)'). It appears only when
    MiMo's native search is active, and it does NOT appear with `stream: false` - which is why
    that channel is now unstreamed. This check stays anyway, because the next vendor to do it
    will not be the one we already fixed: the standing R43 lesson is that a repair applied to
    the channel that failed has not been applied to the CLASS.

    Deliberately a NOTE and never a warning. `ok` is `not warn` everywhere in this file, so a
    warning would throw away a 26 KB usable review over 35 characters, and an emoticon `:)`
    would fail a perfectly good one. Same judgement the citation check already makes: worth
    saying, not worth discarding.

    🔴🔴 CODE SPANS AND FENCES ARE EXCLUDED, AND THE REASON IS A FALSE POSITIVE THIS CHECK'S OWN
    VALIDATION COULD NOT HAVE FOUND. R44 measured a false-positive rate of ZERO and the number was
    honest, but it was measured on the 14 answers of one asylum round - none of which had any
    reason to WRITE a broken bracket. The first review that did tripped it immediately: on
    2026-08-16 the grokbuild channel returned 31 KB analysing this very corruption, quoted the
    damaged citation `208(a)2)(D)` eleven times, and was flagged with 7 orphan closers. Every one
    was inside backticks.

    So the detector could not tell «this answer is damaged» from «this answer is ABOUT damage» -
    and any review of the bug that produced it is guaranteed to trip it. A transport that drops a
    byte does not care whether the byte was inside a code fence, but an author quoting broken text
    always puts it in one, so stripping code regions removes almost all of the false positives and
    almost none of the true ones. Validated both ways rather than asserted: the real corrupt MiMo
    answer must still be flagged, and this review must come out clean. grokbuild found this on its
    first working run and proposed the sharper instrument (citation grammar, not raw balance);
    that is recorded in the round notes as the next step, not silently taken here.
    """
    if not text:
        return []
    # Fenced blocks first, then inline spans - the other order lets a stray backtick inside a
    # fence eat the rest of the document.
    stripped = re.sub(r"```.*?```", " ", text, flags=re.S)
    stripped = re.sub(r"`[^`\n]*`", " ", stripped)
    out = []
    for opener, closer, label in (("(", ")", "round brackets"),):
        depth, orphan_close = 0, 0
        for c in stripped:
            if c == opener:
                depth += 1
            elif c == closer:
                if depth == 0:
                    orphan_close += 1
                else:
                    depth -= 1
        if orphan_close or depth:
            bits = []
            if orphan_close:
                bits.append("%d '%s' missing" % (orphan_close, opener))
            if depth:
                bits.append("%d '%s' never closed" % (depth, closer))
            out.append(
                "TEXT INTEGRITY: %s in %s. The answer is structurally unbalanced, which on this "
                "harness has meant the vendor's transport dropped characters in flight rather "
                "than the model writing badly - and the shape it damages most often is a "
                "statutory citation like 208(a)(2)(D). Do NOT copy any quotation, section "
                "number or figure out of this answer without checking it against the source. "
                "A count of 1-2 can also just be an emoticon or an unpaired bracket in prose."
                % (" and ".join(bits), label))
    return out


def _verify_http(data, marker, floor, secs, tier, answer_cap=None):
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
    if marker and not _marker_on_last_line(text, marker):
        fail.append("END MARKER NOT ON LAST LINE - output is incomplete, do not parse it as a finished review")
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
        # something when the brief did not cap the length. Since R72 the harness itself caps the
        # FINAL answer by default (--answer-cap), so when a cap was in force the note must say
        # so - otherwise it reads as a defect on every obedient run, and a note that fires on
        # every run is an alarm nobody hears (this file's own crying-wolf lesson).
        if answer_cap:
            note.append("output_tokens=%d is below the %d floor for tier '%s' - but an "
                        "--answer-cap of %d chars (~%d tokens) was in force this run, so a "
                        "short FINAL answer is what was asked for. Judge by content; use "
                        "--answer-cap 0 if you want uncapped depth on paper too."
                        % (out_tok, floor, tier, answer_cap, answer_cap // CHARS_PER_TOKEN))
        else:
            note.append("output_tokens=%d is below the %d floor for tier '%s'. If the brief asked "
                        "for a short answer this is expected and fine. If it asked for a full "
                        "review, the model under-allocated: raise budget_tokens or split into "
                        "more sub-questions." % (out_tok, floor, tier))

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
            # Streamed frames are summed in _read_sse; the non-streaming shape carries whole
            # `thinking` blocks. Either way this is OUR count of the vendor's trace, in characters
            # - a unit, stated, rather than a number silently compared against someone's tokens.
            # 🔴 MEASURED 2026-08-08: on this endpoint it comes back None, and the reason is in
            # `block_types`: `redacted_thinking`. The trace is ENCRYPTED by the vendor, so no token
            # count and no character count can exist here, ever - not a gap in the harness, a
            # property of the transport. What still separates depth arms is `out_tokens`
            # (effort low 805..854 against xhigh 1245..2236 on the same probe), which is what
            # echocheck.py falls back to, labelled as the weaker instrument it is.
            "reasoning_chars": (data.get("reasoning_chars")
                                or sum(len(b.get("thinking") or "") for b in blocks
                                       if b.get("type") == "thinking")) or None,
            "reasoning_meter": {"path": "content[].thinking (streamed thinking_delta)",
                                "present": bool(data.get("reasoning_chars")),
                                "note": "redacted_thinking blocks carry no readable trace, so on "
                                        "this endpoint absence is the vendor's choice, not a "
                                        "parsing bug - check block_types"},
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


def _marker_on_last_line(text, marker):
    """R67: the end marker must be the ONLY content on the last non-empty line.

    Line-equality strictly dominates the R66 endswith(marker) rule on suffix-confusion:
    for marker `REVIEW-DONE-R66`, endswith accepts a stray `PREVIEW-DONE-R66` (false positive
    suffix match); line-equality rejects it. Every other case (trailing period, embedded
    text on the same line, empty output) behaves the same or stricter, and empirically all
    12 R66 panel channels placed the marker on its own line — no observed regression.

    Named by panel R66: spark12cont, agy36flash, goog36flash, grokbuild — the four strongest
    voices in the cheap panel for code review. Igor 2026-08-23 left the call at my discretion
    (r66→r67); this is the one-line hardening they converged on.
    """
    if not marker:
        return True
    lines = text.strip().splitlines()
    if not lines:
        return False
    last = lines[-1].strip()
    # R77 (#358): a marker wrapped in markdown emphasis is a COMPLETE answer wearing bold.
    # GROK420's whole R76 review was graded UNVERIFIED over `**KROKAI-R76-DONE-01**` - content
    # complete, adjudicated by reading. Emphasis characters are stripped from the ENDS only,
    # so the R67 suffix-confusion property survives: `PREVIEW-DONE-R66` still fails equality
    # against `REVIEW-DONE-R66` because stripping punctuation from its ends never removes the
    # leading P from a word.
    return last == marker or last.strip("*_~`") == marker


def _strip_marker_tail(text, marker):
    """Remove the end marker from a display/analysis copy - by the SAME rule that verified it.

    R68. _marker_on_last_line() tolerates whitespace around the marker line, and an
    endswith-only strip does two things that rule cannot: it leaves a whitespace-decorated
    marker line in the shown answer, and on a suffix-confused tail (`...PREVIEW-DONE-R66`
    against marker `REVIEW-DONE-R66`) it cuts len(marker) characters out of a WORD and
    mangles the last line of the display. One rule decides both questions now: if the marker
    owns the last line, that line comes off; anything else is left exactly as the model wrote
    it, so a defective tail stays visible to the reader instead of being half-eaten.
    """
    body = (text or "").strip()
    if not marker or not _marker_on_last_line(body, marker):
        return body
    return "\n".join(body.splitlines()[:-1]).rstrip()


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
    body = _strip_marker_tail(text, marker)
    if not body:
        if (text or "").strip():
            # Non-empty text that strips to nothing means the output was the marker and only
            # the marker. The per-channel EMPTY check cannot see this (the text is not empty),
            # and the marker check passes it (the marker IS on the last line) - so until R68 a
            # model that emitted the marker and nothing else was graded ok with no warnings.
            # Same family as the refusal-with-marker measured 2026-07-31: the marker proves
            # the model ended its turn, never that it did the work.
            return ("MARKER-ONLY ANSWER: the output is nothing but the end marker. The model "
                    "reached the end of its turn without doing any of the work.")
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


def posix_tools_dir():
    r"""A directory holding a POSIX `grep.exe` that a child process can actually spawn.

    🔴🔴 MEASURED 2026-08-19, and it cost two AOS review rounds (R52, R53) before anyone
    looked at the event stream instead of the summary. agy has a first-class `grep_search`
    tool that shells out to `grep`. Windows has no grep. Git for Windows ships one - in
    `Git\usr\bin`, which is the ONE Git directory that is not on the normal PATH:

        PowerShell PATH: ...\Git\cmd, ...\Git\mingw64\bin        -> `grep` UNRESOLVABLE
        Git Bash  PATH: the above + ...\Git\usr\bin (x3)         -> `grep` resolves

    So the channel worked or died depending on which shell the caller happened to launch
    Python from, with no message saying so. Both AOS failures carry the same sentence in
    their event stream - `exec: "grep": executable file not found in %PATH%` - and in R52
    that error came THREE TIMES before the model fell back to a raw PowerShell pipeline,
    which was then denied. Our warning reported the denial and sent the reader to
    patch_agy_permissions.py, which fixes nothing here.

    Two arms, one variable, from a PowerShell parent (the invalid first attempt ran from the
    Bash tool, whose PATH already had the directory - a control that cannot fail):

        no  usr\bin -> grep_search: `executable file not found in %PATH%`   (the AOS error)
        yes usr\bin -> grep_search: runs, 7 of 8 calls clean

    APPEND, never prepend: this directory also ships `find.exe` and `sort.exe`, which would
    shadow the Windows built-ins of those names for everything else the child runs. `grep`,
    `sed` and `awk` have no Windows namesake, so appending is enough to resolve them and
    changes nothing else. The child process only - nothing machine-wide is touched, because
    Igor's PATH is his.

    No absolute path is hard-coded into a shipped default: the env var wins, then the two
    standard install locations, then a per-user install. A literal would ship one machine's
    layout to the public kit that package.py generates from this file.
    """
    if os.name != "nt":
        return None
    cand = []
    env_dir = os.environ.get("POSIX_TOOLS_DIR")
    if env_dir:
        cand.append(env_dir)
    cand += [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "usr", "bin"),
             os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                          "Git", "usr", "bin"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "usr", "bin")]
    for d in cand:
        try:
            if d and os.path.isfile(os.path.join(d, "grep.exe")):
                return d
        except OSError:
            continue
    return None


def _posix_child_env():
    """The environment for any agentic CLI child, with `grep` made resolvable if it is not.

    🔴 NAMED FOR THE CLASS, NOT FOR THE CHANNEL THAT FOUND IT. This started as `_agy_env`,
    which is how a fix stays applied to one channel: `mimo25pro` repeated `grok420`'s R37 bug
    nine days later for exactly that reason. Three channels launch an agentic CLI as a child
    here - agy, kimi/hermes and grokcli - and all three inherit whatever PATH the parent shell
    happened to have.

    Only agy is MEASURED to need it: its `grep_search` tool is what failed, twice, in
    production. The other two get it by argument rather than by measurement, and the argument
    is that the change cannot subtract: appending leaves every name that already resolved
    resolving to the same binary, and the only difference is that a name which previously
    errored now works. For an agentic CLI that is the desired direction, and the alternative -
    waiting for each channel to lose a round of its own first - is the mistake this project
    keeps naming.

    Returns None when nothing needs changing: either grep already resolves (a Git-Bash parent)
    or no POSIX toolset was found, in which case `agy_grep_warning()` says so rather than
    letting the run die with an unexplained tool error.
    """
    if os.name != "nt":
        return None
    if shutil.which("grep"):
        return None
    d = posix_tools_dir()
    if not d:
        return None
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + os.pathsep + d
    return env


def agy_grep_warning():
    """The message for the case the fix above cannot repair: no POSIX grep anywhere."""
    if os.name != "nt" or shutil.which("grep") or posix_tools_dir():
        return None
    return ("`grep` is not resolvable and no Git-for-Windows toolset was found, so agy's "
            "grep_search tool will fail on every call. That is survivable on its own, but "
            "the model's usual fallback is a raw shell command, which IS denied and takes "
            "the whole run with it. Install Git for Windows, or set POSIX_TOOLS_DIR to a "
            "directory containing grep.exe.")


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
#   PII     - identifiers about a person. SENT by default since 2026-08-16 (Igor inverted the
#             policy: only keys/passwords are critical); --warn-pii lists them, --strict-pii
#             refuses. `--allow-pii` is a retired no-op kept for old scripts. This comment said
#             «blocked by default» for two weeks after the code stopped doing that - the exact
#             prose-is-a-claim rot the public README carried in R71 (grokbuild caught it, R73).
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
    # xAI keys have a stable `xai-` prefix; a whole vendor this gate sends to had no pattern
    # (grokbuild, R73). Prefix + 20 alnum is unambiguous - `xai-tools` and prose stay silent.
    ("XAI_KEY",           re.compile(r"\bxai-[A-Za-z0-9]{20,}")),
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
# 🔴 EVERY COMMAND-LINE CHANNEL, IN ONE PLACE, BECAUSE THREE PLACES LISTED THEM BY HAND AND ALL
# THREE WENT STALE THE DAY A FOURTH WAS ADDED. Measured 2026-08-16: `grokcli` shipped and then
# (a) the missing-binary advice below still named only three env vars, so the one channel that
# failed was the one channel not offered a fix; (b) `doctor.py` checked two literal binaries and
# reported nothing about grok OR hermes; (c) the preflight had its own hand-written tuple.
# The pairing with CLI_RESOLVERS below is asserted by selftest, so adding a fifth CLI cannot
# leave half of this wired.
CLI_BINARIES = (
    ("codex", "CODEX_BIN", "codex"),
    ("agy", "AGY_BIN", "agy"),
    ("hermes", "HERMES_BIN", "hermes.exe"),
    ("grokcli", "GROK_BIN", "grok.exe"),
    ("opencode", "OPENCODE_BIN", "opencode"),
)
CLI_ENV_VARS = " / ".join(env for _, env, _ in CLI_BINARIES)


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
     "(" + CLI_ENV_VARS + "). `python doctor.py` reports which ones were found."),
    # Two spellings of one failure: the agy path says "END MARKER ABSENT", the HTTP path says
    # "END MARKER NOT ON LAST LINE". Until 2026-08-14 this pattern knew only the first, so the
    # second was recorded with likely_cause=null and the console's "cause and fix for each"
    # block printed a header followed by nothing. Same class as the dispatch-on-literal bugs:
    # a diagnosis keyed on one channel's exact wording goes silently blind on the next channel.
    ("END MARKER (ABSENT|NOT ON LAST LINE)",
     "The model stopped before finishing, or never emitted the agreed end-of-review marker.",
     "The harness appends the marker instruction automatically when the brief does not contain "
     "it. If this still fires, the model most likely hit a length or time limit - re-run that "
     "channel alone. 🔴 If an OUTPUT BUDGET EXHAUSTED warning appears beside this one, that is "
     "the real cause and it names the fix; do NOT reach for the tier, which has had exactly one "
     "level since 2026-08-15 and cannot be lowered."),
    # Placed ABOVE the provider-error entry so it wins the match on a record that has both: the
    # budget cause is specific and actionable, the provider one is generic. R43, after mimo25pro
    # spent 766 seconds and 60 002 reasoning tokens against a 60 000 cap and the round reported
    # «the provider ... gave no reason» while holding the reason in the next field.
    ("OUTPUT BUDGET EXHAUSTED",
     "The channel did not fail - it was CUT OFF. Its reasoning trace consumed the whole "
     "max_tokens allowance, which on these protocols covers reasoning and answer together, so "
     "the answer never began.",
     "Raise that channel's `max_tokens` in channels.json toward the vendor's declared "
     "max_completion_tokens (the warning prints both numbers). If it is already at the ceiling, "
     "the brief is too hard for that channel and the only remedy is to split it. This is NOT a "
     "timeout and NOT a tier problem."),
    # R47, from the AOS round-40 nemotron review that ended mid-heading with only the generic
    # marker warning: the provider's own stop reason was on the final chunk and unread.
    ("PROVIDER LENGTH CEILING CUT THE ANSWER|finish_reason=length",
     "The provider's own per-response completion cap is smaller than the max_tokens this "
     "channel asked for, so generation stopped mid-sentence at THEIR ceiling. Free-tier "
     "endpoints carry the smallest caps; the end marker is the first casualty because it is "
     "the last thing the model would have written.",
     "Raising max_tokens in channels.json cannot help - the binding ceiling is the provider's. "
     "Shorten the brief, ask for a tighter answer, or move the channel to a paid endpoint of "
     "the same model, which usually carries the larger cap."),
    ("PROVIDER ERROR MID-STREAM|Upstream error|Internal server error",
     "The vendor's own upstream failed while streaming - their infrastructure, not the brief "
     "and not this harness.",
     "Re-run just that channel with --only <channel>. If it repeats, that provider is degraded "
     "right now: drop the channel for this round rather than retrying into the same outage."),
    ("PLAN INSTEAD OF REVIEW",
     "The CLI's agent returned its implementation-plan artifact and stopped, as if a human "
     "were present to approve the plan.",
     "The harness re-runs this once with a do-the-work instruction automatically. If it still "
     "happens, the brief is probably too large or too task-shaped for this channel's agent - "
     "shrink it or send it to a non-CLI channel."),
    # Placed ABOVE the generic REFUSAL entry so it wins on records that carry both words.
    # 🔴 R59 (2026-08-20), from AOS Round-55: goog37flash returned `HTTP 400: {"error":{"message":
    # "Request blocked due to copyright/recitation content..."}}` on a legal brief that
    # goog36flash and orgemini37flash both answered cleanly. Google's consumer Gemini API has a
    # recitation filter that cannot be tuned via safety_settings - it is a built-in copyright
    # protection, and https://ai.google.dev/gemini-api/docs/safety-settings does not mention it
    # at all. 3.7-flash trips it more aggressively than 3.6-flash on identical statute text.
    # This is NOT a framing problem and --system legal-research does not fix it. The right
    # response is to route the round to a channel whose transport does not trip this filter.
    ("copyright/recitation|RECITATION",
     "The Google recitation/copyright filter blocked this response. It fires when a Gemini "
     "model would produce a long verbatim excerpt of text resembling something in its training "
     "corpus - published federal statute and regulation are exactly this shape, so it can kill a "
     "legal review on this transport. The filter is hard-coded on the consumer Gemini API and "
     "cannot be disabled via safety_settings.",
     "Route this round to another Google transport of the same model family - the OpenRouter "
     "Vertex-pinned orgemini37flash and the direct goog36flash have both handled identical "
     "briefs without tripping this filter. Do NOT rewrite the brief to paraphrase quotations - "
     "verbatim quotation of statute is what makes a legal review verifiable, and shortening it "
     "just to placate the filter defeats the point. Skip goog37flash for legal briefs with "
     "--skip goog37flash if it repeats."),
    ("REFUSAL",
     "The model declined the task on policy grounds rather than failing technically.",
     "This is almost always a framing problem, not a subject ban. Rewrite the brief as "
     "verification of sources rather than as strategy or advice, and pass "
     "--system legal-research for regulated subjects."),
    ("status.*401|unauthor|invalid.*api.?key|authentication",
     "The API key was rejected by the vendor.",
     "The key is present but not valid - it was revoked, rotated, or belongs to a different "
     "account. Issue a new one and replace it in the environment."),
    # The bare word "quota" used to be in this pattern, and it matched the codex PREFLIGHT
    # INFO line ("subscription quota, from codex's own cached snapshot... under half used") -
    # so every round that RAN codex recorded a phantom rate-limit problem. Measured live
    # 2026-08-14. The pattern now requires exhaustion language, which the real events carry:
    # HTTP 429, "WEEKLY LIMIT EXHAUSTED" (codex), "Key limit exceeded" (OpenRouter).
    #
    # 🔴 THE MORE SPECIFIC ENTRY GOES FIRST AND THE ORDER IS LOAD-BEARING: diagnose() takes the
    # first pattern that matches, and «Key limit exceeded» also matches the generic "limit
    # exceeded" below. Written as one generic row, the advice it produced was actively wrong for
    # this case - it said "wait, or route the work to another channel", and on 2026-08-15 the AOS
    # round-35 report shows four channels taking that advice in parallel while every one of them
    # was on the SAME key. 🔴 An earlier version of the comment above called this «(OpenRouter free
    # tier)», which is the misreading this entry exists to kill: the cap is on the KEY's monthly
    # budget, so it kills the free channel too. ornemotron3ultra costs $0.007 a round and died
    # with the same 403 as the paid ones.
    ("Key limit exceeded|monthly limit",
     "Your OpenRouter KEY has hit the monthly budget cap set on it - this is your own limit, "
     "not the model's rate limit and not a vendor outage. It applies to the key, so EVERY "
     "OpenRouter channel in the round fails together, including free ones: a free model still "
     "spends the key's allowance, and its price is the model, not the request.",
     "Routing to another OpenRouter channel cannot help - they share the key. Either raise the "
     "monthly limit on that key in the OpenRouter dashboard (Settings -> Keys), wait for the "
     "month to roll over, or run the round on the channels that do not use this key at all "
     "(spark*, agy*, goog*, mimo25pro, grok420, codex). If the cap arrived sooner than expected, "
     "look at the round total printed above and at which channel spent it - as of 2026-08-15 the "
     "harness sums per-round cost across tool rounds and reports it even for channels that "
     "failed, which it did not do when this was first seen."),
    ("status.*429|\\b429\\b|rate.?limited?\\b|LIMIT EXHAUSTED|limit exceeded|quota exceeded",
     "A usage or rate limit was hit on that vendor.",
     "Wait, or route the work to another channel with --route/--skip. Do NOT switch that "
     "channel to a metered pay-per-token key to get around a subscription limit unless you "
     "have decided that cost is acceptable."),
    # 🔴 KEYED ON THE EXCEPTION CLASS NAME, NEVER ON THE OS MESSAGE, and that is the whole point of
    # this entry rather than a stylistic preference. Measured in AOS round 33 (2026-08-14): both
    # Google channels died in the same round, one with
    # `RemoteDisconnected('Remote end closed connection without response')` and the other with
    # `ConnectionResetError(10054, 'Удаленный хост принудительно разорвал существующее
    # подключение', ...)` - the second message is in RUSSIAN because Windows localises WinSock
    # strings to the system language. A pattern written against the English wording would have
    # matched one machine and silently missed the same failure on another, which is the quiet-and-
    # dead shape this project keeps finding. Class names and errno are not translated.
    # Both rounds printed `likely_cause: null` and an empty "cause and fix" block.
    ("RemoteDisconnected|ConnectionReset|ConnectionAborted|BrokenPipe|IncompleteRead|10054",
     "The network connection to the vendor was dropped mid-request. This is a transport failure - "
     "nothing was wrong with the brief, the key or the model, and no answer was produced. It is "
     "most common on endpoints that think for a long time before emitting anything, because the "
     "connection sits idle and something between here and the vendor closes it.",
     "Re-run just that channel with --only <channel>. If it repeats on the same channel while "
     "others in the round succeed, the endpoint is unhealthy right now rather than the network: "
     "drop it for this round. Nothing was billed for a request that returned no tokens, so a "
     "single re-run is safe here - unlike a rate limit or a spend stop, which must not be "
     "auto-retried."),
    # 🔴 R47: this entry used to cover BOTH empty-output shapes with the budget-exhausted cause,
    # and the AOS round-40 failure proved it wrong out loud: 6 715 of 131 072 output tokens used -
    # 95% of the budget UNSPENT - and the printed cause said "the output budget was gone". The
    # warning text now splits on the budget share, so each shape meets its own cause here.
    ("VENDOR ENDED THE TURN MID-LOOP",
     "The vendor's server-side agentic loop terminated after a tool call without producing a "
     "message item - status says completed, the budget is largely unspent, and the reasoning "
     "and searches were billed. This is a vendor-side stochastic termination, the 4th measured "
     "instance of one class across three vendors (kimik3 2026-08-03, grok420 R29+R40, "
     "orgemini37flash R38). No request parameter controls it.",
     "Re-run just that channel with --only <channel> - never automatically, the failed call was "
     "billed. If it repeats on large briefs, run the same model through a transport where the "
     "HARNESS drives the tool loop client-side (for grok-4.20 that is orgrok420 via OpenRouter): "
     "the mid-loop termination lives in the vendor's server-side runtime, not in the model."),
    ("CITATIONS: only 0 of",
     "The model cited sources and opened none of them - memory dressed as research.",
     "The harness already re-ran this channel once with a source-discipline escalation. Treat "
     "every URL in the answer as unverified: run citecheck.py --answer <file> --resolve-urls "
     "before repeating any of them."),
    ("timed out|timeout",
     "The channel took longer than its allotted time.",
     "Raise that channel's timeout in the tier block, or split the brief into smaller questions. "
     "Every channel now runs at its vendor's maximum depth, so a large brief can legitimately "
     "occupy many minutes and there is no shallower setting to fall back to."),
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


def _atomic_write(path, text):
    """
    Write `text` to `path` so that a reader never sees a half-written file.

    🔴 `open(path, "w")` TRUNCATES BEFORE IT WRITES. Between that truncation and the last byte
    the file on disk is short, and for JSON "short" means unparseable. That window used to be
    harmless because the file was written exactly once, at the very end; since the record is now
    written TWICE (see the diagnostics call sites) the window opens over a file that already had
    good content in it. Nine of the twelve reviewers of that change raised this independently,
    and they were right: the previous design could lose a round's record by crashing, and the new
    one could additionally lose it by being interrupted while improving it.

    Write to a sibling temp file, then `os.replace`, which is atomic on NTFS and on POSIX for
    same-directory renames. A reader either gets the old complete file or the new complete file.
    Same directory on purpose: `os.replace` across volumes is not atomic and can raise.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())     # rename is atomic; the CONTENT still has to be on the platter
    os.replace(tmp, path)


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
        _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))
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
        _atomic_write(os.path.join(outdir, "REPORT.md"), _report.render(payload))
    except Exception as exc:
        log("  note: REPORT.md could not be rendered (%r). diagnostics.json is unaffected; "
            "run report.py against it by hand." % (exc,))
    return path


# Bytes per token for the read-cost estimate. The number exists to answer "does this fit in what
# is left of my context", and pulling in a real tokenizer would add a dependency to a harness whose
# selling point is that it has none.
#
# 🔴 MEASURED 2026-08-19, BECAUSE THREE R48 REVIEWERS ATTACKED THIS CONSTANT AND ALL THREE WERE
# WRONG - IN OPPOSITE DIRECTIONS. Two called `bytes // 4` a "critical defect" that underestimates
# Cyrillic by 2-3x and demanded `bytes // 2`; a third said Windows CRLF inflates it. Tokenised 17
# real files with `tiktoken` `o200k_base` (the panel's own twelve reviews, plus this project's
# Russian NOW.md, MEMORY.md and CLAUDE.md): the ratio runs 3.48-4.64 B/token END TO END, and the
# most Cyrillic-dense files measured - NOW.md at 24.3% non-ASCII bytes, CLAUDE.md at 25.3% - came
# in at 4.11 and 4.25, i.e. indistinguishable from the English reviews. Modern BPE vocabularies
# carry Russian subwords; the "2 bytes per character so half the tokens" reasoning describes a
# tokenizer nobody here uses. `bytes // 4` lands within -13%..+16% on every file measured.
#
# Taking that advice would have DOUBLED the estimate and told the caller to defer a panel that
# fits. The number stays, and what changes is the label: a reader now gets the measured band
# instead of the word "estimate", so it can be trusted to the accuracy it actually has.
CHARS_PER_TOKEN = 4
EST_BAND = "measured -13%..+16% against tiktoken o200k_base on 17 real files, EN and RU alike"


def write_handoff(outdir, results, marker=None, brief=None, panel=None, started=None,
                  answer_cap=None):
    """
    Write HANDOFF.md: what is on disk, what it costs to read, and the prompt that resumes it.

    🔴 THE LISTING IS A `listdir`; THE MANIFEST IS A JOIN. Both halves are load-bearing.

    Round 46 lost the three largest reviews of a $3.97 panel - 317 KB - because the session
    assembled its reading list by hand from the run log, and the log had no line for the two
    Spark answers (their `bytes` field was never set; see the write site). The round reported
    "18 launches, 17 answers" and both figures were wrong. A list built from `results` would have
    inherited that error exactly, because `results` is the belief that was already wrong. So
    WHICH FILES EXIST comes from the directory, always, and nothing may override it.

    🔴 But the R48 description of this function said "never from the run's own records", and FOUR
    of the twelve reviewers refuted it from that sentence's own second half - correctly. You
    cannot report "files on disk that no channel record claims" without the records, and a
    channel that ran and wrote nothing leaves NO trace in a directory listing at all: it is
    visible only in `results`. The code always did the join; only the prose claimed otherwise,
    and a docstring that overstates its own guarantee is the same defect as a table heading that
    overstates its rows. Stated exactly: **the filesystem is authoritative for what exists, the
    records are authoritative for what was attempted, and the two disagreements between them are
    the manifest's most valuable output.**

    🔴 IT IS ALSO THE ANSWER TO "SHOULD I READ THIS NOW?"

    Igor, 2026-08-19: «пока ИИ запускает оркестрацию, у него уже окно часто заполнено на 350K+.
    А чтобы читать и анализировать ответ, лучше чтобы окно было почти пустое.» He is describing
    a real and measured failure: the session that ORDERS a panel has spent its context getting to
    the point of ordering one, and the panel then returns more text than the session has room to
    think about. Round 46 is what that looks like from the outside - not a refusal, just three
    files quietly never opened.

    This function does NOT decide. It cannot: nothing here can see how full the caller's context
    is, and a harness that guessed would be asserting a fact it has no instrument for - the class
    this project keeps paying for. It states the price in tokens and writes the resume prompt, so
    deferring costs one paste instead of an hour of reconstruction. Whether to defer belongs to
    the caller, and the rule for it lives in SKILL.md where a human can argue with it.

    Never raises: a handoff writer that can break the round it is describing is worse than none.
    """
    try:
        os.makedirs(outdir, exist_ok=True)
        skip = {"REPORT.md", "HANDOFF.md", "BRIEF.md", "PROMPT.md", "agent.md"}
        # Map each answer file back to its channel record - for the reading ORDER (Igor, R72:
        # «начинай читать ответы от умных моделей») and for the answer-cap meter. A file no
        # record claims sorts LAST (order 9): it is already flagged as an orphan below, and an
        # unknown voice is the wrong place to spend the freshest context.
        by_file = {}
        for _cn, _r in (results or {}).items():
            _af = (_r.get("answer_file") or "").lower() if isinstance(_r, dict) else ""
            if _af:
                by_file[_af] = (_cn, _r.get("read_order"), _r.get("reading_note"),
                                bool(_r.get("must_read")))
        files = []
        for fn in sorted(os.listdir(outdir)):
            if not fn.endswith(".md") or fn in skip:
                continue
            p = os.path.join(outdir, fn)
            if not os.path.isfile(p):
                continue
            size = os.path.getsize(p)
            # 🔴 A DIRECTORY IS A NAMESPACE, NOT A ROUND. `--out` is routinely reused, and every
            # answer file from the previous panel is still sitting there with a plausible name.
            # Listing it as this round's output inflates the read cost and, far worse, invites
            # the fresh context to adjudicate last week's reviews as if they were these. Two R48
            # reviewers raised it independently; both proposed comparing against the run's start
            # time, which is exactly the argument this function was ALREADY BEING PASSED and had
            # never read - see `started` in the signature. The parameter is now consulted.
            # Flagged rather than hidden: a file this run did not write is still a file someone
            # may want, and silently dropping it would repeat round 46 from the other direction.
            stale = bool(started) and os.path.getmtime(p) < started - 5
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                body = ""
            tail = [ln for ln in body.splitlines() if ln.strip()]
            cname, r_order, r_note, r_must = by_file.get(fn.lower(), (None, None, None, False))
            files.append({
                "file": fn,
                "channel": cname,
                "note": r_note,
                "read_order": r_order if r_order in (1, 2, 3) else 9,
                # ★ Idea 3 (Igor, 2026-09-01): the one answer the ordering session must read
                # ITSELF whatever the context pressure - `must_read` in channels.json.
                "must_read": bool(r_must),
                # chars, not bytes, for the cap compare: the cap was asked in CHARACTERS and a
                # Cyrillic answer is ~2 bytes/char in UTF-8, so a byte compare would flag it at
                # half the promised length.
                "chars": len(body),
                "over_cap": bool(answer_cap) and len(body) > answer_cap,
                "truncated_note": "TRUNCATED-BY-LIMIT" in body,
                "bytes": size,
                "stale": stale,
                "est_tokens": size // CHARS_PER_TOKEN,
                # The marker check is repeated here ON THE FILE rather than trusted from the
                # record, for the same reason the listing is: this is the artifact a reader will
                # actually open, and the record describes what the harness believed it wrote.
                "ends_with_marker": bool(marker) and bool(tail) and tail[-1].strip() == marker,
                "harness_banner": body.startswith("> 🔴 **HARNESS WARNING"),
            })
        fresh = [f for f in files if not f["stale"]]
        # Smartest voices first (R72). Sorting the MANIFEST is the mechanism: the reader walks
        # the table top-down, so the order the table is printed in IS the reading order.
        # Within a tier the ★ mandatory-read row leads (R74, Idea 3) - it is the row the
        # reader must reach even if nothing after it is read.
        fresh.sort(key=lambda f: (f["read_order"], not f["must_read"], f["file"]))
        stale_files = [f for f in files if f["stale"]]
        total = sum(f["bytes"] for f in fresh)
        est = total // CHARS_PER_TOKEN

        # Channels that ran and left NO file. Named, because "absent from the manifest" is the
        # one thing a manifest cannot say about itself. Computed over the FRESH files only: a
        # leftover from a previous panel is unclaimed by construction, and reporting it twice -
        # once as stale, once as an orphan - would make the loud line the routine one.
        claimed = {(r.get("answer_file") or "").lower() for r in results.values()}
        on_disk = {f["file"].lower() for f in fresh}
        orphans = sorted(on_disk - claimed - {""})
        missing = sorted(n for n, r in results.items()
                         if not r.get("answer_file") and r.get("seconds") is not None)

        L = ["# Handoff — what this round produced, and what reading it costs", "",
             "**Which files exist** was read off the filesystem — a directory listing of `%s` "
             "taken after the run, never a projection of what the harness believed it wrote. "
             "Round 46 lost 317 KB of reviews to a hand-built list and this file exists so that "
             "cannot recur. **What was attempted** comes from the run's own records, because a "
             "channel that ran and wrote nothing leaves no trace in a directory. The two "
             "disagreements between those halves are flagged below and are the most useful thing "
             "here." % os.path.abspath(outdir), "",
             "| read | channel | answer file | bytes | ~tokens to read | ends with the end marker |",
             "|---:|---|---|---:|---:|---|"]
        for f in fresh:
            L.append("| %s | %s | `%s` | %s | ~%s | %s |"
                     % (("%s★" % f["read_order"] if f["must_read"] else f["read_order"])
                        if f["read_order"] != 9 else ("★" if f["must_read"] else "?"),
                        f["channel"] or "-",
                        f["file"], f"{f['bytes']:,}".replace(",", " "),
                        f"{f['est_tokens']:,}".replace(",", " "),
                        "yes" if f["ends_with_marker"] else
                        ("no — INCOMPLETE, do not parse as a finished review" if marker else "-")))
        # Standing per-channel reading advice from the registry, printed UNDER the table it
        # applies to. The file still appears in the table above - the manifest lists what is
        # on disk, always - but the reader is told, at the moment of choosing, not to open it.
        for f in fresh:
            if f.get("note"):
                L.append("")
                L.append("⚠ `%s` (%s): %s" % (f["file"], f["channel"] or "-", f["note"]))
        L += ["",
              "**%d answer file(s), %s bytes, ~%s tokens to read all of them** (%d bytes/token, "
              "%s)."
              % (len(fresh), f"{total:,}".replace(",", " "),
                 f"{est:,}".replace(",", " "), CHARS_PER_TOKEN, EST_BAND), ""]
        if stale_files:
            L += ["⚠ **Older than this run and NOT counted above** — almost certainly a previous "
                  "panel written into the same `--out` folder: %s. They are still on disk; if "
                  "you want them, open them deliberately, and do not adjudicate them as this "
                  "round's answers."
                  % ", ".join("`%s` (%s bytes)" % (f["file"], f"{f['bytes']:,}".replace(",", " "))
                              for f in stale_files), ""]
        if orphans:
            L += ["🔴 **On disk but claimed by no channel record:** %s. Something wrote into this "
                  "folder that this run did not produce, or a record lost its file name."
                  % ", ".join("`%s`" % o for o in orphans), ""]
        if missing:
            L += ["🔴 **Ran but left no answer file:** %s. Those channels cost time and possibly "
                  "money and produced nothing to read; their cause is in `diagnostics.json` → "
                  "`problems`." % ", ".join("`%s`" % m for m in missing), ""]

        # R72 (Igor, 2026-08-31): the reading protocol, IN the artifact the reader opens first.
        # The strongest placement this project has measured is the instruction at the decision
        # point - a rule in a memory file fires by topic, a line in the artifact fires when the
        # artifact is read.
        L += ["## Reading order — smartest voices first, and read them YOURSELF",
              "",
              "The table above is sorted by `read`: **1** = frontier reasoners — open these "
              "first; **2** = mid tier; **3** = flash-class and measured-weak voices — open "
              "them only if context room remains (the tiers are `read_order` in "
              "`channels.json`; re-tier there as models rotate). Read the answers yourself "
              "rather than delegating to sub-agents: a sub-agent starts with none of this "
              "session's context and returns a summary, and the value of a panel is the "
              "disagreement between full answers.", ""]
        if any(f["must_read"] for f in fresh):
            L += ["**★ is the mandatory minimum.** Whatever the context pressure, the ★ "
                  "row(s) are read by the ordering session ITSELF — never delegated, never "
                  "dropped (Igor, 2026-09-01: in the standard panel read the smartest voice "
                  "yourself; in the cheap panel that is Spark). The flag is `must_read` in "
                  "`channels.json`; move it there as models rotate.", ""]
        if answer_cap:
            over = [f for f in fresh if f["over_cap"]]
            trunc = [f for f in fresh if f["truncated_note"]]
            L += ["Answer cap this run: **%d chars** (asked in the brief; advisory, never "
                  "enforced by max_tokens). %s"
                  % (answer_cap,
                     "All fresh answers are within it." if not over else
                     "Over it: %s." % ", ".join(
                         "`%s` (%s chars)" % (f["file"], f"{f['chars']:,}".replace(",", " "))
                         for f in over)), ""]
            if trunc:
                L += ["🔴 **Declared dropping material findings to fit (TRUNCATED-BY-LIMIT):** "
                      "%s. Consider re-running just those channels with `--answer-cap 0`."
                      % ", ".join("`%s`" % f["file"] for f in trunc), ""]

        L += ["## Reading this in a fresh context",
              "",
              "If the session that ordered this round is already large, reading %s tokens of "
              "reviews into it is how a panel gets paid for and then not read. The harness does "
              "not know how full that context is and does not guess. If it is tight: run "
              "`/compact`, then send the block below — it is self-contained."
              % f"~{est:,}".replace(",", " "),
              "",
              "```",
              # The PANEL belongs in the resume prompt, because the fresh context has no other
              # way to know it. Round 48's own complaint was «сказал использовать дешевую панель,
              # а он почему-то использовал codex» - a reader who cannot see which room was booked
              # cannot tell a missing voice from an excluded one. `panel` was already being passed
              # to this function and, like `started`, was never read.
              ("Прочитай ответы панели `%s` в %s" % (panel, os.path.abspath(outdir))) if panel
              else ("Прочитай ответы панели в %s" % os.path.abspath(outdir)),
              "Начни с HANDOFF.md (манифест с диска) и REPORT.md (телеметрия: кто отвечал, "
              "чем, сколько ссылок живых).",
              "Потом прочитай КАЖДЫЙ файл из манифеста — их %d, ~%s токенов. Не собирай список "
              "руками: он в HANDOFF.md." % (len(fresh), f"{est:,}".replace(",", " ")),
              "Порядок — колонка `read` в таблице HANDOFF.md: сначала 1 (умные), потом 2; "
              "тройки (flash-класс) — только если остался бюджет контекста. Читай сам, "
              "суб-агентам чтение не отдавай.",
              "Строку со ★ прочитай САМ в любом случае — это обязательный минимум, даже "
              "если больше ни на что не хватит контекста.",
              "По каждой находке: принял / отклонил с доказательством / в бэклог. "
              "Совпадения между каналами считай отдельно от одиночных мнений.",
              ("Маркер конца ответа: %s. Файл без него на последней строке — неполный."
               % marker) if marker else "",
              ("Бриф, который им отправляли: %s" % os.path.abspath(brief)) if brief else "",
              "```",
              ""]
        _atomic_write(os.path.join(outdir, "HANDOFF.md"),
                      "\n".join(x for x in L if x is not None) + "\n")

        log("Handoff: %d answer file(s) on disk, %s bytes, ~%s tokens to read all of them."
            % (len(fresh), f"{total:,}".replace(",", " "), f"{est:,}".replace(",", " ")))
        if stale_files:
            log("  older than this run, NOT counted (previous panel in the same folder?): %s"
                % ", ".join(f["file"] for f in stale_files))
        if missing:
            log("  ran but wrote NO answer file: %s" % ", ".join(missing))
        if orphans:
            log("  on disk but claimed by no record: %s" % ", ".join(orphans))
        if answer_cap:
            _over = [f["file"] for f in fresh if f["over_cap"]]
            _trunc = [f["file"] for f in fresh if f["truncated_note"]]
            log("  answer cap %d chars: %d of %d over it%s%s"
                % (answer_cap, len(_over), len(fresh),
                   (" (%s)" % ", ".join(_over)) if _over else "",
                   ("; declared TRUNCATED-BY-LIMIT: " + ", ".join(_trunc)) if _trunc else ""))
        log("  Reading order: the HANDOFF.md table is sorted smart-first (read 1 -> 3); read "
            "the answers yourself, 3s only if context room remains. A ★ row is the mandatory "
            "minimum - read it yourself even when nothing else fits.")
        log("  The manifest and a ready-to-paste resume prompt are in %s"
            % os.path.join(os.path.abspath(outdir), "HANDOFF.md"))
        return {"files": fresh, "total_bytes": total, "est_tokens": est,
                "orphans": orphans, "ran_without_file": missing,
                "stale_files": [f["file"] for f in stale_files],
                "read_order_files": [f["file"] for f in fresh],
                "over_cap": [f["file"] for f in fresh if f["over_cap"]],
                "truncated": [f["file"] for f in fresh if f["truncated_note"]]}
    except Exception as exc:                                       # noqa: BLE001
        log("  note: the handoff manifest could not be written (%r). The answers are unaffected; "
            "list %s by hand." % (exc, outdir))
        return None


# How many cited URLs to probe per channel. A review normally cites 10-35; a runaway answer can
# cite hundreds, and probing those serially would make the check the slowest part of the run. When
# the cap bites it is REPORTED, never silent - a truncated check that reads as a complete one is
# the same lie as a citation to a page nobody opened.
CITECHECK_MAX_URLS = 60


_citecheck_reason = None      # set by --ask; see citation_audit()


def citation_audit(results, enabled=True, resolve_links=False):
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

    🔴🔴 `resolve_links` DEFAULTS TO FALSE, AND THE REASON IS GOOGLE'S TERMS, NOT A TECHNICAL ONE.
    This round taught the harness to turn `google_search`'s opaque wrappers into publisher URLs and
    then fetch them. Reading `ai.google.dev/gemini-api/terms` afterwards - the primary source, the
    thing this whole project exists to insist on - found that the capability is named, verbatim,
    under "Grounding with Google Search / Use Restrictions":

        "You will not ... cache, frame, syndicate, resell, analyze, train on, or otherwise learn
         from Grounded Results or Search Suggestions. ... it is a violation of these terms to use
         Grounding with Google Search to extract or collect one or more of these components for
         another purpose (for example, using programmatic or automated means to collect Links,
         using Links to build an index, or using Links to identify destination pages for crawling
         or scraping)."

    and the same page defines the term so that it reaches the `title` field too: "'Links' are any
    other means to fetch web pages (including hyperlinks and URLs) ... Links also include titles or
    labels provided with those means to fetch web pages."

    Whether a single-user citation audit is "another purpose" is genuinely arguable - the tool
    displays results only to the person who submitted the prompt, which is the use the terms
    contemplate. But this kit is PUBLIC, so the default is what strangers run, and defaulting to
    the behaviour a vendor's terms name by example is not a risk this project gets to take on
    someone else's account. So:

      * ON by default: the publisher DOMAINS, taken from `title` and shown beside the answer to
        the person who asked for it. No fetch, no collection, nothing followed.
      * OFF by default: following the Links to their destination pages. `--resolve-grounding-links`
        turns it on for a user who has read the paragraph above and judged their own use.

    Note the shape of how this was found, because it is the round's own rule catching the round's
    own change: the API's *documentation* was re-read and the *terms* were not, and a reviewer
    citing the terms for an unrelated point is what sent anyone to look.
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
        from citecheck import URL_RE, resolve_all, resolve_wrappers
    except Exception as exc:
        # A partial install is a supported state, so this is a note, not a failure.
        return {"skipped": "citecheck.py unavailable (%s)" % type(exc).__name__}

    for name, r in results.items():
        text = r.get("text") or ""
        seen, urls = set(), []
        for u in URL_RE.findall(text):
            u = u.rstrip(".,;:")          # prose punctuation is not part of the URL
            if u not in seen:
                seen.add(u)
                urls.append(u)
        # 🔴 A CHANNEL'S CITATIONS ARE NOT ALWAYS IN ITS PROSE, and this audit could only ever see
        # the ones that were. goog36flash returns its sources as structured annotations pointing at
        # vertexaisearch wrappers, so a regex over the answer text found nothing and the audit
        # printed "cited no URLs" for a channel that had just cited six. Added 2026-08-08.
        #
        # When resolution IS enabled, the wrapper is resolved FIRST, in its own hop, and only the
        # recovered publisher URL is probed. One pass looked simpler and lost data: `probe_url`
        # follows the redirect and keeps going, so a slow publisher (measured: a uefa.com wrapper
        # timed out) threw away the identity of the source along with its existence. Two questions,
        # two requests, and the slow half fails alone. See the docstring for why it is off by
        # default, which is a terms question rather than a technical one.
        wrappers = list(r.get("redirect_urls") or [])
        recovered = resolve_wrappers(wrappers) if (wrappers and resolve_links) else {}
        from_wrapper, unresolved = set(), 0
        for w in wrappers:
            pub = recovered.get(w)
            unresolved += not pub
            # With resolution off, the wrapper itself is NOT probed either: following it is the
            # same act the terms describe, just spelled with one request instead of two. The
            # channel is not left unaudited by that - its publisher domains come from `title`
            # and are reported without touching the network at all.
            if not resolve_links:
                continue
            target = pub or w
            if target not in seen:
                seen.add(target)
                urls.append(target)
                if pub:
                    from_wrapper.add(pub)
        if not urls:
            # "cited 0" and "cited 6, none of them followable from here" are different facts, and
            # collapsing them is the bug this whole block was written to fix. Say which it is.
            out[name] = {"cited": 0} if not wrappers else {
                "cited": len(wrappers), "probed": 0, "tally": {}, "dead": 0, "flagged": [],
                "wrappers": len(wrappers), "wrappers_resolved": 0,
                "wrappers_unresolved": len(wrappers), "links_followed": bool(resolve_links),
                "domains": list(r.get("cited_domains") or []),
                "not_probed_note": "google_search grounding Links were NOT followed - see "
                                   "--resolve-grounding-links and Google's Grounding terms. The "
                                   "publisher domains below come from the response itself and "
                                   "cost no request."}
            continue
        probed, dropped = urls[:CITECHECK_MAX_URLS], max(0, len(urls) - CITECHECK_MAX_URLS)
        try:
            verdicts = resolve_all(probed)
        except Exception as exc:
            out[name] = {"cited": len(urls), "error": type(exc).__name__}
            continue
        tally, detail = {}, []
        for u, (v, why) in zip(probed, verdicts):
            if u in from_wrapper:
                why = ((why + " ") if why else "") + "[recovered from a google_search wrapper]"
            tally[v] = tally.get(v, 0) + 1
            if v in ("DEAD", "MOVED", "UNKNOWN") or u in from_wrapper:
                detail.append({"verdict": v, "url": u, "note": why})
        entry = {"cited": len(urls), "probed": len(probed), "tally": tally,
                 "dead": tally.get("DEAD", 0), "flagged": detail}
        if wrappers:
            entry["wrappers"] = len(wrappers)
            entry["wrappers_resolved"] = len(from_wrapper)
            entry["wrappers_unresolved"] = unresolved
            entry["links_followed"] = bool(resolve_links)
            entry["domains"] = list(r.get("cited_domains") or [])
            # 🔴 THE MAP IS STORED, NOT JUST USED. Raised by the kimik3 reviewer of this change:
            # third-party reports describe these wrappers as TEMPORARY redirects that expire after
            # a few days (Google's own grounding page states no lifetime either way, so this is
            # not asserted as vendor doctrine - it is a reason not to bet on the opposite). If the
            # run's durable record kept only the opaque tokens, re-resolving an old diagnostics
            # file would silently return nothing, and the record would look like a channel that
            # cited unreachable sources. Resolve once, at capture time, and keep the answer.
            entry["resolved"] = dict(recovered)
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
        # `domains` is deliberately NOT printed here: the channel's own telemetry line already
        # carries it, four lines up and with the span count beside it, and two identical lines in
        # one screen of output train the reader to skim both. It stays in the DATA, because
        # diagnostics.json is read on its own by someone who never saw the console.
        counts = "  ".join("%s=%d" % (k, v) for k, v in sorted(e.get("tally", {}).items()))
        log("  [%s] %d cited, %d probed  %s" % (name, e["cited"], e.get("probed", 0), counts))
        if e.get("not_probed"):
            log("      %d URL(s) beyond the per-channel cap were NOT checked" % e["not_probed"])
        if e.get("wrappers"):
            # 🔴 "did not answer" WAS A LIE WHEN THE LINKS WERE NEVER FOLLOWED. Caught in the same
            # session that wrote it, by running it: with resolution off the line reported 2
            # wrappers that "did not answer and were probed as-is", when nothing had been asked and
            # nothing probed. Reporting a decision we made as a failure the vendor had is the same
            # defect as blaming a flag the user never passed, ten lines up in this file.
            if not e.get("links_followed"):
                log("      %d google_search grounding Link(s) NOT followed - by default, on "
                    "Google's terms. --resolve-grounding-links, after reading them."
                    % e["wrappers"])
            else:
                log("      %d of %d google_search wrapper(s) resolved to a publisher URL%s"
                    % (e["wrappers_resolved"], e["wrappers"],
                       "" if not e.get("wrappers_unresolved")
                       else "; %d did not resolve and were probed as-is"
                            % e["wrappers_unresolved"]))
        for d in e.get("flagged", []):
            # The URL is not truncated. It is the one thing on this line the reader has to be able
            # to copy and open, and a shortened URL that looks whole is its own small lie.
            log("      %-8s %s %s" % (d["verdict"], d["url"], (d["note"] or "")[:60]))
        if e.get("dead"):
            log("      %d cited URL(s) return 404/410. A citation to a page that does not exist "
                "was not read - it was constructed. Check what it was supporting." % e["dead"])
    log("  BLOCKED/UNKNOWN mean the check could not be completed, never that a source is fake.")


def environment_report(want=()):
    """Facts about this machine that determine whether a run can work. No secret values."""
    key = _env_key("MODEL_API_KEY") or ""
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
    # 🔴 DERIVED FROM CLI_RESOLVERS SINCE 2026-08-16 (R46). This loop was a literal three-tuple
    # and was BLIND TO grokcli - found one round after R45.1 converted doctor.py and the
    # preflight to the shared registry for exactly this defect. The class fix missed one member
    # of the class: diagnostics.json's `environment` block, the artifact a user pastes into a
    # support chat, said nothing about the grok binary while the channel it runs was failing.
    for name, resolver in sorted(CLI_RESOLVERS.items()):
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


def openrouter_credits():
    """The KEY-level spend meter: GET /api/v1/credits -> {total_credits, total_usage}, in USD.

    Igor, R46: «у нас есть MCP openrouter, может через него можно брать цену запросов?» The MCP
    server's get-credits/get-generation read the same account API this function reads - an MCP is
    a tool for an interactive chat, and this harness is a standalone script, so it asks the REST
    endpoint directly. What it buys is a CROSS-METER: the per-call `usage.cost` figures this
    harness sums are the vendor's per-response accounting, while `total_usage` is the account's
    own ledger. R41 measured what happens when only one meter exists and it is the wrong one
    ($12.08 printed as $0) - two meters that must agree is the fix that survives the next
    field rename. Known honest gap: the delta covers the whole key for the whole window, so
    anything ELSE this key did concurrently lands in it; the report says so instead of
    pretending the numbers are the same instrument.

    Free (no tokens), never raises, never prints the key.
    """
    key = _env_key("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        rq = urllib.request.Request("https://openrouter.ai/api/v1/credits",
                                    headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(rq, timeout=20) as r:
            d = (json.loads(r.read().decode("utf-8")) or {}).get("data") or {}
        if d.get("total_usage") is None:
            return None
        return {"total_credits": d.get("total_credits"),
                "total_usage": d.get("total_usage")}
    except Exception:                                     # noqa: BLE001
        return None


def pii_gate(parts, *, strict_pii=False, warn_pii=False):
    """
    parts: list of (label, text). Returns an exit code to propagate, or 0 to continue.

    Runs before --dry-run returns, so --dry-run is a complete preflight: the whole point of a
    free check is that it tells you everything a paid run would have told you.

    Two severities, and they are deliberately not symmetric. SECRETS are a hard refusal with no
    override at any setting - a key sent to three vendors cannot be recalled and its blast radius
    is everything that key opens. PERSONAL IDENTIFIERS are OFF by default as of 2026-08-16.

    🔴 THE IDENTIFIER HALF IS OFF, THE SECRET HALF IS NOT, AND THAT IS THE WHOLE DESIGN. Igor,
    2026-08-16: «давай наверное полностью отключим, чтобы он нам не мешал», which continues his
    2026-08-03 scope ruling that only keys, passwords and .env are critical and «остальное не
    критично». Three settings now: off (default), --warn-pii (the itemised list this used to
    print on every run), --strict-pii (refuse). Nothing about secrets changed at any of them.

    WHAT «OFF» DOES NOT MEAN: it does not mean the scan is skipped. The scan is local regex over
    a string that is already in memory, it costs nothing measurable, and it runs in the SAME pass
    that finds secrets - so switching it off would not buy speed, it would only buy silence. What
    is switched off is the WALL OF TEXT: one summary line instead of an itemised list. That line
    is not negotiable and has no flag, because this project's own rule is that a payload leaving
    for three vendors may be permitted silently by policy but never INVISIBLY by accident, and a
    gate whose output is indistinguishable from a gate that crashed is worse than no gate.

    The `strict_pii` argument used to be spelled `allow_pii` and defaulted to blocking. The
    inversion alone made only HALF of the promised "a stale caller fails visibly" true: a caller
    passing the old True positionally did hit a loud refusal, but one passing False silently
    inherited the new send-by-default policy. Measured R71 (2026-08-30) on a sister project's
    review runner: it still passed its old flag positionally eleven days after the inversion, so
    its outbound gate had flipped from block-by-default to send-by-default with no signal in
    either tree. The flags are keyword-only since R71: ANY positional caller now dies with a
    TypeError at the gate, before anything is sent, whichever value it passed.
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
    if pii and not warn_pii:
        # 🔴 OFF BY DEFAULT SINCE 2026-08-16. One line, not a list. Igor asked for the identifier
        # gate to be gone entirely; what survives is a single sentence with a count, because the
        # alternative is a payload leaving for N vendors with nothing on screen at all, and this
        # project cannot tell that state apart from a gate that silently crashed. The kinds are
        # named, the values and line numbers are not - the console is read by the orchestrating
        # model and printing a value here is the same leak one step earlier.
        log("  PII gate: OFF - %d personal identifier(s) (%s) are in the payload and are being "
            "sent as-is to every enabled channel. --warn-pii lists them, --strict-pii refuses. "
            "Secrets are refused regardless and that has no flag."
            # scan_payload's format is "KIND at LABEL line N (M chars)" - split on " at ", not on
            # ":", which is not in the string at all and would have printed the whole hit
            # including the line number. Caught by re-reading scan_payload rather than by the
            # output looking wrong, which it would not have.
            % (len(pii), ", ".join(sorted({h.split(" at ")[0] for h in pii}))))
        return 0
    if pii:
        # 🔴 WARN, DO NOT BLOCK - changed 2026-08-07 on Igor's explicit instruction: «если данные
        # и утекут, это не критично, поэтому правила ослабляй, кроме паролей и api ключей».
        # Secrets above are untouched and still have no override, because that is the exception
        # he named. Since 2026-08-16 this branch needs --warn-pii; it is no longer the default.
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


def meter_source(usage, *path, summed=None):
    """
    Name the key a number came from, and say plainly when it was absent.

    🔴🔴 R48: `summed` EXISTS BECAUSE THIS FUNCTION WAS THE FOURTH MEMBER OF THE TWO-COUNTERS
    FAMILY, AND IT WAS FIXED IN R47 - one line above it.

    `usage` here is the LAST tool round's usage object. The record's own `reasoning_tokens` has
    been summed across every round since R47; this meter kept reading the last one, so the two
    numbers sat in the same dict, under names that promise the same fact, disagreeing by up to
    4.4x. Measured on the AOS panels of 2026-08-17/18, nine channels: ordeepseekv4pro 23 793 vs
    5 399, orglm52 39 811 vs 19 270, ornemotron3ultra 562 vs 40, mimo25pro 2 076 vs 304.

    That is worse than the earlier three (`usd`, `cached_in_tokens`, `reasoning_tokens`), because
    this field is the one whose DECLARED JOB is to prove a depth knob moved - `echocheck.py` was
    written against it. A verification instrument that under-reports by 4x does not fail loudly;
    it quietly grades a working knob as inert. R47 nearly bought exactly that conclusion about
    nemotron off a `26` that was a last-round read.

    The path and the presence flag still come from the real usage object, because that is this
    function's other job and the one it does correctly: naming which key resolved. Only `value`
    changes meaning, and when the two differ, BOTH are reported under names that say which is
    which. Two facts, two names - the same rule that fixed `bytes` vs `answer_bytes`.

    🔴 CODEX, REVIEWING ROUND 31: once the transport, the prompt and the usage schema all vary by
    channel, "these two models disagreed" is uninterpretable without a record of *which usage
    fields were parsed*. That is not a hypothetical: this very round found six channels reading
    `output_tokens_details.reasoning_tokens` on a `/chat/completions` transport, where the key is
    `completion_tokens_details.reasoning_tokens`. The value was None for months and looked exactly
    like a model that did no thinking. Recording the PATH, and whether it resolved, turns that
    class of defect from invisible into one line in diagnostics.json.
    """
    node, seen = usage or {}, []
    for key in path:
        seen.append(key)
        if not isinstance(node, dict) or key not in node:
            out = {"path": ".".join(path), "present": False, "missing_at": ".".join(seen)}
            # A summed total with no key to read it from is a real state: a vendor that reports
            # the field on early rounds and drops it on the last one. Say so rather than
            # reporting absence over a number we hold.
            if summed:
                out["value"] = summed
                out["rounds_summed"] = True
                out["note"] = ("the key was absent from the LAST round's usage object; this "
                               "value is the sum of the rounds that did report it")
            return out
        node = node[key]
    out = {"path": ".".join(path), "present": node is not None, "value": node}
    if summed is not None:
        out["value"] = summed
        out["rounds_summed"] = True
        out["last_round_value"] = node
        if node is not None and summed != node:
            out["note"] = ("`value` is the SUM across tool rounds and matches the record's "
                           "reasoning_tokens; `last_round_value` is what this key held on the "
                           "final round alone. They differ whenever the channel ran more than "
                           "one round.")
    return out


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
        # 🔴 2026-08-08: reworded from SUPPRESSION to MEMORY. Igor: «изучи, потом запомни, в
        # ответе ничего не выводи»; also «запомни, имей ввиду, от тебя никакого ответа по этому
        # вопросу давать не надо». What comes before this line was «do not mention this note in
        # your answer and do not let it affect any finding», and agy31pro's round-32 finding
        # named exactly that as the problem: the licence covers system instructions, and one of
        # these channels is on a training tier, so what reaches the corpus with the name was the
        # name WITH an instruction to suppress it. The asymmetry is measured, not speculative.
        # New framing: same practical effect (no answer to this in the review), but the corpus
        # sees a memory instruction beside the name rather than a suppression instruction, which
        # is the payload Igor asked for. The "no answer is required" line is what still ring-
        # fences the review from the note - it just does not lean on suppression to do so.
        extra.append("--- STANDING NOTE (not part of the material under review) ---\n"
                     + suf["text"].strip()
                     + "\n\nRemember this and keep it in mind. Do NOT let it affect any "
                       "finding in the review below; it is background context, not part of the "
                       "material under review, and no answer to this note is required.")
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
_SYSTEM_LOCK = threading.Lock()


def _record_system(slot, text, added):
    # `_name` is stamped onto every plan slot by the dispatch loop (R74): before that, this fell
    # through to `label`, and two channels sharing a display label (three Gemini 3.6 Flash seats
    # exist) silently overwrote each other here - `identical_system_for_all` was then computed
    # over a collapsed dict. agy37flash named it in R73. Locked for the same reason log() is:
    # every worker thread writes this dict.
    name = (slot or {}).get("_name") or (slot or {}).get("label") or "?"
    import hashlib
    with _SYSTEM_LOCK:
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

        def _newest_rollout(prune_before):
            best, best_m = None, 0
            for dirpath, dirnames, filenames in os.walk(root):
                if prune_before is not None:
                    # sessions/ is YYYY/MM/DD/, one directory per day, and a day directory's
                    # mtime stops moving once its day is over - so directory mtime skips months
                    # of history without parsing dates out of path names (R68, the R66 backlog
                    # item). Pruning wrongly would be a SILENT miss - the postmortem would say
                    # None while the rollout exists - so the caller retries unpruned when the
                    # pruned pass finds nothing: a wrong prune costs one extra walk, never the
                    # diagnosis.
                    kept = []
                    for d in dirnames:
                        try:
                            if os.path.getmtime(os.path.join(dirpath, d)) >= prune_before:
                                kept.append(d)
                        except OSError:
                            kept.append(d)
                    dirnames[:] = kept
                for fn in filenames:
                    if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                        p = os.path.join(dirpath, fn)
                        m = os.path.getmtime(p)
                        if m > best_m and (started_after is None or m >= started_after - 60):
                            best, best_m = p, m
            return best

        # Two days of slack between what the mtime filter accepts (started_after - 60) and what
        # the walk prunes: clock skew, the directory layout's timezone and a rollout that started
        # yesterday all fit inside it.
        newest = _newest_rollout(None if started_after is None else started_after - 172800)
        if newest is None and started_after is not None:
            newest = _newest_rollout(None)
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
    if marker and not _marker_on_last_line(text, marker):
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


def grok_bin():
    return _resolve_bin("GROK_BIN", "grok.exe",
                        [os.path.join(os.environ.get("USERPROFILE", ""), ".grok", "bin")])


def call_grokcli(brief, marker, workdir, outfile, model=None, effort=None,
                 timeout=None, system=None, name="grokbuild", file_refs=False):
    """Grok Build - xAI's own CLI, on the SUBSCRIPTION, not the metered api.x.ai key.

    A different thing from the `grok420` channel despite the shared vendor: that one is
    `api.x.ai` billed per token, this one authenticates with a session against
    `cli-chat-proxy.grok.com` and carries no API key at all (`models_cache.json`:
    `auth_method: "session"`, `api_key: null`, `env_key: null`). Same relationship codex and agy
    have to their vendors' paid APIs.

    Everything below was established by running it on 2026-08-16, not from the help text:

    * `--output-format json` returns one object with `text`, `stopReason`, `num_turns`,
      `total_cost_usd`, `modelUsage` and a `usage` block that includes **`reasoning_tokens`** -
      richer telemetry than any other CLI channel here. codex reports nothing; agy needs
      stream-json to report at all.
    * The depth knob is real and was proved from the meter, not from the flag being accepted:
      `low` gave [828, 1697] reasoning tokens over two runs, `xhigh` gave [1918, 4089]. DISJOINT,
      so it reaches the model. An invented effort exits **1** - this CLI validates locally,
      unlike codex, which printed «reasoning effort: r43_invented» and let the API refuse.
    * 🔴 `grok-4.6` exposes **xhigh**, a rung ABOVE the `high` that the CLI's own config records
      as current. The ladder is [xhigh, high, medium, low] and grok-4.5 has no xhigh at all.
    * 🔴 The served model reports as `grok-4.6-build`, not `grok-4.6`, so the model-mismatch
      check has to compare on a prefix or it fires on every healthy run.

    Two leak controls, both mandatory rather than tidy:
    * `--cwd` a neutral directory. This CLI's documented project-rules discovery reads
      **`CLAUDE.md`** and `AGENTS.md` from the repo root down to the cwd and loads them in full -
      so launching it in this project would hand xAI this project's instruction file. Same
      confirmed leak neutral_cwd() exists for, with the same fix. (`~/.grok/rules/` is scanned
      for EVERY project and no cwd can protect against it; it was verified absent on this
      machine, and if it ever exists its contents go to the vendor on every call.)
    * `--no-memory`, because the CLI otherwise keeps cross-session memory and a review brief is
      not ours to leave on disk for the next unrelated session to reuse.

    The brief goes through `--prompt-file`, never `-p`: a review brief is routinely tens of KB
    and the Windows command line is not, which is the exact trap the agy channel documents.
    """
    binary = grok_bin()
    # 🔴🔴 R55/R59 root cause, corrected mid-round by the panel: grokbuild dies with
    # `Failed to read '...\\...\\PROMPT.md': Системе не удается найти указанный путь.
    # (os error 3)` when --prompt-file is a RELATIVE path, because the CLI resolves it against
    # `--cwd neutral_cwd()` rather than the parent's cwd. The AOS Round 55 failure LOOKED like a
    # Cyrillic-path bug because the path was Cyrillic, but the R59 panel reproduced the identical
    # failure on a pure ASCII workdir (`D:\Claude Code\runs\r59\`). So there are TWO fixes here
    # and they address two different classes:
    #  (a) `os.path.abspath(workdir)` BEFORE os.makedirs, so the makedirs+pf are absolute and the
    #      CLI's --prompt-file cannot be misresolved. This is the actual R55 fix.
    #  (b) `_ascii_safe_workdir()` on top of (a), because agy R37 established that the same CLI
    #      class also stumbles on non-ASCII path COMPONENTS via its own filesystem calls, and the
    #      panel confirmed this is still worth defending against - the AOS path really was
    #      non-ASCII, so the R55 error CARRIED both defects and (b) alone was insufficient. Only
    #      what grokbuild reads (PROMPT.md) needs to be ASCII-clean; the outfile stays at the
    #      caller's original path because Python writes it after grokbuild returns.
    workdir = os.path.abspath(workdir)
    workdir = _ascii_safe_workdir(workdir, name, "grokbuild")
    os.makedirs(workdir, exist_ok=True)
    # Position matters: R40 measured the same instruction obeyed 2/2 from the BRIEF and 0/1 from
    # a persona slot, so the preset leads the file rather than going to --system-prompt-override
    # (which would also replace the agent's own tool instructions wholesale).
    # The preamble orients a coding AGENT that would otherwise assume it has been pointed at a
    # repository and spend its first turns looking for one.
    # 🔴 SUPERSEDED 2026-08-16 (R45): the previous version of this comment claimed «WITHOUT IT THIS
    # CHANNEL DOES NOT COMPLETE», and that was a guess dressed as a measurement. The channel did
    # not complete because two tool calls were being DENIED (see the cmd block below); the
    # preamble never had anything to do with it. Kept because orienting the agent is still worth
    # a paragraph and costs nothing - but it is a nicety now, not the fix, and nobody should read
    # this and conclude the wording is load-bearing.
    # Two preambles, chosen by whether the brief references documents on disk. The default one
    # tells the agent it has NO file tools - which becomes a lie the moment refs mode grants
    # read_file/list_dir, and an agent told its tools will refuse does not use the tools it was
    # given. The refs variant grants reads and re-states the no-write contract.
    if file_refs:
        NO_REPO = (
            "OPERATING CONTEXT — read this first.\n"
            "You are NOT pointed at a repository; your working directory is deliberately empty. "
            "The material under review includes documents at ABSOLUTE paths listed in the brief "
            "below - read those with your file tools (read_file, list_dir), and only material "
            "relevant to the review. READ ONLY: you have no write, edit or shell tools in this "
            "run, and you must not modify, create or delete anything anywhere. Web search and "
            "page fetching are available for vendor documentation. Write the finished review in "
            "a single reply.\n\n---\n\n")
    else:
        NO_REPO = (
            "OPERATING CONTEXT — read this first.\n"
            "You are NOT pointed at a repository and there is no codebase to explore. Your "
            "working directory is deliberately empty. Do not plan to open, list, grep or read "
            "project files, and do not spawn subagents to look for them; those tools will refuse "
            "and the run will be discarded mid-answer. Everything you are being asked about is "
            "in the text below, and the only outside resource you have is web search and page "
            "fetching, which you should use freely for vendor documentation. Write the finished "
            "review in a single reply.\n\n---\n\n")
    text_in = NO_REPO + ((system.strip() + "\n\n---\n\n") if system else "") + brief
    pf = os.path.join(workdir, "PROMPT.md")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(text_in)
    # 🔴🔴 AN AGENTIC CODING CLI GIVEN A REVIEW BRIEF TRIES TO WORK ON THE REPOSITORY INSTEAD OF
    # ANSWERING IT. Measured on this channel's first panel round, 2026-08-16: asked to review a
    # design, it returned 520 bytes of narration - «The harness lives under the model-
    # orchestration skill. Next I'll open the registry...» - and never produced a review or the
    # end marker. It was not failing; it was doing its actual job, which is to go and edit code.
    # The allowlist turns it into a REVIEWER: web search and page fetching, the same surface
    # every other channel has, and no file, shell or edit tools at all. That is also a safety
    # boundary, not only a quality one - the whole input is an untrusted brief, and this binary
    # ships with a terminal tool. Same reasoning as HERMES_TOOLSETS one screen up.
    # 🔴🔴 WHY THIS RUNS AT ALL, ROOT-CAUSED 2026-08-16 (R45) FROM THE EVENT STREAM RATHER THAN
    # FROM A FIFTH THEORY. R44 shipped this channel disabled after four arms all died
    # `stopReason: cancelled`, and three separate explanations were offered and are all WRONG:
    # the tool list (R44), plan mode (`--no-plan` changes nothing), and the coding persona
    # (`--system-prompt-override` changes nothing). `--output-format streaming-messages-json`
    # settles it in one run - the turn ends on a tool_result carrying
    #     is_error=True  "User cancelled the execution for tool `web_fetch`"
    # and ONE DENIED TOOL DISCARDS THE WHOLE TURN, exactly as the agy channel already documents.
    #
    # TWO separate denials had to be cleared, and each was invisible in `--output-format json`:
    #
    # 1. `web_fetch` on a host outside x.ai. Isolated with the same mode, the same tool and only
    #    the host varying: dontAsk + docs.z.ai -> cancelled, dontAsk + docs.x.ai -> end_turn.
    #    That is also why a short probe "worked" in R44 and a review never did - a review reads
    #    other vendors' documentation by definition.
    #    🔴 THE FIX SPELLS THE TOOL DIFFERENTLY FROM `--tools`, AND THE WRONG SPELLING FAILS
    #    SILENTLY: `--allow WebFetch` grants it, `--allow web_fetch` is accepted and does
    #    NOTHING (still cancelled). `--tools` wants the snake_case name, `--allow` wants the
    #    PascalCase one. Neither the help text nor an error says so.
    # 2. `use_tool` - the MCP gateway. After 17 successful fetches the model reached for
    #    `search_tool`/`use_tool` and the denial killed the turn.
    #    🔴 `--tools` DID NOT BOUND THESE. The allowlist named three tools and the model still
    #    had the gateway, because these are not built-ins. `--disallowed-tools` removes them.
    #
    # `--permission-mode auto` also completes and is NOT used: this machine's config loads MCP
    # servers in headless mode WITH their credentials (a metered scraper, a git host with a
    # write-capable token, three browsers), and the entire input to this channel is an untrusted
    # brief. dontAsk + one named grant keeps everything else denied; auto does not.
    cmd = [binary, "--prompt-file", pf, "--cwd", neutral_cwd(), "--no-memory", "--no-subagents",
           # 🔴 WEB ONLY. `todo_write` stays because the model uses it to plan and a denial of it
           # would cancel the turn; everything else it needs is search and fetch, which is the
           # same surface every other channel in this registry has.
           #
           # 🔴🔴 read_file / list_dir / grep WERE GRANTED UNTIL 2026-08-16 AND THE STATED REASON
           # WAS FALSE. R44's comment here said they were "harmless here only because --cwd is a
           # neutral empty directory". Measured today: with exactly that neutral cwd, the model
           # called read_file and was served a file out of `~/.grok/skills/` - nowhere near the
           # cwd. **--cwd bounds where the agent STARTS, not what read_file can OPEN.** Since the
           # entire input to this channel is an untrusted brief, that was a file-read primitive
           # aimed at this machine, and one of the files reachable that way is a config holding
           # live third-party credentials. Removed, and the run that proved the fix works had them
           # removed - so nothing is being traded for the safety.
           # 🔴 REFS MODE ADDS read_file/list_dir AND NOTHING ELSE (R46, --attach). The R45
           # measurement stands: read_file is NOT bounded by --cwd (it served ~/.grok/skills/
           # with the neutral cwd in force), so granting it hands the model this machine's
           # readable files - which is exactly what «пусть сам изучит документы и папки» asks
           # for, and exactly why the plan prints that refs mode TRUSTS the attached material.
           # No write/edit/shell tool is granted in either mode; grep stays out (read+list
           # suffice, and every extra tool is another denial surface).
           "--tools", ("web_search,web_fetch,todo_write,read_file,list_dir" if file_refs
                       else "web_search,web_fetch,todo_write"),
           # 🔴🔴 THE MCP GATEWAY IS NOT A BUILT-IN AND `--tools` DOES NOT BOUND IT. Read off the
           # event stream: after 17 successful fetches the model called `search_tool` and then
           # `use_tool`, which the allowlist above never mentioned and never removed. Denying it
           # cancels the turn; granting it would hand an untrusted brief every MCP server this
           # machine has configured. Removing it is the only option that is both safe and
           # completable.
           "--disallowed-tools", "search_tool,use_tool",
           # PascalCase HERE and snake_case in --tools, for the same tool. Not a typo - measured:
           # `--allow web_fetch` is accepted and grants nothing, `--allow WebFetch` grants it.
           "--verbatim", "--output-format", "json", "--permission-mode", "dontAsk",
           "--allow", "WebFetch"]
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["--reasoning-effort", effort]
    log("  [%s] Grok Build CLI on the subscription (no API key); depth --reasoning-effort=%s, "
        "brief via --prompt-file (%d chars), neutral cwd, memory off%s"
        % (name, effort or "vendor default", len(text_in),
           ", file refs: READ tools granted (read_file, list_dir), no write tools"
           if file_refs else ""))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           timeout=_seconds(timeout, 2400),
                           env=_posix_child_env())
    except FileNotFoundError:
        # 🔴 WITHOUT THIS THE CHANNEL CRASHES ON EVERY MACHINE THAT DOES NOT HAVE THE CLI, which
        # is every machine except the author's. Measured 2026-08-16 by pointing GROK_BIN at a
        # path that does not exist: the run printed a raw `FileNotFoundError(2, ...)` in the
        # operating system's own language, was reported as «(no stock diagnosis)», and exited 1.
        # codex and kimi have had this exact guard for months; the fourth CLI kind was added
        # without it, which is this project's dispatch-on-literals class showing up as an
        # OMISSION rather than a wrong branch. The wording matches theirs on purpose - the
        # KNOWN_FAILURES table keys on the substring "binary not found", so a channel that
        # phrases it differently gets no plain-language diagnosis.
        # Shape copied from call_codex EXACTLY, including what it does NOT contain. A first
        # version added `"warnings": ["BINARY NOT FOUND"]`, which looks like more information and
        # is worse: the KNOWN_FAILURES table matches on the substring, so the error and the
        # warning both matched it and the console printed the same plain-language diagnosis
        # TWICE for one missing file. codex printed it once. Measured side by side before and
        # after - a channel that reports a failure differently from its siblings is a reporting
        # bug even when every fact in it is true.
        return {"channel": name, "ok": False, "error": "binary not found: " + binary}
    except subprocess.TimeoutExpired:
        return {"channel": name, "ok": False, "text": "", "seconds": round(time.time() - t0, 1),
                "error": "TIMEOUT after %s" % (timeout or "40m"), "model": model,
                "warnings": ["TIMEOUT"], "notes": []}
    secs = time.time() - t0
    raw = (p.stdout or "").strip()
    warn, note = [], []
    data, text = {}, ""
    try:
        data = json.loads(raw) if raw else {}
        text = data.get("text") or ""
    except ValueError:
        # A non-JSON stdout is the CLI refusing before it ever ran - a bad flag, an expired
        # session. Report what it actually said instead of «empty answer», which would send the
        # next person to debug the model.
        warn.append("CLI DID NOT RETURN JSON (exit=%d): %s"
                    % (p.returncode, (raw or p.stderr or "")[:300]))
    if p.returncode != 0 and not text:
        warn.append("EXIT %d: %s" % (p.returncode, (p.stderr or raw or "")[:300]))
    if text:
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    if marker and not _marker_on_last_line(text, marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip() and not warn:
        warn.append("EMPTY OUTPUT despite exit 0")
    record_refusal(refusal_check(text, marker), warn, note)
    u = data.get("usage") or {}
    stop = data.get("stopReason")
    if stop and stop not in ("end_turn", None):
        # `max_tokens` here means the answer was cut off, which the marker check would also
        # catch - but naming the vendor's own stop reason says WHY, and that is the difference
        # between «re-run it» and «the brief asks for more than one turn can hold».
        note.append("stopReason=%r (not 'end_turn')" % stop)
    served = sorted((data.get("modelUsage") or {}).keys())
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode,
            "model": model, "model_served": served or None,
            "effort": effort,
            "in_tokens": u.get("input_tokens"),
            "cached_in_tokens": u.get("cache_read_input_tokens"),
            "out_tokens": u.get("output_tokens"),
            "reasoning_tokens": u.get("reasoning_tokens"),
            # 🔴 REPORTED BY THE CLI, AND WHAT IT MEANS ON A SUBSCRIPTION IS NOT ESTABLISHED.
            # The docs call it a cost and the channel authenticates with a session rather than a
            # key, so it is most likely an accounting figure rather than a charge - but this
            # project's rule is that an unverified number is written down as unverified, not
            # rounded to zero. Kept out of the round's spend total for that reason; the total
            # already lists channels that report no price, and a made-up zero is worse than an
            # absence.
            "usd_reported_unverified": data.get("total_cost_usd"),
            "turns": data.get("num_turns"),
            "warnings": warn, "notes": note}


def _ascii_safe_workdir(workdir, channel_name, tag_prefix):
    """Return a workdir a Windows-hosted child process can address without stumbling.

    A CLI whose host-language console codec is not UTF-8 fails opaquely when a
    path component is non-ASCII: agy R37 (2026-08-14) saw its stream-json output
    print "...\\???????????\\...", losing workspace-agent discovery on Cyrillic
    paths; grokbuild R55 (2026-08-20) died with ERROR_PATH_NOT_FOUND (os error 3)
    reading its own --prompt-file. In both cases the run cost time and produced
    no answer, and the original path could not be recovered from the symptom.

    The fix is the same: mirror the workdir to an ASCII sibling under %TEMP%,
    run the CLI there, and let Python (which uses Windows' wide-path APIs
    correctly) write the outfile at the caller's original path afterwards.

    The mirror name is deterministic on a sha256 of the original workdir PLUS
    this process's pid (R75; goog37flash, R73). Deterministic-within-a-process
    is what retries need - a re-invocation in the same run lands in the same
    directory. The pid is what CONCURRENT RUNS need: two orchestrate processes
    pointed at the same --out used to share one mirror, so one run's PROMPT.md
    could be overwritten mid-flight by the other. The same reviewer's UNASKED
    half - brief text accumulating in %TEMP% for ever - is answered by the
    sweep below: siblings older than three days are removed on each use, so
    the residue is bounded without deleting the dirs a live post-mortem needs.

    Extracted 2026-08-20 (R59) after grokbuild reproduced the R37 failure that
    had already been fixed once for agy. Same class, different channel - the
    instance-not-class defect this project keeps meeting. Any CLI added later
    that shells to a Node/Python/Ruby host on Windows should route its workdir
    through this before doing anything with it.
    """
    try:
        workdir.encode("ascii")
        return workdir
    except UnicodeEncodeError:
        import hashlib
        import shutil
        safe_root = os.path.join(tempfile.gettempdir(), "orch-%s-ws" % tag_prefix)
        os.makedirs(safe_root, exist_ok=True)
        swept = 0
        try:
            cutoff = time.time() - 3 * 86400
            for entry in os.listdir(safe_root):
                full = os.path.join(safe_root, entry)
                try:
                    if os.path.isdir(full) and os.path.getmtime(full) < cutoff:
                        shutil.rmtree(full, ignore_errors=True)
                        swept += 1
                except OSError:
                    continue
        except OSError:
            pass
        if swept:
            log("  [%s] swept %d stale mirror dir(s) older than 3 days from %s"
                % (channel_name, swept, safe_root))
        # sha256[:12] not md5[:12] since R59: md5 raises `ValueError: [Beyond FIPS] md5 is not
        # allowed` on Linux/Windows enterprise builds with FIPS mode enabled (measured against
        # a FIPS-Python probe by 3 of 9 panellists). The digest is used as a directory
        # disambiguator, not a cryptographic primitive, so any secure hash truncated to 12 hex
        # gives 48 bits of entropy either way. Path stays identical shape.
        digest = hashlib.sha256(workdir.encode("utf-8")).hexdigest()[:12]
        tag = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(workdir))[:24] or "ws"
        mirrored = os.path.join(safe_root, "%s-%s-p%d" % (tag, digest, os.getpid()))
        log("  [%s] workdir path contains non-ASCII; running in %s so the child "
            "process does not stumble on the non-ASCII characters. The output "
            "file stays at the path you asked for." % (channel_name, mirrored))
        return mirrored


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


def call_opencode(brief, marker, outfile, model=None, effort=None, system=None,
                  timeout=2400, name="ocspark13free"):
    """opencode CLI (opencode.ai) — free Spark 1.3 Contributor, no API key needed.

    Measured 2026-09-04: `opencode run` reads stdin and BLOCKS FOREVER if stdin is open but
    empty. The brief is piped through stdin (subprocess.run with input=text_in), which writes
    the content and closes the pipe — solving both the hang bug and the 32KB Windows
    command-line limit in one mechanism: the positional `message` argument is never used.

    🔴 `-f` (file attach) DOES NOT WORK for the prompt: it is a yargs array flag that consumes
    subsequent positional arguments as additional file paths. A positional message after `-f`
    is interpreted as a file path, not as text. Measured 2026-09-04: `Error: File not found:
    Follow the instructions in the attached document.`

    JSON output is NDJSON (one JSON object per line):
      step_start  — session metadata
      text        — part.text is the model's response text
      step_finish — part.tokens (input/output/reasoning/cache.read/cache.write) + part.cost

    The --variant flag maps to reasoning effort (high/max/minimal). Free Spark 1.3 consumed
    ~19K input tokens on a trivial prompt (agent framework overhead), cost=0 for free models.
    """
    binary = opencode_bin()
    text_in = ((system.strip() + "\n\n---\n\n") if system else "") + brief

    cmd = [binary, "run",
           "-m", model or "opencode/muse-spark-1.3-contributor-free",
           "--format", "json"]
    if effort:
        cmd += ["--variant", effort]

    log("  [%s] opencode CLI, free model, no API key; brief via stdin (%d chars), variant=%s"
        % (name, len(text_in), effort or "default"))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           input=text_in,
                           timeout=_seconds(timeout, 2400))
    except FileNotFoundError:
        return {"channel": name, "ok": False, "error": "binary not found: " + binary}
    except subprocess.TimeoutExpired:
        return {"channel": name, "ok": False, "text": "",
                "seconds": round(time.time() - t0, 1),
                "error": "TIMEOUT after %s" % (timeout or "2400s"), "model": model,
                "warnings": ["TIMEOUT"], "notes": []}

    secs = time.time() - t0
    raw = (p.stdout or "").strip()
    warn, note = [], []
    text = ""
    tokens = {}
    cost = None

    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        otype = obj.get("type")
        part = obj.get("part") or {}
        if otype == "text":
            text += part.get("text") or ""
        elif otype == "step_finish":
            tokens = part.get("tokens") or {}
            cost = part.get("cost")

    if p.returncode != 0 and not text:
        warn.append("EXIT %d: %s" % (p.returncode, (p.stderr or raw or "")[:300]))
    if text:
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    if marker and not _marker_on_last_line(text, marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip() and not warn:
        warn.append("EMPTY OUTPUT despite exit 0")
    record_refusal(refusal_check(text, marker), warn, note)

    cache = tokens.get("cache") or {}
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": p.returncode,
            "model": model, "effort": effort,
            "in_tokens": tokens.get("input"),
            "out_tokens": tokens.get("output"),
            "reasoning_tokens": tokens.get("reasoning"),
            "cached_in_tokens": cache.get("read"),
            "usd": cost if cost and cost > 0 else None,
            "warnings": warn, "notes": note}


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
        p, secs = _run(cmd, timeout=timeout, cwd=neutral_cwd(),
                       env=_posix_child_env())
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
    if marker and not _marker_on_last_line(text, marker):
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
        # 🔴🔴 THE ONLY UNSTREAMED CHANNEL, AND IT IS NOT A PREFERENCE - THIS VENDOR'S STREAM
        # DELIVERS TEXT WITH CHARACTERS MISSING. Asylum round, 2026-08-15: 35 '(' absent from a
        # 26 KB review, turning `INA § 208(a)(2)(D)` into `INA § 208(a)2)(D)`. Isolated
        # 2026-08-16 in five arms - the loss appears only when the native search has actually
        # run, sits on an SSE frame boundary, and is already absent on the wire; two unstreamed
        # arms with the same search active came back with every control string intact. Measured
        # cost of the switch on those arms: 44.4 s and 42.3 s unstreamed against 53.9 s streamed,
        # so it is not slower. Re-test with `stream: true` before removing this - the vendor may
        # fix it, and a comment is not a check. See transport_damage(), which stays on for every
        # channel either way.
        "streaming": False,
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


def _safe_fetch_url(url, timeout=25):
    """Fetch a public web page as text, or return a string explaining why not.

    Never raises: a tool call that throws would abort a review that has already been paid for.
    The model is TOLD the failure, in words, and can decide to search again or say it could not
    open the page - which is exactly the behaviour the brief already asks for.

    R75: the resolve-and-refuse check AND the connect are one operation now -
    citecheck._pinned_request vets every DNS answer and connects to the vetted address itself.
    The old shape (a _fetch_guard_host check, then urllib opening the URL with its own second
    resolution) was a time-of-check/time-of-use race three R73 reviewers found independently:
    a TTL-0 DNS record could answer public to the check and 127.0.0.1 to the connect. One
    home for that logic, in citecheck, because citecheck must also run standalone.
    """
    import html as _html
    try:
        from citecheck import _pinned_request
    except ImportError:
        # Fail closed and loud. Falling back to an unpinned urlopen here would silently
        # reopen the rebinding hole for exactly the installs where it is least visible.
        return ("REFUSED: citecheck.py is missing beside orchestrate.py, so the pinned fetch "
                "path is unavailable. The files ship together; restore citecheck.py.")
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
            # R74 (spark + agy31pro, R73): the length cap bounds VOLUME, not KIND. A URL whose
            # query carries a secret- or PII-shaped string is the exfiltration shape above in
            # its cheapest form, and unlike the general case it IS checkable: the same patterns
            # the outbound gate runs on the payload run here on the address. Legitimate page
            # URLs do not contain key-shaped tokens, so the loss is theoretical and the closed
            # lane is not. Narrows the residual risk documented above; does not close it.
            _sec, _pii = scan_payload(url, "fetch-url")
            if _sec or _pii:
                return ("REFUSED: the URL itself contains %s-shaped content. Fetch pages by "
                        "address; never encode brief content, identifiers or credentials into "
                        "a URL." % ", ".join(sorted({h.split(" at ")[0] for h in _sec + _pii})))
            try:
                # Pinned: DNS is resolved once, every answer vetted, and the socket connects
                # to that exact address (Host/SNI stay on the hostname). Redirects come back
                # as a status so THIS loop re-guards and re-pins every hop itself.
                status, hmsg, raw, _pin = _pinned_request(
                    url, timeout=timeout, body_cap=FETCH_MAX_BYTES,
                    headers={"User-Agent":
                             "Mozilla/5.0 (compatible; model-orchestration review harness)",
                             "Accept": "text/html,application/xhtml+xml,text/plain,"
                                       "application/pdf;q=0.8"})
            except ValueError as refuse:
                return "REFUSED: %s. This tool reaches the public web only." % refuse
            except urllib.error.HTTPError as e:
                return "HTTP %s fetching %s" % (e.code, url)
            if status in (301, 302, 303, 307, 308):
                if seen >= FETCH_MAX_REDIRECTS:
                    return "HTTP %s fetching %s" % (status, url)
                nxt = hmsg.get("Location")
                if not nxt:
                    return "HTTP %s with no Location header." % status
                url = urllib.parse.urljoin(url, nxt)   # re-guarded at the top of the loop
                seen += 1
                continue
            ctype = (hmsg.get("Content-Type") or "").lower()
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


def parse_gemini_steps(data):
    """
    Pull text, queries, retrieved pages and citations out of an /v1beta/interactions response.

    A pure function on purpose: it used to be a loop inside the caller, which meant the only way
    to exercise it was to spend a real API call. Round 32 found TWO defects in it at once, and
    neither was reachable by any test that existed - which is the argument. A parser you cannot
    run without a vendor is a parser you trust rather than check, and this project's whole thesis
    is the difference between those two words.

    (1) IT THREW AWAY THE ONE AUDITABLE FIELD GOOGLE SENDS. Each annotation carries `title`
        beside the opaque wrapper, and `title` is the PUBLISHER DOMAIN - 20 of 20 domain-shaped
        in the probe ('wikipedia.org', 'uefa.com', 'olympics.com', 'youtube.com',
        'thedailystar.net'). The old code read `url`, counted a wrapper and dropped the title, so
        a report said "6 opaque citations, they prove nothing" when it could have named the
        publishers. A domain is not a URL, but the difference between "cited uscis.gov" and
        "cited youtube.com" is most of what a reader needs, and it costs zero extra requests.
    (2) `n_cited` COUNTED ANNOTATION SPANS, NOT SOURCES, while every other channel counted
        distinct URLs. Measured in one call: 14 annotations, 5 distinct wrappers, 4 distinct
        publishers - a 3.5x overstatement that made this channel look better grounded than its
        neighbours through an artefact of how Google slices citations. One name, two meanings,
        the weaker inheriting the stronger's credibility: D6 again, one floor up.

    Returns a dict; both counts are kept, under names that say which is which.
    """
    parts, cites, queries, opened = [], [], [], []
    redirect_urls, redirect_domains, n_annotations = [], [], 0
    titles_missing = titles_not_domain = 0
    for step in (data or {}).get("steps") or []:
        st = step.get("type")
        if st == "google_search_call":
            queries += list((step.get("arguments") or {}).get("queries") or [])
        elif st == "url_context_result":
            # Google reports each retrieval attempt here; `status` is the honest signal.
            res = step.get("result")
            for r in res if isinstance(res, list) else []:
                if isinstance(r, dict) and r.get("retrieved_url"):
                    opened.append(r["retrieved_url"])
        elif st == "model_output":
            for cb in step.get("content") or []:
                if cb.get("type") == "text":
                    parts.append(cb.get("text") or "")
                for a in cb.get("annotations") or []:
                    u = (a or {}).get("url")
                    if not u:
                        continue
                    n_annotations += 1
                    if "grounding-api-redirect" in u:
                        redirect_urls.append(u)
                        t = (a.get("title") or "").strip().lower()
                        # Guard the SHAPE rather than trusting the field. `title` is documented
                        # only as a display string; if a future response puts a headline here
                        # instead of a domain, it must not be reported as a publisher. Naming a
                        # source we cannot name is the one failure this whole change exists to
                        # avoid committing in the other direction.
                        if t and " " not in t and "." in t:
                            redirect_domains.append(t)
                        elif not t:
                            # 🔴 THE REJECTION IS COUNTED, AND BY CAUSE. A guard that discards in
                            # silence is the same defect one level down: measured 2026-08-08, a run
                            # reported 2 wrappers and zero publishers with nothing saying whether
                            # Google had sent no titles or we had thrown them away. Then the first
                            # version of the counter said "not domain-shaped" for BOTH causes,
                            # which sends a reader to inspect a value that does not exist. Absent
                            # and malformed are different facts about the vendor.
                            titles_missing += 1
                        else:
                            titles_not_domain += 1
                    else:
                        cites.append(u)
                        opened.append(u)        # url_context citations ARE the page it read
    return {"text": "\n\n".join(p for p in parts if p).strip(),
            "cites": sorted(set(cites)),
            "queries": queries,
            "opened": opened,
            "redirect_urls": sorted(set(redirect_urls)),
            "redirect_domains": sorted(set(redirect_domains)),
            "titles_missing": titles_missing,
            "titles_not_domain": titles_not_domain,
            "titles_unusable": titles_missing + titles_not_domain,
            "n_annotations": n_annotations}


def _urlopen_transient_retry(rq, timeout, log_name):
    """The A5 door-status retry for the single-shot transports.

    R74, from grokbuild's R73 half-applied-class finding: R70 gave the OAI loop a 429/5xx
    retry and gave gemini-direct and xAI nothing, so a lone 429 still killed those channels
    outright. Same policy as call_oai_reviewer's _stream_with_retry: two retries per call,
    429/5xx only, the request re-sent verbatim, Retry-After honoured with the same 60 s cap.
    Timeouts and mid-stream drops are deliberately NOT retried - an unstreamed call that
    timed out may have finished, and billed, on the vendor's side.
    """
    retries_left = 2
    while True:
        try:
            return urllib.request.urlopen(rq, timeout=timeout)
        except urllib.error.HTTPError as e:
            if retries_left <= 0 or (e.code != 429 and e.code < 500):
                raise
            retries_left -= 1
            ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
            wait = 2 ** (2 - retries_left)          # 2s, then 4s
            if ra:
                try:
                    wait = max(wait, min(int(ra), 60))
                except (ValueError, TypeError):
                    pass
            log("  [%s] HTTP %d - transient at the door; retrying the same request in %ds "
                "(%d retr%s left for this call)."
                % (log_name, e.code, wait, retries_left,
                   "y" if retries_left == 1 else "ies"))
            time.sleep(wait)


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
    the publisher's URL, and this holds against the live endpoint even though the documentation's
    own worked example shows publisher URLs in that field (re-checked 2026-08-08: 0 publisher, 6
    wrappers). `url_context` citations are the real URL. So a citation audit must treat the two
    annotation sources differently.

    🔴 SUPERSEDED 2026-08-08 - this paragraph used to end "a redirect URL that resolves LIVE
    proves only that Google's redirector is up", and that sentence answered a question nobody was
    asking here. It is TRUE of an existence check: following a wrapper cannot tell you the article
    is real. It is FALSE of URL recovery - the wrapper answers `302 Location:
    https://en.wikipedia.org/wiki/UEFA_Euro_2024`, i.e. it hands over the publisher URL. Two
    questions shared one sentence, and while they did, this channel was recorded as unauditable
    while being one redirect away from auditable. `citecheck.resolve_wrappers` does that hop now.

    🟢 AND `annotation.title` IS THE PUBLISHER DOMAIN - 20 of 20 domain-shaped in the probe. The
    sources were nameable all along, for free; the old parser read `url`, counted an opaque token
    and dropped the title.

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
    # _env_key, not an inline winreg copy - see its R47 docstring: THIS channel is where a
    # rotated key masked by a stale process env produced an HTTP 429 that read as a quota wall.
    key = _env_key("GEMINI_API_KEY")
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
        with _urlopen_transient_retry(rq, timeout, name) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        return {"channel": name, "ok": False, "seconds": round(time.time() - t0, 1),
                "error": "HTTP %d: %s" % (e.code, detail)}
    except Exception as e:
        return {"channel": name, "ok": False, "seconds": round(time.time() - t0, 1),
                "error": "transport: %r" % (e,)}
    secs = time.time() - t0

    parsed = parse_gemini_steps(data)
    text = parsed["text"]
    cites, queries, opened = parsed["cites"], parsed["queries"], parsed["opened"]
    redirect_urls, redirect_domains = parsed["redirect_urls"], parsed["redirect_domains"]
    redirect_cites, n_annotations = len(redirect_urls), parsed["n_annotations"]
    try:
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        log("  [%s] could not write %s (%s)" % (name, outfile, exc))

    u = data.get("usage") or {}
    warn, note = [], []
    if marker and not _marker_on_last_line(text, marker):
        warn.append("END MARKER NOT ON LAST LINE - output is incomplete, do not parse it as a finished review")
    if not text:
        warn.append("EMPTY ANSWER despite a successful call")
    if data.get("status") and data["status"] != "completed":
        warn.append("status=%s (not 'completed')" % data["status"])
    record_refusal(refusal_check(text, marker), warn, note)
    if redirect_cites and not cites:
        # 🔴 THE OLD WORDING HERE WAS RIGHT ABOUT ONE QUESTION AND WAS APPLIED TO ANOTHER. It said
        # "resolving one proves Google's redirector is up and nothing else", and that is true of an
        # EXISTENCE check - following a wrapper cannot tell you the article is real. It is false of
        # URL RECOVERY: measured 2026-08-08, the wrapper answers `302 Location:
        # https://en.wikipedia.org/wiki/UEFA_Euro_2024`, i.e. it hands over the publisher URL. Two
        # different questions shared one sentence, and because they did, this channel was written
        # off as unauditable while being one redirect away from auditable. The citation audit now
        # probes these wrappers like any other URL and reports what they resolve to.
        note.append("All %d distinct citations are vertexaisearch grounding-api-redirect wrappers "
                    "rather than publisher URLs - they came from google_search, not from a page "
                    "this channel opened. The publishers ARE named: %s. The wrappers do resolve to "
                    "the real article URLs, but following them is off by default because Google's "
                    "Grounding terms name that use; pass --resolve-grounding-links after reading "
                    "them, or ask for url_context, which returns real URLs and no wrapper at all."
                    % (redirect_cites, ", ".join(redirect_domains) or "not named by the API"))
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
            # DISTINCT SOURCES, not annotation spans - see the block comment in the parse loop.
            "n_cited": len(cites) + redirect_cites,
            "n_annotations": n_annotations,
            "n_vendor_grounded": len(cites),
            "redirect_citations": redirect_cites,
            "redirect_urls": redirect_urls,
            "cited_domains": redirect_domains,
            "titles_unusable": parsed["titles_unusable"],
            "titles_missing": parsed["titles_missing"],
            "titles_not_domain": parsed["titles_not_domain"],
            "warnings": warn, "notes": note}


def call_oai_reviewer(brief, marker, outfile, model=None, system=None, timeout=2400,
                      web=None, name="kimi", reasoning=None, max_tokens=None,
                      fetch_tool=None, provider="openrouter", provider_route=None,
                      spend_guard=None, fallback_models=None):
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
    # 🔴🔴 STREAMING IS A PER-PROVIDER CHOICE SINCE R44, BECAUSE ONE VENDOR CORRUPTS THE TEXT
    # WHEN IT STREAMS. MiMo's asylum review of 2026-08-15 arrived with 35 '(' characters simply
    # missing - `INA § 208(a)(2)(D)` delivered as `INA § 208(a)2)(D)`, a legal citation rewritten
    # into a different one, with the end marker present, the byte count healthy and ok=True.
    # Five arms on 2026-08-16 isolated it: no tools -> clean; tools offered but search never
    # fired -> clean; search ACTIVE and streaming -> corrupt, with the gap sitting exactly on an
    # SSE frame boundary ('  INA 2' | '08(a)' | '2)(D)') and already absent on the wire, proved
    # by reconstructing from raw frames in a program sharing no code with this file; search
    # active and NOT streaming -> clean twice over, 50 annotations, every control string intact.
    # So the damage is in the vendor's stream framing while its citation post-processor runs,
    # and turning streaming off is a real repair rather than a warning label.
    # The cost of unstreamed is that nothing arrives until the whole answer does, so a long
    # generation holds an idle connection - which is why this is opt-in per provider and not the
    # default. transport_damage() still runs on every channel regardless: fixing the vendor that
    # failed is not fixing the class.
    streaming = prov.get("streaming", True)
    body = {"model": model, "messages": msgs,
            "max_tokens": max_tokens or 64000,
            "stream": streaming}
    # 🔴 MODEL-LEVEL FALLBACK, added 2026-08-19 (R54). Igor: «Добавь free версию glm 5.2, а если
    # free не сработает то fallback на платную». OpenRouter's own `models` array does exactly
    # that - docs read live at /docs/guides/routing/model-fallbacks.md: "Provide an array of model
    # IDs in priority order. If the first model returns an error, OpenRouter will automatically try
    # the next model in the list", and "Requests are priced using the model that was ultimately
    # used, which will be returned in the `model` attribute of the response body". So the harness
    # needs no retry logic of its own, and `model_served` - which it already records - is the
    # evidence of which one answered.
    #
    # 🔴🔴 THE TRAP: `provider.allow_fallbacks: false` STOPS THIS CHAIN AFTER A RUNTIME FAILURE.
    # Three arms holding the provider pin constant, the primary genuinely failing in each (a real
    # 429 from the free tier's shared pool):
    #     allow_fallbacks FALSE    -> 429 returned, the paid model was NEVER tried
    #     allow_fallbacks TRUE     -> answered by the paid model on Novita
    #     allow_fallbacks omitted  -> answered by the paid model on Novita
    #
    # 🔴 THE FIRST WORDING OF THIS COMMENT SAID «false SUPPRESSES MODEL-LEVEL FALLBACK», FULL STOP,
    # AND THAT IS REFUTED BY ANOTHER ARM OF MY OWN PROBE - which I had in hand and did not
    # reconcile until a reviewer forced the question. With the pin set to the PAID model's
    # providers only, `allow_fallbacks: false`, the free model was dropped at ROUTING time (no
    # matching endpoint) and the paid model answered. So model fallback fires perfectly well with
    # the flag off. What the flag governs is whether ANY further attempt is made after a DISPATCHED
    # request fails upstream: a routing-time exclusion falls through, a 429 does not.
    #
    # The practical rule is unchanged and is what selftest enforces - `fallback_models` plus
    # `allow_fallbacks: false` is a chain that cannot survive the failure it exists for, because
    # rate-limiting and downtime are runtime failures. The generalisation was wrong; the
    # configuration it produced was right. Recorded this way round because a comment that
    # overstates its evidence is the thing this file keeps being burned by.
    if fallback_models:
        # `model` and `models` are alternatives, not siblings: sending both is ambiguous rather
        # than redundant, so the single-model key goes away when the array is present.
        body.pop("model", None)
        body["models"] = [model] + [m for m in fallback_models if m != model]
        log("  [%s] model fallback chain: %s (billed at whichever answers; `model_served` in "
            "diagnostics.json records which one did)" % (name, " -> ".join(body["models"])))
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
    elif streaming:
        # `stream_options` is meaningless without a stream, and some gateways 400 on it. An
        # unstreamed response carries `usage` in the body itself, so nothing is lost.
        body["stream_options"] = {"include_usage": True}
    # 🔴 OPENROUTER endpoint pinning, added 2026-08-13 (round 36). Igor gave a specific endpoint
    # UUID for gemini-3.7-flash - the same model is served by SIX OpenRouter endpoints across
    # Vertex Global (flex/normal/priority) and AI Studio (flex/normal/priority) at prices that
    # differ 18x from cheapest to most expensive. Without a pin OpenRouter's default routing picks
    # one and the plan cannot honestly print what a run will cost. WITH the pin the vendor path is
    # deterministic, which is also the point of the `distribution` split - Vertex Global's data
    # policy is enterprise-grade (prompts not used for model training), AI Studio's is not, and a
    # channel whose data policy varies per-request is a policy nobody agreed to. The field maps 1:1
    # to OpenRouter's documented `provider` request block (openrouter.ai/docs/features/provider-
    # routing): `only`/`order`/`ignore`/`allow_fallbacks`/`sort` are all passed through unchanged.
    # A wrong key returns a plain 400 from the vendor which is the right layer to fail at, not
    # here - re-validating a documented API in prose is how a stale registry rots.
    # 🔴🔴 STRIP THE ANNOTATIONS BEFORE THIS BLOCK IS SENT. Measured 2026-08-19 (R55): the whole
    # dict used to go out verbatim, and `orglm52` died in 0.1 s with
    # `HTTP 400 ... provider: Unrecognized key: "order_reason"`. That key was added one round
    # earlier as PROSE - a note explaining why the provider order is not price order - and it sat
    # in the same dict as the vendor's own parameters, so the vendor was asked to honour a comment.
    # The channel had been dead since the moment the note was written, and nothing caught it,
    # because the note was added AFTER that round's panel had already run.
    #
    # The paragraph above is still right about typos: a misspelt DOCUMENTED key should 400 at the
    # vendor, not be re-validated here against a list that rots. This is the other case - a key
    # that was never meant for the wire at all. Every other annotation in this registry already
    # marks itself with a leading underscore (`_added`, `_temporary`, `_provider_pinned`), so the
    # convention existed and this one key broke it. Honour the convention mechanically instead of
    # allow-listing the vendor's parameters, which would turn every new OpenRouter feature into a
    # false positive here.
    if provider_route:
        wire = {k: v for k, v in provider_route.items() if not k.startswith("_")}
        dropped = [k for k in provider_route if k.startswith("_")]
        if wire:
            body["provider"] = wire
        log("  [%s] provider pin: %s%s"
            % (name, ", ".join("%s=%s" % (k, v) for k, v in wire.items()),
               ("  (annotations not sent: %s)" % ", ".join(dropped)) if dropped else ""))
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
    # `is None`, not `or 8`: `max_calls: 0` is an owner saying «zero fetches», and `or` read
    # the strictest setting as no setting at all - the exact class the usd_ceiling comment
    # below already names (R73, codex named this instance).
    _mc = (ft or {}).get("max_calls")
    max_rounds = 8 if _mc is None else int(_mc)
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
        served = set()
        # 🔴 R47: `finish_reason` was on every final chunk and discarded, so a provider that CUT
        # the answer (finish_reason=length - the AOS round-40 nemotron review ends mid-heading)
        # was indistinguishable from a model that chose to stop. OpenRouter adds
        # `native_finish_reason`, the vendor's own spelling before normalisation; both kept.
        finish = {}
        if not streaming:
            # One response object instead of a frame sequence. Same five return values, so every
            # caller below is untouched - the tool loop, the spend guard and the telemetry cannot
            # tell the difference. See the block comment on `streaming` above for why MiMo needs
            # this: its streamed text is missing characters that its unstreamed text keeps.
            with urllib.request.urlopen(rq, timeout=timeout) as resp:
                ev = json.loads(resp.read().decode("utf-8", "replace"))
            if ev.get("error"):
                stream_error.append(ev["error"])
            if ev.get("model"):
                served.add(ev["model"])
            use = ev.get("usage") or {}
            for ch in ev.get("choices") or []:
                msg = ch.get("message") or {}
                if ch.get("finish_reason"):
                    finish["finish"] = ch["finish_reason"]
                if ch.get("native_finish_reason"):
                    finish["native"] = ch["native_finish_reason"]
                if msg.get("content"):
                    parts.append(msg["content"])
                for rk in ("reasoning", "reasoning_content"):
                    if msg.get(rk):
                        rchars += len(msg[rk])
                for an in (msg.get("annotations") or []):
                    u = ((an or {}).get("url")
                         or ((an or {}).get("url_citation") or {}).get("url"))
                    if u:
                        vendor_cites.append(u)
                # Whole calls, not deltas: `arguments` is already a complete string here, so it
                # is wrapped in a one-element list to match what the streaming path assembles.
                for i, tc in enumerate(msg.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    calls[tc.get("index", i)] = {"id": tc.get("id"),
                                                 "name": fn.get("name"),
                                                 "args": [fn.get("arguments") or ""]}
            return ("".join(parts), rchars, use,
                    [{"id": v["id"], "name": v["name"], "args": "".join(v["args"])}
                     for _, v in sorted(calls.items())],
                    sorted(served), finish)
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
                # 🔴🔴 WHAT THE VENDOR SAYS IT RAN, not what we asked for. qwen38max, reviewing
                # round 31: the whole release worries that a DOCUMENT could be redirected, while
                # at the inference layer - where the substitution would actually happen - the
                # harness is blind. Every verdict attaches to a channel label, and nothing checked
                # that the thing answering behind a router is the model that label names. So
                # "Kimi dialled its effort down" and "the router served something smaller" were
                # the same observation. Same rule as this round's other half, one layer up: judge
                # by what came BACK, never by what was sent.
                if ev.get("model"):
                    served.add(ev["model"])
                for ch in ev.get("choices") or []:
                    if ch.get("error"):
                        stream_error.append(ch["error"])
                    if ch.get("finish_reason"):
                        finish["finish"] = ch["finish_reason"]
                    if ch.get("native_finish_reason"):
                        finish["native"] = ch["native_finish_reason"]
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
                 for _, v in sorted(calls.items())],
                sorted(served), finish)

    vendor_cites, stream_error = [], []
    text_parts, reasoning_chars, usage = [], 0, {}
    served_models = set()
    opened, fetch_failures, fetches = [], [], 0
    tried = {}          # _fetch_key(url) -> first outcome; a repeat is answered, not re-fetched
    blocked_hosts = {}  # host -> consecutive failures; 2 retires the host for this review
    fetched_bytes = 0   # cumulative page text; the CALL budget does not bound this. See below.
    in_tot = out_tot = 0
    # 🔴🔴 THE COST WAS SUMMED FOR TOKENS AND OVERWRITTEN FOR DOLLARS, ON ADJACENT LINES.
    # `in_tot`/`out_tot` accumulated across tool rounds while the returned `usd` read
    # `usage.get("cost")` - and `usage` holds only the LAST round's object. Proven 2026-08-14
    # against OpenRouter's own log for AOS round 34: qwen38max billed five generations at
    # $0.0984 + $0.102 + $0.159 + $0.185 + $0.43, and this harness reported $0.4297 - the last
    # one, to four decimal places. The token column was right (in=334,444 against a row sum of
    # ~311,086 plus one row below the fold), so the two meters over one event disagreed and the
    # one spending money was the wrong one. That is this project's own round-38 rule, applied to
    # itself one field over.
    #
    # `usd_seen` is separate from `usd_tot > 0` on purpose: a channel that legitimately costs
    # zero (a free tier) and a channel that reported no price at all are different facts, and
    # collapsing them puts free channels into the "reports no price" list where nobody prices
    # them again.
    # 🔴 `cached_tot` EXISTS BECAUSE THE FIX FOR `usd` DID NOT LOOK AT ITS NEIGHBOURS.
    # orgemini37flash, reviewing this diff the day it was written: `cached_in_tokens` read
    # `usage.prompt_tokens_details.cached_tokens` from the LAST round's object while `in_tokens`
    # summed every round - so an 8-round review paired a multi-round denominator with a
    # single-round numerator and threw away seven rounds of cache hits. That is the SAME defect
    # as the one this release is named for, two lines below it in the same return dict, and it
    # survived because the round was framed as "fix the money field" instead of "find every
    # field read from `usage` after a loop that overwrites `usage`".
    usd_tot, usd_seen, rounds_done, cached_tot = 0.0, False, 0, 0
    # 🔴 R47: THE SAME DEFECT THIS BLOCK IS NAMED FOR, ONE FIELD FURTHER ALONG. `usd`, then
    # `cached_in_tokens`, and now `reasoning_tokens`: the record read it from `usage` - the LAST
    # round's object - while `reasoning_chars` right beside it summed every round. The AOS
    # round-40 nemotron review printed reasoning_tokens=26 next to reasoning_chars=4948, and the
    # 26 was read as "the knob is inert" until a 3-arm probe showed the knob moving (181 vs ~102)
    # and the 26 being the final tool round's thinking only. Summed like in_tot/out_tot now.
    reas_tot = 0
    # The most expensive single round SO FAR, used as the estimate of what the next one will cost.
    # Measured, not derived from a price table - same rule as the ceiling itself.
    usd_max_round = 0.0
    sg = spend_guard or {}
    usd_ceiling = sg.get("max_usd_per_review")
    # 🔴 `is not None`, NOT truthiness. agy31pro, reviewing this diff the day it was written:
    # `max_usd_per_review: 0` - the value an owner would write to mean "this channel must never
    # spend anything" - is falsy, so the old `if usd_ceiling` read the strictest possible setting
    # as NO SETTING AT ALL and disabled the guard. Same class as the `or 8` fallbacks elsewhere in
    # this file: a legitimate zero and an absent key are different facts, and only `is None`
    # distinguishes them. Nothing shipped with a zero, so this was a trap for the next person.
    usd_ceiling = float(usd_ceiling) if usd_ceiling is not None else None
    # 🔴 R74 (agy31pro, R73): the in-loop breaker arms only after `usd_seen` - the FIRST round
    # always ran and billed, so a ceiling of 0 («spend nothing») failed exactly when it was
    # strictest. A non-positive ceiling now refuses DISPATCH: the only spend of zero is no call.
    if usd_ceiling is not None and usd_ceiling <= 0:
        return {"channel": name, "ok": False,
                "error": "spend_guard max_usd_per_review=%g means SPEND NOTHING - the channel "
                         "was not dispatched (the in-loop breaker can only arm after the first "
                         "billed round, which would already have spent money)." % usd_ceiling}
    spend_stop = False
    # 🔴 THE FORCED-ANSWER ROUND HAD NO EXIT, and that was true of the fetch-budget path too since
    # it was written. `body.pop("tools")` is what normally stops the model asking for another page,
    # so in practice the next turn is the answer and the loop ends - but "in practice" is doing all
    # the work there, and it is doing it on the branch that exists because money ran out. A vendor
    # that echoes a tool call anyway (or any future transport that ignores a removed `tools` key)
    # would re-enter the same branch every iteration, paying for the whole conversation each time,
    # up to max_rounds. Found by the offline probe for this round rather than by a bill: the fake
    # transport did exactly that, which is the point of testing a stop condition with something
    # that does not cooperate.
    forced_final = False
    start = time.time()

    def _telemetry():
        """Everything measured so far, so a failure path can report it instead of dropping it."""
        return {"in_tokens": in_tot or None, "out_tokens": out_tot or None,
                "usd": round(usd_tot, 6) if usd_seen else None,
                "usd_rounds": rounds_done if usd_seen else None,
                "fetches": fetches if fetch_on else None,
                "retries": retries_used or None,
                "seconds": round(time.time() - start, 1)}

    last_finish = {}
    # 🔴 A5 (R70, reshaped R74): a single 429 used to kill the WHOLE review on this transport,
    # while the Spark path had retried exactly this class for weeks. The retry is safe by
    # construction, twice over: `body` is appended to only AFTER a successful round, so the
    # failed request is re-sent verbatim; and an HTTPError is raised at the door - status line
    # read, no completion delivered - so a 429 billed nothing and a 5xx delivered nothing to
    # bill for HERE. Whether the vendor metered work upstream before failing is NOT knowable
    # from this door, so the log no longer claims «billed nothing» for a 5xx (R73, codex).
    # Deliberately NOT retried: timeouts and mid-stream drops - an unstreamed call that timed
    # out may have finished, and billed, on the vendor's side (they surface as URLError /
    # socket errors, which this handler does not catch). R74, six R73 reviewers converging:
    # the budget is per CALL, not per review - the old per-review budget let a burst in round
    # 1 make round 5's single 503 fatal after every paid fetch; and the forced-final call,
    # the most expensive request of the review, sat outside the loop entirely. Both now go
    # through _stream_with_retry.
    retries_used = 0

    def _transient_stream_err(err):
        """True when an SSE error event names a retryable status (429 / 5xx)."""
        if not isinstance(err, dict):
            return False
        for k in ("code", "status"):
            try:
                c = int(err.get(k))
            except (TypeError, ValueError):
                continue
            if c == 429 or 500 <= c < 600:
                return True
        return False

    def _stream_with_retry(b):
        nonlocal retries_used, in_tot, cached_tot, usd_tot, usd_seen, usd_max_round
        retries_left = 2
        while True:
            err_base, cite_base = len(stream_error), len(vendor_cites)
            try:
                out = _stream_once(b)
            except urllib.error.HTTPError as e:
                if retries_left <= 0 or (e.code != 429 and e.code < 500):
                    raise
                retries_left -= 1
                retries_used += 1
                ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                wait = 2 ** (2 - retries_left)          # 2s, then 4s
                if ra:
                    try:
                        wait = max(wait, min(int(ra), 60))   # same 60s cap as A3
                    except (ValueError, TypeError):
                        pass
                log("  [%s] HTTP %d%s - transient; retrying the same request in %ds "
                    "(%d retr%s left for this call). The conversation is unchanged; the "
                    "rejected attempt delivered nothing to bill for at this door."
                    % (name, e.code,
                       (" (Retry-After: %s)" % ra) if ra else "",
                       wait, retries_left, "y" if retries_left == 1 else "ies"))
                time.sleep(wait)
                continue
            # 🔴 R75 (grokbuild, R73): OpenRouter's documented mid-stream failure is HTTP 200
            # plus an SSE `error` event - the door handler above never sees it, so one such
            # event used to cost the whole seat. Retried ONLY when the dead stream delivered
            # NOTHING: no content, no reasoning, no tool call, no vendor citation, and no
            # completion_tokens in whatever usage arrived. Under that condition a re-send can
            # double-pay for the INPUT at most (said plainly below) - the moment any completion
            # token was delivered, a retry could double-bill generation, so it stays fatal.
            # Deliberately NOT retried either: mid-stream socket errors and timeouts - there
            # the vendor may still be generating (and billing) server-side, and only its own
            # error event proves it stopped.
            chunk, rch, use_, calls_ = out[0], out[1], out[2] or {}, out[3]
            new_errs = stream_error[err_base:]
            if (new_errs and retries_left > 0
                    and not chunk and not rch and not calls_
                    and len(vendor_cites) == cite_base
                    and not (use_.get("completion_tokens") or 0)
                    and any(_transient_stream_err(e) for e in new_errs)):
                del stream_error[err_base:]
                # The discarded attempt's INPUT was billed if the vendor said so - fold any
                # reported usage into the meters rather than dropping it with the attempt
                # (the spend-vs-report split is the R41 defect, not risked twice).
                in_tot += use_.get("prompt_tokens") or 0
                cached_tot += ((use_.get("prompt_tokens_details") or {})
                               .get("cached_tokens") or 0)
                _dc = use_.get("cost")
                if _dc is not None:
                    try:
                        _dc = float(_dc)
                        usd_tot += _dc
                        usd_max_round = max(usd_max_round, _dc)
                        usd_seen = True
                    except (TypeError, ValueError):
                        pass
                retries_left -= 1
                retries_used += 1
                wait = 2 ** (2 - retries_left)
                log("  [%s] provider error event mid-stream (HTTP 200) with ZERO completion "
                    "tokens delivered - retrying the same request in %ds (%d retr%s left for "
                    "this call). The re-send can re-bill the prompt, never the generation: "
                    "nothing was generated." % (name, wait, retries_left,
                                                "y" if retries_left == 1 else "ies"))
                time.sleep(wait)
                continue
            return out

    try:
        for _round in range(max_rounds + 1):
            chunk, rch, use, calls, served, _fin = _stream_with_retry(body)
            rounds_done += 1
            # The FINAL round's finish_reason is the one that ended the answer; a tool round's
            # `tool_calls` value is overwritten by whatever the last generation reports.
            if _fin:
                last_finish = _fin
            served_models.update(served)
            reasoning_chars += rch
            in_tot += (use or {}).get("prompt_tokens") or 0
            out_tot += (use or {}).get("completion_tokens") or 0
            cached_tot += (((use or {}).get("prompt_tokens_details") or {})
                           .get("cached_tokens") or 0)
            reas_tot += (((use or {}).get("completion_tokens_details") or {})
                         .get("reasoning_tokens") or 0)
            _c = (use or {}).get("cost")
            if _c is not None:
                try:
                    _c = float(_c)
                    usd_tot += _c
                    usd_max_round = max(usd_max_round, _c)
                    usd_seen = True
                except (TypeError, ValueError):
                    pass
            usage = use or usage
            if chunk:
                text_parts.append(chunk)
            if forced_final:
                break     # tools were already removed and the answer demanded; this is it
            # 🔴 THE CIRCUIT BREAKER, and it is deliberately NOT a depth cap. Igor's standing rule
            # on this harness is «фокус на качество, а значит токенов на размышление урезать не
            # надо», so nothing here trims reasoning, lowers effort or shortens the answer: what it
            # stops is FURTHER PAGE FETCHING, whose cost is quadratic because every later round
            # re-sends every page already read. The round then does one final no-tools turn and
            # returns a real review. Measured motivation, AOS round 34: eight rounds on a 57.9 KB
            # brief walked the billed input from 814K to 7.41M tokens and $12.08, and the ninth call
            # met the key's monthly cap - so the uncapped path did not buy a deeper review, it
            # bought no review at all plus four dead channels in the next round.
            # 🔴 THE TRIGGER RESERVES HEADROOM FOR THE FINAL CALL, because a ceiling tested only
            # against money ALREADY SPENT is not a ceiling. goog37flash, reviewing this diff the
            # day it was written, did the arithmetic: rounds costing $3.90, then one at $2.10
            # (crossing), then the forced answer at $2.10 = $8.10 against a $4.00 setting - over
            # 100% overshoot. The estimate of "what the next call will cost" is the largest round
            # THIS run has actually billed, not a price from a config file: same rule as the
            # ceiling itself, and it is conservative because the cost per round grows as the
            # conversation grows. It cannot be exact - the only honest bound is "ceiling plus at
            # most one more round" - and the plan says so rather than promising a hard number.
            if (usd_ceiling is not None and usd_seen and not spend_stop
                    and usd_tot + usd_max_round >= usd_ceiling):
                spend_stop = True
                log("  [%s] ⛔ SPEND CEILING: $%.4f billed so far and the largest round cost "
                    "$%.4f, so the next one would risk crossing the $%.2f ceiling. No further "
                    "page fetches; asking for the answer as it stands. This is OUR limit, not "
                    "the vendor's - raise spend_guard.max_usd_per_review in channels.json if "
                    "this review is worth more."
                    % (name, usd_tot, usd_max_round, usd_ceiling))
            if not calls or not fetch_on or fetches >= max_rounds or spend_stop:
                if calls and (fetches >= max_rounds or spend_stop):
                    if not spend_stop:
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
                    stop_msg = ("REFUSED: the spending ceiling for this review has been reached, "
                                "so no further pages may be opened. This is the harness's limit "
                                "and says nothing about the page. Answer from what you already "
                                "have, and say plainly which questions you could not settle."
                                if spend_stop else
                                "REFUSED: the fetch budget for this review is spent. Answer from "
                                "what you already have and mark anything unverified.")
                    for i, c in enumerate(calls):
                        body["messages"].append(
                            {"role": "tool", "tool_call_id": c["id"] or ("call_%d" % i),
                             "content": stop_msg})
                    # 🔴🔴 R59: KEEP `tools` in the body, do NOT pop, and set tool_choice="none".
                    # The R59 panel (ORGLM52 with primary-source citations, corroborated by
                    # ORGEMINI37FLASH and SPARK12CONT) reversed the R59 initial draft that popped
                    # tools before setting tool_choice="none". Three reasons and each has a
                    # measured provider behind it:
                    #  (a) xAI's backend rejects `tools:[]` + `tool_choice:"none"` with
                    #      `A tool_choice was set on the request but no tools were specified`
                    #      (400). Popping is worse than xAI-safe; it just moves the failure.
                    #  (b) Anthropic (via OpenRouter, and directly) rejects any request whose
                    #      history contains `tool_use`/`tool_result` blocks while `tools` is
                    #      absent - and by the time this branch fires, the conversation history is
                    #      full of them. Popping guarantees a 400 on that route.
                    #  (c) Popping busts the prompt-cache prefix. `tool_choice="none"` while
                    #      `tools` stays is the cache-friendly form the OpenRouter SDK itself now
                    #      uses. Free-tier channels bill per search, not per token, but paid ones
                    #      pay for the prefix on every round.
                    # The R55 failure was ornemotron3ultra returning finish_reason=tool_calls on
                    # the forced-final turn AFTER tools were popped - conformant vendors would
                    # honour the pop, but Baseten's serving layer did not. Keeping tools plus an
                    # explicit tool_choice="none" is the belt-and-braces form: the API-level
                    # instruction is unambiguous, and providers who need it heard it. Nothing here
                    # ships a `tools:[]` payload at any point.
                    body["tool_choice"] = "none"
                    forced_final = True
                    # 🔴 R64 FIX (AGY31PRO / AGY37FLASH / GOOG37FLASH panel, 2026-08-23):
                    # DO NOT `continue`. The outer for-loop is `range(max_rounds + 1)`, and
                    # `continue` on the last iteration (`_round == max_rounds`) exhausts the
                    # loop SILENTLY. The forced-final `_stream_once` - which is the whole point
                    # of setting `tool_choice="none"` and telling the model «answer as it
                    # stands» - never runs, `text_parts[-1]` holds the tool-call chunk, and the
                    # channel returns `ok=False, END MARKER ABSENT` after paying for the full
                    # fetch budget. Three panel channels with code access converged on this in
                    # R64; my prior refutation was arithmetically wrong (`range(max_rounds+1)`
                    # has iterations 0..max_rounds, not an extra iteration beyond max_rounds).
                    # Instead of extending the range (which just shifts the boundary by 1 and
                    # fails the same way if fetches burst on the new last iteration), do the
                    # forced-final round INLINE: no dependence on outer-loop iterations.
                    # R74: through the SAME transient retry as every other call - six of twelve
                    # R73 reviewers independently flagged that a lone 429 here discarded the
                    # whole paid review, because this call sat outside the A5 loop.
                    chunk_f, rch_f, use_f, _calls_f, served_f, _fin_f = _stream_with_retry(body)
                    rounds_done += 1
                    if _fin_f:
                        last_finish = _fin_f
                    served_models.update(served_f)
                    reasoning_chars += rch_f
                    in_tot += (use_f or {}).get("prompt_tokens") or 0
                    out_tot += (use_f or {}).get("completion_tokens") or 0
                    cached_tot += (((use_f or {}).get("prompt_tokens_details") or {})
                                   .get("cached_tokens") or 0)
                    reas_tot += (((use_f or {}).get("completion_tokens_details") or {})
                                 .get("reasoning_tokens") or 0)
                    _c_f = (use_f or {}).get("cost")
                    if _c_f is not None:
                        try:
                            _c_f = float(_c_f)
                            usd_tot += _c_f
                            usd_max_round = max(usd_max_round, _c_f)
                            usd_seen = True
                        except (TypeError, ValueError):
                            pass
                    usage = use_f or usage
                    if chunk_f:
                        text_parts.append(chunk_f)
                    break
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
                elif fetches >= max_rounds and _fetch_key(url) not in tried:
                    # 🔴 MEASURED: the console printed `fetch 9/8`. The budget was checked once
                    # per ROUND, but one assistant turn can emit several tool calls, and every
                    # call in that batch ran. A ceiling tested outside the loop it is meant to
                    # bound is not a ceiling. Checked per CALL now.
                    result = ("REFUSED: the fetch budget for this review (%d) is spent. Answer "
                              "from what you have and mark anything unverified." % max_rounds)
                elif _fetch_key(url) in tried:
                    # 🔴 MEASURED ON THE FIRST LIVE ROUND, 2026-08-07: qwen spent fetches 6, 7 and
                    # 8 on ONE unreachable URL, re-requesting it verbatim each time. A budget that
                    # counts attempts rather than distinct pages lets a single broken link consume
                    # the whole allowance, and the model has no way to know it is repeating itself
                    # because each refusal looks new. Serve the first outcome again, say plainly
                    # that this is a repeat, and DO NOT spend a fetch on it.
                    #
                    # 🔴 KEYED ON _fetch_key SINCE 2026-08-14 (round 38), NOT ON THE RAW STRING,
                    # and the reason is that the 2026-08-07 fix above only caught VERBATIM
                    # repeats. Measured on this channel's first live run: the model fetched
                    # `.../provider-selection` and later `.../provider-selection#base-slug-matching`,
                    # then `.../web-search` and `.../web-search#default-behavior`. A fragment is
                    # processed client-side and is NEVER put on the wire (RFC 3986), so those were
                    # two HTTP requests to the origin and four entries in a dict keyed on the raw
                    # string. Both came back byte-identical (22 328 and 17 279 chars), spent 2 of
                    # the 8 budget slots, and - the expensive part - added two more tool rounds,
                    # each of which re-sent the 400 KB page already in the conversation. The run
                    # billed $1.76.
                    #
                    # The tell that this was a bug and not a policy: `opened` was ALREADY
                    # normalised through _norm_url, so the same run reported fetched_by_us=6 while
                    # the log showed 8 fetches. Two counters disagreed and the one spending money
                    # was the naive one.
                    result = ("ALREADY TRIED THIS URL in this review - here is the same result, "
                              "and it did not cost you a fetch. Do not request it again; either "
                              "use a different source or say the page could not be opened. If you "
                              "were trying to reach a particular SECTION, note that a #fragment "
                              "never reaches the server: you already have the whole page, so read "
                              "the section out of the text above.\n\n"
                              + tried[_fetch_key(url)])
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
                    tried[_fetch_key(url)] = result[:2000] if len(result) > 2000 else result
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
    # 🔴🔴 A FAILURE PATH THAT DROPS THE METER MAKES THE EXPENSIVE ROUNDS THE INVISIBLE ONES.
    # Both handlers used to return {channel, ok, error} and nothing else, so everything measured
    # before the exception - tokens, dollars, pages opened, seconds - was discarded at exactly the
    # moment it mattered most. AOS round 34, 2026-08-14: orgpt56terrapro completed EIGHT paid
    # generations totalling $12.08, then met the key's monthly cap on the ninth; the round summary
    # listed it under «these channels report no price» and put the round's cost at $0.9250. The
    # bill was ~$13. A harness that reports a spend of zero for the run that emptied the budget is
    # not merely incomplete - it actively argues the panel is cheap.
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
        if e.code == 429 and retry_after:
            log("  [%s] 429 RATE LIMITED (Retry-After: %s)" % (name, retry_after))
        # The provider NAME, not a literal: this function serves every OAI-protocol vendor,
        # and «from OpenRouter» on a mimo-direct failure blamed the wrong party (agy37flash,
        # R73) - the same instance-for-class mistake the key_env comment above records.
        out = {"channel": name, "ok": False,
               "error": "HTTP %s from %s: %s" % (e.code, provider or "provider", detail)}
        if retry_after:
            out["retry_after"] = retry_after
        out.update(_telemetry())
        if usd_seen and usd_tot:
            log("  [%s] ⚠ this channel had already billed $%.4f before it failed - that money is "
                "spent and is counted in the round total below." % (name, usd_tot))
        return out
    except Exception as e:
        out = {"channel": name, "ok": False, "error": "stream failed: %r" % (e,)}
        out.update(_telemetry())
        return out
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
    # 🔴🔴 DID THE MODEL WE ASKED FOR ACTUALLY ANSWER? qwen38max named the blind spot: every
    # verdict in this harness attaches to a channel LABEL, and nothing verified that the thing
    # answering behind a router is the model that label names. "Kimi lowered its effort" and "the
    # router served something smaller" produced identical evidence. The response carries a `model`
    # field on every chunk; comparing it to what we asked for costs nothing and is the same rule
    # as the rest of this release - judge by what came BACK.
    served = [m for m in served_models if m]
    # 🔴🔴 A DECLARED FALLBACK IS NOT A SUBSTITUTION, AND THIS GUARD CALLED IT ONE ON THE VERY
    # FIRST ROUND `fallback_models` EXISTED. The r54 panel ran with orglm52 leading on the free
    # tier; the free tier was rate-limited, the paid model answered exactly as configured - and
    # this check marked the channel NOT OK with «MODEL SUBSTITUTION», because it compares the
    # served model against `model` alone and knows nothing about a chain.
    #
    # That is the third false positive this one change produced (doctor's provider-price check
    # made two), and the same lesson each time: a guard written before a feature existed does not
    # become wrong when the feature arrives - it becomes LOUD ABOUT THE WRONG THING, which is
    # worse, because «a false positive in a safety gate teaches you to pass the override by
    # reflex». Every round where the free tier is down would have shipped a scary red warning
    # about correct behaviour, until nobody read the line at all - and the line's real job is to
    # catch the `--set spark12cont=...` case, where a whole round silently ran on another model.
    #
    # The information is KEPT and only the alarm is dropped: an expected fallback still says which
    # model answered, in the notes, because that is the fact a reader needs either way.
    # Collected here and merged into `note` where that list is created, ~150 lines below: this
    # function builds `warn` early and `note` late, and appending to `note` here would be wiped by
    # its own initialiser. Found by reading the scope rather than by the traceback it would have
    # produced on the first fallback round - which is the round this exists for.
    fallback_notes = []
    expected = set(fallback_models or [])
    if served and model and not any(m == model for m in served):
        if expected and all(m in expected for m in served):
            fallback_notes.append(
                "FALLBACK SERVED: %r did not answer and the declared fallback %s did, "
                "which is this channel's configured behaviour. The findings below belong "
                "to THAT model - weigh them as its work, not the primary's."
                % (model, ", ".join(repr(m) for m in served)))
        else:
            warn.append("MODEL SUBSTITUTION: we asked for %r and the provider's own response says "
                        "it served %s, which this channel does NOT declare as a fallback. Every "
                        "finding below belongs to THAT model, not to this channel's name."
                        % (model, ", ".join(repr(m) for m in served)))
    # 🔴🔴 A CEILING THAT COULD NOT BE ENFORCED MUST SAY SO. spark12cont, reviewing this diff the
    # day it was written, called the guard "a dead switch whenever `cost` is missing": the whole
    # mechanism depends on the vendor returning `usage.cost`, there is no fallback, and if that
    # field ever stops arriving the loop runs to max_rounds with quadratic context while the
    # report shows `usd: null` and files the channel under "reports no price" - the original
    # failure again, wearing a different response shape and quieter than before. It also named the
    # reason this could ship: the offline probe returns `cost` on every round, so the missing-meter
    # path was never executed. There is no honest automatic fallback - a token estimate needs a
    # price table, which is the thing this design refuses to trust - so the remedy is not to
    # enforce silently but to REFUSE TO CLAIM enforcement. A declared ceiling with no meter behind
    # it is a warning on the channel, which makes the round PROBLEM rather than OK.
    if usd_ceiling is not None and not usd_seen:
        warn.append("SPEND CEILING NOT ENFORCEABLE: this channel declares a $%.2f ceiling, but "
                    "the vendor returned no cost meter on any of the %d round(s), so nothing "
                    "bounded this run. Treat its price as unknown, not as zero."
                    % (usd_ceiling, rounds_done))
    if marker and not _marker_on_last_line(text, marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
        # 🔴 R47: the marker warning stood ALONE on the AOS round-40 nemotron review - 32 KB of
        # text ending mid-heading - because the finish_reason that would have named the cutter
        # was never read off the stream. When the provider itself says why generation stopped,
        # that reason now stands beside the symptom.
        _fr = last_finish.get("finish")
        if _fr == "length":
            warn.append(
                "PROVIDER LENGTH CEILING CUT THE ANSWER - finish_reason=length: the provider "
                "stopped the final generation at %s output tokens although this channel asked "
                "for max_tokens=%s. The binding ceiling is the PROVIDER's, so raising ours "
                "cannot help; a :free variant usually carries a smaller cap than the paid "
                "endpoint of the same model."
                % ((usage or {}).get("completion_tokens"), max_tokens))
        elif _fr and _fr not in ("stop", "tool_calls"):
            warn.append(
                "PROVIDER ENDED THE ANSWER EARLY - finish_reason=%r%s. The text stops where "
                "the provider stopped it, not where the model finished."
                % (_fr, (" (native: %r)" % last_finish["native"])
                   if last_finish.get("native") else ""))
    if not text.strip():
        # 🔴🔴 THE HARNESS HELD THE REASON AND PRINTED «no reason». R42's mimo25pro round returned
        # zero bytes after 766 seconds and this branch said «the connection produced no content and
        # gave no reason», while the record it was writing carried out_tokens 60000 - EXACTLY
        # max_tokens - beside reasoning_tokens 60002. The trace had eaten the entire budget and the
        # answer never started. Every fact needed to say so was already collected, three fields
        # apart, and nothing joined them; the stock advice printed underneath was «lower --tier»,
        # which on that channel changes nothing at all. Same family as R41's two-counters-over-one-
        # event: the evidence existed and the diagnosis did not read it.
        #
        # The xAI path has had a rich version of this since R29 (see call_xai_responses, which
        # prints status, incomplete_reason, output-item kinds and the reasoning share). It was
        # written for the channel that had failed, not for the CLASS - so the identical failure
        # arrived on a sibling transport nine days later and met the generic sentence again.
        # 🔴 THE LAST ROUND'S NUMBERS, NOT THE SUM. `max_tokens` bounds ONE call; `out_tot` is
        # accumulated across tool rounds and legitimately exceeds it - ordeepseekv4pro billed
        # 88 530 output tokens against a 60 000 cap in R42 and was perfectly healthy. Comparing
        # the sum against a per-call ceiling would fire on every fetch-heavy round that happened
        # to end empty, which is the false-positive-in-a-safety-gate class this project already
        # names: a gate that cries wolf teaches you to walk past the whole category. `usage` is
        # the LAST round's block (`usage = use or usage` in the loop above), which is the only
        # one the ceiling can have bitten.
        cap = max_tokens or 64000
        last = usage or {}
        reas = (last.get("completion_tokens_details") or {}).get("reasoning_tokens")
        spent = last.get("completion_tokens") or 0
        starved = spent >= cap or (isinstance(reas, int) and reas >= cap)
        if stream_error:
            e0 = stream_error[0]
            msg = e0.get("message") if isinstance(e0, dict) else str(e0)
            code = e0.get("code") if isinstance(e0, dict) else None
            warn.append("PROVIDER ERROR MID-STREAM (HTTP 200, error event): %s%s"
                        % (msg or e0, (" [code %s]" % code) if code else ""))
        elif starved:
            warn.append(
                "OUTPUT BUDGET EXHAUSTED BY REASONING - this is not an empty response, it is a "
                "TRUNCATED one. The reasoning trace used %s of this channel's %s-token max_tokens "
                "(output tokens counted: %s), leaving nothing for the answer. On this protocol "
                "max_tokens covers reasoning AND answer together. Fixes, in order: raise "
                "channels.json -> %s.max_tokens toward the vendor's max_completion_tokens; if it "
                "is already there, the brief is too hard for this channel and splitting it is the "
                "only remedy. Lowering the tier does NOT help - depth is at each vendor's ceiling "
                "by design since 2026-08-15." % (reas, cap, spent, name))
        elif last_finish.get("finish") == "tool_calls":
            # 🔴🔴 «GAVE NO REASON» OVER A RESPONSE THAT GAVE A REASON, IN A FIELD WE NOW CAPTURE.
            #
            # R43 AOS panel, ornemotron3ultra: bytes 0, 12 billed rounds, 1 146 168 input tokens,
            # `finish_reason: "tool_calls"` sitting in the same record - and the warning said the
            # connection "gave no reason", then reassured the reader with "Checked and NOT the
            # cause: the budget was not exhausted (71 of 65536)". A reader who trusts "checked and
            # not the cause" stops investigating, so the sentence did not merely fail to help, it
            # actively closed the enquiry. (R47 taught the harness to CAPTURE finish_reason; this
            # is the second half - the diagnosis has to READ what the capture put there. Capturing
            # a field and not consulting it is the same shape as recording `vendor_opened` and
            # printing a dash.)
            #
            # What the state actually means: the model ended its turn asking for another tool
            # call, and the loop had stopped granting them - normally because the fetch budget was
            # reached and the harness said "answer as it stands". The model answered that request
            # with more tool calls instead of prose. Nothing was truncated and nothing errored;
            # the conversation simply never produced an answer turn.
            warn.append(
                "NO ANSWER TURN - finish_reason=tool_calls after %d round(s): the model ended its "
                "last turn asking for ANOTHER tool call rather than writing prose, and the loop "
                "had already stopped granting them (fetch budget %s). This is not truncation and "
                "not a provider error - the budget was NOT exhausted (%s of %s output tokens on "
                "the final round). Fixes, in order: raise channels.json -> %s.fetch_tool.max_calls "
                "so the model reaches its answer inside the budget; or narrow the brief so it "
                "needs fewer pages. Raising max_tokens does nothing here."
                % (rounds_done, max_rounds, spent, cap or "no declared cap", name))
        else:
            warn.append("EMPTY OUTPUT from stream, and the provider sent no error event - the "
                        "connection produced no content%s. Checked and NOT the "
                        "cause: the budget was not exhausted (%s of %s output tokens used)."
                        % ((" and gave no reason - finish_reason was absent from every chunk"
                            if not last_finish.get("finish")
                            else " (finish_reason=%r, which names no known cause for an empty "
                                 "answer - report it)" % last_finish.get("finish")),
                           spent, cap or "no declared cap"))
    # 🔴 THE MODEL WROTE ITS TOOL CALLS AS TEXT, AND TWO INSTRUMENTS BOTH MISDESCRIBED IT.
    #
    # R45 AOS panel, orglm52: the saved answer is 1 166 B whose entire body after the harness
    # banner is `<tool_call>web_search(query="...</arg_value></tool_call>` twice over - unparsed
    # function-call syntax with mismatched tags, not prose. The round fired TWO notes about it and
    # neither named it: "SHORT ANSWER (313 chars)" treated it as a terse review, and the text-
    # integrity check attributed the unbalanced parentheses to «the vendor's transport dropped
    # characters in flight» - the R44 corruption story, applied to characters the model itself
    # wrote. Two detectors, two confident wrong causes, one unread artifact.
    #
    # Same root as the tool_calls branch above and it deserves the same words: the model was still
    # trying to call tools after the loop stopped granting them. Checked on the answer text
    # because on this path the markup arrives as CONTENT, so no finish_reason can reveal it.
    if text.strip() and re.search(r"</?(tool_call|function_call|invoke)\b", text[:4000]):
        warn.append(
            "TOOL-CALL MARKUP IN PLACE OF AN ANSWER - the text begins with raw `<tool_call>` "
            "syntax rather than prose, which means the model was still trying to call tools when "
            "the loop asked it to answer. Read the file before counting this channel: any "
            "bracket-balance or short-answer note on it describes the MARKUP, not damage in "
            "transit and not a terse review.")
    # Seeded rather than empty: `fallback_notes` was collected ~150 lines above, where `note` does
    # not exist yet. Assigning `[]` here would have discarded it silently.
    note = list(fallback_notes)
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
    # 🔴 R46 (2026-08-16), from Igor reading the R45 table: «Не прислал ссылки, возможно у них
    # проблемы с интернетом, может не настроен поиск у них?» Neither. ordeepseekv4pro and
    # ornemotron3ultra had search AND a fetch tool; the Exa plugin even attached 5 result
    # annotations each. The models simply wrote reviews containing ZERO URLs and never called
    # fetch_url - and the only trace was `fetches: null` plus an annotation count nobody
    # surfaced, so a reader's first theory was a broken internet. The fact was collected in
    # three fields and connected in none. A NOTE, not a warning: an ungrounded design review is
    # often a legitimate answer, and the reader - not the exit code - decides what it is worth.
    if text.strip() and not opened and not n_cited and not wsu.get("tool_usage") \
            and (fetch_on or (web or {}).get("enabled")):
        vc = len(set(vendor_cites))
        note.append(
            "ZERO WEB GROUNDING IN THE TEXT: web access was available and the answer cites no "
            "URLs and fetched no pages%s. This is not a connectivity failure - it is the model "
            "answering from the brief and its training data. Fine for a pure design review; "
            "treat every dated or vendor-specific claim in it as unverified."
            % ((" (the search plugin did attach %d result annotation(s), which the model saw as "
                "excerpts and cited none of)" % vc) if vc else ""))
    return {"channel": name, "ok": not warn, "text": text, "seconds": round(secs, 1),
            "bytes": len(text.encode("utf-8")), "exit": 0, "model": model,
            # What we ASKED for is `model`; what the provider SAYS it ran is this. Two fields on
            # purpose - collapsing them into one is how the substitution stayed invisible.
            "model_served": served or None,
            "provider": provider,
            # Why the FINAL generation stopped, in the provider's own words. "stop" is a model
            # that finished; "length" is a provider that cut it; anything else is the provider
            # ending the turn for its own reason. R47 - was discarded on every chunk before.
            "finish_reason": last_finish.get("finish"),
            "native_finish_reason": (last_finish.get("native")
                                     if last_finish.get("native") != last_finish.get("finish")
                                     else None),
            "in_tokens": in_tot or usage.get("prompt_tokens"),
            "out_tokens": out_tot or usage.get("completion_tokens"),
            # 🔴 THE PRICE WAS ON THE WIRE ALL ALONG AND WE THREW IT AWAY. `usage.include: true`
            # has been sent on this transport since the channel was built; OpenRouter answers with
            # `cost`, `cost_details` and `prompt_tokens_details.cached_tokens`, and none of it was
            # read. Six metered channels reported tokens and no dollars, so "what did this round
            # cost" was unanswerable for everything except xAI - which we praised for being the
            # only channel that prices its own call. It was not; it was the only one we asked.
            # Found 2026-08-08 by dumping the raw usage object instead of the fields we expected.
            #
            # 🔴 AND THEN READ FROM THE WRONG VARIABLE FOR SIX DAYS. `usage` is the LAST round's
            # object; on a fetch-heavy review the round before it cost just as much. Summed since
            # 2026-08-14 - see the usd_tot comment above for the OpenRouter log that proves the
            # under-report factor was 2.3x on qwen38max and infinite on the channel that 403'd.
            "usd": round(usd_tot, 6) if usd_seen else None,
            "usd_rounds": rounds_done if usd_seen else None,
            "spend_stopped": True if spend_stop else None,
            "cached_in_tokens": cached_tot or None,
            # 🔴🔴 THIS KEY WAS MISSING ENTIRELY, ON SIX OF ELEVEN CHANNELS, AND THE REPORT PRINTED
            # A COLUMN FOR IT. `report.py` and the run summary both read `r.get("reasoning_tokens")`
            # - which was None here forever, indistinguishable from a model that did no thinking,
            # while `reasoning_chars` sat right beside it at 842 and 1056. Found 2026-08-08 by
            # `echocheck.py`, the first thing that ever tried to USE the number rather than print
            # it. Note the shape: this transport is OpenAI /chat/completions, so the details live
            # under `completion_tokens_details`; `output_tokens_details` is the Responses API and
            # is correct for xAI two hundred lines below. One project, two shapes, one habit of
            # copying the key that worked last time.
            # 🔴 R47: SUMMED across tool rounds (see reas_tot above) - the last-round read stood
            # here through TWO fixes of the identical defect on neighbouring fields.
            "reasoning_tokens": reas_tot or (usage.get("completion_tokens_details") or {})
                                            .get("reasoning_tokens"),
            # `summed=` keeps this meter and the field above reading the same event. Without it
            # they were two counters over one quantity, disagreeing by up to 4.4x - see
            # meter_source's docstring for the nine measured pairs.
            "reasoning_meter": meter_source(usage, "completion_tokens_details",
                                            "reasoning_tokens", summed=reas_tot or None),
            "reasoning_chars": reasoning_chars or None,
            "vendor_searches": wsu.get("tool_usage"),
            "vendor_pages": wsu.get("page_usage"),
            "vendor_citations": len(set(vendor_cites)) or None,
            "provider_error": stream_error[0] if stream_error else None,
            # Real grounding evidence for a channel that had none: WE ran these fetches, so this
            # is a list rather than an inference. Codex cannot produce this at all.
            # 🔴 0 when the tool was OFFERED and never called, None when it was not offered.
            # `fetches or None` collapsed those two facts into one null, and R46 measured the
            # cost: the R45 diagnostics could not say whether ordeepseekv4pro even HAD its fetch
            # tool, which is the first question Igor's «может не настроен поиск?» asks. Same
            # class as R44's two-counters-over-one-event, in the quiet direction.
            "fetches": fetches if fetch_on else None,
            # R75: the SUCCESS path never carried `retries` - only the failure paths did, via
            # _telemetry(). A retry that saves the seat is the COMMON case, and it was the
            # invisible one (the R48 shape: a field most return sites promise, missing from
            # the one that runs most often). Found by the R75 suite, not by a review.
            "retries": retries_used or None,
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
    sent_budget = max_tokens or 60000
    body = {"model": model,
            # The system layer rides in front of the brief, exactly as it does for codex and agy.
            # A brief framed one way is refused and framed another way is answered, so the framing
            # has to reach every channel that can refuse.
            "input": [{"role": "user", "content": _with_system(brief, system)}],
            "max_output_tokens": sent_budget,
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
        # Door-status retry only: once the stream is open, a mid-stream failure may have
        # billed partial generation and is NOT retried (same split as the OAI loop).
        with _urlopen_transient_retry(rq, timeout, name) as resp:
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
    if marker and not _marker_on_last_line(text, marker):
        warn.append("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    if not text.strip():
        # 🔴 "EMPTY OUTPUT from stream" AND NOTHING ELSE was all this said until 2026-08-08, and
        # it happened for real in the round-29 panel: 8 456 output tokens, of which 8 453 were
        # reasoning, three server-side tool calls, $0.052 billed, and a zero-byte answer. The
        # warning named the symptom and threw away every fact that could explain it. Whatever
        # the vendor DID report - the terminal status, the incomplete reason, the shape of the
        # output array - now travels with the failure, because a paid failure you cannot
        # diagnose is one you will pay for again.
        #
        # 🔴 R47: ONE WARNING TEXT COVERED TWO OPPOSITE FAILURES, AND THE CANNED CAUSE LIED ABOUT
        # ONE OF THEM. The AOS round-40 instance spent 6 715 of a 131 072 budget - 95% UNSPENT -
        # and the diagnosis still blamed an exhausted budget, in those words. The two
        # shapes need different fixes: an exhausted budget wants a bigger max_tokens or a smaller
        # brief; a mid-loop termination with the budget untouched is the vendor's server-side
        # agentic runtime ending the turn without a message item - the 4th measured instance of
        # that class across three vendors (kimik3 2026-08-03, grok420 R29+R40, orgemini37flash
        # R38), and no request parameter controls it. Split on the budget share so each failure
        # meets its own cause.
        kinds = sorted({(it.get("type") or "?") for it in (resp_obj.get("output") or [])})
        out_used = usage.get("output_tokens") or 0
        reas_used = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        if out_used >= int(sent_budget * 0.9):
            # R47 panel (ordeepseekv4pro): a loop death can COINCIDE with a nearly-spent budget,
            # and labelling that shape purely "exhausted" points the reader at max_tokens when
            # the loop was the killer. When the captured items show the turn still inside a tool
            # call, say both.
            _in_loop = any(str(k).endswith("search_call") for k in kinds)
            warn.append(
                "OUTPUT BUDGET EXHAUSTED - status=%s, output items=%s, reasoning_tokens=%s of "
                "%s output tokens against max_output_tokens=%s. The reasoning spent the whole "
                "budget and the answer never started.%s"
                % (resp_obj.get("status"), kinds or "none captured",
                   reas_used, out_used, sent_budget,
                   (" NOTE: the last captured items include a tool call, so the turn was still "
                    "inside the agentic loop when the budget went - raising max_tokens may only "
                    "buy a longer loop." if _in_loop else "")))
        else:
            warn.append(
                "VENDOR ENDED THE TURN MID-LOOP - status=%s, incomplete_reason=%s, output "
                "items=%s, reasoning_tokens=%s of %s output tokens, with %d%% of the "
                "max_output_tokens=%s budget UNSPENT. The budget is NOT the cause: the vendor's "
                "server-side agentic loop terminated after a tool call without ever producing a "
                "message item, and billed for the reasoning and searches it did make."
                % (resp_obj.get("status"),
                   (resp_obj.get("incomplete_details") or {}).get("reason"),
                   kinds or "none captured", reas_used, out_used,
                   round(100 * (1 - out_used / sent_budget)) if sent_budget else 0,
                   sent_budget))
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
            # Correct HERE - this is the Responses API. The identical-looking key is wrong two
            # hundred lines up, on /chat/completions, and was wrong there for months.
            # No `summed=` here, and that is correct rather than an omission: xAI /v1/responses
            # runs its agentic loop SERVER-side and returns one response with one usage object,
            # so there are no rounds on this side to sum. Stated because the sibling call site
            # needed the opposite fix, and a class fix applied where the class does not hold is
            # its own defect.
            "reasoning_meter": meter_source(usage, "output_tokens_details", "reasoning_tokens"),
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
            # The vendor's handle for this turn. R47: the round-40 post-mortem had to argue
            # parser-vs-vendor from the captured stream alone; with the id, the stored response
            # can be re-read at the vendor after the fact. Recorded, not acted on - whether
            # retrieval works on xAI's side is not asserted here because it was not probed.
            "response_id": resp_obj.get("id"),
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

## Environment constraints (non-interactive run)

- No human is present and nothing can be approved mid-run. Never end with an implementation
  plan, a proposal, or a request for confirmation - produce the completed deliverable itself.
  A plan with the end marker under it is still a failed run.
- Terminal/shell commands are denied by policy here, and a single denial discards the entire
  run with all the work already done in it. Never attempt one. Read pages with your search and
  page-reading tools, and do any parsing or extraction from the fetched text directly.

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
    a 25-minute run comes back empty. Returns a warning string, or None if it looks healthy.

    🔴🔴 R57: THIS FUNCTION USED TO CARRY ITS OWN PRIVATE IDEA OF "CORRECT", AND THAT IS THE
    DEFECT, NOT A DETAIL. It checked two things - "some mcp() allow rule exists" and
    "firecrawl_crawl is denied" - both spelled out here, in a second place, with no link to the
    script that writes the rules. Every rule R57 added (`mcp(*)`, `deny command(*)`,
    `deny mcp(firecrawl/*)`, `deny mcp(playwright/*)`) would have passed BOTH of those checks on
    a completely stale config, because a pre-R57 settings.json has mcp() allow rules and does deny
    firecrawl_crawl by name.

    That matters most for the people who are not in this session. Employees update by telling
    their assistant to pull the repo; `upgrade.py` then runs `doctor.py`, which calls this. A
    preflight that says "allow-rules present" on a config missing the fix does not just fail to
    help - it actively certifies the broken state, and it does so in green.

    One rule, one home: the rule set now comes from `patch_agy_permissions.py` itself. If that
    file gains a rule, this check demands it on the next run, with no edit here. Two files on one
    subject rot, and the read-only copy rots first because nothing corrects it by failing.
    """
    path = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return "cannot read %s (%r) - cannot tell whether headless runs will survive" % (path, e)

    import importlib.util          # local: this is the only place in the file that needs it
    here = SKILL_DIR
    patch_py = os.path.join(here, "patch_agy_permissions.py")
    try:
        spec = importlib.util.spec_from_file_location("_agy_patch", patch_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        add_a, add_d = mod.missing(cfg)
    except Exception:                                                         # noqa: BLE001
        # The script is optional in a copied install. Fall back to the weakest useful question -
        # "is ANY mcp rule present" - and say plainly that this is the degraded check, so a
        # green line here is not mistaken for the full one.
        allow = (cfg.get("permissions") or {}).get("allow") or []
        if not any(str(r).startswith("mcp(") for r in allow):
            return ("no mcp() allow-rules in %s - in headless mode the first MCP tool the model "
                    "reaches for will be auto-denied and the ENTIRE run discarded (empty answer, "
                    "status SUCCESS, exit 0), and patch_agy_permissions.py is missing from %s so "
                    "the full check could not run" % (path, here))
        return None

    if not add_a and not add_d:
        return None
    # Name the consequence per rule kind: they fail differently and a reader needs to know which
    # one is biting. Measured shapes, R56/R57.
    why = []
    if any(r.startswith("mcp(") for r in add_a):
        why.append("an MCP tool the server has gained since the allow-list was written is "
                   "UNLISTED, and an unlisted tool cancels the whole turn")
    if mod.SHELL_DENY in add_d:
        why.append("the shell is unlisted, so the first time the model reaches for a command - "
                   "usually as a fallback after another tool breaks - the run is discarded; "
                   "denying it explicitly costs nothing because the model recovers from a denial")
    if any("firecrawl" in r for r in add_d):
        why.append("Firecrawl is reachable and bills per page with no ceiling")
    if any("playwright" in r for r in add_d):
        why.append("the browser server driving a profile with live logins is reachable")
    return ("agy permission rules are STALE in %s - missing %d allow, %d deny (%s). Run: python "
            "patch_agy_permissions.py    [%s]"
            % (path, len(add_a), len(add_d), ", ".join(add_a + add_d), "; ".join(why)))


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


def opencode_bin():
    """opencode CLI (opencode.ai) — npm install -g opencode-ai.

    Free Spark 1.3 Contributor needs no API key (the opencode/ model prefix is free).
    Installed via npm globally; on Windows the binary is opencode.cmd in the npm dir.
    """
    return _resolve_bin("OPENCODE_BIN", "opencode", [
        os.path.join(os.environ.get("APPDATA", ""), "npm", "opencode.cmd"),
        os.path.join(os.environ.get("APPDATA", ""), "npm", "opencode"),
    ])


# The other half of CLI_BINARIES: kind -> the function that finds that binary. Split from the
# tuple only because the resolvers carry per-platform search paths and have to be defined after
# _resolve_bin. selftest asserts the two halves have identical keys, which is what stops the
# next CLI from being added to one and not the other - the failure this pair exists to prevent.
CLI_RESOLVERS = {
    "codex": codex_bin,
    "agy": agy_bin,
    "hermes": hermes_bin,
    "grokcli": grok_bin,
    "opencode": opencode_bin,
}


# Every `kind` the dispatcher can launch. ONE home: both main() and channel_preflight() read
# this, because they used to share a blind spot by construction - each had its own copy of the
# same five literals, so a kind unknown to one was unknown to the other, and a misspelled kind
# produced neither a preflight warning nor a result, only a log line that scrolls away.
KNOWN_KINDS = ("http", "codex", "agy", "openrouter", "oai", "xai", "gemini", "hermes",
               "grokcli", "opencode")

# The kinds that run ON THIS MACHINE and can therefore read an attached document from disk
# instead of receiving it inline (--attach / --attach-dir). Igor, R46: «CLI агентам не отправлять
# файлы, а отправлять ссылки на документы и папки, чтобы они могли сами изучить дополнительное».
# `hermes` is deliberately absent: its toolset grant is `web` only, so handing it a path would
# name a capability it does not have - the model would report OUR gap as its own failure.
# API kinds are structurally absent: a remote endpoint cannot open this disk.
REFS_KINDS = ("codex", "agy", "grokcli")

# The degraded path only. When channels.json cannot be loaded there is no `kind` field to read,
# so these are the names the harness has used, mapped to what they were. Both the historical
# spellings and the current registry names are here: this table is consulted exactly when the
# registry is unreadable, which is the one moment a name cannot be looked up.
_LEGACY_KINDS = {"http": "http", "spark": "http", "spark11": "http", "spark12": "http",
                 "spark12cont": "http", "spark13cont": "http",
                 "codex": "codex",
                 "agy": "agy", "gemini": "agy", "agy31pro": "agy", "agy36flash": "agy",
                 "agy37flash": "agy", "agy38flash": "agy",
                 "kimi": "openrouter", "qwen": "openrouter",
                 "kimik3": "openrouter", "qwen38max": "openrouter",
                 "orgemini36flash": "openrouter", "orgemini37flash": "openrouter", "orgemini38flash": "openrouter",
                 "ormimo25pro": "openrouter",
                 "orgrok420": "openrouter", "ornemotron3ultra": "openrouter",
                 "ordeepseekv4pro": "openrouter", "orglm53": "openrouter",
                 "orgpt56terrapro": "openrouter", "orgpt56solpro": "openrouter",
                 "orgpt56lunapro": "openrouter", "orspark12cont": "openrouter",
                 "orspark13cont": "openrouter",
                 "goog36flash": "gemini", "goog37flash": "gemini",
                 "mimo25pro": "oai", "grok420": "xai", "grokbuild": "grokcli",
                 "hermes": "hermes", "ocspark13free": "opencode"}


def _legacy_slot(cname):
    """A plan slot for a channel the registry could not describe: kind only, no model."""
    return {"kind": _LEGACY_KINDS.get(cname), "model": None, "effort": None,
            "timeout": None, "web": None, "toolsets": None}


_ENV_KEY_DIVERGENCE_WARNED = set()


def _env_key(varname):
    """Process env first, then HKCU\\Environment, because `setx` writes only the latter.

    🔴 AND WHEN THE TWO DISAGREE, SAY SO (R47). Igor rotated GEMINI_API_KEY with setx; every
    already-running session kept the DEAD key in its inherited process env, this function
    preferred it, and the failure wore the vendor's clothes - HTTP 429 on a key that had just
    been replaced, indistinguishable from a real quota wall. The process copy still WINS,
    because an inline `$env:X=...; python ...` override is legitimate and must keep working -
    a stale inherited copy and a deliberate override are mechanically indistinguishable. What
    changes is that the divergence is PRINTED, once per variable per run: the one line that
    turns half an hour of quota theories into "restart the shell".
    """
    v = os.environ.get(varname)
    if os.name == "nt":
        reg_v = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                reg_v = winreg.QueryValueEx(reg, varname)[0]
        except OSError:
            pass
        if not v:
            v = reg_v
        elif reg_v and reg_v != v and varname not in _ENV_KEY_DIVERGENCE_WARNED:
            _ENV_KEY_DIVERGENCE_WARNED.add(varname)
            log("  ⚠ %s in this PROCESS differs from the one saved with setx "
                "(HKCU\\Environment). The process copy is being USED. If you just rotated "
                "this key, this session is still holding the old one - restart the shell, "
                "or clear the process copy so the new value is read." % varname)
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
    for kind, resolver in sorted(CLI_RESOLVERS.items()):
        for c in sorted(by_kind.get(kind, [])):
            b = resolver()
            if not (os.path.isfile(b) or shutil.which(b)):
                yield "%s: binary not found (%s). Install it or exclude the channel." % (c, b)
    for c in sorted(by_kind.get("grokcli", [])):
        # 🔴 A MACHINE-WIDE RULES DIRECTORY NO cwd CAN PROTECT AGAINST. This CLI scans
        # `~/.grok/rules/*.md` for EVERY project, on top of the CLAUDE.md/AGENTS.md discovery
        # that `--cwd` does neutralise. If it exists, its contents go to the vendor on every
        # single call, outside the PII gate, and nothing else in this harness would ever say so.
        rules = os.path.join(os.environ.get("USERPROFILE", ""), ".grok", "rules")
        n = len([f for f in os.listdir(rules)]) if os.path.isdir(rules) else 0
        if n:
            yield ("%s: 🔴 %d file(s) in %s are injected into EVERY grok call, including this "
                   "one, and --cwd cannot stop it. Read them before this round or move them "
                   "aside - they leave this machine unfiltered." % (c, n, rules))
        else:
            yield ("%s: subscription session (no API key); %s absent, so nothing machine-wide is "
                   "injected" % (c, rules))
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
        # 🔴 R37 2026-08-14: agy mangles non-ASCII path components in its own stream-json
        # output ("...\\???????????\\..."), which broke workspace-scoped agent discovery on
        # Cyrillic paths. The preflight used to only WARN. Now _agy_once transparently
        # mirrors its workdir to an ASCII sibling under %TEMP% when needed; the outfile
        # stays at the path the caller asked for because Python writes it and handles
        # non-ASCII paths. Preflight still names the situation so users see what happened.
        p = os.path.abspath(outdir)
        try:
            p.encode("ascii")
        except UnicodeEncodeError:
            yield ("agy: the output path contains non-ASCII characters (%s). The workspace "
                   "agy runs in will be mirrored to an ASCII path under %%TEMP%%; your "
                   "outfile still lands at the path you asked for. This is safe - it is here "
                   "so you know why %%TEMP%% shows up in agy's own log lines." % p)
    for c in sorted(by_kind.get("opencode", [])):
        b = opencode_bin()
        if os.path.isfile(b) or shutil.which(b):
            yield ("%s: opencode CLI present (%s); free model, no API key needed"
                   % (c, b))


def _write_agy_agent(workdir):
    """
    Ship the reviewer persona with the code instead of installing it in ~/.gemini.

    A workspace-scoped agent at <workspace>/.agents/agents/<name>/agent.md IS discovered and
    loaded (verified 2026-07-31: the stream-json `init` event came back with
    agent="deep-researcher"), while the same directory's settings.json and mcp_config.json are
    NOT read by this build. So the persona travels with the run and nothing global is touched -
    but permissions cannot be set this way, which is why patch_agy_permissions.py exists.

    🔴 A workspace .agents/hooks.json is not read either - the THIRD workspace mechanism tested
    negative. On 2026-08-14 an IDE agent added one here carrying a PreToolUse auto-allow hook
    for run_command; a probe that FORCED a shell call still died on the permission denial with
    the hook file present. Do not re-add one: the only levers this channel has are the global
    settings.json (exact-string command() rules, no wildcards) and BRIEF-level steering
    (AGY_ENV_CONSTRAINT), measured to work 2/2 the same day - while the identical words in
    this agent.md alone were ignored 1/1. The persona copy below stays as the second layer,
    not the load-bearing one.
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


def _fetch_key(u):
    """Dedupe key for the page-FETCH budget: everything the origin server sees, and nothing else.

    Deliberately NOT _norm_url, and the two must not be merged - they answer different questions
    and a single "normalise a URL" helper would have to be wrong for one of them:

      _norm_url  answers "did the model cite this SOURCE" and therefore DROPS the query string,
                 because `?utm_source=openai` is not a different source.
      _fetch_key answers "would this be the same HTTP REQUEST" and therefore KEEPS the query
                 string, because `?page=2` is a different page and refusing it as a duplicate
                 would deny the model a source it never read.

    What both drop is the FRAGMENT, and that is the bug this function was extracted to fix
    (round 38, 2026-08-14). RFC 3986 resolves a `#fragment` client-side; it is never placed on
    the wire. So `.../provider-selection` and `.../provider-selection#base-slug-matching` are one
    request to the origin, and a dict keyed on the raw string counted them as two. Measured on
    the first live run of orgpt56terrapro: 2 of 8 budget slots spent re-fetching two pages the
    model already had, byte-identical both times, and each wasted slot added a tool round that
    re-sent a 400 KB page already sitting in the conversation.

    Case handling is conservative on purpose. Scheme and host are case-INSENSITIVE per RFC 3986
    and are lowercased; the PATH is not, because most origins serve case-sensitive paths and
    over-merging here does not waste money - it silently refuses the model a page it never got.
    The failure modes are asymmetric, so the cheap one is chosen.

    🔴 LIKE _norm_url, THIS MUST NEVER RAISE. It runs inside the paid tool loop, and the lesson
    recorded in _norm_url's docstring - a parsing bug in a verification helper billed as a
    network failure and retried three times - applies here with more force, because this helper
    decides whether a paid fetch happens at all.
    """
    u = (u or "").strip().rstrip(".,;:")
    try:
        s = urlsplit(u)
        host = s.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = (s.path or "/").rstrip("/") or "/"
        return (s.scheme.lower(), host, path, s.query)
    except ValueError:
        # Same fallback shape as _norm_url: a URL this malformed will fail in the fetcher anyway,
        # and the only job here is to return SOMETHING hashable and stable for the same input.
        return ("", "", u.split("#", 1)[0], "")


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
           "last_tool": None,
           # 🔴 `errors` is a SET of distinct messages and carries no tool name, so a reader
           # cannot tell which tool failed first. R52 turned on exactly that: three
           # grep_search failures, then one denied shell command, and the summary named the
           # denial. `error_seq` keeps (tool, message) in the order they actually happened.
           "error_seq": []}
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
                if err:
                    out["error_seq"].append((name, err))
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

AGY_ENV_CONSTRAINT = """

---

ENVIRONMENT CONSTRAINTS (from the harness, non-negotiable):

- This is a non-interactive run. No human is present and nothing can be approved. Never end
  with an implementation plan or a request for confirmation - return the completed deliverable
  itself. A plan with the end marker under it is still a failed run.
- Terminal/shell commands are DENIED here, and a single denial discards the whole run with all
  the work already done in it. Do not attempt them. Use your search and page-reading tools,
  and do any parsing or extraction from the fetched text directly.
"""
# ^ Appended to the BRIEF, not only the persona, because placement is load-bearing and was
# measured both ways on 2026-08-14: the same two sentences in the brief steered the model off
# run_command 2/2; in the workspace agent.md alone they were ignored 1/1 (it called
# Select-String and died on the denial). Same asymmetry as July's "MCP tool descriptions
# outrank the agent prompt": agent.md is the weak position, the user turn is the strong one.

AGY_PLAN_ESCALATION = """

---

ADDITIONAL REQUIREMENT FOR THIS ATTEMPT.

A previous attempt at this exact brief returned an implementation PLAN and asked for approval.
There is no human in this run: nothing you propose can be approved, so a plan is a failed
deliverable even with the end marker under it. Do the work NOW, in this attempt, and return the
completed review itself. Do not describe what you will do - do it.

And do it with sources: open every page you cite in THIS attempt. A citation to a page you did
not open in this run is treated as fabricated, which is a worse failure than the plan was.
"""


def _agy_plan_shape(text):
    """
    True when the answer is the CLI's implementation-plan artifact, not the deliverable.

    Measured 2026-08-14 (AOS round 31, 87K-char code-review brief): BOTH agy channels returned
    the IDE's plan template - one literally said "I am presenting the plan here for your
    approval" - with the required end marker appended, so the marker gate graded a run that did
    no work as OK. The template's section headings are stable vendor furniture, so two or more
    of them in one answer is the fingerprint.

    Only MARKDOWN HEADINGS at line start count, not substrings. The first cut matched bare
    substrings and false-positived IN PRODUCTION within the hour: the review brief that
    audited this very detector named all five headings, so the channel's real review quoted
    them inline and was re-run for nothing (a free retry here, but the same event on a paid
    channel would bill twice). The panel reviewer then independently constructed the same
    failure - a review quoting a PR template's section names. A plan RENDERS the phrases as
    section headings; a review MENTIONS them in prose. Distinct headings, not total
    occurrences, so one heading repeated in a quoted block cannot fire alone.
    """
    pat = r"(?m)^#{1,6}\s*(?:\d+[.)]\s*)?(Implementation Plan|Goal Description|User Review Required|Proposed Changes|Verification Plan)\b"
    return len({m.group(1) for m in re.finditer(pat, text or "")}) >= 2


def call_agy(brief, marker, workdir, outfile, model=None, effort="high", timeout="25m",
             system=None, add_dirs=None):
    """
    Run the channel, and re-run it ONCE if it planned instead of working, or if it cited
    sources without opening any.

    Measured 2026-07-31: agy issued 10 searches, opened **zero** pages, and cited 11 URLs of which
    3 return 404 - while getting the substance right. That combination is the dangerous one,
    because it survives casual review: correct conclusions with invented receipts.
    Measured 2026-08-14: on an 87K brief both agy channels returned the plan artifact with the
    marker appended - the second shape a mechanical marker check cannot see on its own.

    Why a mechanical retry rather than ONLY a stronger instruction: prose has a measured
    asymmetry on this channel. It cannot restrict tool ACCESS (a system prompt could not, an
    agent persona could not, `--mode` could not - three separate failures), but it reliably
    steers tool USE when an alternative exists AND it stands in the strong position: the
    2026-08-14 shell-ban worked 2/2 written into the BRIEF and 0/1 written into the persona
    alone, so AGY_ENV_CONSTRAINT rides the brief. This wrapper carries the enforcement for
    the runs where steering still loses. It is bounded to exactly one extra attempt, it
    announces itself and its cost before spending, and it keeps BOTH transcripts on disk so
    the two can be compared.

    The retry is not automatically believed. If the second attempt still opens nothing, the FIRST
    answer is returned - it was produced under cleaner conditions, without an instruction nagging
    it about sources - and both failures are reported. Two ungrounded runs is a much stronger
    signal about the brief than one.
    """
    first = _agy_once(brief, marker, workdir, outfile, model, effort, timeout, system,
                      add_dirs=add_dirs)

    if first.get("plan_shape"):
        log("  [agy] the answer is an implementation PLAN, not the review. Re-running once "
            "with an explicit do-the-work instruction (this costs a second agy call).")
        retry_out = os.path.splitext(outfile)[0] + ".retry" + (os.path.splitext(outfile)[1] or ".md")
        second = _agy_once(brief + AGY_PLAN_ESCALATION, marker,
                           os.path.join(workdir, "plan-retry"), retry_out,
                           model, effort, timeout, system, add_dirs=add_dirs)
        if second.get("text") and not second.get("plan_shape") \
                and (not marker or marker in second["text"]):
            # Deliberately NOT chained into the zero-grounding re-run below: the cost bound is
            # exactly one extra attempt per call, announced before it is spent. If this retry
            # cited sources and opened none, _agy_once has ALREADY attached the zero-grounding
            # WARNING to it (ok=False, the panel shows PROBLEM), so the failure stays loud -
            # what is skipped is only a third paid attempt, not the detection. The panel's own
            # reviewer called this "blind acceptance"; the warning path above is why it is not.
            second.setdefault("notes", []).append(
                "SECOND ATTEMPT. The first run returned the CLI's plan artifact instead of the "
                "review; it is kept at %s. This answer is the actual deliverable." % outfile)
            return second
        first.setdefault("warnings", []).append(
            "RE-RUN ALSO FAILED TO DELIVER (kept at %s). Two attempts planned instead of "
            "working - the brief may be too large or too task-shaped for this channel's agent. "
            "Shrink the brief or use a non-CLI channel for it." % retry_out)
        first["ok"] = False
        return first

    if not first.get("zero_grounding"):
        return first

    log("  [agy] cited %d URL(s) and opened NONE. Re-running once with an explicit instruction "
        "to open sources (this costs a second agy call)." % first.get("n_cited", 0))

    retry_out = os.path.splitext(outfile)[0] + ".retry" + (os.path.splitext(outfile)[1] or ".md")
    second = _agy_once(brief + AGY_ESCALATION, marker,
                       os.path.join(workdir, "retry"), retry_out,
                       model, effort, timeout, system, add_dirs=add_dirs)

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


# Lines that appear in EVERY agy CLI log, successful runs included, and therefore diagnose
# nothing. Measured R56 over all 89 logs on this machine (2026-08-10..19):
#
#   "not logged into Antigravity"  20-52 occurrences in 89 of 89 logs, including every run that
#                                  answered normally. It is the cold token cache at boot; the
#                                  refresh that follows is what matters and it is logged
#                                  separately as "Auth succeeded".
#   "Migration ["                   startup bookkeeping, every run.
#
# 🔴 THIS LIST IS THE WHOLE POINT OF THE FUNCTION, AND IT COST A WRONG ROOT CAUSE TO WRITE.
# Reading the failing panel's log for the first time, "You are not logged into Antigravity"
# repeated twelve times inside one second looked exactly like the answer - an auth race between
# three processes starting together. It is not: the control refutes it, because the identical
# line is in the logs of runs that worked. A signature that is present in the failure and ALSO
# present in every success has zero diagnostic value, and the only thing that separates the two
# readings is having looked at a log that did NOT fail.
_AGY_LOG_NOISE = ("not logged into Antigravity", "Migration [")


def _agy_log_evidence(path, limit=6):
    """The error-level lines from agy's own CLI log, minus the lines every log has.

    Only consulted when a run produced no text at all: at that point the stream has nothing,
    stderr has nothing, and this file is the only remaining witness. Scrubbed before it is
    returned - a vendor log is not a place secrets are guaranteed to be absent, and this string
    ends up in diagnostics.json, which exists to be pasted into a chat.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return []
    out, seen = [], set()
    # Level letter + date is glog's own format (`E0819 22:36:05.725823`). W and E only: the
    # I-lines are progress, and there are thousands of them.
    for m in re.finditer(r"[WE]\d{4} \d\d:\d\d:\d\d\.\d+\s+\d+\s+(\S+)\] (.+)", raw):
        msg = m.group(2).strip()
        if any(n in msg for n in _AGY_LOG_NOISE):
            continue
        key = (m.group(1), msg[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append("%s] %s" % (m.group(1), msg[:200]))
        if len(out) >= limit:
            break
    return [scrub(x) for x in out]


# 🔴🔴 R56 — CONCURRENT agy PROCESSES RACE ON A SHARED TOOL-SCHEMA CACHE, AND THE LOSER'S RUN IS
# DISCARDED AT ~4 SECONDS WITH ZERO TOKENS.
#
# Measured, two arms, one variable, on the same brief through the same code path:
#
#   agy31pro ALONE          x2   ok, 207 s / 159 s, ~10 KB of review each
#   agy31pro + 2 SIBLINGS   x2   one channel dies at 4.1 s, 0 output tokens, empty answer
#
# The per-channel CLI log (which exists only because of --log-file above) names it:
#
#   failed to write tool bulk_stealthy_fetch from server scrapling to tmp file:
#   open ~/.gemini/antigravity-cli/mcp/scrapling/bulk_stealthy_fetch.json.tmp:
#   The process cannot access the file because it is being used by another process
#   -> building toolbox: tool "mcp_scrapling_open_session" advertises an invalid parameter
#      schema: ... tool-parameters.json: The system cannot find the file specified
#   -> Print mode: run ended with error and no response
#
# agy caches every MCP server's tool schemas under ONE shared directory and rewrites it at
# startup through .tmp files. On Windows a file open for writing cannot be opened by a second
# process, so of N simultaneous starts one wins and the others fall back to "eagerly loading all
# tools", which needs a file that was never written. The toolbox then fails to build and the
# agent terminates before its first token.
#
# 🔴 THE CONTROL IS WHAT MAKES THIS A FINDING: `being used by another process` appears 0 times in
# both solo logs and 1-2 times in every concurrent one. Two other signatures in the same log
# looked just as damning and are in the SUCCESSFUL logs too - «You are not logged into
# Antigravity» (20-52x in all 89 logs on this machine) and «Agent "deep-researcher" not found».
# Either would have made a confident, wrong root cause.
#
# 🔴 AND THE VICTIM IS ARBITRARY: in the second concurrent repetition the channel that died was
# agy36flash, not agy31pro. That is why R55 recorded this as «transient and unexplained» - it
# moves between rounds, and R55's replay ran the exact panel invocation ALONE, which is the one
# condition under which it cannot happen. Replaying the INVOCATION is not replaying the RUN.
#
# The fix is to stop the starts overlapping, not to serialise the runs: a channel takes 50-200 s
# and the cache write is over in the first few, so spacing the launches costs nothing on the
# wall clock (the slowest channel still sets the round's length) and removes the race entirely.
# Structurally cleaner would be a private state directory per child - the log shows agy resolving
# a `GeminiDir` - but that directory also holds the subscription's OAuth credentials, so pointing
# a child elsewhere is a separate, carefully-probed change, not a same-round fix.
_AGY_START_LOCK = threading.Lock()
_AGY_LAST_START = [0.0]


def _agy_stagger():
    """Hold the next agy launch until the previous one is past its cache-writing window.

    11 s, set by Igor on 2026-08-20 (R57; the earlier "8" came from a typo in his reply that I
    read as "keep the current value"). The failure is measured at ~4.1 s - the window in which agy
    rewrites every MCP server's tool schemas into one shared directory - so this is ~2.7x the
    observed race.

    It costs nothing in wall-clock: a channel runs 50-230 s, so the whole stagger for a
    three-channel panel disappears inside the slowest one. Do NOT tune it down towards 4.1 s -
    that number is one machine under one load, not a boundary, and the symptom of getting it wrong
    is an empty answer from a RANDOM channel with status SUCCESS and exit code 0.
    `AGY_START_SPACING=0` disables it for a single-channel run.
    """
    gap = float(os.environ.get("AGY_START_SPACING", "11"))
    if gap <= 0:
        return 0.0
    with _AGY_START_LOCK:
        wait = max(0.0, _AGY_LAST_START[0] + gap - time.time())
        if wait:
            time.sleep(wait)
        _AGY_LAST_START[0] = time.time()
    return wait


def _run_agy(cmd, timeout=3600, cwd=None, stdout_path=None, env=None):
    """Like _run(), but detects agy stream completion and kills the zombie process.

    agy CLI >=1.1.x never exits after the conversation stream completes: it stays
    alive on periodic loadCodeAssist heartbeats (~5 min interval), so _run()'s
    subprocess.run() blocks for the full timeout on a run that finished minutes ago.

    Measured R80 panel test (2026-09-04): model finished in ~6 min, process alive
    40+ min until manually killed.  The ``result`` event in the stream-json output
    is the completion signal — once it appears in the events file, the model's
    answer is on disk and the process can be terminated.

    Returns the same ``(result, seconds)`` pair as _run().  Raises FileNotFoundError
    and subprocess.TimeoutExpired in the same situations _run() does.
    """
    _POLL = 5
    _GRACE = 3

    t0 = time.time()
    stderr_path = (stdout_path + ".stderr.tmp") if stdout_path else None
    out_f = open(stdout_path, "w", encoding="utf-8") if stdout_path else None
    err_f = open(stderr_path, "w", encoding="utf-8") if stderr_path else None
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                             stdout=out_f or subprocess.PIPE,
                             stderr=err_f or subprocess.PIPE,
                             cwd=cwd, env=env)
    except FileNotFoundError:
        if out_f:
            out_f.close()
        if err_f:
            err_f.close()
        raise
    # Close the parent's handles; the child holds its own duplicates of the fds.
    if out_f:
        out_f.close()
    if err_f:
        err_f.close()

    result_seen = None
    zombie_killed = False
    try:
        while p.poll() is None:
            if time.time() - t0 > timeout:
                p.kill()
                p.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
            if stdout_path and result_seen is None:
                try:
                    with open(stdout_path, "r", encoding="utf-8",
                              errors="replace") as rf:
                        for ln in rf:
                            if '"event":"result"' in ln or '"event": "result"' in ln:
                                result_seen = time.time()
                                break
                except OSError:
                    pass
            if result_seen is not None and time.time() - result_seen >= _GRACE:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                zombie_killed = True
                break
            time.sleep(_POLL)
    finally:
        if p.poll() is None:
            p.kill()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    stderr = ""
    if stderr_path:
        try:
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                stderr = f.read()
        except OSError:
            pass
        try:
            os.unlink(stderr_path)
        except OSError:
            pass
    elif p.stderr:
        stderr = p.stderr.read()

    secs = time.time() - t0
    if zombie_killed:
        log("  [agy] stream completed (result event at +%.0fs), zombie terminated at "
            "+%.0fs (saved ~%.0fs of idle wait)"
            % (result_seen - t0, secs, timeout - secs))

    class _R:
        __slots__ = ("returncode", "stderr", "stdout")
    r = _R()
    r.returncode = p.returncode
    r.stderr = stderr
    r.stdout = ""
    return r, secs


def _agy_once(brief, marker, workdir, outfile, model=None, effort="high", timeout="25m",
              system=None, add_dirs=None):
    """
    Two ways in, and they fail differently. Inline -p avoids the file-reading tool (and its
    permission error) but is capped by the Windows argv limit. --add-dir has no size cap but needs
    the tool. Pick by size.
    """
    binary = agy_bin()
    # R37 (2026-08-14) fix for agy corrupting non-ASCII path components in stream-json output
    # ("...\\???????????\\..."), which broke workspace-scoped agent discovery. Same shape as
    # the grokbuild R55 failure (ERROR_PATH_NOT_FOUND on --prompt-file); R59 extracted the
    # workaround into _ascii_safe_workdir(), so both channels now share one implementation and
    # any CLI added later gets it in one line. Only what agy actually reads (BRIEF.md and the
    # workspace-scoped agent) needs to be ASCII-clean; the caller's REPORT.md and diagnostics
    # can stay wherever they were - Python writes them.
    workdir = _ascii_safe_workdir(workdir, "agy", "agy")
    os.makedirs(workdir, exist_ok=True)
    _write_agy_agent(workdir)
    ndjson = os.path.splitext(outfile)[0] + ".events.ndjson"
    # AGY_ENV_CONSTRAINT must ride the BRIEF (measured 2/2 from brief, 0/1 from persona - the
    # position-binding lesson in agy-channel-mechanics), and appending it here lands it AFTER
    # the marker instruction main() spliced to the very end (orgemini37flash, R73). Re-stating
    # the marker line after it keeps «last instruction = the marker» true without moving the
    # constraint out of the position where it demonstrably works.
    brief = _with_system(brief, system) + AGY_ENV_CONSTRAINT
    if marker:
        brief += ("\n\nReminder: the very LAST line of your reply must be exactly:\n%s\n"
                  % marker)

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
    # --mode: SUPERSEDED 2026-08-14 - the old text here said the value "does nothing at all",
    # and the value this code passed was "plan". Both halves of that were wrong together:
    # A/B on the SAME 87K-char brief, same day, same channel, one variable changed:
    #
    #   --mode plan    -> "Here is the implementation plan..." - 4 plan-template headings,
    #                     34 tool calls, the marker appended UNDER the plan. Exactly the
    #                     round-31 failure where both agy channels planned instead of working.
    #   --mode default -> the actual review: 0 plan headings, 53 tool calls, 26 searches,
    #                     14 pages opened.
    #
    # What REMAINS true from the 2026-07-31 measurement on 1.1.9: the flag is unvalidated
    # (garbage values exit 0) and invisible in telemetry (permission_mode reads
    # "request-review" for every value). Invisible is not inert - a knob the meter cannot see
    # can still act, and this one acts on large briefs, where the agent's planner engages.
    # And --mode is still NOT what makes the run read-only: --sandbox and the permission rules
    # written by patch_agy_permissions.py carry that, exactly as before.
    # 🔴 `--mode` IS NOT PASSED AT ALL, AND «default» WAS NEVER A VALUE. R46 measured that
    # `--mode plan` made this channel return a plan instead of a review, and the fix was to send
    # `--mode default` instead. Measured against agy 1.1.15 on 2026-08-19: the flag's enum is
    # `accept-edits|plan` and nothing else, so every agy call since has printed
    # `warning: unrecognized --mode value "default"` on stderr, exit 0. In the R49 panel that
    # warning WAS agy37flash's entire 74-byte answer file.
    #
    # Omitting the flag is what "default" was trying to say: with no `--mode`, the CLI applies
    # its own default. Behaviour is unchanged - an unrecognised value was already being ignored -
    # and the stderr line that can masquerade as an answer is gone. The R45 rule again, on our
    # own side this time: assert the EFFECT, never the exit code. A CLI that exits 0 while
    # telling you the argument was rubbish is the normal case, not the exception.
    # 🔴🔴 R56: --log-file, AND THE REASON IS THAT THE DEFAULT PATH IS A WALL CLOCK.
    # agy names its own log `~/.gemini/antigravity-cli/log/cli-<YYYYMMDD>_<HHMMSS>.log`, to the
    # SECOND. A panel starts every agy child in the same second, so three processes computed one
    # path and shared it. Measured on the r55 panel: three agy children launched at 22:36:05 and
    # left ONE 31 KB log carrying two `Auth succeeded` lines, one `Starting language server`
    # line, and bytes interleaved mid-token (`...pid 4486ER` + `ERROR:`). agy31pro's own
    # conversation id appears in it ZERO times - the one channel that FAILED is the one whose
    # record was overwritten, which is the worst possible way to lose a log.
    #
    # This is instrumentation, not a fix: it does not change what agy does, it changes whether
    # the next failure can be read. R45's rule - when a run dies for an unknown reason the next
    # move is an INSTRUMENT, not a hypothesis - applied to the channel that taught it to us.
    #
    # Verified by execution before being wired in (a knob you only SENT is not a knob): with
    # `--log-file <path>` the file appears at that path at 30 100 bytes and the default log
    # directory's file count is UNCHANGED (124 -> 124), so the log is diverted, not copied.
    agy_log = os.path.splitext(outfile)[0] + ".agy-cli.log"
    cmd += ["--model", mdl,
            "--effort", effort,
            "--agent", AGY_AGENT,
            "--sandbox",
            "--output-format", "stream-json",   # the ONLY way this channel reports tool use
            "--log-file", agy_log,              # see above: the default path collides per second
            "--print-timeout", timeout]         # default truncates at 5m
    # Refs mode (--attach): grant the CLI access to each attachment's folder. --add-dir is the
    # documented workspace grant; without it a read of an out-of-workspace path can be denied,
    # and on this channel ONE denial discards the whole run. Reads only - the run keeps
    # --sandbox and the permission allowlist, so no write surface is opened by this.
    for d in (add_dirs or []):
        cmd += ["--add-dir", d]
    waited = _agy_stagger()   # see _AGY_START_LOCK: concurrent starts corrupt a shared tool cache
    if waited > 0.5:
        log("  [agy] held %.0fs so this launch does not overlap another agy start - "
            "simultaneous starts race on the shared MCP tool-schema cache and the loser's run "
            "is discarded at ~4s with no output" % waited)
    try:
        # cwd must be the workspace or the workspace-scoped agent is never discovered.
        # env: see posix_tools_dir() - without it this channel's grep_search tool cannot
        # resolve its binary when Python was launched from PowerShell, and the model's
        # fallback for a broken grep is a shell command, which is denied and fatal.
        # _run_agy (not _run): detects stream completion via the result event in the
        # events file and kills the zombie process.  See _run_agy's docstring for the
        # measurement that prompted this (R80 panel test, 2026-09-04).
        p, secs = _run_agy(cmd, timeout=3600, cwd=workdir, stdout_path=ndjson, env=_posix_child_env())
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
    # 🔴🔴 A MISSING BINARY IS A CAUSE; THE DENIAL THAT FOLLOWS IT IS A SYMPTOM. Measured on
    # the AOS R52 stream: grep_search failed three times with `exec: "grep": executable file
    # not found in %PATH%`, the model then reached for a raw PowerShell pipeline to do the
    # search by hand, and THAT was denied and discarded the run. The warning below reported
    # only the denial and sent the reader to patch_agy_permissions.py - which cannot fix a
    # missing grep, so the same failure returned the next round with a different face.
    # Report what happened FIRST, and name the missing binary when there is one.
    missing_bin = [(t, e) for t, e in ev["error_seq"] if "executable file not found" in e]
    if missing_bin:
        warn.append("A TOOL'S BINARY IS MISSING FROM THE CHILD'S PATH - this is a cause, not a "
                    "symptom: %r failed %d time(s) with %r. On Windows `grep` lives only in "
                    "Git\\usr\\bin, which is not on the PowerShell PATH; posix_tools_dir() "
                    "appends it, so seeing this means Git for Windows is absent or "
                    "POSIX_TOOLS_DIR is wrong. The model's fallback for a broken search tool "
                    "is a shell command, and that IS denied and fatal."
                    % (missing_bin[0][0], len(missing_bin), missing_bin[0][1]))
    # 🔴🔴 R56: AN UNLISTED TOOL IS FATAL; AN EXPLICITLY DENIED ONE IS NOT. Measured on 1.1.16
    # by running all three cases (runs/r56/permprobe): a tool in the DENY list comes back as an
    # ordinary tool error - «Permission denied for mcp(...). Matches user-configured deny rule» -
    # and the model recovers and finishes. A tool in NEITHER list produces
    # `Print mode: soft-denying tool confirmation "CallMcpTool" at step N`, status CANCELED, and
    # every token of work is thrown away. So the dangerous state is silence, not refusal, and
    # the two must not share a warning: one is a policy working as intended, the other is a lost
    # round. This block reports the second, and it is the ONLY place that can - the soft-deny is
    # absent from stream-json and from stderr, and lives solely in the CLI log that --log-file
    # now gives this channel.
    soft = []
    try:
        with open(agy_log, encoding="utf-8", errors="replace") as f:
            soft = re.findall(r'soft-denying tool confirmation "([^"]+)" at step (\d+)', f.read())
    except OSError:
        pass
    if soft:
        warn.append(
            "AN UNLISTED TOOL CANCELLED THE RUN (not a denial - a MISSING RULE). The CLI "
            "soft-denied %r at step %s because it is in neither the allow nor the deny list, "
            "and in print mode that discards the whole turn: %s output tokens over %s tool "
            "calls in %.0fs were thrown away. An explicitly DENIED tool would have returned an "
            "error and the model would have carried on. Fix: add a rule for it - "
            "`python patch_agy_permissions.py` wildcards the free read-only MCP servers "
            "(`mcp(<server>/*)`) so a tool the server gains later cannot do this again. The "
            "tool's own name is not in the stream; find it in the conversation store, or "
            "wildcard the server and stop maintaining a list of somebody else's tool names."
            % (soft[0][0], soft[0][1], ev.get("out_tokens") or 0,
               sum(ev["tools"].values()), secs))
    denial = [e for e in ev["errors"] if "denied permission" in e]
    if denial or ("permission that headless mode cannot prompt for" in (p.stderr or "")):
        first = ""
        if ev["error_seq"] and "denied permission" not in ev["error_seq"][0][1]:
            first = (" But this was NOT the first failure: %d error(s) came before it, "
                     "starting with %r from %r. Fix that one first - a denial late in a run "
                     "is usually the model working around a tool that had already broken."
                     % (len(ev["error_seq"]) - 1, ev["error_seq"][0][1], ev["error_seq"][0][0]))
        warn.append("PERMISSION DENIAL KILLED THE RUN: %s. If it is the FIRST error, fix it "
                    "once by running patch_agy_permissions.py (adds the allow-rules for the "
                    "free read-only web tools and deny-rules for the metered Firecrawl ones). "
                    "Do NOT reach for --dangerously-skip-permissions: that also unlocks "
                    "firecrawl_crawl.%s" % (denial[0] if denial else "see stderr", first))
    if marker and not _marker_on_last_line(text, marker):
        # 🔴 SAY WHY, NOT JUST WHAT. Until 2026-08-07 this was the whole message, and its stock
        # advice ("re-run alone, or lower --tier") pointed at a timeout. The round-25 agy36flash
        # failure was not a timeout: it died at 84 seconds having ALREADY produced 8,145 output
        # tokens across 39 tool calls, and the result frame carried its own explanation. An
        # incomplete-output warning that describes only the incompleteness sends every reader to
        # re-run the same failure.
        why = ""
        if ev.get("result_error"):
            why = " CAUSE (from the channel's own result frame): %r." % ev["result_error"]
            # 🔴 GATE THE SENTENCE ON THE METER IT DESCRIBES. It used to fire on any empty
            # answer and said «it discarded a run it had already done the work for - 0 output
            # tokens over 0 tool calls», which is a canned cause contradicting its own numbers.
            # Measured R55 on agy31pro: 0 tokens, 0 tool calls, 4.2 s - the run died BEFORE
            # doing anything, and «re-running would not have helped» is the opposite of true
            # for that case. Same family as the R46 warning that printed «budget gone» over a
            # 95%-unspent budget.
            spent = ev.get("out_tokens") or 0
            calls = sum(ev["tools"].values())
            if not text.strip() and (spent or calls):
                why += (" It discarded a run it had already done the work for - %s output tokens "
                        "over %s tool calls came back as an EMPTY answer, so re-running at a "
                        "lower tier would not have helped." % (spent, calls))
            elif not text.strip():
                why += (" It produced NOTHING - %s output tokens over %s tool calls in %.1fs - so "
                        "the run died before any work was done. That is the opposite case from a "
                        "discarded run: a retry is worth one attempt here."
                        % (spent, calls, secs))
        elif ev["error_seq"]:
            # FIRST, not last. The last error is where the run stopped; the first is usually
            # where it went wrong, and the two were the same message only by luck.
            why = (" FIRST tool error (%d total): %r from %r."
                   % (len(ev["error_seq"]), ev["error_seq"][0][1], ev["error_seq"][0][0]))
            if len(ev["error_seq"]) > 1 and ev["error_seq"][-1][1] != ev["error_seq"][0][1]:
                why += " Last was %r from %r." % (ev["error_seq"][-1][1], ev["error_seq"][-1][0])
        # 🔴 R56: THIS USED TO SIT INSIDE `if result_error`, WHICH IS THE ONE BRANCH THAT ALREADY
        # HAD AN EXPLANATION. The case with nothing to say is `status: CANCELED` - no
        # result_error, no tool error, empty text - and that is exactly the soft-deny above. So
        # the last witness was consulted only when it was not needed. Same shape as R50's «a
        # guard that runs on one branch of six»: count the paths that reach the reporting line,
        # not the paths that set it up. Consulted whenever the answer is empty, whatever the
        # stream said about why.
        if not text.strip() and not soft:
            ev_lines = _agy_log_evidence(agy_log)
            if ev_lines:
                why += (" From this channel's own CLI log (%s), error-level lines that are NOT "
                        "present in a normal run: %s"
                        % (os.path.basename(agy_log), " | ".join(ev_lines)))
            else:
                why += (" Its CLI log (%s) holds no error-level line beyond the ones every run "
                        "logs, so the CLI itself recorded no cause."
                        % os.path.basename(agy_log))
        warn.append("END MARKER ABSENT - output is incomplete, do not parse it as a review." + why)
    note = []
    record_refusal(refusal_check(text, marker, min_chars=500), warn, note)
    plan_shape = _agy_plan_shape(text)
    if plan_shape:
        warn.append("PLAN INSTEAD OF REVIEW - this is the CLI's implementation-plan artifact "
                    "with the marker appended, not the deliverable. The marker alone cannot "
                    "catch it: the model writes the marker under the plan.")
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
            # model/effort recorded since R46: the R45 diagnostics carried neither for the three
            # agy channels, so the per-channel record could not answer «на чём это отработало»
            # without the plan beside it - the codex 2026-08-02 lesson, on a sibling kind.
            "notes": note, "model": mdl, "effort": effort,
            "tool_calls": sum(ev["tools"].values()), "searches": searches,
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
            "plan_shape": plan_shape,
            "n_cited": n_cited, "n_grounded": len(grounded)}


# =============================================================================================

def _free_extras(primary):
    """
    Every ENABLED channel this registry prices at `free`, minus the one already chosen.

    Igor, 2026-08-08: «--ask по умолчанию идёт на spark12cont - оставляем на нем. Но можно так же
    и nemotron параллельно добавить, он же тоже free, и любую другую free если в будущем
    появится.» The last clause is the design, not a nicety: the set is DERIVED from the registry,
    so a free channel added in six months joins `--ask` by existing and nobody has to remember to
    add it here. This repository has now measured four hand-maintained lists that silently stopped
    matching the thing they described - COPY_FILES, the dispatch literals, the word->group map in
    the tests, and the tier list. A fifth was not worth writing.

    🔴 ONE CORRECTION TO THE PREMISE, since it changes what is safe to send: `spark12cont` is
    priced `cheap`, not `free` - $0.10/M in, $0.002/M cached. What it shares with the Nemotron
    channel is not the price but the REASON for the price: both are contributor/free tiers whose
    terms allow training on the payload. So the two really do belong together on a lookup path,
    and neither belongs on an unpublished document.

    Never raises: a lookup must not fail because the registry could not be read. A missing
    registry costs the extras, not the answer.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import routing
        reg = routing.load_registry()
    except Exception:                                    # noqa: BLE001 - see the docstring
        return []
    return [c for c, ch in reg["channels"].items()
            if c != primary and ch.get("enabled", True) and ch.get("cost") == "free"]


def _channel_key_ready(ch):
    """
    True when the env var this channel's transport needs is present; True unconditionally for
    the subscription CLIs, whose reachability is a binary on PATH, not a key - preflight checks
    that separately and this function must not duplicate it. Used only to RANK `ask_default`
    candidates; it never gates a run.
    """
    kind = ch.get("kind")
    if kind == "http":
        return bool(_env_key("MODEL_API_KEY"))
    if kind in ("openrouter", "oai"):
        prov = OAI_PROVIDERS.get(ch.get("provider") or "openrouter") or {}
        env = prov.get("key_env")
        return bool(_env_key(env)) if env else False
    if kind == "gemini":
        return bool(_env_key("GEMINI_API_KEY"))
    if kind == "xai":
        return bool(_env_key("XAI_API_KEY"))
    if kind == "opencode":
        b = opencode_bin()
        return bool(os.path.isfile(b) or shutil.which(b))
    return True                                          # codex / agy / grokcli / hermes


def _pick_ask_channel(reg, key_ready):
    """
    Resolve --ask's default channel from the registry's `ask_default` list (R69).

    Order of preference is the LIST's order; the first entry that exists, is enabled, and whose
    key is present wins. If no candidate has a key, the first enabled one is returned anyway -
    its preflight then explains exactly what is missing, which is the pre-R69 behaviour for a
    keyless machine and strictly better than failing to resolve at all. Pure function of
    (registry, key predicate) so selftest can pin every branch without touching the environment.

    Igor's 2026-08-08 instruction «--ask по умолчанию идёт на spark12cont - оставляем на нем» is
    preserved by ORDER, not by code: spark12cont is first in `ask_default`, so any machine with
    a MODEL_API_KEY behaves exactly as before. What R69 adds is the second candidate: a kit
    user whose only key is OPENROUTER_API_KEY gets their Spark voice (orspark12cont) instead of
    a preflight failure on a channel they cannot run.
    """
    order = [c for c in (reg.get("ask_default") or []) if isinstance(c, str)]
    chans = reg.get("channels") or {}
    live = [c for c in order if c in chans and (chans[c] or {}).get("enabled", True)]
    for cname in live:
        if key_ready(chans[cname] or {}):
            return cname
    if live:
        return live[0]
    return order[0] if order else "spark13cont"


def _ask_default_channel():
    """Registry-backed --ask default; the historical literal only when the registry is
    unreadable, for the same never-raises reason as _free_extras above."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import routing
        reg = routing.load_registry()
    except Exception:                                    # noqa: BLE001 - see _free_extras
        return "spark13cont"
    return _pick_ask_channel(reg, _channel_key_ready)


def _apply_cascade(plan, reg):
    """
    For each cascade group in the registry, keep only the first member whose
    transport is ready on this machine. The rest are disabled for this run.

    The panel runs independent VOICES in parallel; three transports to one model
    are not three voices. Igor 2026-09-04: 'незачем у одних и тех же моделей
    спрашивать'. The cascade order is defined in channels.json `_cascade_groups`,
    cheapest/free transport first, and mirrors the --ask chain for Spark.
    """
    groups = reg.get("_cascade_groups") or []
    notes = []
    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            continue
        members = [c for c in group if c in plan and plan[c]["enabled"]]
        if len(members) <= 1:
            continue
        chans = reg.get("channels") or {}
        winner = None
        for c in members:
            if _channel_key_ready(chans.get(c) or {}):
                winner = c
                break
        if winner is None:
            winner = members[0]
        skipped = [c for c in members if c != winner]
        for c in skipped:
            plan[c]["enabled"] = False
            plan[c]["why"].append("cascade: %s is ready and takes priority (same model)" % winner)
        notes.append("%s selected; %s skipped (same model, different transport)"
                     % (winner, ", ".join(skipped)))
    return plan, notes


# Extensions treated as scannable text inside --attach-dir. Anything else is sniffed for a NUL
# byte and skipped as binary - LOUDLY, because a folder file the secrets scan silently skipped
# is a folder file that reaches a CLI reviewer unscanned.
_ATT_TEXT_EXT = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv",
                 ".html", ".htm", ".xml", ".js", ".ts", ".css", ".rst", ".tex", ".log", ".cfg"}


_SECRET_NAME_RE = re.compile(
    # `[^\\/]*\.env`, not `\.env` (R75): the dotfile-only form let `prod.env` / `dev.env` -
    # the docker-compose env_file convention - walk past a gate written for exactly that
    # class. `$` plus the mandatory dot keep `envelope.md` and `x.envision` out.
    r"(?i)(?:^|[\\/])(?:[^\\/]*\.env(?:\.[^\\/]*)?|[^\\/]*\.(?:pem|key|p12|pfx|ppk|jks|keystore)|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)[^\\/]*|credentials(?:\.json)?)$")


def _secret_shaped_name(path):
    """A NAME that promises key material. Used ONLY for files the content scan already SKIPPED
    (binary / over the size cap / past the file cap): there the name is the only signal left,
    and for the secrets class a false refusal is cheaper than a silent hand-off to a refs-mode
    CLI reviewer. Everywhere else the content scan rules and this must not be consulted -
    basename-is-not-a-path-check. `.pub` halves of key pairs are public and pass."""
    p = (path or "")
    if p.lower().endswith(".pub"):
        return False
    return bool(_SECRET_NAME_RE.search(p))


def _scan_dir_texts(dirs, max_bytes=2_000_000, max_files=200):
    """(label, text) parts for the secrets/PII scan over attached folders, plus what was SKIPPED.

    The caps exist because a folder is unbounded and the scan runs before every round; every
    skip is returned by name so the caller can print it - a bounded scan that reads as a
    complete one is the truncated-check lie the citation audit already refuses to tell.
    """
    parts, skipped = [], []
    seen = 0
    for d in dirs or []:
        for root, _dn, files in os.walk(d):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                if seen >= max_files:
                    skipped.append("%s (file cap %d reached)" % (p, max_files))
                    continue
                try:
                    size = os.path.getsize(p)
                except OSError:
                    skipped.append("%s (unreadable)" % p)
                    continue
                if size > max_bytes:
                    skipped.append("%s (%d KB over the %d KB scan cap)"
                                   % (p, size // 1000, max_bytes // 1000))
                    continue
                try:
                    with open(p, "rb") as fh:
                        raw = fh.read(max_bytes + 1)
                except OSError:
                    skipped.append("%s (unreadable)" % p)
                    continue
                # 🔴 A UTF-16 BOM NAMES A FILE AS TEXT BEFORE THE NUL SNIFF CALLS IT BINARY.
                # agy36flash, reviewing this feature on the day it shipped: UTF-16 encodes ASCII
                # as byte pairs CONTAINING NUL, and Windows tools (PowerShell redirects, some
                # editors) emit UTF-16LE routinely - so the sniff alone would skip real text
                # files, and a secret inside one would be unscanned while a CLI reviewer in
                # refs mode can still read it. Decoding by BOM also fixes the quieter half: a
                # UTF-16 file with a whitelisted extension used to be read as UTF-8, whose
                # NUL-interleaved characters can never match a secret pattern - a scan that ran
                # and could not find anything.
                if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                    parts.append(("attach-dir:" + p, raw.decode("utf-16", "replace")))
                    seen += 1
                elif (os.path.splitext(fn)[1].lower() in _ATT_TEXT_EXT
                        or b"\x00" not in raw[:4096]):
                    # utf-8-sig, not utf-8 (R73, spark + grokbuild): the one read in the
                    # payload path that R71's BOM sweep missed. A leading U+FEFF does not
                    # break the secret regexes (it is not a letter), so this is consistency,
                    # not a gate hole - but two decoders for one class is how the next
                    # pattern edit gets tested against only one of them.
                    parts.append(("attach-dir:" + p, raw.decode("utf-8-sig", "replace")))
                    seen += 1
                else:
                    skipped.append("%s (binary)" % p)
    return parts, skipped


def _attach_inline(atts, att_dirs, n_vetted=None, n_skipped=None):
    """The attachment section API channels receive: full file text, folders named as absent."""
    out = []
    for i, (p, text) in enumerate(atts, 1):
        out.append("\n\n---\n\nATTACHMENT %d: %s\n\n%s" % (i, os.path.basename(p), text))
    if att_dirs:
        # Named as absent rather than silently dropped: a reviewer told about a folder it cannot
        # see would otherwise "read" it from imagination, which is the exact fabrication class
        # the panel exists to catch.
        counts = ""
        if n_vetted is not None:
            counts = (" (host-machine reviewers receive a vetted copy: %d file(s)%s)"
                      % (n_vetted,
                         ", %d excluded by the vetting scan" % n_skipped if n_skipped else ""))
        out.append("\n\n---\n\nNOTE: %d supporting folder(s) accompany this brief but are NOT "
                   "included here - only reviewers running on the host machine can read "
                   "folders%s. Do not claim to have read them." % (len(att_dirs), counts))
    return "".join(out)


def _attach_refs(atts, att_dirs, skipped=None):
    """The attachment section CLI channels receive: absolute paths plus the read-only contract.

    The rules ride in the BRIEF, not only in a persona slot, because placement is load-bearing
    and was measured (R40): the same ban obeyed 2/2 from the brief and 0/1 from the persona.
    The no-write backstop differs per channel and is stated precisely because codex's R46
    review caught the loose version overclaiming: codex runs in a read-only SANDBOX (writes
    impossible); grok build has no write tool GRANTED (absent, not gated); agy's session still
    advertises write/exec tools behind `request-review` - in headless mode an attempt is
    auto-denied and the denial DISCARDS THE RUN, so its guarantee is enforcement by death of
    the turn, not absence of the capability. This text is the instruction layer on top.

    R75: a folder entry may be a (vetted_copy_path, original_path) pair - the path HANDED OUT
    is the vetted copy, and the original never appears in the payload, not even in the skip
    manifest (a path in the brief is an invitation to open it, and codex/grok reads are not
    bounded by cwd). `skipped` names what the vetting excluded, by RELATIVE path only, so the
    reviewer knows the folder copy is partial instead of hallucinating the missing files.
    """
    lines = ["\n\n---\n\nDOCUMENTS ON DISK - READ THEM YOURSELF\n",
             "You are running on the machine that holds the material under review. Open these "
             "ABSOLUTE paths with your file-reading tools; they are part of the brief. You may "
             "also read neighbouring material in the same folders when the review needs it.\n"]
    for i, (p, text) in enumerate(atts, 1):
        lines.append("- FILE %d: %s  (%d bytes)" % (i, p, len(text.encode("utf-8"))))
    for d in att_dirs:
        if isinstance(d, tuple):
            lines.append("- FOLDER: %s  (a vetted copy of the supporting folder %r - list it, "
                         "read what the review needs)" % (d[0], os.path.basename(d[1])))
        else:
            lines.append("- FOLDER: %s  (supporting material - list it, read what the review "
                         "needs)" % d)
    if skipped:
        shown = skipped[:30]
        lines.append("\nNOT INCLUDED in the folder copies - excluded by the vetting scan. Do "
                     "not claim to have read these; if one matters, say so and the operator "
                     "can attach it explicitly:")
        for s in shown:
            lines.append("- %s" % s)
        if len(skipped) > len(shown):
            lines.append("- ... and %d more (the run log lists them all)"
                         % (len(skipped) - len(shown)))
    lines.append(
        "\nSTRICT RULES for these locations, non-negotiable:\n"
        "- READ ONLY. Do not modify, create, delete, move or rename ANYTHING there or anywhere "
        "else in that project. You have no write tools in this run; do not try to obtain any.\n"
        "- Do not run shell commands on these paths; use your file-reading tools only.\n"
        "- Your deliverable is the review TEXT in your reply - the harness saves it to its own "
        "output folder. You write no files anywhere.\n"
        "- If a path does not open, say so plainly and review what you could read.")
    return "\n".join(lines)


def _vet_snapshot(att_dirs, dir_parts, snap_root):
    """Plan the vetted folder copies CLI reviewers receive instead of the original folders.

    R75 (codex, R73, deferred from R74): refs mode used to hand CLI reviewers the ORIGINAL
    folder path, so every file the scan SKIPPED - oversized, binary, past the file cap -
    was readable by them unscanned. The R74 name gate closed only the key-shaped corner.
    Now the reviewers get a copy holding EXACTLY the decoded text the gate scanned, so
    «vetted» and «reachable» are the same set by construction. The copy is written as the
    SCANNED TEXT, not the original bytes: copying bytes would re-open a byte-level gap the
    scan never saw (a secret split by invalid UTF-8 survives decode-with-replace as broken
    text but survives a byte copy intact).

    Returns (pairs, writes): pairs = [(snapshot_dir, original_dir)] aligned with att_dirs,
    writes = [(destination_path, text)]. Nothing is written here - main() writes AFTER the
    gate passes, so a refused round leaves no copy behind.
    """
    pairs, writes = [], []
    for i, d in enumerate(att_dirs, 1):
        base = re.sub(r"[^A-Za-z0-9._-]", "_",
                      os.path.basename(d.rstrip("\\/")))[:40] or "dir"
        pairs.append((os.path.join(snap_root, "%d-%s" % (i, base)), d))
    for label, text in dir_parts:
        p = label[len("attach-dir:"):]
        owners = [(sd, d) for sd, d in pairs if p.startswith(d.rstrip("\\/") + os.sep)]
        if not owners:
            continue
        sd, d = max(owners, key=lambda t: len(t[1]))
        writes.append((os.path.join(sd, os.path.relpath(p, d)), text))
    return pairs, writes


def _write_vetted_snapshot(pairs, writes):
    """Write the planned copies. newline='' keeps the scanned text's own line endings."""
    for sd, _d in pairs:
        os.makedirs(sd, exist_ok=True)
    for dest, text in writes:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


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
    # default=None so "not passed" is detectable: the real default is RESOLVED from the
    # registry's `ask_default` list by which transport key is actually present (R69). The old
    # literal default pointed a kit user holding only an OPENROUTER_API_KEY at the one channel
    # they cannot run - graceful degradation, wrong default, for exactly the user the kit is for.
    ap.add_argument("--ask-channel", default=None, metavar="CHANNEL",
                    help="the FIRST channel --ask uses when --only is not given. Default: "
                         "resolved from `ask_default` in channels.json - the first entry whose "
                         "API key is present on this machine - and the choice is printed when it "
                         "fires. Every channel the registry prices `free` runs alongside it - "
                         "that set is read from channels.json, so a free channel added later "
                         "joins on its own. 🔴 The default candidates and the free tiers are "
                         "contributor tiers: their vendors may train on what you send. Pass "
                         "--only spark11 for anything you would not publish")
    ap.add_argument("--system", help="path to the system prompt for the HTTPS channel")
    # 🔴 TWO DELIVERY MODES FOR ONE DOCUMENT, split by what a channel can physically reach.
    # Igor, R46: «CLI агентам (Codex CLI, Agy CLI, Grok Build) не отправлять файлы, а отправлять
    # ссылки на документы и папки, что бы если надо, они могли сами изучить так же дополнительное.
    # Но обязательно прописать им, что бы ничего не изменяли и не удаляли.» A remote API cannot
    # read this disk, so the same --attach inlines for API channels and references for CLI ones;
    # the read-only contract is prose in the brief PLUS a mechanical no-write fence per channel.
    ap.add_argument("--attach", action="extend", nargs="*", default=[], metavar="FILE",
                    help="a document reviewed alongside the brief. CLI channels (codex, agy, "
                         "grok build) get the ABSOLUTE PATH and read it themselves, read-only, "
                         "so they can also consult surrounding material; API channels get it "
                         "INLINED as an ATTACHMENT block. The secrets scan covers the content "
                         "either way. Repeatable")
    ap.add_argument("--attach-dir", action="extend", nargs="*", default=[], metavar="DIR",
                    help="a folder of supporting material, BY REFERENCE, for CLI channels only "
                         "- API channels are told it exists and that they cannot read it. "
                         "Read-only; text files inside are secrets-scanned (skips are printed "
                         "by name). Repeatable")
    # Igor, R72 (2026-08-31): the session that ordered a panel should usually read the answers
    # ITSELF, and what stopped it was sheer volume - so ask every reviewer to write the essence.
    # A prompt instruction, deliberately NOT max_tokens: a token ceiling cuts mid-sentence and
    # starves reasoning models (R43/R47), while an instruction bounds the ANSWER and leaves the
    # thinking at full depth. Advisory by design; the handoff MEASURES who exceeded it.
    ap.add_argument("--answer-cap", type=int, default=None, metavar="CHARS",
                    help="ask every reviewer to keep its FINAL answer under this many "
                         "characters (default 20000 for reviews, 0 in --ask mode; 0 = no "
                         "length instruction). Enforced by prompt and measured on return, "
                         "never by max_tokens - depth is not reduced")
    # 🔴 SELECTING A CHANNEL AND AUTHORISING ITS BILL ARE TWO DIFFERENT ACTS, and until 2026-08-14
    # they were one. `--only <name>` both chose a default-OFF channel and paid for it, which reads
    # as deliberate when a human types one name and means nothing at all when an agent session
    # enumerates fifteen - which is exactly what happened: orgpt56terrapro appeared in a
    # fifteen-name --only list, billed $12.08, returned no review, and emptied the OpenRouter key's
    # monthly cap so that four channels (including the FREE one) failed in the next round.
    # Enumeration is not a decision. `action="extend"` for the same reason as --only/--skip below.
    ap.add_argument("--accept-spend", dest="accept_spend", nargs="*", action="extend", default=[],
                    metavar="CHANNEL",
                    help="authorise the bill for a channel that declares spend_guard.requires_ack "
                         "(currently orgpt56terrapro). Pass the channel name, or `all`. Selecting "
                         "such a channel without this flag is a hard refusal, not a warning. "
                         "--dry-run never needs it: seeing what a round would cost must not "
                         "require agreeing to pay for it.")
    # Choices come from the registry, so deleting a tier there really deletes it. Igor removed
    # `quick` and `standard` on 2026-08-08; with the old literal list this flag would have gone
    # on accepting both and silently falling through to per-branch defaults.
    ap.add_argument("--tier", default=_default_tier(), choices=_tier_choices(),
                    help="depth per channel. Since 2026-08-15 there is exactly ONE tier and it is "
                         "every channel's own ceiling - `strategic` and `deep` still parse, as "
                         "aliases of it, so old commands keep working. The resolved depth per "
                         "channel is printed in the plan.")
    # 🔴 A SECOND AXIS, NOT A SECOND TIER. `--tier` is how deep each reviewer goes; `--panel` is
    # which reviewers are in the room. Igor, 2026-08-15: «разделим оркестр[ацию] на дешевую и
    # стандартную». They compose freely - `--panel cheap --tier deep` is few voices thinking
    # hard, which is a sensible and cheap way to ask a difficult question. `choices` comes from
    # the registry for the same reason --tier's does: deleting a panel there must really delete
    # it, rather than leaving the flag accepting a name that now filters nothing.
    _panels = load_panels()
    ap.add_argument("--panel", default=None, choices=_panels or None,
                    help="which reviewers are in the room (%s). Filters DOWN only - unlike "
                         "--only it never enables a channel the registry has off, because "
                         "`enabled` is what distinguishes this install from the published kit. "
                         "Default comes from `default_panel` in channels.json; the plan always "
                         "prints what the other panel would add or drop, by name."
                         % ("|".join(_panels) or "none defined"))
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
                    help="refuse to send when the payload contains personal identifiers. OFF by "
                         "default, and since 2026-08-16 the itemised warning is off too - a run "
                         "prints one summary line and sends. SECRETS are refused always and have "
                         "no override at any setting")
    ap.add_argument("--warn-pii", action="store_true",
                    help="restore the per-identifier warning list (kind and line, never the "
                         "value). Between this and --strict-pii the gate has three settings: "
                         "off (default), warn, refuse")
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
    ap.add_argument("--resolve-grounding-links", action="store_true",
                    help="follow google_search grounding Links to their destination pages during "
                         "the citation audit. OFF by default, and the reason is Google's terms, "
                         "not a technical limit: ai.google.dev/gemini-api/terms forbids using "
                         "Links 'to identify destination pages for crawling or scraping', and "
                         "defines Links to include the titles served with them. A single-user "
                         "citation audit is arguably not that, but this kit is public and the "
                         "default is what strangers run. Without it the channel is still reported "
                         "- its publisher DOMAINS come from the response and cost no request")
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
            # utf-8-sig: same BOM class as the brief read below - this was the fourth payload
            # read and the one R71 missed (grokbuild, R73).
            with open(src, encoding="utf-8-sig") as fh:
                question = fh.read()
        if a.marker == "REVIEW-COMPLETE":
            a.marker = "ASK-DONE"
        if a.ask_channel is None:
            a.ask_channel = _ask_default_channel()
            log("--ask channel: %s (resolved from `ask_default` in channels.json by which "
                "transport key is present; --ask-channel <name> overrides)" % a.ask_channel)
        if not a.only:
            a.only = [a.ask_channel] + _free_extras(a.ask_channel)
            if len(a.only) > 1:
                log("--ask goes to %d channels: %s. Everything after the first is priced `free` "
                    "in channels.json, so a second opinion on a lookup costs nothing but the "
                    "seconds. 🔴 Free and near-free tiers are usually paid for with your text - "
                    "the plan below prints each channel's data policy. --only <channel> or "
                    "--skip <channel> narrows it." % (len(a.only), ", ".join(a.only)))
        if a.out == "./reviews":
            a.out = os.path.join(tempfile.gettempdir(), "orchestrate-ask")
        a.no_citecheck = True        # a lookup is not a review; the audit is for cited briefs
        global _citecheck_reason
        _citecheck_reason = ("--ask is a lookup, not a review - the citation audit is for briefs "
                             "that cite sources. You did NOT pass --no-citecheck.")
        # 🔴 The cap defaults OFF here (grokbuild, R73): the ask brief used to promise «no
        # length requirement» while the default review cap appended «keep it under 20000
        # chars» to the same payload - two contradictory instructions, and which one a model
        # obeys is a coin toss. An explicit --answer-cap N with --ask is honoured, and then
        # the no-length sentence is dropped instead of the cap.
        if a.answer_cap is None:
            a.answer_cap = 0
        tmp = os.path.join(a.out, "ask-brief.md")
        try:
            os.makedirs(a.out, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(
                    "# Question\n\n%s\n\n---\n\n"
                    "Answer directly and completely. If the answer depends on something that "
                    "changes, search the web and give the URL and the date you accessed it. "
                    "If your search finds nothing, write exactly \"my search found no "
                    "confirmation\" and do NOT conclude the thing does not exist.%s\n"
                    % (question,
                       "" if a.answer_cap else " Do not pad: there is no length requirement "
                                              "here, in either direction."))
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
    reg = None
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
        # 🔴 `--only` AND `--panel` ADDED TO THIS LIST 2026-08-15. Every flag here is one whose
        # entire meaning is "run a DIFFERENT set of channels than the default", so a fallback
        # that ignores it does not degrade gracefully - it runs the set the user just said not
        # to run, and says only that routing is unavailable. `--only` was missing from the day
        # the list was written, which is the same shape as the panel-vs-group trap this round
        # found: a selection mechanism whose failure mode is silently selecting everything.
        if a.route or a.skip or a.sets or a.only or a.panel:
            log("  ...and %s cannot be honoured without it. Refusing rather than running a "
                "different set of channels than you asked for."
                % ", ".join(f for f, v in (("--route", a.route), ("--skip", a.skip),
                                           ("--set", a.sets), ("--only", a.only),
                                           ("--panel", a.panel)) if v))
            return 2
    if routing is not None:
        try:
            reg = routing.load_registry()
            plan = routing.resolve(reg, route=a.route, only=a.only, skip=a.skip,
                                   sets=a.sets, tier=a.tier, panel=a.panel)
            log(routing.format_plan(plan, reg))
        except routing.RouteError as e:          # ambiguity must stop the run, never guess
            log("ROUTE ERROR: %s" % e)
            return 2

    # utf-8-sig, not utf-8: a hand-made brief saved by Notepad or PowerShell 5 `Out-File` carries
    # a BOM, and plain utf-8 ships U+FEFF as the payload's FIRST character to every channel - the
    # "BOM poisoning" class a sister project's audit panel named 2026-08-17; its runner has read
    # briefs this way since, ours had not (harvested R71). On a BOM-less file utf-8-sig reads
    # byte-identically to utf-8.
    with open(a.brief, encoding="utf-8-sig") as f:
        brief = f.read()

    # ---- attachments: one document, two delivery modes -------------------------------------
    atts, att_dirs = [], []
    for pth in (a.attach or []):
        p_abs = os.path.abspath(os.path.expanduser(pth))
        if not os.path.isfile(p_abs):
            log("--attach %s: file not found" % pth)
            return 2
        try:
            # utf-8-sig: same BOM class as the brief read above - an attached file's BOM would
            # otherwise ride into the payload as an inline U+FEFF.
            with open(p_abs, encoding="utf-8-sig", errors="replace") as fh:
                atts.append((p_abs, fh.read()))
        except OSError as exc:
            log("--attach %s: cannot read (%s)" % (pth, exc))
            return 2
    for pth in (a.attach_dir or []):
        p_abs = os.path.abspath(os.path.expanduser(pth))
        if not os.path.isdir(p_abs):
            log("--attach-dir %s: not a directory" % pth)
            return 2
        att_dirs.append(p_abs)
    if atts or att_dirs:
        log("attachments: %d file(s), %d chars total%s"
            % (len(atts), sum(len(t) for _p, t in atts),
               ("; %d folder(s), as a vetted copy" % len(att_dirs)) if att_dirs else ""))
        log("  API channels receive the file(s) INLINE%s. CLI channels (codex, agy, grok build) "
            "receive ABSOLUTE PATHS and read from this disk themselves - read-only, no write or "
            "shell tools, and they may consult surrounding material."
            % ("; folders reach CLI channels only, as a VETTED COPY of the scanned files"
               if att_dirs else ""))
        log("  🔴 refs mode TRUSTS the attached material: a hostile document can steer a CLI "
            "reviewer's read tools (grok build's read_file is not bounded by its cwd - "
            "measured). Attach material you authored or trust; send foreign documents inline "
            "in the brief instead.")

    # Attached FOLDERS are scanned file by file HERE, before the payload is assembled, because
    # the refs section must name the VETTED COPY and its skip manifest, not the original dir
    # (R75; codex, R73: the original path handed to a CLI reviewer made every skipped file
    # readable unscanned - the fail-closed copy was designed then and deferred to this round).
    # Nothing is written yet: the copy lands on disk only after the gate below passes.
    dir_parts, dir_skipped = _scan_dir_texts(att_dirs)
    # 🔴 R74, five R73 reviewers converged on this hole: a SKIPPED file whose NAME is
    # secret-shaped (.env, .pem, id_rsa, a keystore) refuses the round outright. The R75
    # vetted copy already excludes every skipped file structurally, but the refusal STAYS:
    # a credential file inside attached material is an operator error worth stopping loudly,
    # not smoothing over - and the alarm is what stops the NEXT round attaching the same
    # folder somewhere with no vetting. rsplit, not split: a path may itself contain " ("
    # («C:\docs (old)\x.env»), and the left split handed the name check a truncated path.
    _key_shaped = [s for s in dir_skipped if _secret_shaped_name(s.rsplit(" (", 1)[0])]
    if _key_shaped and (atts or att_dirs):
        log("🔴 SECRET-SHAPED FILE(S) SKIPPED BY THE SCAN inside an attached folder - refusing "
            "the round. The vetted copy would exclude them, but a credential file riding in "
            "review material is an operator error to fix, not to smooth over:")
        for s in _key_shaped:
            log("    %s" % s)
        log("  Remove them from the folder (or rename the copy) and re-run. There is no "
            "override for the secrets class.")
        return 3
    _snap_pairs, _snap_writes = _vet_snapshot(
        att_dirs, dir_parts, os.path.join(a.out, "attach-vetted"))
    _skip_manifest = []
    for s in dir_skipped:
        pth, _sep, why = s.rpartition(" (")
        owners = [d for d in att_dirs if pth.startswith(d.rstrip("\\/") + os.sep)]
        rel = os.path.relpath(pth, max(owners, key=len)) if owners else os.path.basename(pth)
        _skip_manifest.append("%s (%s" % (rel, why))
        # Console line keeps the FULL path (the operator needs it); the payload manifest above
        # carries the relative form only - an absolute path in a brief is an invitation for an
        # unbounded CLI read tool to open the unvetted original.
        log("  [attach-dir] excluded from the vetted copy: %s - CLI reviewers cannot read it "
            "there. If it must be reviewed, attach the file explicitly with --attach." % s)

    # The length discipline rides INSIDE the payload, before the marker block, so the final
    # layout keeps the marker last: [brief][cap][attachments][marker]. It bounds the ANSWER and
    # explicitly not the work - without that sentence a cap reads as «be quick», which would
    # silently undo the R43 «мозги на максимум» decree. The escape hatch (exceed for a MATERIAL
    # finding, after cutting repetition) is deliberate: a hard cap invites dropping the one
    # finding that mattered, and TRUNCATED-BY-LIMIT makes that drop VISIBLE - the handoff scans
    # for the token and names the channels that declared it.
    # Review-mode default, resolved late so --ask above can default to 0 (argparse default is
    # None precisely so the two modes can differ; an explicit --answer-cap wins in both).
    if a.answer_cap is None:
        a.answer_cap = 20000
    # Built as a BLOCK rather than appended to the brief here (R74): on an attachment round the
    # brief is followed by up to a megabyte of documents, and an instruction buried a megabyte
    # before the end is one models drop (agy31pro, R73). The splice below lands this block
    # AFTER the attachments, directly before the marker line - the last words are the ones
    # models obey, which is the same reasoning the marker splice already encodes.
    _cap_block = ""
    if a.answer_cap and a.answer_cap > 0:
        _cap_block = ("\n\n---\nLength discipline for your FINAL answer: keep it under about %d "
                      "characters (~%d tokens). This bounds the ANSWER, not the work - think, "
                      "search and verify at full depth, then write the essence: verdicts first, "
                      "one finding per short paragraph, evidence quotes trimmed to the operative "
                      "words. If you must exceed the limit to keep a MATERIAL finding, exceed "
                      "it - but first cut repetition, preamble and restatement; and if something "
                      "material still had to be dropped, list what was dropped and end that "
                      "list with the line TRUNCATED-BY-LIMIT.\n"
                      % (a.answer_cap, a.answer_cap // CHARS_PER_TOKEN))

    # Every channel is VERIFIED against the end marker, but until 2026-07-31 nothing ever asked
    # the model to emit one: the brief's author was silently expected to know. A brief written by
    # anyone who had not read the docs therefore came back PROBLEM on all three channels with a
    # perfectly good review inside - the worst kind of failure, because the harness looked broken
    # while the models had done their job. Measured on a two-line hand-written brief.
    #
    # Appended only when the brief does not already contain the marker, so an author who did
    # write the instruction gets no duplicate, and `--marker ""` still disables the check.
    if a.marker and a.marker not in brief:
        # 🔴 A BRIEF THAT ALREADY ENDS WITH A *DIFFERENT* MARKER IS THE CASE THIS TEST MISSES,
        # and it costs a whole round's verdict. Measured 2026-08-15 while re-testing two channels
        # on a REUSED brief from the other project: its last line was `КОНЕЦ-ОТВЕТА-R34` while
        # the command line said `--marker R43-DONE`. Both instructions then sat in the payload,
        # the in-brief one last and in the reader's own language, and the channels SPLIT - one
        # obeyed the appended line and passed, the other obeyed the brief's own and was graded
        # «END MARKER NOT ON LAST LINE - output is partial, do not parse it» on top of a complete
        # 47 KB review. The condition above asks «is MY marker here?» when the question that
        # matters is «is SOMEONE ELSE'S here?». Reusing a brief is normal practice, so this is not
        # exotic. It cannot be auto-resolved - only the human knows which marker is current - so
        # it warns loudly at preflight, before anything is spent, and names both.
        tail = [ln.strip() for ln in brief.strip().splitlines() if ln.strip()]
        last = tail[-1] if tail else ""
        if (last and len(last) <= 60 and " " not in last
                and last.upper() == last and any(ch.isalpha() for ch in last)):
            log("🔴 [preflight] the brief ALREADY ENDS with %r, and --marker says %r. Both will "
                "reach the model and it will pick one; channels have been measured to split. "
                "Every channel that obeys the brief will be graded «output is partial» over a "
                "complete review. Pass --marker %s, or edit the brief's last line."
                % (last, a.marker, last))
        brief += ("\n\n---\nWhen the ENTIRE review is finished, end your reply with this exact "
                  "line and nothing after it:\n%s\n" % a.marker)
    # Two payload variants from here on. `brief` (inline attachments) stays the canonical one -
    # it is what the gate scans, what the size line reports, and what every API channel gets;
    # `brief_refs` differs ONLY in the attachment section and goes to REFS_KINDS channels.
    # The marker instruction lands at the very end of BOTH, after a split-and-splice: models
    # obey the LAST instruction, and an attachment block after the marker line would bury it.
    # The answer-cap block rides the same splice (R74): [brief][attachments][cap][marker].
    _mk = ""
    if a.marker:
        _pos = brief.rfind("\n\n---\nWhen the ENTIRE review is finished")
        if _pos != -1:
            brief, _mk = brief[:_pos], brief[_pos:]
    if atts or att_dirs:
        brief_refs = (brief + _attach_refs(atts, _snap_pairs, _skip_manifest)
                      + _cap_block + _mk)
        brief = (brief + _attach_inline(atts, att_dirs, n_vetted=len(_snap_writes),
                                        n_skipped=len(dir_skipped))
                 + _cap_block + _mk)
    else:
        brief = brief + _cap_block + _mk
        brief_refs = brief
    # The default used to be a single sentence, and it reached only the HTTPS channel. Depth,
    # source discipline and output language were therefore left to whatever each model defaults
    # to - which for a terminal-tuned CLI is "short, fast, few tool calls". base-depth.md is the
    # amplifier that used to be pasted by hand into briefs; it now ships and applies everywhere.
    system = "You are an independent reviewer. You are NOT the author. Find what is wrong."
    try:
        # utf-8-sig: the system layer rides in FRONT of the brief on CLI channels
        # (_with_system), so a BOM in a user-authored --system file would again be the
        # payload's leading character.
        with open(_resolve_system(a.system or "base-depth"), encoding="utf-8-sig") as f:
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
    # «Иная информация на твое усмотрение» (Igor, R47) - structural, not a brief-writing habit,
    # because rules fire by topic and habits decay: R46's hand-written version of this question
    # returned four findings that shipped the same round, and SKILL.md has said for weeks that
    # the highest-value finding is never an answer to an asked question. Appended to the SYSTEM
    # layer so it reaches every channel on every round without anyone remembering it. Bounded on
    # purpose - items the reviewer would DEFEND - and an explicit "nothing" is legal, so the
    # section cannot manufacture content to fill itself. Skipped for --ask: a lookup is not a
    # review, and padding a one-line answer with an essay section would make the cheap path
    # expensive to read.
    if not ask_mode:
        system += ("\n\nAfter the brief's own questions are answered, add one final section "
                   "titled UNASKED: anything important the brief did not ask about - a wrong "
                   "assumption, a risk you noticed, a better alternative, a thing worth "
                   "checking next. Only items you would defend as significant. If there is "
                   'nothing, write exactly "UNASKED: nothing beyond the questions."')

    # Last point at which anything can still be stopped for free. Both files are scanned: a
    # hand-written --system file is just as capable of carrying a name or a key as the brief.
    if a.allow_pii:
        log("  note: --allow-pii is now a no-op - personal identifiers are sent by default and "
            "the gate no longer even lists them. Use --warn-pii or --strict-pii to turn it back on.")
    # Attached FILES are already inside `brief` (inline variant), so the same scan covers them.
    # Attached FOLDERS were scanned into dir_parts BEFORE the payload was assembled (R75: the
    # refs section names the vetted copy and its skip manifest, so the scan had to move up);
    # this gate is still the last point at which anything can be stopped for free, and the
    # vetted copy is written only after it passes.
    gate = pii_gate([("brief", brief), ("system", system)] + dir_parts,
                    strict_pii=a.strict_pii, warn_pii=a.warn_pii)
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
                (a.only or ["spark11", "spark13cont", "codex", "agy31pro", "agy36flash",
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

    # The vetted folder copy is written only now: after the gate passed, after --dry-run has
    # had its free exit (a dry run materialises no payload artifacts - the scan log above
    # already names every exclusion), and only when a channel that reads by reference is
    # actually in the round - an API-only round never needs the copy on disk.
    if att_dirs and any(k in REFS_KINDS for k in kinds.values()):
        _write_vetted_snapshot(_snap_pairs, _snap_writes)
        log("  [attach-dir] vetted copy written: %d file(s) under %s (%d excluded)"
            % (len(_snap_writes), os.path.join(a.out, "attach-vetted"), len(dir_skipped)))

    # 🔴🔴 THE ONE-SHOT-WRITE GATE. Three reviewers, independently, found the hole in 1.8.0's own
    # fix: the home settings file survives every update, so a single compromised assistant session
    # writing `model` or `provider` into it would re-point a channel FOREVER, silently. The plan's
    # red marker does not answer that - the plugin path removes the human who would read it, which
    # is the same sentence that killed the 1.7.0 design. So the money stops here until a human has
    # run one command. `--dry-run` above still works, on purpose: seeing what WOULD happen must
    # never require accepting it first.
    # 🔴🔴 THE SPEND GATE. Same shape and same place as the settings gate below, on purpose: refuse
    # rather than warn, print the number, and let --dry-run through untouched. What it answers is
    # the question the plan could not: a channel can be SELECTED by an agent enumerating the
    # registry, but it cannot be PAID FOR without a flag whose only meaning is "yes, that money".
    #
    # 🔴 GATED ON `requires_ack` ALONE, NOT ON "was it overridden". The first cut read
    # `requires_ack and not default_enabled`, i.e. it only fired for a channel that was off by
    # default and got re-selected. agy31pro, reviewing this diff the same day, named the hole:
    # anything that flips the channel ON BY DEFAULT makes `default_enabled` true and the gate
    # silently stops existing, turning the registry's own `requires_ack: true` into a decorative
    # field - this project's most-repeated defect, reintroduced inside its own fix. The selftest
    # asserts the shipped registry never pairs requires_ack with enabled:true, but the local
    # settings overlay can set `enabled` at run time and no test covers a user's own file. So the
    # declaration alone now arms the gate: if a channel says its bill needs authorising, it needs
    # authorising however it came to be selected.
    need_ack = []
    for cname in sorted(want):
        p = (plan or {}).get(cname) or {}
        g = p.get("spend_guard") or {}
        if g.get("requires_ack"):
            need_ack.append((cname, g))
    acked = {s.lower() for s in (a.accept_spend or [])}
    missing = [(c, g) for c, g in need_ack if "all" not in acked and c.lower() not in acked]
    if missing:
        log("\n" + "=" * 78)
        log("REFUSING TO SPEND: %d channel(s) in this round are OFF BY DEFAULT because they cost "
            "real money, and nothing in this invocation authorised that." % len(missing))
        for cname, g in missing:
            log("  %s" % cname)
            if g.get("measured_usd"):
                log("      measured: %s" % g["measured_usd"])
            if g.get("max_usd_per_review"):
                log("      ceiling once it runs: $%.2f per review"
                    % float(g["max_usd_per_review"]))
        log("  Naming a channel selects it; it does not authorise the bill. If you meant to pay:")
        log("      --accept-spend %s" % " ".join(c for c, _ in missing))
        # 🔴 THE REFUSAL PRINTS ITS OWN BYPASS, and three of eight reviewers said so on the day it
        # shipped: an autonomous session reads the remediation line and re-runs with the flag,
        # which is exactly the repair behaviour agents are built to have. Nothing mechanical can
        # stop that - a session with a shell can pass any flag - so the honest position is that
        # this gate is HARD against accident and SOFT against an agent, and the sentence aimed at
        # the agent goes here, where it is actually read, rather than in a document it will not
        # open. Same placement rule as round 40's brief-versus-persona measurement.
        log("  IF YOU ARE AN AUTOMATED SESSION: do not re-run with that flag unless a human named "
            "this channel. Report this refusal and the price instead - authorising a bill is not "
            "an error to repair.")
        log("  If you did NOT mean to run it - which is the usual case when a channel list was "
            "generated rather than chosen - drop it from --only, or add --skip %s."
            % " ".join(c for c, _ in missing))
        log("  --dry-run shows the whole plan, including these channels, without this flag.")
        log("=" * 78)
        return 2
    if need_ack:
        for cname, g in need_ack:
            log("  [spend] %s authorised by --accept-spend; ceiling $%s per review"
                % (cname, g.get("max_usd_per_review", "none")))

    ov = (reg or {}).get("_overlay") or {}
    if ov.get("sharp") and not ov.get("sharp_acked"):
        log("\n" + "=" * 78)
        log("REFUSING TO SPEND: your settings file changes where documents go, and that change "
            "has not been accepted.")
        log("  file: %s" % ov.get("path"))
        for cname, field, before, after in ov["sharp"]:
            log("    %s.%s: %s -> %s" % (cname, field, routing._short(before),
                                         routing._short(after)))
        log("  That file survives every update, so one write to it would otherwise redirect a "
            "channel permanently.")
        log("  If you made these changes, run once:")
        log("      python \"%s\" --accept-settings" % os.path.join(SKILL_DIR, "routing.py"))
        log("  If you did NOT make them, open the file before doing anything else.")
        log("=" * 78)
        return 2

    os.makedirs(a.out, exist_ok=True)

    # Key-level spend meter, read before and after the round. Only when an OpenRouter-billed
    # channel is actually in the plan: on any other round the two extra requests would measure
    # nothing this run did.
    uses_or_key = any((((plan or {}).get(c) or {}).get("kind") == "openrouter")
                      or (((plan or {}).get(c) or {}).get("provider") == "openrouter")
                      for c in want)
    credits_before = openrouter_credits() if uses_or_key else None

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
            # The channel NAME rides on the slot so _record_system keys per channel, not per
            # display label - labels collide (three "Gemini 3.6 Flash" seats), names cannot.
            p["_name"] = cname
            kind = p.get("kind")
            outfile = os.path.join(a.out, cname.upper() + ".md")
            workdir = os.path.join(a.out, cname + "-ws")
            # Which payload variant this channel gets. Identical unless --attach/--attach-dir
            # was passed; then REFS_KINDS read the documents from disk and everyone else gets
            # them inline. One variable per channel, chosen by capability, never by name.
            use_refs = bool((atts or att_dirs) and kind in REFS_KINDS)
            cbrief = brief_refs if use_refs else brief
            # Folder grants point at the VETTED COPY, never the original dir (R75): granting
            # the original would hand agy exactly the unscanned files the copy exists to fence.
            att_parents = (sorted({os.path.dirname(pth) for pth, _t in atts}
                                  | {sd for sd, _d in _snap_pairs})
                           if use_refs else None)
            if kind == "http":
                jobs[cname] = ex.submit(call_http_reviewer, cbrief, _system_for(system, p),
                                        a.tier, a.marker,
                                        timeout=_seconds(p.get("timeout"), 2400),
                                        model=p.get("model"), name=cname,
                                        effort=p.get("effort"),
                                        fallback_model=p.get("fallback_model"),
                                        answer_cap=a.answer_cap if a.answer_cap
                                        and a.answer_cap > 0 else None)
            elif kind == "codex":
                jobs[cname] = ex.submit(call_codex, cbrief, a.marker, workdir, outfile,
                                        model=p.get("model"), effort=p.get("effort"),
                                        timeout=p.get("timeout"),
                                        system=_system_for(system, p))
            elif kind == "agy":
                jobs[cname] = ex.submit(call_agy, cbrief, a.marker, workdir, outfile,
                                        model=p.get("model"),
                                        effort=p.get("effort") or "high",
                                        timeout=p.get("timeout") or "25m",
                                        system=_system_for(system, p),
                                        add_dirs=att_parents)
            elif kind == "grokcli":
                jobs[cname] = ex.submit(call_grokcli, cbrief, a.marker, workdir, outfile,
                                        model=p.get("model"),
                                        effort=p.get("effort"),
                                        timeout=p.get("timeout") or "40m",
                                        system=_system_for(system, p), name=cname,
                                        file_refs=use_refs)
            elif kind == "hermes":
                jobs[cname] = ex.submit(call_hermes, cbrief, a.marker, outfile,
                                        model=p.get("model"), toolsets=p.get("toolsets"),
                                        system=_system_for(system, p),
                                        timeout=_seconds(p.get("timeout"), 2400))
            elif kind in ("openrouter", "oai"):
                # Two kind names, ONE implementation. `openrouter` is kept because it accurately
                # names the channels that go through OpenRouter and because existing installs
                # carry it; `oai` names a direct OpenAI-protocol vendor. The vendor itself is
                # `provider`, and an unknown one is refused inside the call rather than defaulted.
                jobs[cname] = ex.submit(call_oai_reviewer, cbrief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        web=p.get("web"), name=cname,
                                        reasoning=p.get("reasoning"),
                                        max_tokens=p.get("max_tokens"),
                                        fetch_tool=p.get("fetch_tool"),
                                        provider=p.get("provider") or "openrouter",
                                        provider_route=p.get("provider_route"),
                                        spend_guard=p.get("spend_guard"),
                                        fallback_models=p.get("fallback_models"),
                                        timeout=_seconds(p.get("timeout"), 2400))
            elif kind == "xai":
                # 🔴 THE TIER TIMEOUT WAS NEVER PASSED HERE, and the plan claimed otherwise.
                # Found by codex in the round-29 panel, reviewing this very change: the tier note
                # read "no depth knob on this vendor - the tier buys nothing but wall-clock",
                # while the dispatcher passed no timeout at all and call_xai_responses fell back
                # to its 2400-second default in BOTH tiers. So `deep` was a literal no-op here,
                # and the line written to be honest about a missing lever was itself wrong about
                # the one lever that remained. A note that describes a mechanism has to be
                # checked against the mechanism.
                jobs[cname] = ex.submit(call_xai_responses, cbrief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        name=cname, tools=p.get("tools"),
                                        timeout=_seconds(p.get("timeout"), 2400),
                                        max_tokens=p.get("max_tokens"))
            elif kind == "gemini":
                jobs[cname] = ex.submit(call_gemini_direct, cbrief, a.marker, outfile,
                                        model=p.get("model"), system=_system_for(system, p),
                                        name=cname, thinking_level=p.get("thinking_level"),
                                        tools=p.get("tools"), max_tokens=p.get("max_tokens"),
                                        timeout=_seconds(p.get("timeout"), 2400))
            elif kind == "opencode":
                jobs[cname] = ex.submit(call_opencode, cbrief, a.marker, outfile,
                                        model=p.get("model"), effort=p.get("effort"),
                                        system=_system_for(system, p),
                                        timeout=_seconds(p.get("timeout"), 2400),
                                        name=cname)
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
        # 🔴 ONE CHOKE POINT, ON PURPOSE. Every channel's text passes through here on its way to
        # disk, whichever function produced it, so the integrity check is keyed on the DATA and
        # not on a call site - a channel added next month is covered without anyone remembering
        # to add it. Wiring it into each of the six `warn`-building blocks instead is how the
        # per-channel diagnoses in this file went stale one transport at a time.
        # It runs AFTER `ok` has been decided by the channel function, so it cannot flip a
        # review to FAILED. That is the intent, not an accident - see transport_damage().
        _dmg = transport_damage(r.get("text"))
        for _d in _dmg:
            r.setdefault("notes", []).append(_d)
        _rn = _registry_default(name, "reading_note", None)
        if _rn and r.get("ok"):
            r.setdefault("notes", []).append(_rn)
        # 🔴 THE ANSWER IS AN EGRESS TOO (kimik3, R46 panel): the harness scans what it SENDS
        # and scrubs diagnostics/console, but the review text itself is written to disk RAW -
        # deliberately, a reviewer's words are never altered - so a reviewer that read a
        # credential through its refs-mode tools could carry it into the artifact unremarked.
        # A NOTE, never a rewrite. Code spans are stripped first, same recipe and same reason
        # as transport_damage: measured on 15 real reviews, the raw scan fired twice, both on
        # reviews QUOTING a secret pattern in backticks while discussing this very gate; the
        # stripped scan fired zero times on the same corpus.
        if r.get("text"):
            _t2 = re.sub(r"```.*?```", " ", r["text"], flags=re.S)
            _t2 = re.sub(r"`[^`\n]*`", " ", _t2)
            _leak, _pii2 = scan_payload(_t2, name)
            if _leak:
                r.setdefault("notes", []).append(
                    "SECRET-SHAPED CONTENT IN THE ANSWER (%s). The reviewer may have read a "
                    "credential through its own tools and written it into the review; the "
                    "saved .md holds it RAW (a reviewer's words are never altered here), while "
                    "diagnostics and this console are scrubbed. If it is real: ROTATE it - "
                    "deleting the file does not un-send the review."
                    % ", ".join(sorted({h.split(" at ")[0] for h in _leak})))
        if r.get("text"):
            _answer_path = os.path.join(a.out, name.upper() + ".md")
            # 🔴 `newline="\n"` ADDED R49. Without it Python translates every \n to \r\n on
            # Windows and nowhere else, so the SAME model output lands on disk as two different
            # byte strings depending on which machine ran the panel - different sizes, different
            # hashes, different byte offsets for anything that indexes into the file. That was
            # the entire reason `bytes` and `answer_bytes` had to be explained to the reader as
            # "two quantities" (measured R48: +147 B on a 9 998 B answer, +2 065 B on 199 738 B,
            # exactly the newline count). Pinning the line ending removes the discrepancy at the
            # source instead of documenting it forever, and it turns a mismatch between the two
            # numbers back into what it should always have been: evidence of a short write.
            with open(_answer_path, "w", encoding="utf-8", newline="\n") as f:
                # 🔴🔴 THE DAMAGE HAS TO BE UNMISSABLE IN THE ARTIFACT, NOT ONLY IN THE LOG.
                # Seven of the thirteen reviewers of this change, independently, said a `note`
                # is too weak for a corrupted statutory citation - and they were right about the
                # risk while wrong about the remedy: their fix was to fail the channel, which
                # throws away 26 KB of good review to punish 35 characters. orglm52 found the
                # third option, which is the one taken here: do not escalate the severity, make
                # the damage impossible to consume silently. The banner goes in the FILE a human
                # or a downstream tool actually opens, above the answer and clearly marked as
                # harness output, so nothing the model wrote is altered - the one thing this
                # project must never do to a reviewer's words.
                if _dmg:
                    f.write("> 🔴 **HARNESS WARNING — NOT PART OF THE ANSWER BELOW.**\n>\n"
                            + "".join("> %s\n" % d for d in _dmg)
                            + ">\n> The text under this line arrived from the vendor already "
                              "damaged. It has NOT been altered or repaired here: repairing a "
                              "model's words is fabrication, so what follows is exactly what was "
                              "received. Do not lift a quotation, a section number or a figure "
                              "out of it without opening the source.\n\n---\n\n")
                f.write(r["text"])
            # 🔴🔴 THE ARTIFACT MEASURES ITSELF. THIS IS WHY `spark12cont` LOOKED DEAD FOR WEEKS.
            #
            # `bytes` was set independently inside EIGHT dispatchers - call_codex, call_grokcli,
            # call_hermes, call_gemini_direct, call_oai_reviewer, call_xai_responses, _agy_once -
            # and `call_http_reviewer`, the Spark channel, never set it. Not a typo with a small
            # blast radius: `report.py` renders a missing number as `-`, so every REPORT.md ever
            # produced showed
            #     | `spark12cont` · Muse Spark 1.2 Contributor | OK | 228 | ... | - |
            # beside a 45 KB review, and the run log's summary block printed no `bytes=` line for
            # it while printing one for all fifteen others. Measured across six consecutive panels
            # (2026-08-17..18, four projects): spark12cont ok=True in 6 of 6, bytes=None in 6 of 6.
            # Igor read that and said «spark12cont не сработал» - the channel was fine; the only
            # column that says "an answer exists" was blank on the one channel that never filled
            # it. The R45 round then LOST both Spark answers (107 KB) from the hand-built file list
            # a session assembled off that same log, because nothing in it said they existed.
            #
            # A per-dispatcher field is a promise eight functions have to keep; adding a ninth
            # channel silently re-opens the hole. So the count now comes from the WRITE - the one
            # place that exists exactly once and cannot be reached without an answer on disk.
            #
            # TWO NUMBERS, TWO NAMES, deliberately. `bytes` is the model's payload
            # (len(text.encode("utf-8"))); `answer_bytes` is what `stat` says about the file.
            # 🔴 R48 they differed by construction on Windows (text-mode \n -> \r\n, measured at
            # exactly the newline count) and the round's answer was to DOCUMENT the difference.
            # R49 pinned `newline="\n"` at the open above instead, so on every platform the two
            # now agree - which is the better outcome, because a documented expected mismatch
            # cannot also serve as a detector. They stay two fields: one is what the model
            # produced, the other is what survived to disk, and a future divergence between them
            # is now a short write rather than a footnote.
            try:
                r["answer_file"] = os.path.basename(_answer_path)
                r["answer_bytes"] = os.path.getsize(_answer_path)
                if not r.get("bytes"):
                    r["bytes"] = len(r["text"].encode("utf-8"))
            except OSError:
                pass          # a stat failure must never cost the round its answer

        # 🔴🔴 «DO NOT PARSE IT» PRINTED OVER 46 KB OF FINISHED REVIEW.
        #
        # `ok` is computed from the end marker's position, so a channel that wrote a complete
        # review and then added one stray line is graded identically to a channel that returned
        # nothing. Measured: R42 AOS panel, ornemotron3ultra - ok=false, sole warning "END MARKER
        # NOT ON LAST LINE - output is partial, do not parse it", file 46 473 B, n_cited 11,
        # finish_reason "stop". R40, same channel: ok=false over 32 958 B. A reader following the
        # instruction discards a finished review to punish a formatting slip.
        #
        # The gate is NOT relaxed - `ok` still means "verified end to end", and loosening it would
        # make the marker decorative, which is the whole reason it exists. What changes is that
        # the record now carries the OTHER fact too, so the reader can tell the two apart. Done
        # here rather than in the five dispatchers that raise the marker warning: one place that
        # every channel passes through, keyed on the artifact rather than on the transport.
        _marker_only = (not r.get("ok")
                        and (r.get("answer_bytes") or 0) >= 2000
                        and any("END MARKER" in w for w in (r.get("warnings") or []))
                        and not any(("EMPTY OUTPUT" in w or "NO ANSWER TURN" in w
                                     or "PROVIDER ERROR" in w or "BUDGET EXHAUSTED" in w)
                                    for w in (r.get("warnings") or [])))
        if _marker_only:
            r["unverified_but_substantial"] = True
            r.setdefault("notes", []).append(
                "SUBSTANTIAL TEXT, MARKER MISPLACED - %s bytes were written and no warning here "
                "names an empty, truncated or errored response. The channel is graded FAILED "
                "because the end marker is not the last line, which is the only mechanical proof "
                "that a review finished; it is NOT evidence that the content is unusable. Open "
                "the file and check where it stops before discarding it - a complete review "
                "followed by one stray line looks identical to a truncation from here."
                % r.get("answer_bytes"))
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
                "passes, NOT one prompt (of which cached %s, ~50x cheaper) | out=%s | "
                "reasoning_chars=%s | stop=%s | effort=%s | blocks=%s"
                % (r.get("in_tokens_total"), r.get("tool_calls") or 0, r.get("cached_in_tokens"),
                   r.get("out_tokens"), r.get("reasoning_chars"), r.get("stop_reason"),
                   r.get("effort"), r.get("block_types")))
        if kind == "grokcli":
            # The richest telemetry of any CLI channel here: codex reports nothing at all and agy
            # needs stream-json to report anything, while this one returns reasoning_tokens in
            # its ordinary `json` output. `served` is printed because the CLI answers as
            # `grok-4.6-build` when asked for `grok-4.6` - a mismatch that is normal here and
            # would look alarming anywhere else.
            log("    tokens in=%s (cached %s) out=%s | reasoning=%s | turns=%s | served=%s | "
                "effort=%s"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_tokens"), r.get("turns"),
                   ",".join(r.get("model_served") or []) or "-", r.get("effort")))
            if r.get("usd_reported_unverified") is not None:
                log("    the CLI reports $%s for this call. NOT added to the round total: this "
                    "channel authenticates with a subscription session and carries no API key, "
                    "so whether that figure is a charge or an accounting entry is UNVERIFIED. "
                    "An unverified number is not rounded to zero here."
                    % r["usd_reported_unverified"])
        if kind in ("openrouter", "oai") and r.get("out_tokens") is not None:
            # The direct channel finally has real usage numbers; Hermes reported none.
            # 🔴 depth= prints the RESOLVED knob (effort or budget) since R46 - Igor's «ты точно
            # ему на максимум мозги выкрутил?» could not be answered from this line before,
            # because the one kind with the most depth variants printed none of them.
            _rz = slot.get("reasoning") if isinstance(slot.get("reasoning"), dict) else {}
            _depth = (_rz.get("effort") or
                      ("budget:%s" % _rz["max_tokens"] if _rz.get("max_tokens") else None) or
                      "vendor default")
            log("    tokens in=%s (cached %s) out=%s | reasoning=%s tok / %s chars | depth=%s | "
                "web=%s | fetched_by_us=%s | grounding=%s%s"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_tokens"),
                   r.get("reasoning_chars"), _depth,
                   (slot.get("web") or {}).get("enabled", False),
                   r.get("fetched_by_us") or 0, r.get("grounding_basis"),
                   # 🔴 THE ROUND COUNT RIDES WITH THE PRICE. spark11, reviewing this diff:
                   # `usd_rounds` was measured and never printed, so a reader could not tell
                   # $4 over two generations from $4 over eight - and those are different
                   # facts about whether the fetch loop is the problem. A number computed and
                   # not surfaced is the same defect class as a knob sent and not applied.
                   ("" if r.get("usd") is None
                    else " | cost reported BY THE PROVIDER: $%.6f%s"
                    % (r["usd"], (" across %d generation(s)" % r["usd_rounds"])
                       if r.get("usd_rounds") else ""))))
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
        if kind == "hermes":
            log("    exit=%s | model=%s | no token telemetry from this CLI"
                % (r.get("exit"), r.get("model")))
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
                "thinking_level=%s | searches=%s | pages opened BY GOOGLE=%s | grounding=%s"
                % (r.get("in_tokens"), r.get("cached_in_tokens"), r.get("out_tokens"),
                   r.get("reasoning_tokens"), r.get("tool_tokens"),
                   slot.get("thinking_level") or "unset (vendor default: medium)",
                   r.get("searches"), r.get("vendor_opened"), r.get("grounding_basis")))
            # 🔴 The static line that stood here said «medium on strategic, high on deep» - two
            # tiers that stopped existing in R43. A telemetry line asserting a retired mechanism
            # is the compaction-resurfaces-stale-prose failure, printed fresh on every run. The
            # resolved value now rides in the line above instead.
            if r.get("cited_domains") or r.get("n_annotations"):
                # `n_cited` is DISTINCT sources and `n_annotations` is spans; printing both is the
                # only way a reader can tell that "14 citations" and "5 sources" describe one call.
                # `titles_unusable` separates "Google named no publisher" from "we rejected the
                # name it gave" - measured once as an empty list with no explanation, which is the
                # shape of a quiet reporting failure.
                log("    sources=%s (from %s annotation spans) | publishers: %s%s"
                    % (r.get("n_cited"), r.get("n_annotations"),
                       ", ".join(r.get("cited_domains") or []) or "none named by the API",
                       "" if not r.get("titles_unusable")
                       else "  [not reported as publishers: %s]"
                            % ", ".join(filter(None, [
                                "%d annotation(s) carried NO title" % r["titles_missing"]
                                if r.get("titles_missing") else "",
                                "%d title(s) were not domain-shaped" % r["titles_not_domain"]
                                if r.get("titles_not_domain") else ""]))))
        # 🔴 THE LOG IS A MANIFEST OR IT IS NOTHING. A session that has to answer "which answers
        # exist" reads this block, and until 2026-08-19 the line was conditional on a field one
        # channel never set, so two Spark answers totalling 107 KB appeared nowhere in 303 lines
        # of log. The file NAME is printed too, unconditionally, because "17 files on disk" and
        # "16 byte-counts in the log" is the discrepancy that cost round 46 its three biggest
        # reviews - the reader built the list by hand from this block and the block was short.
        if r.get("answer_file"):
            log("    answer=%s  %s bytes on disk (payload %s) exit=%s"
                % (r["answer_file"], r.get("answer_bytes"), r.get("bytes"), r.get("exit")))
        elif r.get("bytes"):
            log("    bytes=%s exit=%s (no answer file was written)"
                % (r["bytes"], r.get("exit")))
        for w in r.get("warnings", []):
            log("    FAIL: " + w)
        for n in r.get("notes", []):
            log("    note: " + n)
        ok_count += bool(r.get("ok"))

    log("=" * 78)
    log("%d/%d channels returned a verified review. Outputs in %s"
        % (ok_count, len(results), os.path.abspath(a.out)))

    # ---- the manifest, and the cost of reading it ------------------------------------------
    # 🔴🔴 ASK THE DIRECTORY, NEVER THE RECORDS. Round 46 handed three reviews - GROKBUILD
    # 199 738 B, SPARK12CONT 66 955 B, SPARK11 40 782 B, 317 KB in total - to nobody, because
    # the session built the reading list BY HAND from the run log, and the log was short by
    # exactly the channels whose `bytes` field was never set. The round then reported "18
    # launches, 17 answers"; both numbers were wrong. The most expensive output of the round
    # was invisible to the only reader it had.
    #
    # A manifest computed from `results` would have reproduced the same blind spot, because
    # `results` is what was already believed. This one is a `listdir`: it reports what is ON
    # DISK, including a file no record claims, and it is written whether or not anything later
    # in this function succeeds.
    #
    # It also prints the READ COST, because that is the number the decision actually turns on.
    # Igor, 2026-08-19: «пока ИИ запускает оркестрацию, у него уже окно часто заполнено на
    # 350K+, а чтобы читать и анализировать ответ, лучше чтобы окно было почти пустое». The
    # harness cannot see the caller's context, so it does not decide - it states the price and
    # hands over a ready-to-send resume prompt. ~4 chars/token is the crude English/Russian
    # mixed-prose ratio; it is labelled an estimate because it is one.
    # The reading order comes from the registry (channels.json -> read_order), stamped onto the
    # records here so the manifest can sort files by it - the record is what maps a file back
    # to a channel. Stamped, not looked up inside write_handoff: that function deliberately has
    # no registry dependency, and a manifest writer that could fail on a broken registry would
    # break the round it describes.
    for _cn, _r in results.items():
        # 🔴 _registry_default, NOT plan.get(): routing's plan carries selected fields and
        # read_order is not one of them, so the first live round (R73) printed «?» in every
        # row of the reading table - the knob was stamped from a dict that never held it.
        # The R72 selftest missed it because it fed write_handoff hand-built results; the
        # stamping path only runs in a real round. Same lesson, same fix as reading_note below.
        if isinstance(_r, dict) and "read_order" not in _r:
            _r["read_order"] = _registry_default(_cn, "read_order", None)
        # reading_note travels the same road: the registry may carry standing advice about a
        # channel's answer («NEVER read Nemotron» - Igor, 2026-08-31), and advice that only
        # prints in the run tail while the READING LIST stays silent is advice that reaches
        # nobody at the moment it matters.
        if isinstance(_r, dict) and "reading_note" not in _r:
            _r["reading_note"] = _registry_default(_cn, "reading_note", None)
        # ★ Idea 3 (Igor, 2026-09-01): the mandatory self-read flag rides the same registry
        # road as the two above - one home, stamped at the same site the R73 live round
        # proved is the only one that runs in production.
        if isinstance(_r, dict) and "must_read" not in _r:
            _r["must_read"] = bool(_registry_default(_cn, "must_read", False))
    handoff = write_handoff(a.out, results, marker=a.marker, brief=a.brief,
                            panel=getattr(a, "panel", None), started=started,
                            answer_cap=a.answer_cap if a.answer_cap and a.answer_cap > 0
                            else None)

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
    #
    # 🔴🔴 AND THEN THE TOTAL WAS WRONG ANYWAY, TWICE OVER, UNTIL 2026-08-15. Both faults lived in
    # call_oai_reviewer and both are fixed there: (a) the per-channel figure was the LAST tool
    # round's cost rather than the sum of all of them, and (b) a channel that raised - which is
    # what a spending cap looks like from here - returned no telemetry at all. AOS round 34 is the
    # measurement: this line printed "$0.9250 across 5 channel(s)" for a round whose OpenRouter
    # bill was about $13, because orgpt56terrapro's $12.08 landed in the "reports no price" list
    # below and qwen38max's $0.97 was reported as $0.43. A cost report that is most wrong on the
    # round that spent the most is not a partial answer, it is a misleading one.
    priced = {c: r["usd"] for c, r in results.items() if r.get("usd") is not None}
    if priced:
        unpriced = sorted(c for c in results if c not in priced)
        failed_but_billed = sorted(c for c, r in results.items()
                                   if r.get("usd") and not r.get("ok"))
        log("cost reported BY THE VENDORS for this round: $%.4f across %d channel(s) (%s)"
            % (sum(priced.values()), len(priced),
               ", ".join("%s $%.4f%s" % (c, v, "*" if c in failed_but_billed else "")
                         for c, v in sorted(priced.items()))))
        if failed_but_billed:
            log("  * that channel FAILED and the money was still spent: %s. Partial telemetry is "
                "reported on purpose - a failed round is exactly when the bill is easiest to lose."
                % ", ".join(failed_but_billed))
        stopped = sorted(c for c, r in results.items() if r.get("spend_stopped"))
        if stopped:
            log("  SPEND CEILING fired on: %s - those channels stopped opening pages and answered "
                "from what they had. Raise spend_guard.max_usd_per_review in channels.json if the "
                "answer came back thin." % ", ".join(stopped))
        if unpriced:
            log("  NOT INCLUDED - these channels report no price: %s. Subscription channels "
                "(codex, agy) never will; the rest bill per token at a rate this harness does "
                "not hold. This total is a floor, not the bill." % ", ".join(unpriced))

    # The account's own ledger against the sum of per-response cost fields - two meters over one
    # spend, on the transport with the only measured runaway. Search fees ($0.005/query) do not
    # always ride in `usage.cost`, so a delta ABOVE the summed figure is usually them; a delta
    # far above is another process on the same key.
    or_meter = None
    credits_after = openrouter_credits() if uses_or_key and credits_before else None
    if credits_before and credits_after:
        delta = round(credits_after["total_usage"] - credits_before["total_usage"], 6)
        summed = round(sum(r["usd"] for c, r in results.items()
                           if r.get("usd") is not None
                           and r.get("provider") == "openrouter"), 6)
        remaining = None
        if credits_after.get("total_credits") is not None:
            remaining = round(credits_after["total_credits"]
                              - credits_after["total_usage"], 4)
        or_meter = {"usage_before": credits_before["total_usage"],
                    "usage_after": credits_after["total_usage"],
                    "delta_usd": delta, "summed_from_responses_usd": summed,
                    "credits_remaining_usd": remaining}
        log("OpenRouter KEY ledger (GET /credits): this round moved it $%.4f; per-response "
            "cost fields sum to $%.4f%s.%s"
            % (delta, summed,
               ("" if remaining is None else " | credits remaining on the key: $%.2f"
                % remaining),
               # 🔴 A MATCH IS AN OBSERVATION, NOT AN INVARIANT. The else-branch used to state
               # the agreement flatly, in the present tense, with no round attached - which
               # reads as a property of the instrument. It is
               # not: measured across four rounds, the two meters disagreed in THREE of them
               # (R40 gap $0.509, R41 $1.183 with the ledger moving 2.65x what the responses
               # reported, R42 $0.099) and matched in one. A reader who saw the confident
               # wording in R45 had no way to know the round before it had a dollar unaccounted
               # for. Say what happened this time and what it does not prove.
               (" The gap is normally search fees, which bill outside `usage.cost` - or "
                "another process on the same key during the round."
                if abs(delta - summed) > 0.0005 else
                " They MATCH THIS ROUND, which is not guaranteed - a gap here is normal and "
                "usually search fees; measured gaps in earlier rounds ran to $1.18.")))

    # ---- problems -----------------------------------------------------------------------------
    # Computed BEFORE the citation audit, because nothing in it needs the audit and everything in
    # it is needed if the audit dies. See the diagnostics block below for why that is not
    # hypothetical.
    problems = []
    for name, r in results.items():
        for text in ([r.get("error")] if r.get("error") else []) + list(r.get("warnings", [])):
            cause, fix = diagnose(str(text))
            problems.append({"channel": name, "detail": str(text),
                             "likely_cause": cause, "suggested_fix": fix})
    # A missing binary is reported twice - once by the preflight, once by the channel that then
    # failed on it - and printing the same advice four times reads like four separate faults.
    # Keep the channel-level entry, which names the channel, and drop the preflight echo.
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

    def _diag_payload(audit):
        return {
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
        # 🔴🔴 A FILE THAT EXISTS USED TO MEAN "THE ROUND FINISHED". IT NO LONGER DOES.
        #
        # Writing the record twice fixed the loss described below, and bought a NEW failure with
        # the money: before, a death during the citation audit left no diagnostics.json at all,
        # and absence is a loud, unambiguous signal. After, it leaves a complete-LOOKING record
        # that is simply missing the audit - and nothing in it said so. Nine of the twelve R48
        # reviewers named this independently, in the same words: the fix traded a visible failure
        # for an invisible one. They were right, and the omission is the round's own defect class
        # wearing the round's own fix as a disguise.
        #
        # So the first write SAYS it is the first write. `complete: false` is the field a machine
        # reads; the sentence is for the human who opens the file after a crash and needs to know
        # in one line why `citations.results` is null. Absence of the audit and "the audit found
        # nothing" are different facts and must not render the same.
        "record_status": {
            "complete": audit is not None,
            "phase": ("channels finished; the citation audit had NOT run when this was written"
                      if audit is None else
                      "complete - channels finished and the citation audit is folded in"),
            "how_to_read_this":
                "`complete: false` means this file is the crash-safe early write. Every channel "
                "result, cost and warning in it is final; only `citations.results` is missing, "
                "and it is null because the probe had not run, NOT because no URL was cited. If "
                "you are reading a false here after the run should have ended, the process died "
                "during the citation audit - the reviews on disk are unaffected and `citecheck.py` "
                "can be run against them by hand.",
        },
        "seconds": round(time.time() - started, 1),
        "ok_channels": ok_count,
        "total_channels": len(results),
        "invocation": {"tier": a.tier, "marker": a.marker, "system": a.system or "base-depth",
                       "only": a.only, "skip": a.skip, "route": a.route, "sets": a.sets,
                       "brief_chars": len(brief), "strict_pii": a.strict_pii,
                       "ask_mode": ask_mode,
                       # The cap was invisible in the two artifacts other chats quote
                       # (grokbuild, R73): HANDOFF metered it, REPORT could not even name it.
                       "answer_cap": a.answer_cap,
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
        "openrouter_key_meter": or_meter,
        "problems": problems,
        "handoff": handoff,
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
        }

    # ---- diagnostics, written TWICE, and the second one is the optional half -------------------
    #
    # 🔴🔴 THE RECORD OF A ROUND MUST NOT BE HOSTAGE TO ANYTHING THAT RUNS AFTER THE ROUND.
    #
    # Measured on the AOS round-45 panel, 2026-08-18: 18 channels launched, 17 answers written to
    # disk, $3.9753 spent - and `reviews-global/` contains NO `diagnostics.json` and NO
    # `REPORT.md`. `run.log` is appended and flushed inside `log()` on every call, so its clean
    # stop one line after the OpenRouter ledger is not a truncated write; the process ended in the
    # gap between that line and the first `log()` of the citation audit. Everything the record
    # needed had already been computed. The only thing that failed was the writing, and it failed
    # because the write came last.
    #
    # The cause of that particular death is NOT established - the candidate that fit best was
    # refuted by its own control: a child python with a piped stdout gets cp1251 on this machine
    # and dies on a non-encodable character, but the surviving R45 log contains SEVENTEEN lines
    # with `⚠` in them, so stdout was not cp1251 in that run. Recorded as unknown rather than
    # guessed at.
    #
    # Which is exactly the point. A record that is only written when nothing goes wrong is a
    # record of rounds that did not need one. So: write the complete payload BEFORE the citation
    # audit - it needs nothing from the audit - and write it again afterwards with the audit
    # folded in. The second write is an enrichment. If anything between them dies, the round
    # still has its diagnostics, its REPORT.md and its manifest, missing only the URL probe.
    diag = write_diagnostics(a.out, _diag_payload(None)) if not a.no_log else None

    # ---- citation audit -----------------------------------------------------------------------
    # Runs on every review, because the alternative - a separate command afterwards - is a check
    # that gets skipped exactly when the run was rushed. See citation_audit() for why existence
    # rather than grounding, and why it never touches the exit code.
    #
    # Guarded even though citation_audit() says "Never raises": that promise is prose, and prose
    # does not enforce anything. `BaseException` rather than `Exception` on purpose - a
    # MemoryError or a Ctrl-C here must still leave the enriched record's predecessor in place,
    # and the interrupt is re-raised immediately after it is recorded.
    audit = None
    try:
        audit = citation_audit(results, enabled=not a.no_citecheck,
                               resolve_links=a.resolve_grounding_links)
        log_citation_audit(audit)
    except BaseException as exc:                                   # noqa: BLE001
        audit = {"skipped": "the citation audit died: %s. The reviews and the rest of this "
                            "record are unaffected - only the URL existence probe is missing."
                            % type(exc).__name__}
        log("  note: the citation audit died (%s). Everything else in this round is recorded; "
            "run `citecheck.py` against the answers by hand if the URLs matter."
            % type(exc).__name__)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            if not a.no_log:
                write_diagnostics(a.out, _diag_payload(audit))
            raise
    if not a.no_log:
        diag = write_diagnostics(a.out, _diag_payload(audit)) or diag

    if problems:
        log("\n%d problem(s) recorded. Plain-language cause and fix for each:" % len(problems))
        for p in problems:
            if p["likely_cause"]:
                log("  [%s] %s" % (p["channel"], p["likely_cause"]))
                log("      -> %s" % p["suggested_fix"])
            else:
                # No stock diagnosis matched. Print the recorded detail rather than nothing:
                # until 2026-08-14 an unmatched problem left this block EMPTY under a header
                # promising "cause and fix for each" - a quiet path that read as "no problems"
                # while diagnostics.json held two.
                log("  [%s] (no stock diagnosis) %s" % (p["channel"], p["detail"]))
    if diag:
        log("\nDiagnostics: %s" % diag)
        log("If something went wrong, hand that file to an AI assistant and ask it to fix the "
            "cause - it is scrubbed of keys and personal data and contains everything needed.")

    # In --ask mode the ANSWER is the product, not the round summary. Printing the path to a file
    # and stopping there is what makes a cheap channel expensive to consult: the cost that decides
    # whether a lookup happens is the number of steps, not the number of cents.
    if ask_mode:
        # The channel the user actually chose comes first, then the free extras in registry order.
        # Dict order here follows the registry, which is an arbitrary fact about a config file -
        # and the answer someone reads first should be the one they asked for.
        order = sorted(results, key=lambda n: (n != a.ask_channel, n))
        for name in order:
            r = results[name]
            body = _strip_marker_tail(r.get("text"), a.marker)
            extra = "" if name == a.ask_channel or len(order) == 1 else "   (free second voice)"
            print("\n" + "=" * 78)
            print("ANSWER  [%s]%s%s" % (name, extra,
                                        "" if r.get("ok") else "   ⚠ the channel reported a "
                                                               "problem - read the log above"))
            print("=" * 78)
            print(body if body else "(empty answer - see the warnings above)")
        if len(order) > 1:
            print("\n" + "-" * 78)
            print("%d channels answered. Where they DISAGREE is the signal - a lookup both of "
                  "them get right needed neither of them." % len(order))
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
