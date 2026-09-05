#!/usr/bin/env python3
"""Recover batch items whose JSON did not parse, without paying for them twice.

MEASURED 2026-08-17: 1 of 12 items (8.3%) emitted invalid JSON despite an
explicit schema in the prompt — a lone backslash before a non-escape character.
At batch scale that is ~83 paid-for-and-lost items per 1000, and a batch cannot
be partially re-run: you would resubmit and pay again.

So repair locally first, and only re-submit what genuinely cannot be read.
Every repair is RECORDED, because a silently repaired payload is a payload
nobody audits.

The repairs are deliberately conservative and syntactic. None of them invents
content; if a repair changes what the model said, that is a bug, not a feature.
The one partial-content repair — `cut-at-last-comma` for outputs TRUNCATED at
a token ceiling (R10: Gemini cut at 10,457 chars, gpt-5.4 at 5,363) — DELETES
the broken tail, never fabricates a closing value, and stamps the object
`_truncated: true` so an aggregation can never mistake it for a complete answer.

SHAPES (since 2026-09-02 the panel spans four lanes with four result shapes):
  * `or-poll`      — OpenRouter poll body: results[].response.body.choices[0]
  * `google-poll`  — Google batchGenerateContent poll: inlinedResponses walk,
                     text in candidates[0].content.parts[] (thought parts skipped)
  * `openai-jsonl` — OpenAI direct batch output file: one JSON line per item,
                     response.body is a /v1/responses or /v1/chat/completions object
  * `parsed-failures` — this project's own <tag>.parsed.json, whose failures[]
                     carry raw_text/head kept exactly for this tool
Shape is AUTO-DETECTED from the file content and printed. Detection failing is
loud, never a silent zero-item pass (the R09 empty-glob lesson: an audit that
examines no items reports success).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)
# A backslash that is not the start of a legal JSON escape.
LONE_BACKSLASH = re.compile(r'\\(?!["\\/bfnrtu])')
TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def cut_at_last_comma(text: str) -> str:
    """Truncated-output repair (task #77, folded in here 2026-09-02 rather than
    a second salvage script — inventory rule). Walk back to the last comma at
    array/object level, drop the unfinished element, close every open bracket.
    Content-DELETING by design: the half-written element is unreadable anyway;
    what parses afterwards is exactly what the model finished saying."""
    s = FENCE.sub("", text).strip()
    i = s.rfind(",")
    while i > 0:
        head = s[:i]
        # close whatever is open, innermost first
        stack = []
        in_str = esc = False
        for ch in head:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str and ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif not in_str and ch in "}]":
                if stack:
                    stack.pop()
        if in_str:
            i = s.rfind(",", 0, i)
            continue
        return head + "".join(reversed(stack))
    return s


def repairs(text: str) -> list[tuple[str, str]]:
    """Ordered (name, result) candidates, cheapest and least invasive first."""
    out = [("as-is", text)]
    stripped = FENCE.sub("", text).strip()
    out.append(("strip-fence", stripped))
    out.append(("escape-lone-backslash", LONE_BACKSLASH.sub(r"\\\\", stripped)))
    out.append(("drop-trailing-comma",
                TRAILING_COMMA.sub(r"\1",
                                   LONE_BACKSLASH.sub(r"\\\\", stripped))))
    # Last resort: the outermost {...} span, for prose wrapped around the object.
    i, j = stripped.find("{"), stripped.rfind("}")
    if i != -1 and j > i:
        out.append(("outermost-braces",
                    LONE_BACKSLASH.sub(r"\\\\", stripped[i:j + 1])))
    # Truly last: amputate the truncated tail. Ordered after everything content-
    # preserving so it can never win when a lossless repair would have parsed.
    out.append(("cut-at-last-comma",
                LONE_BACKSLASH.sub(r"\\\\", cut_at_last_comma(stripped))))
    return out


# --------------------------------------------------------------------------
# shape walkers: each yields (custom_id, text, usage) for every item found
# --------------------------------------------------------------------------
def _walk_or_poll(body: dict):
    for r in body.get("results", []):
        cid = r.get("custom_id")
        try:
            msg = r["response"]["body"]["choices"][0]["message"]["content"]
            usage = r["response"]["body"].get("usage", {})
        except (KeyError, IndexError, TypeError):
            yield cid, None, {"reason": "no content in response"}
            continue
        yield cid, msg, usage


def _walk_google_poll(body: dict):
    # reuse the 7-path result walk from google_batch by hand (kept dependency-free)
    node = body
    for path in (("response", "inlinedResponses", "inlinedResponses"),
                 ("response", "inlinedResponses"),
                 ("inlinedResponses", "inlinedResponses"),
                 ("inlinedResponses",)):
        n = body
        ok = True
        for k in path:
            if isinstance(n, dict) and k in n:
                n = n[k]
            else:
                ok = False
                break
        if ok and isinstance(n, list):
            node = n
            break
    else:
        node = []
    for item in node:
        cid = (item.get("metadata") or {}).get("key") or item.get("key") or "?"
        resp = item.get("response") or item
        cands = resp.get("candidates") or []
        if not cands:
            yield cid, None, {"reason": "no candidates"}
            continue
        text = "".join(p.get("text", "")
                       for p in cands[0].get("content", {}).get("parts", [])
                       if not p.get("thought"))
        yield cid, text, resp.get("usageMetadata", {})


def _walk_openai_jsonl(lines: list[str]):
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            yield "?", None, {"reason": "result line itself is not JSON"}
            continue
        cid = r.get("custom_id")
        body = (r.get("response") or {}).get("body") or {}
        text = ""
        if "output" in body:  # /v1/responses shape
            for item in body.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            text += c.get("text", "")
        elif "choices" in body:  # /v1/chat/completions shape
            try:
                text = body["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                text = ""
        yield cid, text or None, body.get("usage", {})


def _walk_parsed_failures(data: dict):
    for f in data.get("failures", []):
        text = f.get("raw_text") or f.get("head")
        yield f.get("custom_id"), text, {"reason": f.get("json_error") or
                                         f.get("error") or "recorded failure"}


def detect_and_walk(path: pathlib.Path):
    """Returns (shape_name, iterator). Loud on failure — never a silent empty pass."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl" or ("\n" in raw.strip() and
                                   raw.lstrip().startswith("{") and
                                   '"custom_id"' in raw.splitlines()[0]):
        try:
            first = json.loads(raw.strip().splitlines()[0])
            if "custom_id" in first and "response" in first:
                return "openai-jsonl", _walk_openai_jsonl(raw.splitlines())
        except json.JSONDecodeError:
            pass
    doc = json.loads(raw)
    body = doc.get("body", doc)
    if isinstance(body, dict) and "results" in body:
        return "or-poll", _walk_or_poll(body)
    if isinstance(body, dict) and ("inlinedResponses" in json.dumps(body)[:200000]):
        return "google-poll", _walk_google_poll(body)
    if "failures" in doc or "parsed" in doc:
        return "parsed-failures", _walk_parsed_failures(doc)
    raise SystemExit(
        f"REFUSING: could not detect a known result shape in {path}. Known: "
        f"or-poll / google-poll / openai-jsonl / parsed-failures. An undetected "
        f"shape must fail loudly — a walker that matches nothing would print a "
        f"clean pass over a file it never read (R09 lesson).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", required=True,
                    help="a result file from ANY lane: OR <tag>.poll.json, Google "
                         "<tag>.poll.json, an OpenAI batch output .jsonl, or this "
                         "project's own <tag>.parsed.json (failures are re-tried)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    src = pathlib.Path(a.poll)
    shape, items = detect_and_walk(src)
    print(f"shape detected: {shape}")
    recovered, still_broken, log = [], [], []
    n_seen = 0

    for cid, msg, usage in items:
        n_seen += 1
        if msg is None:
            still_broken.append({"custom_id": cid,
                                 "reason": usage.get("reason", "no content")})
            continue

        for name, candidate in repairs(msg):
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue  # a bare string/number parsing is not a recovery
            obj["_usage"] = usage
            obj["_repair"] = name
            if name == "cut-at-last-comma":
                obj["_truncated"] = True  # aggregation must not read this as complete
            recovered.append(obj)
            log.append({"custom_id": cid, "repair": name})
            break
        else:
            still_broken.append({"custom_id": cid, "reason": "no repair parsed",
                                 "head": msg[:300]})

    if n_seen == 0:
        raise SystemExit(f"REFUSING: shape '{shape}' walked ZERO items in {src}. "
                         f"Zero items is a detection failure, not a clean pass.")

    pathlib.Path(a.out).write_text(
        json.dumps({"parsed": recovered, "failures": still_broken},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    by_repair: dict[str, int] = {}
    for e in log:
        by_repair[e["repair"]] = by_repair.get(e["repair"], 0) + 1
    print(f"recovered {len(recovered)} / still broken {len(still_broken)}")
    print(f"repairs used: {by_repair}")
    for e in log:
        if e["repair"] != "as-is":
            print(f"  REPAIRED {e['custom_id']} via {e['repair']}")
    for b in still_broken:
        print(f"  UNRECOVERABLE {b['custom_id']}: {b['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
