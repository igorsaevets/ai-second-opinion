#!/usr/bin/env python3
"""Turn N per-lens JSON verdicts into one ranked report plus a cost/meter table.

Deliberately mechanical. The findings are merged, counted and sorted by plain
Python — no model is asked to summarise the models, because a summariser is a
second opinion wearing the costume of an aggregation, and it can invent a
finding that no lens produced.

LANE-AGNOSTIC since R08. The two lanes report usage in incompatible shapes and
only one of them reports money at all:

  * OpenRouter — `poll["body"]["usage"]["cost"]` is a real vendor meter, and
    per-item cache shows up as `prompt_tokens_details.cached_tokens`.
  * Google Batch — there is NO cost field anywhere in the response. The number
    in `parsed["meter"]["cost_arith"]` is arithmetic against a published price,
    which is a different kind of claim and is labelled as such wherever it is
    printed. `--poll` is therefore optional; when `parsed` carries a `meter`
    block that block wins, because it was computed at run time by the code that
    actually knew which lane it was on.

Reading a cost off a price list and calling it a meter is exactly the error this
project's invariants forbid, so the report never prints the word "meter" over an
arithmetic figure.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

SEV_ORDER = {"fatal": 0, "serious": 1, "moderate": 2, "minor": 3}
DIFF_ORDER = {"easy": 0, "moderate": 1, "hard": 2, "impossible": 3}


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def panel_mode(lanes: list[str], out_path: pathlib.Path, title: str) -> int:
    """PREMIUM PANEL shape: 1 brief × N models, one answer per lane.

    Extends this script rather than adding aggregate_panel.py (inventory rule,
    2026-09-02). Deliberately mechanical, and deliberately LESS than the lens
    mode: cross-lane 'agreement' between free-text claims is NOT computed,
    because a similarity score wearing the costume of convergence is exactly
    the R07 anti-pattern. What IS mechanical and honest:
      * per-lane usage / meter table (meter labelled meter, arithmetic labelled
        arithmetic — they are different kinds of claim);
      * every finding, per lane, sorted by severity;
      * verbatim-duplicate report (exact normalized-text matches only);
      * truncation/repair flags surfaced (a `_truncated` answer must never read
        as a complete one).
    Grouping real agreement across differently-worded findings is the READING
    session's job, same as the cheap panel."""
    data: list[tuple[str, dict]] = []
    for spec in lanes:
        if "=" not in spec:
            raise SystemExit(f"REFUSING: --lane needs NAME=PATH, got {spec!r}")
        name, p = spec.split("=", 1)
        path = pathlib.Path(p)
        if not path.exists():
            raise SystemExit(f"REFUSING: lane '{name}' file does not exist: {path}. "
                             f"A missing lane must fail loudly, not aggregate as "
                             f"an absence of findings.")
        data.append((name, json.loads(path.read_text(encoding="utf-8"))))

    L: list[str] = [f"# {title}\n"]
    L.append("## Per-lane status and usage\n")
    L.append("| lane | answers | failures | prompt tok | out tok | thinking | "
             "cost | cost kind | flags |\n|---|---:|---:|---:|---:|---:|---:|---|---|")
    total_arith = 0.0
    for name, d in data:
        parsed = d.get("parsed", [])
        meter = d.get("meter", {})
        u0 = (parsed[0].get("_usage", {}) if parsed else {})
        pt = meter.get("prompt_tokens") or u0.get("prompt_tokens") or \
            u0.get("input_tokens") or 0
        ot = meter.get("completion_tokens") or u0.get("completion_tokens") or \
            u0.get("output_tokens") or 0
        th = meter.get("thoughts_tokens") or \
            (u0.get("completion_tokens_details", {}) or {}).get("reasoning_tokens") or \
            (u0.get("output_tokens_details", {}) or {}).get("reasoning_tokens") or 0
        money = meter.get("cost_meter")
        kind = "METER" if money is not None else "arithmetic"
        if money is None:
            money = meter.get("cost_arith")
        if isinstance(money, (int, float)):
            total_arith += money
            money_s = f"${money:.4f}"
        else:
            money_s = "—"
        flags = []
        for o in parsed:
            if o.get("_truncated"):
                flags.append("TRUNCATED")
            if o.get("_repair") and o["_repair"] != "as-is":
                flags.append(f"repair:{o['_repair']}")
        L.append(f"| {name} | {len(parsed)} | {len(d.get('failures', []))} | "
                 f"{pt:,} | {ot:,} | {th:,} | {money_s} | {kind} | "
                 f"{' '.join(flags) or '—'} |")
    L.append(f"\nSum of the cost column: ${total_arith:.4f} — a MIX of meters and "
             f"arithmetic; per-lane rows above say which is which. Never quote "
             f"this sum as a metered total.\n")

    # findings per lane
    all_claims: dict[str, list[str]] = collections.defaultdict(list)
    for name, d in data:
        L.append(f"## {name}\n")
        for o in d.get("parsed", []):
            if o.get("verdict"):
                L.append(f"**Verdict.** {o['verdict']}\n")
            fs = sorted(o.get("findings", []),
                        key=lambda f: SEV_ORDER.get(f.get("severity"), 9))
            for i, f in enumerate(fs, 1):
                claim = f.get("claim") or f.get("attack") or ""
                L.append(f"### {name}-{i:02d} [{f.get('severity')}] {claim[:120]}\n")
                for k, label in (("claim", "Claim"), ("where", "Where"),
                                 ("why", "Why"), ("fix", "Fix")):
                    if f.get(k):
                        L.append(f"**{label}.** {f[k]}\n")
                if f.get("sources"):
                    L.append(f"**Sources.** {', '.join(map(str, f['sources']))}\n")
                all_claims[_norm(claim)].append(f"{name}-{i:02d}")
            obs = o.get("other_observations") or []
            if obs:
                L.append("**Other observations.**\n")
                for ob in obs:
                    note = ob.get("note") if isinstance(ob, dict) else str(ob)
                    L.append(f"- {note}")
                L.append("")
        for fl in d.get("failures", []):
            L.append(f"- ⚠️ unparsed item: {fl.get('custom_id')} — "
                     f"{fl.get('json_error') or fl.get('reason') or ''}")
        L.append("")

    dupes = {k: v for k, v in all_claims.items() if k and len(v) > 1}
    L.append("## Verbatim duplicates across lanes — the ONLY mechanical convergence\n")
    if dupes:
        for k, ids in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
            L.append(f"- {len(ids)} lanes word-for-word: {', '.join(ids)} — «{k[:140]}»")
    else:
        L.append("None. Differently-worded agreement is NOT measured here — "
                 "read the lanes; convergence judgment belongs to the reader.")
    L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"panel lanes: {len(data)}  answers: "
          f"{sum(len(d.get('parsed', [])) for _, d in data)}  "
          f"failures: {sum(len(d.get('failures', [])) for _, d in data)}")
    print(f"written: {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", help="lens mode: one parsed.json of N lenses")
    ap.add_argument("--poll", help="OpenRouter poll body; omit on lanes that "
                                   "carry their meter inside --parsed")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Adversarial red team, N lenses over one record")
    ap.add_argument("--lane", action="append", default=[],
                    help="panel mode: NAME=path/to/parsed.json, repeatable — "
                         "one per premium-panel lane (1 brief × N models shape)")
    a = ap.parse_args()

    if a.lane:
        return panel_mode(a.lane, pathlib.Path(a.out), a.title)
    if not a.parsed:
        ap.error("either --parsed (lens mode) or --lane (panel mode) is required")

    data = json.loads(pathlib.Path(a.parsed).read_text(encoding="utf-8"))
    poll = {}
    if a.poll:
        raw = json.loads(pathlib.Path(a.poll).read_text(encoding="utf-8"))
        poll = raw.get("body", raw) or {}
    meter = data.get("meter", {})

    rows: list[dict] = []
    for lens in data["parsed"]:
        for f in lens.get("findings", []):
            f = dict(f)
            f["lens"] = lens.get("lens", "?")
            rows.append(f)

    rows.sort(key=lambda f: (SEV_ORDER.get(f.get("severity"), 9),
                             DIFF_ORDER.get(f.get("cure_difficulty"), 9)))

    by_sev = collections.Counter(f.get("severity") for f in rows)
    by_lens = collections.Counter(f["lens"] for f in rows)

    L: list[str] = []
    L.append(f"# {a.title}\n")
    ident = poll.get("id") or poll.get("name") or meter.get("batch") or "n/a"
    L.append(f"Batch `{ident}` · model `{poll.get('model') or meter.get('model', 'n/a')}`\n")

    u = poll.get("usage", {})
    created, finalized = poll.get("created_at"), poll.get("finalized_at")
    mins = (finalized - created) / 60 if created and finalized else None
    money = u.get("cost")
    L.append("## Usage\n")
    L.append("| metric | value |\n|---|---:|")
    L.append(f"| items parsed | {meter.get('parsed', len(data['parsed']))} |")
    L.append(f"| items failed | {meter.get('failed', len(data.get('failures', [])))} |")
    if mins:
        L.append(f"| wall clock | {mins:.1f} min |")
    for key, label in (("prompt_tokens", "prompt tokens"),
                       ("cached_tokens", "of which cached"),
                       ("completion_tokens", "completion tokens"),
                       ("thoughts_tokens", "thinking tokens")):
        v = u.get(key, meter.get(key))
        if v is not None:
            L.append(f"| {label} | {v:,} |")
    if money is not None:
        L.append(f"| **cost (vendor meter)** | **${money:.6f}** |")
    elif meter.get("cost_arith") is not None:
        # NOT a meter. Google returns no cost field; this is a price-list
        # multiplication and the report has to say so on the same line.
        L.append(f"| cost (ARITHMETIC, not a meter) | ${meter['cost_arith']:.6f} |")
        if meter.get("cost_excludes"):
            L.append(f"| not included above | {meter['cost_excludes']} |")
    L.append("")

    # Per-item cache visibility: the standing open question is whether a shared
    # prefix inside ONE batch is ever served from cache. Report it, never assume it.
    # Implicit caching measured 0 here (R04); explicit caching measured 99.66% (R07).
    L.append("## Cache, per item — measured, not assumed\n")
    L.append("| lens | prompt | cached | reasoning | completion |\n|---|---:|---:|---:|---:|")
    for lens in data["parsed"]:
        us = lens.get("_usage", {})
        pd = us.get("prompt_tokens_details", {}) or {}
        cd = us.get("completion_tokens_details", {}) or {}
        prompt = us.get("prompt_tokens", us.get("promptTokenCount", 0))
        cached = pd.get("cached_tokens", us.get("cachedContentTokenCount", 0))
        reason = cd.get("reasoning_tokens", us.get("thoughtsTokenCount", 0))
        comp = us.get("completion_tokens", us.get("candidatesTokenCount", 0))
        L.append(f"| {lens.get('lens')} | {prompt:,} | {cached:,} | "
                 f"{reason:,} | {comp:,} |")
    L.append("")

    L.append("## Findings by severity\n")
    L.append("| severity | n |\n|---|---:|")
    for s in ("fatal", "serious", "moderate", "minor"):
        if by_sev.get(s):
            L.append(f"| {s} | {by_sev[s]} |")
    L.append(f"\nTotal findings: **{len(rows)}** across {len(by_lens)} lenses.\n")

    # 🔴 The total is bounded by the PROMPT, not by the document: SCHEMA_INSTRUCTION
    # says "Between three and eight findings". Printing the per-lens spread makes
    # that visible, so nobody reads "62 findings" as a property of the record.
    L.append("## Findings per lens — read this before quoting a total\n")
    L.append("The output contract asks for *between three and eight* findings per lens, so the\n"
             "total is a function of lens count and that instruction. A tight cluster means the\n"
             "quota is binding and the true defect count is NOT being measured.\n")
    L.append("| lens | n | severities |\n|---|---:|---|")
    for lens in data["parsed"]:
        fs = lens.get("findings", [])
        spread = collections.Counter(f.get("severity") for f in fs)
        pretty = " ".join(f"{k}={spread[k]}" for k in ("fatal", "serious", "moderate", "minor")
                          if spread.get(k))
        L.append(f"| {lens.get('lens')} | {len(fs)} | {pretty} |")
    counts = [len(l.get("findings", [])) for l in data["parsed"]]
    L.append(f"\nPer-lens spread: min {min(counts)}, max {max(counts)}, "
             f"mean {sum(counts) / len(counts):.2f} — against an allowed range of 3–8.\n")

    L.append("## Every finding, worst first\n")
    L.append("IDs are `F01…` in this sorted order and are referenced by the novelty diff.\n")
    for i, f in enumerate(rows, 1):
        f["id"] = f"F{i:02d}"
        L.append(f"### F{i:02d}. [{f.get('severity')}] {f.get('lens')} — "
                 f"cure: {f.get('cure_difficulty')}\n")
        L.append(f"**Officer's objection.** {f.get('attack')}\n")
        L.append(f"**Where.** {f.get('where')}\n")
        L.append(f"**Why it bites.** {f.get('why_it_bites')}\n")
        L.append(f"**Lawful cure.** {f.get('lawful_cure')}\n")

    L.append("## Per-lens verdicts\n")
    for lens in data["parsed"]:
        L.append(f"- **{lens.get('lens')}** — {lens.get('verdict')}")
        L.append(f"  - leads with: {lens.get('strongest_single_finding')}")
        L.append(f"  - gets right: {lens.get('what_the_petition_gets_right_on_this_lens')}")
    L.append("")

    if data.get("failures"):
        L.append("## Items that did not parse\n")
        for f in data["failures"]:
            L.append(f"- `{f.get('custom_id')}` — {f.get('json_error', 'no content')}")

    out = pathlib.Path(a.out)
    out.write_text("\n".join(L), encoding="utf-8")

    # Machine-readable twin. The novelty diff runs against this, not against the
    # markdown, so a change to the prose layout can never silently move a finding.
    side = out.with_suffix(".json")
    side.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"findings: {len(rows)}  severities: {dict(by_sev)}")
    print(f"per-lens counts: {[len(l.get('findings', [])) for l in data['parsed']]}")
    print(f"written: {out}")
    print(f"written: {side}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
