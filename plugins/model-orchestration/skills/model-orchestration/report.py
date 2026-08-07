#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render diagnostics.json into the run report a human actually reads.

    python report.py <path to diagnostics.json> [--out REPORT.md]

WHY THIS FILE EXISTS. `diagnostics.json` has been written by the harness, to a fixed schema,
since 2026-08-02 - it is not invented per run. The thing that WAS invented per run is the prose
report: each session wrote its own "how the orchestration went" summary by hand, choosing its own
columns, and therefore choosing what to leave out. Igor, 2026-08-07: «если я запрошу у ИИ
диагностические данные и отчет как сработала оркестрация, он должен писать какой тир он выбрал,
в нашем случае deep, а то вдруг он выберет слабый ответ, а я это и не узнаю.»

That names the exact failure mode of a hand-written summary: **the setting that was not chosen
well is the setting the author does not think to mention.** A tier is invisible in the output -
a `quick` review and a `deep` review are both fluent - so the only protection is a template that
prints it whether or not anyone thought it interesting. Every field below is here because leaving
it out would hide a decision rather than a detail.

Written automatically next to diagnostics.json at the end of every run, so "give me the
diagnostics" is a file to open rather than a summary to compose. It is a pure function of the
JSON: it adds no interpretation the harness did not already record, and it can be regenerated
from an old run months later.
"""

import argparse
import json
import os
import sys

# The seat each channel is meant to occupy. Printed next to the telemetry because a number without
# a job is not evidence: kimi returning few tokens is normal for a code channel and alarming for a
# deep reader, and only the reader knows which it was supposed to be.
_UNKNOWN = "-"


def _fmt_int(n):
    if n is None:
        return _UNKNOWN
    try:
        return "{:,}".format(int(n)).replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _rows(d):
    """One row per channel, merging the plan (what we asked for) with the result (what happened).

    Merged deliberately. They were two separate tables in the hand-written reports, and the
    interesting failures live in the DIFFERENCE between them - a channel planned at xhigh that
    returned in 84 seconds is the shape of the round-25 agy36flash failure, and it is invisible
    unless the request and the outcome sit on one line.
    """
    plan = d.get("plan") or {}
    chans = d.get("channels") or {}
    cites = ((d.get("citations") or {}).get("results")) or {}
    out = []
    for name in sorted(set(plan) | set(chans)):
        p = plan.get(name) or {}
        r = chans.get(name) or {}
        c = cites.get(name) or {}
        tally = c.get("tally") or {}
        out.append({
            "name": name,
            "ok": r.get("ok"),
            "ran": name in chans,
            "model": p.get("model") or r.get("model") or _UNKNOWN,
            "label": p.get("model_label") or _UNKNOWN,
            "overridden": bool(p.get("model_overridden")),
            "default": p.get("model_default"),
            "effort": r.get("effort") or p.get("effort") or _UNKNOWN,
            "seconds": r.get("seconds"),
            "bytes": r.get("bytes"),
            "in_tokens": r.get("in_tokens"),
            "out_tokens": r.get("out_tokens"),
            "reasoning": r.get("reasoning_tokens") or r.get("thinking"),
            "tools": r.get("tool_calls"),
            "searches": r.get("searches"),
            "denied": r.get("denied"),
            "tool_errors": r.get("tool_errors"),
            "cited": c.get("cited"),
            "grounded": r.get("n_grounded"),
            "live": tally.get("LIVE"),
            "dead": c.get("dead"),
            "data_policy": p.get("data_policy"),
            "web": (p.get("web") or {}).get("enabled"),
            "warnings": r.get("warnings") or [],
            "notes": r.get("notes") or [],
        })
    return out


def render(d):
    L = []
    inv = d.get("invocation") or {}
    env = d.get("environment") or {}
    rows = _rows(d)

    L.append("# Orchestration run report")
    L.append("")
    L.append("Generated from `diagnostics.json` by `report.py`. Every number here was recorded by "
             "the harness during the run; nothing is recalled or estimated.")
    L.append("")

    # ---- the settings block. Tier first, by explicit request and for a good reason. ----
    L.append("## What was asked for")
    L.append("")
    L.append("| setting | value | why it matters |")
    L.append("|---|---|---|")
    # 🔴 FAIL LOUDLY, NOT BLANKLY. Found by the kimi channel reviewing this very file on the day
    # it was written: «if a field is missing/null in the JSON, does the renderer fail, or render a
    # blank that reads as "fine"? It must fail loudly.» Measured on three mutations - key removed,
    # key renamed, whole `invocation` object gone - and all three printed a tidy `?`, which scans
    # as "not applicable" rather than as "this report failed at its one job".
    #
    # That is this project's signature defect appearing inside the instrument built to prevent it:
    # the report exists BECAUSE a tier is invisible in the output, and its own failure mode was
    # to make the tier invisible in the report. A placeholder is an answer; absence must not be
    # spelled the same way as a value.
    tier = inv.get("tier")
    if tier:
        L.append("| **TIER** | **%s** | depth and timeouts for every channel. A weak tier "
                 "produces a fluent, shallow review that reads exactly like a deep one. |" % tier)
    else:
        L.append("| **TIER** | 🔴 **NOT RECORDED - DO NOT TRUST THIS RUN'S DEPTH** | "
                 "`invocation.tier` is absent from diagnostics.json. Either the schema changed "
                 "and this renderer is reading the wrong key, or the run did not record it. "
                 "Treat the review as of UNKNOWN depth until you find the tier in the run log. |")
    L.append("| system preset | %s | `legal-research` reframes a legal brief as source "
             "verification; without it some channels refuse on policy. |"
             % (inv.get("system") or "(default)"))
    L.append("| brief size | %s chars | search results accumulate on top of this, so `in` tokens "
             "below will be much larger. |" % _fmt_int(inv.get("brief_chars")))
    L.append("| end marker | `%s` | a channel whose output does not end with it is INCOMPLETE. |"
             % (inv.get("marker") or "?"))
    L.append("| channels | %s of %s returned a verified review | |"
             % (d.get("ok_channels"), d.get("total_channels")))
    L.append("| wall clock | %s s | |" % _fmt_int(round(d.get("seconds") or 0)))
    for key, label in (("only", "--only"), ("skip", "--skip"), ("route", "--route")):
        if inv.get(key):
            L.append("| %s | %s | channel selection was narrowed. |" % (label, inv[key]))
    if inv.get("sets"):
        L.append("| **--set** | **%s** | 🔴 a model was overridden away from the registry default. |"
                 % ", ".join(inv["sets"]))
    L.append("| PII gate | %s | |"
             % ("BYPASSED (--allow-pii)" if inv.get("allow_pii") else "enforced"))
    L.append("")

    # ---- model identity. The thing a name can lie about. ----
    L.append("## Which model actually answered")
    L.append("")
    L.append("| channel | model | effort | data policy |")
    L.append("|---|---|---|---|")
    for r in rows:
        flag = " 🔴 **OVERRIDDEN** (registry default: `%s`)" % r["default"] if r["overridden"] else ""
        L.append("| `%s` | %s `%s`%s | %s | %s |"
                 % (r["name"], r["label"], r["model"], flag, r["effort"],
                    (r["data_policy"] or "-")[:110]))
    L.append("")
    if any(r["overridden"] for r in rows):
        L.append("🔴 **A model was overridden.** One channel = one model is the rule; a channel "
                 "running something other than its registry default means someone passed `--set`. "
                 "That is visible here and nowhere else in the outputs.")
        L.append("")

    # ---- telemetry ----
    L.append("## What each channel actually did")
    L.append("")
    L.append("| channel · model | verdict | s | in tok | out tok | reasoning | tools | searches | bytes |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if not r["ran"]:
            verdict = "not run"
        elif r["ok"]:
            verdict = "OK"
        else:
            verdict = "🔴 FAILED"
        # 🔴 THE MODEL TRAVELS WITH THE CHANNEL NAME IN EVERY TABLE. Igor, 2026-08-07: «пусть ИИ
        # выводит codex и название модели, а не просто Codex». This table is what another chat
        # quotes when asked how the orchestration went, and `codex` alone does not say whether it
        # ran GPT-5.4 or 5.6 - a channel whose model rotates by design. Resolved at render time
        # from the run's own plan, so it cannot drift the way a static label would.
        who = "`%s`" % r["name"]
        if r["label"] and r["label"] != _UNKNOWN:
            who += " · %s" % r["label"]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (who, verdict, _fmt_int(r["seconds"]), _fmt_int(r["in_tokens"]),
                    _fmt_int(r["out_tokens"]), _fmt_int(r["reasoning"]), _fmt_int(r["tools"]),
                    _fmt_int(r["searches"]), _fmt_int(r["bytes"])))
    L.append("")
    L.append("`-` means the channel does not report that number, which is different from zero. "
             "Codex reports tokens but never which pages it opened; the Spark channels report "
             "tokens and tool-call counts; the agy channels report everything; the OpenRouter "
             "channels report tokens but not tool calls.")
    L.append("")

    # ---- grounding. The section that stops a citation count being mistaken for evidence. ----
    L.append("## Citations - existence, and separately, grounding")
    L.append("")
    L.append("| channel | cited | resolved LIVE | DEAD | actually opened |")
    L.append("|---|---|---|---|---|")
    for r in rows:
        if r["cited"] is None:
            continue
        opened = _UNKNOWN if r["grounded"] is None else "%s of %s" % (r["grounded"], r["cited"])
        L.append("| `%s` | %s | %s | %s | %s |"
                 % (r["name"], _fmt_int(r["cited"]), _fmt_int(r["live"]),
                    _fmt_int(r["dead"]), opened))
    L.append("")
    L.append("🔴 **These two columns are not the same claim and the difference is the whole "
             "point.** *LIVE* means the URL resolves - it does not mean the model read it. "
             "*actually opened* comes from that channel's own tool telemetry and is the only "
             "grounding evidence there is. A channel can cite seven live government URLs having "
             "opened two of them; the other five came from memory and are exactly where "
             "fabricated document numbers appear. Where *actually opened* shows `-`, the channel "
             "reports no tool telemetry and grounding is **unknown, not good**.")
    L.append("")

    # ---- problems ----
    probs = d.get("problems") or []
    L.append("## Problems")
    L.append("")
    if not probs:
        L.append("None recorded.")
    for p in probs:
        L.append("- 🔴 **`%s`** - %s" % (p.get("channel"), p.get("detail")))
        if p.get("likely_cause"):
            L.append("  - cause: %s" % p["likely_cause"])
        if p.get("suggested_fix"):
            L.append("  - fix: %s" % p["suggested_fix"])
    L.append("")
    for r in rows:
        for w in r["warnings"]:
            L.append("- 🔴 `%s`: %s" % (r["name"], w))
        for n in r["notes"]:
            L.append("- `%s` note: %s" % (r["name"], n))
    L.append("")

    # ---- environment, last: needed for a bug report, noise for a reader. ----
    L.append("## Environment")
    L.append("")
    for k in ("python", "platform", "codex_version", "agy_version", "spark_endpoint", "cwd"):
        if env.get(k):
            L.append("- `%s`: %s" % (k, env[k]))
    L.append("")
    L.append("## What this report cannot tell you")
    L.append("")
    L.append("- **Whether a review is CORRECT.** Every number here is about process, not truth. "
             "A channel can do 55 searches and be wrong.")
    L.append("- **Whether the models agreed for independent reasons.** Same-family voices "
             "(the two Spark channels, the two Gemini channels) share training data and blind "
             "spots, so their agreement is one vote counted twice, not corroboration.")
    L.append("- **What a channel was sent beyond the brief.** Nothing here records the system "
             "preset, which is identical for every channel.")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("diagnostics", help="path to diagnostics.json")
    ap.add_argument("--out", help="write here instead of stdout")
    a = ap.parse_args()
    with open(a.diagnostics, encoding="utf-8") as f:
        d = json.load(f)
    text = render(d)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %s (%d bytes)" % (a.out, len(text.encode("utf-8"))))
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
