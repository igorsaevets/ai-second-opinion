#!/usr/bin/env python3
"""Verbatim batch transports for the premium panel — the ONLY code this bundle
borrows from the source project's lens harnesses.

The source project (github.com/igorsaevets/second-opinion-batch) runs these
functions inside two case-review harnesses, heavy_batch.py and google_batch.py,
which hard-wire a reviewer persona, a lens schema and a corpus floor. Those
harnesses are domain tools and do not travel; the wire mechanics below are
domain-free and travel VERBATIM — copied functions, not a rewrite, so the fixes
measured there (URLError surfaced as a status, the double-nested result walk,
cached tokens billed at the cached rate) arrive intact.

Provenance — sha256 of the source files at copy time (2026-09-05):
  heavy_batch.py   a161ce3aecae7356bf4f61e98b7e01469d42d26111487df03e6b94f3398a286c
                   -> post(), get()                       (lines 285-307)
  google_batch.py  2c9653471e262ef5247c50c368a4133ea147fb67350f84e2da8be4726cdf8924
                   -> API, _req(), _find_responses(), parse_results()
                                                          (lines 70, 84-101, 250-345)

Heritage asymmetry, kept on purpose: _req() converts URLError/socket failures
into a status tuple (an R06 lesson from the source project); post()/get() catch
HTTPError only. Unifying them here would break the verbatim property.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


# --------------------------------------------------------------------------
# OpenRouter / OpenAI HTTP  (heavy_batch.py, verbatim)
# --------------------------------------------------------------------------
def post(url: str, payload: dict, key: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:4000]}


def get(url: str, key: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:4000]}


# --------------------------------------------------------------------------
# Google Gemini API transport  (google_batch.py, verbatim)
# --------------------------------------------------------------------------
API = "https://generativelanguage.googleapis.com/v1beta"


def _req(url: str, key: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:6000]}
    except urllib.error.URLError as e:
        # R06 lesson: a bare URLError (DNS/socket) used to kill a whole run because
        # only HTTPError was caught. Surface it as a status, never as a traceback.
        return 503, {"error": f"URLError: {str(e.reason)[:300]}"}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"error": f"{type(e).__name__}: {str(e)[:300]}"}


# --------------------------------------------------------------------------
# Google batch response parsing  (google_batch.py, verbatim)
# --------------------------------------------------------------------------
def _find_responses(body: dict) -> list[dict]:
    """Walk the plausible homes of the inline result array.

    The docs name `inlinedResponses` but do not pin its parent, and an operation
    envelope may wrap it in `response` or `metadata`. Rather than assume one and
    silently report zero results, try each and say which one hit.
    """
    for path in (("response", "inlinedResponses", "inlinedResponses"),
                 ("response", "inlinedResponses"),
                 ("inlinedResponses", "inlinedResponses"),
                 ("inlinedResponses",),
                 ("metadata", "inlinedResponses"),
                 ("response", "responses"),
                 ("dest", "inlinedResponses")):
        node = body
        ok = True
        for k in path:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                ok = False
                break
        if ok and isinstance(node, list):
            print(f"  result array found at: {'.'.join(path)}  (n={len(node)})")
            return node
    return []


def parse_results(body: dict, pricing) -> tuple[list, list, dict]:
    """`pricing` is a prices.Pricing for the batch tier of the model that ran.
    Cost accumulates PER ITEM because gemini-3.1-pro-preview switches rate at a
    200k-prompt threshold per request, not per job."""
    parsed, failures = [], []
    tot_in = tot_out = tot_th = tot_cached = 0
    cost = 0.0

    for item in _find_responses(body):
        cid = (item.get("metadata") or {}).get("key") or item.get("key") or "?"
        if "error" in item and item.get("error"):
            failures.append({"custom_id": cid, "error": str(item["error"])[:600]})
            continue
        resp = item.get("response") or item
        cands = resp.get("candidates") or []
        if not cands:
            failures.append({"custom_id": cid, "error": "no candidates",
                             "head": json.dumps(resp)[:500]})
            continue
        # thoughts and answer are separate parts when includeThoughts is on
        text = "".join(p.get("text", "")
                       for p in cands[0].get("content", {}).get("parts", [])
                       if not p.get("thought"))
        u = resp.get("usageMetadata", {})
        i, o, th = (u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                    u.get("thoughtsTokenCount", 0))
        c = u.get("cachedContentTokenCount", 0)
        tot_in += i; tot_out += o; tot_th += th; tot_cached += c
        # Cached prompt tokens bill at the CACHED rate, not the batch input rate.
        # Billing them at the input rate overstated a cached 12-lens run by 2.4x
        # on 2026-08-18 — a meter that lies high is still a meter that lies.
        cost += pricing.cost(i, o + th, c)

        clean = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.M).strip()
        try:
            obj = json.loads(clean)
        except json.JSONDecodeError as e:
            # R04 measured 8.3% of items returning invalid JSON despite an explicit
            # schema. Keep the text — salvage_json.py runs on it later. A batch
            # cannot be partially re-run, so throwing this away costs a full resubmit.
            failures.append({"custom_id": cid, "json_error": str(e),
                             "head": clean[:400], "raw_text": clean})
            continue
        obj["_usage"] = {"prompt_tokens": i, "completion_tokens": o,
                         "thoughts_tokens": th, "cached_tokens": c}
        parsed.append(obj)

    # ⚠️ Storage is NOT included here: it is a function of how long the cache is held,
    # which this function cannot see. Add it from the caller. At $0.50 per 1M tokens
    # per hour a 93K cache is ~$0.047/hour, which dwarfs the token saving if held.
    uncached_in = max(0, tot_in - tot_cached)
    meter = {
        "prompt_tokens": tot_in, "cached_tokens": tot_cached,
        "uncached_prompt_tokens": uncached_in,
        "cache_hit_fraction": round(tot_cached / tot_in, 4) if tot_in else 0.0,
        "completion_tokens": tot_out,
        "thoughts_tokens": tot_th, "cost_arith": round(cost, 6),
        "cost_excludes": "cache STORAGE ($0.50 per 1M tokens per hour) — add per run",
        "rate_in_per_m": pricing.in_per_m,
        "rate_cached_in_per_m": pricing.cached_in_per_m,
        "rate_out_per_m": pricing.out_per_m,
        "rate_tier_threshold": pricing.tier_threshold_tokens or None,
        "meter_source": f"{pricing.meter} [{pricing.name}] — resolved from "
                        f"models_snapshot.json at run time; Google returns NO "
                        f"cost field, this is not a meter.",
        "parsed": len(parsed), "failed": len(failures),
    }
    return parsed, failures, meter
