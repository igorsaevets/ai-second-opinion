#!/usr/bin/env python3
"""One synchronous Flex-tier call with (optionally) live web — a PANEL LANE.

Generalises probe_flex_web.py (whose extractors are imported, not copied)
into the runner the premium panel dispatches for its live-web seat(s). One
invocation = one model answering one brief. Batch lanes are batch_one.py; this
file exists because Flex is the only tier that accepts web tools (measured
R10/R11: every vendor's *batch* endpoint rejects or sandboxes them).

Measured traps this file exists to not repeat:
  * Probe C (2026-08-23): status "incomplete" — 8,000 max_output_tokens eaten
    ENTIRELY by reasoning across 25 web calls, zero message text, still billed.
    → output headroom defaults to the model ceiling and REFUSES to run web with
    less than 25K reserve; an "incomplete" response is parsed, saved, costed and
    marked, never treated as an error-shaped nothing.
  * Probe D (2026-08-23): OR echoed service_tier "flex" and billed STANDARD to
    the cent — an empty flex pool silently standard-routes. → the OR path pins
    `provider.only` when asked and ALWAYS prints a billed-vs-expected verdict
    off `usage.cost`; echo and HTTP 200 prove nothing (the operator's rule: «если
    скидки нет — писать, что скидки нет»).
  * Probe D cost shape: 16 fetches pushed prompt_tokens to 1.53M and $1.13 for
    ONE question — fetched content bills as INPUT. → `--max-tool-calls` is the
    web budget contract (prompt-level limits are known-failed), and
    `search_context_size: low` + `--allowed-domains` bound what comes back.
  * The OpenAI SDK auto-retries 408 twice on flex — invisible double-billing
    risk vs the no-auto-retry invariant. This file uses urllib: NOTHING retries.

Reads OPENAI_API_KEY / OPENROUTER_API_KEY. Never prints or logs a key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


PFW = _load("probe_flex_web")   # post(), extract_openai(), extract_openrouter()
PR = _load("prices")

MIN_WEB_OUTPUT_TOKENS = 25_000  # OpenAI-recommended reasoning reserve; Probe C floor


def build_openai(a, brief: str) -> dict:
    body: dict = {
        "model": a.model,
        "input": brief,
        "service_tier": a.tier,
        "reasoning": {"effort": a.effort},
        "max_output_tokens": a.max_output,
    }
    if a.web == "on":
        tool: dict = {"type": "web_search",
                      "search_context_size": a.search_context}
        if a.allowed_domains:
            tool["filters"] = {"allowed_domains": a.allowed_domains.split(",")}
        body["tools"] = [tool]
        # THE web budget contract. A prompt-level "use at most N searches" is
        # known-failed guidance; this field is enforced by the API.
        body["max_tool_calls"] = a.max_tool_calls
    return body


def build_openrouter(a, brief: str) -> dict:
    body: dict = {
        "model": a.model,
        "messages": [{"role": "user", "content": brief}],
        "service_tier": a.tier,
        "reasoning": {"effort": a.effort},
        "max_tokens": a.max_output,
    }
    if a.web == "on":
        body["plugins"] = [{"id": "web", "max_results": a.max_web_results}]
    if a.provider_only:
        # Probe D lesson: without a pin an empty flex pool routes to standard
        # SILENTLY. The tag string comes from the operator/call-plan (e.g.
        # "openai/flex" as listed on /endpoints) — wire acceptance is itself
        # smoke-gated, so pass it verbatim and judge by the meter.
        body["provider"] = {"only": a.provider_only.split(",")}
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["openai", "or"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--tier", choices=["flex", "standard"], default="flex")
    ap.add_argument("--web", choices=["on", "off"], default="off")
    ap.add_argument("--brief", required=True, help="file with the COMPLETE item text "
                    "(posture + brief + schema); this script adds nothing to it")
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--marker", default="", help="end-marker string; its absence in "
                    "the answer text is flagged INCOMPLETE (advisory)")
    ap.add_argument("--effort", default="xhigh",
                    help="reasoning effort. Ceiling rule: vendor max always. gpt-5.5 "
                         "documents xhigh; if a model 400s on it, the call bills "
                         "nothing — re-run with --effort high and RECORD the ceiling "
                         "in models_snapshot.json.")
    ap.add_argument("--max-output", type=int, default=0,
                    help="0 = model ceiling from models_snapshot.json (falls back "
                         "128000 with a printed note)")
    ap.add_argument("--max-tool-calls", type=int, default=12,
                    help="OpenAI web budget: hard cap on tool calls. Probe D burned "
                         "$1.13 on 16 uncapped fetches — default stays below that.")
    ap.add_argument("--search-context", choices=["low", "medium", "high"],
                    default="low")
    ap.add_argument("--allowed-domains", default="",
                    help="comma list for web_search filters.allowed_domains (OpenAI)")
    ap.add_argument("--max-web-results", type=int, default=3, help="OR plugin cap")
    ap.add_argument("--provider-only", default="",
                    help="OR: comma list pinned into provider.only (flex endpoint "
                         "tag recipe). Empty = no pin, and the meter verdict below "
                         "is the only truth about what tier billed.")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="flex queues: docs advise raising the client timeout to "
                         "15 min; default is 30 to survive a long brief")
    a = ap.parse_args()

    rundir = pathlib.Path(a.rundir); rundir.mkdir(parents=True, exist_ok=True)
    brief_p = pathlib.Path(a.brief)
    if not brief_p.exists():
        print(f"REFUSING: --brief {brief_p} does not exist", file=sys.stderr)
        return 2
    brief = brief_p.read_text(encoding="utf-8")
    if len(brief.strip()) < 200:
        print(f"REFUSING: brief is {len(brief)} chars — below the 200-char floor. "
              f"An empty brief bills a full flex call for a review of nothing "
              f"(the R09 empty-input lesson).", file=sys.stderr)
        return 2

    # ---- pricing + ceilings from the snapshot, never from memory ----------
    if a.path == "openai":
        pricing = PR.openai_direct(a.model, a.tier)
        snap_m = PR._load().get("openai_direct", {}).get("models", {}).get(a.model, {})
        ceiling = snap_m.get("context_out", 0)
        key = os.environ.get("OPENAI_API_KEY")
        url = PR.endpoint("openai_direct.endpoint_responses")
        extract = PFW.extract_openai
    else:
        # OR flex has NO trusted arithmetic (the one meter billed standard);
        # expected-flex = sync price x 0.5 is printed only to judge the meter.
        m = PR._load().get("openrouter", {}).get("models", {}).get(a.model, {})
        if not m:
            print(f"REFUSING: {a.model} not in models_snapshot.json openrouter "
                  f"block — capture it before spending.", file=sys.stderr)
            return 2
        pricing = PR.Pricing(f"or-{a.tier}/{a.model}",
                             m["prompt_per_1m"] * (0.5 if a.tier == "flex" else 1.0),
                             m["completion_per_1m"] * (0.5 if a.tier == "flex" else 1.0),
                             cached_in_per_m=m.get("input_cache_read_per_1m", 0.0),
                             meter="EXPECTATION ONLY — usage.cost is the meter and "
                                   "the last meter on this path billed STANDARD")
        ceiling = 0
        key = os.environ.get("OPENROUTER_API_KEY")
        url = "https://openrouter.ai/api/v1/chat/completions"
        extract = PFW.extract_openrouter
    if not key:
        print(f"REFUSING: API key for path '{a.path}' not set", file=sys.stderr)
        return 2

    if a.max_output <= 0:
        a.max_output = ceiling or 128_000
        if not ceiling:
            print(f"note: no context_out for {a.model} in snapshot — defaulting "
                  f"max_output to 128,000 (family ceiling); capture the real one.")
    if a.web == "on" and a.max_output < MIN_WEB_OUTPUT_TOKENS:
        print(f"REFUSING: --max-output {a.max_output} with web on. Probe C burned "
              f"8,000 tokens of headroom entirely on reasoning across 25 web calls "
              f"and produced NO text — floor is {MIN_WEB_OUTPUT_TOKENS:,}.",
              file=sys.stderr)
        return 2

    body = (build_openai if a.path == "openai" else build_openrouter)(a, brief)
    tag = a.tag or f"flex-{a.path}-{a.model.replace('/', '_')}"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    print(f"lane: {a.path} {a.model} tier={a.tier} web={a.web} "
          f"effort={a.effort} max_output={a.max_output:,}"
          + (f" max_tool_calls={a.max_tool_calls}" if a.web == "on"
             and a.path == "openai" else ""))
    print(f"rates (expectation, NOT a meter): {pricing.describe()}")
    t0 = time.monotonic()
    # PFW.post uses urllib with NO retry of any kind: a billable error surfaces
    # once and stops, per the no-auto-retry-on-billable invariant.
    status, resp, _raw = PFW.post(url, body, headers)
    wall = time.monotonic() - t0

    (rundir / f"{tag}.json").write_text(
        json.dumps({"path": a.path, "model": a.model, "tier_requested": a.tier,
                    "web": a.web, "http_status": status, "wall_s": round(wall, 1),
                    "request_shape": {k: body[k] for k in body
                                      if k not in ("input", "messages")},
                    "response_body": resp}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"HTTP {status}  wall {wall:.0f}s")
    if status >= 400 or status == 0:
        print(json.dumps(resp.get("error", resp), ensure_ascii=False, indent=2)[:2000])
        print("NOT RETRYING: a billable error is retried only by a human decision.")
        return 1

    text, queries, usage = extract(resp)
    resp_status = resp.get("status", "")
    incomplete = (resp_status == "incomplete") or (not text.strip())
    marker_ok = (a.marker in text) if a.marker else None

    # ---- money verdict ----------------------------------------------------
    if a.path == "openai":
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cached = (usage.get("input_tokens_details", {}) or {}).get("cached_tokens", 0)
        r_tok = (usage.get("output_tokens_details", {}) or {}).get("reasoning_tokens", 0)
        cost_arith = pricing.cost(in_tok, out_tok, cached)
        n_web = len(queries)
        web_fee = n_web * 10.00 / 1000
        print(f"usage: in={in_tok:,} (cached {cached:,})  out={out_tok:,} "
              f"(reasoning {r_tok:,})  web_calls={n_web}")
        print(f"cost_arith tokens ${cost_arith:.4f} + web fee ${web_fee:.4f} "
              f"(ARITHMETIC — OpenAI returns no cost field; the echoed tier "
              f"'{resp.get('service_tier')}' is testimony, the next-day billing "
              f"dashboard is the meter)")
        meter_block = {"cost_arith": round(cost_arith + web_fee, 6),
                       "cost_meter": None,
                       "tier_echo": resp.get("service_tier"),
                       "web_calls": n_web}
    else:
        cost = (usage or {}).get("cost")
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        expected_flex = pricing.cost(in_tok, out_tok)
        print(f"usage: in={in_tok:,} out={out_tok:,}  OR usage.cost="
              f"{'$%.6f (REAL METER)' % cost if cost is not None else 'ABSENT'}")
        if cost is not None and expected_flex > 0:
            ratio = cost / expected_flex if expected_flex else 0
            if ratio > 1.5:
                print(f"🔴 СКИДКИ НЕТ: metered ${cost:.4f} is {ratio:.2f}x the flex "
                      f"expectation ${expected_flex:.4f} — the pool standard-routed "
                      f"(Probe D failure mode). Do not book this lane as flex.")
            else:
                print(f"🟢 meter ${cost:.4f} vs flex expectation "
                      f"${expected_flex:.4f} ({ratio:.2f}x) — discount visible.")
        meter_block = {"cost_arith": round(expected_flex, 6), "cost_meter": cost,
                       "tier_echo": resp.get("service_tier"), "web_calls": len(queries)}

    if incomplete:
        print(f"⚠️ INCOMPLETE: response status={resp_status!r}, text_chars="
              f"{len(text)}. This still BILLED — saved and marked, not discarded.")
    if marker_ok is False:
        print(f"⚠️ end marker '{a.marker}' NOT found in answer text.")

    (rundir / f"{tag}.md").write_text(text, encoding="utf-8")
    parsed_obj = {"_lane": tag, "_usage": usage, "_raw_text_chars": len(text)}
    if incomplete:
        parsed_obj["_incomplete"] = True
    # try to parse the answer as the panel JSON; failures carry raw_text for salvage
    parsed, failures = [], []
    try:
        cleaned = text.strip()
        i, j = cleaned.find("{"), cleaned.rfind("}")
        obj = json.loads(cleaned[i:j + 1]) if i != -1 and j > i else json.loads(cleaned)
        obj.update(parsed_obj)
        parsed.append(obj)
    except (json.JSONDecodeError, ValueError):
        failures.append({"custom_id": tag, "json_error": "answer is not JSON",
                         "raw_text": text, "head": text[:400]})
    (rundir / f"{tag}.parsed.json").write_text(
        json.dumps({"parsed": parsed, "failures": failures,
                    "meter": {**meter_block,
                              "prompt_tokens": in_tok, "completion_tokens": out_tok,
                              "meter_source": pricing.meter}},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {tag}.json / {tag}.md / {tag}.parsed.json in {rundir}")
    return 0 if (parsed and not incomplete) else 1


if __name__ == "__main__":
    raise SystemExit(main())
