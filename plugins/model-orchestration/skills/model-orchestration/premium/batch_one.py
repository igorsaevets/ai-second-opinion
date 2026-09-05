#!/usr/bin/env python3
"""ONE arbitrary item through a TRUE batch lane: OR / OpenAI-direct / Google-direct.

The lens harnesses (heavy_batch.py, google_batch.py) fan N case-review lenses
over one corpus and hard-wire the USCIS-officer SYSTEM prompt, the lens schema
and a 20K-char corpus floor. A premium-panel seat is the opposite shape — ONE
brief, arbitrary system text, arbitrary schema — and forcing it through the
lens scripts would silently prepend the officer persona to, say, a pricing
review. Hence this runner: the generic single-item primitive the dispatcher
(premium_panel.py) spawns per offline seat, also useful for future smokes.

It ADDS NOTHING to the input text: the caller composes posture + brief +
schema into --input-file. Batch mechanics are reused, not reimplemented — the
transports travel verbatim in batch_transport.py (provenance in its header):
  * OR       — post/get (ex heavy_batch), /api/beta/batches, usage.cost REAL meter
  * Google   — _req/_find_responses (ex google_batch; BATCH_STATE_*, double nesting)
  * OpenAI   — the R03-verified lifecycle (runs/2026-08-16-smoke/submit.py):
               multipart /v1/files -> /v1/batches -> output file JSONL, parsed
               with salvage_json's walker
Prices resolve ONLY через prices.py рядом (valid-through guards included).

Submit is IRREVERSIBLE SPEND. This file never retries a billable error and
never auto-resubmits; poll exits 3 while running (poll_loop.py compatible).
Reads OPENROUTER_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY; never logs a key.
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


HB = _load("batch_transport")  # post/get (verbatim ex heavy_batch)
GB = HB                        # API, _req, _find_responses, parse_results (ex google_batch)
SJ = _load("salvage_json")     # _walk_openai_jsonl
PR = _load("prices")

MIN_INPUT_CHARS = 200  # a panel brief is small but an EMPTY one must be loud


def _key(name: str) -> str:
    """A missing key must refuse in one line, not die in a KeyError traceback —
    on a stranger's machine the traceback reads as a broken tool, not a missing
    prerequisite. The value is returned to the caller and never logged."""
    v = os.environ.get(name, "")
    if not v:
        raise SystemExit(
            f"REFUSING: {name} is not set — this lane cannot run without it. "
            f"Set the environment variable and re-run (see the kit README for "
            f"which lane needs which key).")
    return v


def _read_inputs(a) -> tuple[str, str]:
    ip = pathlib.Path(a.input_file)
    if not ip.exists():
        raise SystemExit(f"REFUSING: --input-file {ip} does not exist")
    text = ip.read_text(encoding="utf-8")
    if len(text.strip()) < MIN_INPUT_CHARS:
        raise SystemExit(
            f"REFUSING: input is {len(text)} chars, below the {MIN_INPUT_CHARS} "
            f"floor. A batch over an empty brief bills in full and returns a "
            f"confident review of nothing (R09 lesson).")
    system = ""
    if a.system_file:
        sp = pathlib.Path(a.system_file)
        if not sp.exists():
            raise SystemExit(f"REFUSING: --system-file {sp} does not exist")
        system = sp.read_text(encoding="utf-8")
    return system, text


# --------------------------------------------------------------------------
# submit per lane
# --------------------------------------------------------------------------
def submit_or(a, system: str, text: str, rundir: pathlib.Path) -> int:
    key = _key("OPENROUTER_API_KEY")
    msgs = ([{"role": "system", "content": system}] if system else []) + \
        [{"role": "user", "content": text}]
    payload = {
        "endpoint": "/v1/chat/completions",   # NOT /api/v1/... — 400s (R03)
        "model": a.model,
        "requests": [{
            "custom_id": a.tag, "method": "POST",
            "body": {"messages": msgs,
                     "max_tokens": a.max_output,
                     "reasoning": {"effort": a.effort},
                     # AI Studio retains 55 days; Vertex retains nothing.
                     "provider": {"zdr": True}},
        }],
    }
    status, body = HB.post("https://openrouter.ai/api/beta/batches", payload, key)
    (rundir / f"{a.tag}.create.json").write_text(
        json.dumps({"status": status, "body": body, "lane": "or"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OR submit HTTP {status}  id={body.get('id')}")
    if status >= 400:
        print(json.dumps(body, indent=2)[:1500])
        return 1
    return 0


def submit_openai(a, system: str, text: str, rundir: pathlib.Path) -> int:
    key = _key("OPENAI_API_KEY")
    if a.endpoint == "responses":
        url_field = "/v1/responses"
        body = {"model": a.model,
                "input": ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": text}],
                "reasoning": {"effort": a.effort},
                "max_output_tokens": a.max_output}
    else:
        url_field = "/v1/chat/completions"
        body = {"model": a.model,
                "messages": ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": text}],
                "reasoning_effort": a.effort,
                "max_completion_tokens": a.max_output}
    line = {"custom_id": a.tag, "method": "POST", "url": url_field, "body": body}
    jsonl = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

    # multipart upload — byte-for-byte the R03-verified shape
    boundary = "----batchonepanel"
    mp = b""
    mp += f"--{boundary}\r\n".encode()
    mp += b'Content-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'
    mp += f"--{boundary}\r\n".encode()
    mp += b'Content-Disposition: form-data; name="file"; filename="one.jsonl"\r\n'
    mp += b"Content-Type: application/octet-stream\r\n\r\n"
    mp += jsonl + b"\r\n"
    mp += f"--{boundary}--\r\n".encode()
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        PR.endpoint("openai_direct.batch_tier.upload").split()[1], data=mp,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            up = json.loads(r.read().decode())
            up_status = r.status
    except urllib.error.HTTPError as e:
        print(f"upload FAILED HTTP {e.code}: {e.read().decode()[:800]}")
        return 1
    file_id = up.get("id")
    print(f"upload HTTP {up_status}  file_id={file_id}")

    status, body2 = HB.post(
        PR.endpoint("openai_direct.batch_tier.create").split()[1],
        {"input_file_id": file_id, "endpoint": url_field,
         "completion_window": "24h"}, key)
    (rundir / f"{a.tag}.create.json").write_text(
        json.dumps({"status": status, "body": body2, "lane": "openai",
                    "file_id": file_id, "endpoint": url_field},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAI batch create HTTP {status}  id={body2.get('id')}")
    if status >= 400:
        print(json.dumps(body2, indent=2)[:1500])
        return 1
    return 0


def submit_google(a, system: str, text: str, rundir: pathlib.Path) -> int:
    key = _key("GEMINI_API_KEY")
    req: dict = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "maxOutputTokens": a.max_output,
            "thinkingConfig": {"thinkingBudget": a.thinking_budget,
                               "includeThoughts": True},
        },
    }
    if system:
        req["systemInstruction"] = {"parts": [{"text": system}]}
    if a.search:
        # Documented-with-example inside batch; UNMEASURED here until the smoke.
        # Never with cachedContent (FAIL code 3, R10) — this runner has no cache
        # path at all, so the composition cannot arise. Schema stays prompt-level:
        # googleSearch does not compose with response_schema either.
        req["tools"] = [{"googleSearch": {}}]
    payload = {"batch": {"display_name": f"panel-{a.tag}",
                         "input_config": {"requests": {"requests": [
                             {"request": req, "metadata": {"key": a.tag}}]}}}}
    status, body = GB._req(
        f"{GB.API}/models/{a.model}:batchGenerateContent", key, payload)
    (rundir / f"{a.tag}.create.json").write_text(
        json.dumps({"status": status, "body": body, "lane": "google",
                    "search": a.search}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"Google batch create HTTP {status}  name={body.get('name')}")
    if status >= 400:
        print(json.dumps(body, indent=2)[:1500])
        return 1
    return 0


# --------------------------------------------------------------------------
# poll per lane — exit 0 done / 1 failed / 3 still running (poll_loop contract)
# --------------------------------------------------------------------------
def _finish(rundir: pathlib.Path, tag: str, text: str | None, usage: dict,
            meter: dict) -> int:
    parsed, failures = [], []
    if text:
        (rundir / f"{tag}.md").write_text(text, encoding="utf-8")
        try:
            i, j = text.find("{"), text.rfind("}")
            obj = json.loads(text[i:j + 1] if i != -1 and j > i else text)
            obj["_usage"] = usage
            parsed.append(obj)
        except (json.JSONDecodeError, ValueError):
            failures.append({"custom_id": tag, "json_error": "answer is not JSON",
                             "raw_text": text, "head": text[:400]})
    else:
        failures.append({"custom_id": tag, "reason": "no text in result"})
    (rundir / f"{tag}.parsed.json").write_text(
        json.dumps({"parsed": parsed, "failures": failures, "meter": meter},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"parsed={len(parsed)} failures={len(failures)}  "
          f"cost: {meter.get('cost_meter') if meter.get('cost_meter') is not None else meter.get('cost_arith')} "
          f"({'METER' if meter.get('cost_meter') is not None else 'arithmetic'})")
    return 0 if parsed else 1


def poll_or(a, rundir: pathlib.Path) -> int:
    key = _key("OPENROUTER_API_KEY")
    created = json.loads((rundir / f"{a.tag}.create.json").read_text(encoding="utf-8"))
    bid = created["body"]["id"]
    status, body = HB.get(f"https://openrouter.ai/api/beta/batches/{bid}", key)
    (rundir / f"{a.tag}.poll.json").write_text(
        json.dumps({"status": status, "body": body}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    st = body.get("status")
    print(f"HTTP {status}  batch status: {st}")
    if st != "completed":
        if st in ("failed", "cancelled", "expired"):
            print(json.dumps(body, indent=2)[:1200])
            return 1
        return 3
    usage = body.get("usage", {}) or {}
    text = None
    for r in body.get("results", []):
        try:
            text = r["response"]["body"]["choices"][0]["message"]["content"]
            usage = r["response"]["body"].get("usage", usage)
        except (KeyError, IndexError, TypeError):
            pass
    meter = {"cost_meter": (body.get("usage") or {}).get("cost"),
             "cost_arith": None,
             "prompt_tokens": usage.get("prompt_tokens"),
             "completion_tokens": usage.get("completion_tokens"),
             "meter_source": "OR usage.cost — REAL vendor meter"}
    return _finish(rundir, a.tag, text, usage, meter)


def poll_openai(a, rundir: pathlib.Path) -> int:
    key = _key("OPENAI_API_KEY")
    created = json.loads((rundir / f"{a.tag}.create.json").read_text(encoding="utf-8"))
    bid = created["body"]["id"]
    status, body = HB.get(f"https://api.openai.com/v1/batches/{bid}", key)
    (rundir / f"{a.tag}.poll.json").write_text(
        json.dumps({"status": status, "body": body}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    st = body.get("status")
    print(f"HTTP {status}  batch status: {st}  counts: {body.get('request_counts')}")
    if st in ("failed", "expired", "cancelled"):
        # expiry still bills completed work — say so rather than pretending $0
        print("TERMINAL non-success. NOTE: OpenAI bills any COMPLETED work even "
              "on expiry; check output/error files before assuming $0.")
        print(json.dumps(body.get("errors") or {}, indent=2)[:1200])
        return 1
    if st != "completed":
        return 3
    out_id = body.get("output_file_id")
    if not out_id:
        print("completed but no output_file_id — inspect poll.json")
        return 1
    import urllib.request
    req = urllib.request.Request(
        f"https://api.openai.com/v1/files/{out_id}/content",
        headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        results_raw = r.read().decode()
    (rundir / f"{a.tag}.results.jsonl").write_text(results_raw, encoding="utf-8")
    text, usage = None, {}
    for cid, t, u in SJ._walk_openai_jsonl(results_raw.splitlines()):
        if t:
            text, usage = t, u
    pricing = PR.openai_direct(a.model, "batch")
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    cached = (usage.get("input_tokens_details", {}) or {}).get("cached_tokens", 0)
    meter = {"cost_meter": None,
             "cost_arith": round(pricing.cost(in_tok, out_tok, cached), 6),
             "prompt_tokens": in_tok, "completion_tokens": out_tok,
             "meter_source": pricing.meter + f" [{pricing.name}]"}
    return _finish(rundir, a.tag, text, usage, meter)


def poll_google(a, rundir: pathlib.Path) -> int:
    key = _key("GEMINI_API_KEY")
    created = json.loads((rundir / f"{a.tag}.create.json").read_text(encoding="utf-8"))
    name = created["body"].get("name") or created["body"].get("batch", {}).get("name")
    status, body = GB._req(f"{GB.API}/{name}", key)
    (rundir / f"{a.tag}.poll.json").write_text(
        json.dumps({"status": status, "body": body}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    state = (body.get("metadata", {}).get("state") or body.get("state")
             or body.get("done"))
    print(f"HTTP {status}  state: {state}")
    s = str(state).upper()
    if s.endswith(("_FAILED", "_CANCELLED", "_EXPIRED")):
        print(json.dumps(body, indent=2)[:1500])
        return 1
    if not (s.endswith("_SUCCEEDED") or s in ("TRUE", "DONE")):
        return 3
    pricing = PR.google_batch(a.model)
    parsed, failures, meter = GB.parse_results(body, pricing)
    text = None
    for item in GB._find_responses(body):
        resp = item.get("response") or item
        cands = resp.get("candidates") or []
        if cands:
            text = "".join(p.get("text", "")
                           for p in cands[0].get("content", {}).get("parts", [])
                           if not p.get("thought"))
    usage = (parsed[0].get("_usage", {}) if parsed
             else {"prompt_tokens": meter.get("prompt_tokens")})
    meter["cost_meter"] = None
    return _finish(rundir, a.tag, text, usage, meter)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", choices=["or", "openai", "google"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--mode", choices=["build", "submit", "poll"], required=True)
    ap.add_argument("--input-file", required=True,
                    help="the COMPLETE user text (posture + brief + schema); "
                         "this runner adds nothing")
    ap.add_argument("--system-file", default="")
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--effort", default="xhigh",
                    help="or/openai reasoning effort; ceiling rule — vendor max")
    ap.add_argument("--max-output", type=int, default=120000,
                    help="output+reasoning headroom; ceiling rule. OR sol-pro "
                         "registry documents 120000; OpenAI 5.5 context_out is "
                         "128000; Google flash outputTokenLimit is 65536")
    ap.add_argument("--thinking-budget", type=int, default=0,
                    help="google lane REQUIRES an explicit value (flash ceiling "
                         "24576 measured; pro-preview ceiling NOT yet captured — "
                         "state the call-plan number, never a guess)")
    ap.add_argument("--search", action="store_true",
                    help="google lane: googleSearch tool inside batch — documented, "
                         "unmeasured here until the 1-item smoke; per-query fee on "
                         "top; 30-day grounding retention (PII policy applies)")
    ap.add_argument("--endpoint", choices=["responses", "chat"], default="chat",
                    help="openai lane inner endpoint. 'responses' is R03-verified "
                         "for gpt-5.4; gpt-5.5's batch support is documented for "
                         "Chat Completions — 'chat' is the safe pin until measured")
    a = ap.parse_args()

    rundir = pathlib.Path(a.rundir); rundir.mkdir(parents=True, exist_ok=True)

    if a.lane == "google" and a.mode in ("build", "submit") and a.thinking_budget <= 0:
        print("REFUSING: google lane needs --thinking-budget (ceiling rule: the "
              "model's max, stated explicitly; flash=24576 measured, pro-preview "
              "uncaptured).", file=sys.stderr)
        return 2
    if a.search and a.lane != "google":
        print("REFUSING: --search is the google batch grounding flag; web on "
              "or/openai batch lanes is rejected by the vendors (measured R10).",
              file=sys.stderr)
        return 2

    system, text = ("", "")
    if a.mode in ("build", "submit"):
        system, text = _read_inputs(a)

    if a.mode == "build":
        pricing = {"or": lambda: PR.or_batch(a.model),
                   "openai": lambda: PR.openai_direct(a.model, "batch"),
                   "google": lambda: PR.google_batch(a.model)}[a.lane]()
        est_in = (len(system) + len(text)) / 3.9
        print(f"lane={a.lane} model={a.model} tag={a.tag}")
        print(f"input chars: {len(text):,} (+system {len(system):,})  "
              f"~{est_in:,.0f} tok (ESTIMATE)")
        print(f"rates: {pricing.describe()}")
        if not pricing.discount:
            print(f"🔴 НЕТ СКИДКИ: {pricing.discount_reason}")
            return 1
        print(f"est. $ at 8k out: {pricing.cost(int(est_in), 8000):.4f} "
              f"(ARITHMETIC; web/search fees NOT included)")
        return 0

    if a.mode == "submit":
        t0 = time.monotonic()
        rc = {"or": submit_or, "openai": submit_openai,
              "google": submit_google}[a.lane](a, system, text, rundir)
        print(f"submit wall: {time.monotonic() - t0:.1f}s")
        return rc

    return {"or": poll_or, "openai": poll_openai,
            "google": poll_google}[a.lane](a, rundir)


if __name__ == "__main__":
    raise SystemExit(main())
