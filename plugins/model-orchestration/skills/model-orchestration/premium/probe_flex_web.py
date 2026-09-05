#!/usr/bin/env python3
"""Probe C+D: does the Flex service tier accept web tools?

Batch mode is documented offline-sandbox and refuses dynamic tool execution
(web_search, MCP). Flex uses the SAME endpoint as sync with 50% off. If Flex
accepts web tools, R11 gains web-enabled lanes at batch prices without the
sandbox refusal.

Two paths, both ~$0.02-0.05 to close definitively:
  C: Direct OpenAI /v1/responses + service_tier:flex + tools:[web_search]
  D: OpenRouter /api/v1/chat/completions + service_tier:flex + plugins:[web]

The question is API acceptance, not brain depth. The probe question is a
public fact (the latest stable Python release shown on python.org) - no PII,
no case material, no third-party names.

Reads OPENAI_API_KEY or OPENROUTER_API_KEY. Never prints or logs the key.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

PROBE_QUESTION = (
    "What is the latest stable Python 3 release version shown on "
    "https://www.python.org/downloads/ as of today? "
    "Return: (a) the version exactly as printed (e.g. '3.13.1'), "
    "(b) the URL you fetched, (c) one sentence quoting the surrounding text. "
    "If you CANNOT access the web, say so explicitly and do not guess."
)


def build_openai(model: str) -> dict:
    return {
        "model": model,
        "input": PROBE_QUESTION,
        "tools": [{"type": "web_search"}],
        "service_tier": "flex",
        "reasoning": {"effort": "high"},
        "max_output_tokens": 8000,
    }


def build_openrouter(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_QUESTION}],
        "plugins": [{"id": "web", "max_results": 3}],
        "service_tier": "flex",
        "reasoning": {"effort": "high"},
        "max_tokens": 8000,
    }


def post(url: str, body: dict, headers: dict) -> tuple[int, dict, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return r.status, {"raw": raw[:4000]}, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:8000]
        try:
            return e.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return e.code, {"raw": raw}, raw
    except urllib.error.URLError as e:
        return 0, {"error": f"URLError: {str(e.reason)[:400]}"}, str(e)


def extract_openai(resp: dict) -> tuple[str, list[str], dict]:
    """Return (assistant_text, web_search_queries, usage)."""
    text_parts = []
    ws_queries = []
    for item in resp.get("output", []):
        t = item.get("type")
        if t == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text_parts.append(c.get("text", ""))
        elif t == "web_search_call":
            action = item.get("action", {})
            q = action.get("query") or action.get("type") or "?"
            ws_queries.append(q)
    return "".join(text_parts), ws_queries, resp.get("usage", {})


def extract_openrouter(resp: dict) -> tuple[str, list[str], dict]:
    """OR returns OpenAI-chat shape. Look at choices[0].message and any tool_calls."""
    choices = resp.get("choices", [])
    if not choices:
        return "", [], resp.get("usage", {})
    msg = choices[0].get("message", {})
    text = msg.get("content", "") or ""
    ws_queries = []
    annotations = msg.get("annotations") or []
    for a in annotations:
        if a.get("type") == "url_citation":
            ws_queries.append(a.get("url_citation", {}).get("url", "?"))
    return text, ws_queries, resp.get("usage", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--path", choices=["C", "D"], required=True,
                    help="C = OpenAI direct; D = OpenRouter")
    ap.add_argument("--model", default="",
                    help="C default: gpt-5.4; D default: openai/gpt-5.6-sol-pro")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    rundir = pathlib.Path(a.rundir)
    rundir.mkdir(parents=True, exist_ok=True)

    if a.path == "C":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            print("REFUSING: OPENAI_API_KEY not set", file=sys.stderr)
            return 2
        model = a.model or "gpt-5.4"
        url = "https://api.openai.com/v1/responses"
        body = build_openai(model)
        extract = extract_openai
        tag = a.tag or "probe-c-openai-flex-web"
    else:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("REFUSING: OPENROUTER_API_KEY not set", file=sys.stderr)
            return 2
        model = a.model or "openai/gpt-5.6-sol-pro"
        url = "https://openrouter.ai/api/v1/chat/completions"
        body = build_openrouter(model)
        extract = extract_openrouter
        tag = a.tag or "probe-d-openrouter-flex-web"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    print(f"path={a.path}  url={url}")
    print(f"model={model}  service_tier=flex  effort=high  web=on")
    print(f"question: {PROBE_QUESTION[:120]}...")
    print()

    status, resp, raw = post(url, body, headers)

    (rundir / f"{tag}.json").write_text(
        json.dumps({
            "path": a.path,
            "url": url,
            "http_status": status,
            "request_body_shape": {
                "model": body.get("model"),
                "service_tier": body.get("service_tier"),
                "has_web_tool": bool(body.get("tools") or body.get("plugins")),
                "reasoning": body.get("reasoning"),
            },
            "response_body": resp,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"HTTP {status}")
    if status >= 400 or status == 0:
        err = resp.get("error", resp)
        print("ERROR:")
        print(json.dumps(err, ensure_ascii=False, indent=2)[:2000])
        return 1

    text, queries, usage = extract(resp)

    if queries:
        print(f"[web tool used, {len(queries)} query/citation(s):]")
        for q in queries[:5]:
            print(f"  - {q[:120]}")
        print()

    print("=== ANSWER ===")
    print(text[:3000])
    print()

    print(f"usage: {json.dumps(usage)}")

    # Rough cost arithmetic. Verify against provider meter in the JSON file.
    if a.path == "C":
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        r_tok = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
        # gpt-5.4 published: ~$2.50/M in, ~$20/M out. Flex = 50% off.
        cost_arith = (in_tok * 1.25 + out_tok * 10.00) / 1e6
        print(f"cost_arith (Flex 50% off, gpt-5.4 assumed rates): ${cost_arith:.4f}")
        print(f"reasoning_tokens: {r_tok}")
    else:
        # OR provides usage.cost as a real meter for many models.
        cost = usage.get("cost")
        if cost is not None:
            print(f"OR usage.cost: ${cost:.4f} (real meter)")
        else:
            in_tok = usage.get("prompt_tokens", 0)
            out_tok = usage.get("completion_tokens", 0)
            # sol-pro OR sync: ~$2/M in, ~$10/M out; Flex = 50% off.
            cost_arith = (in_tok * 1.00 + out_tok * 5.00) / 1e6
            print(f"cost_arith (Flex 50% off, sol-pro assumed rates): ${cost_arith:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
