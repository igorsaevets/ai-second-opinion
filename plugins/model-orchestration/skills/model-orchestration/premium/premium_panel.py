#!/usr/bin/env python3
"""Premium second-opinion panel — THIN DISPATCHER over the per-lane runners.

The operator, 2026-09-02, verbatim: «весь код из batch скриптов не интегрировать в твой
единый файл, а в твоем файле просто добавить, что есть скрипт, который premium
запускает Batch. Просто у Batch совсем другая специфика работы и лучше не
смешивать.» So this file knows NO wire shapes. It composes the item text,
enforces the money/PII gates, spawns the runners, and aggregates:

    offline seats  -> batch_one.py   (OR / OpenAI-direct / Google-direct batch)
    live-web seat  -> flex_lane.py   (OpenAI-direct Flex + web_search)
    report         -> aggregate_findings.py --lane ... (panel mode)

Shape: 1 brief × N models (the operator's pick over the N×M matrix). v0.3 default
lineup (panel-converged 2026-09-02; flash seat = CANARY, reported separately,
never counted into premium convergence):

    solpro   or-batch      openai/gpt-5.6-sol-pro:batch   $1/$5 (promo ≤2026-11-21)
    gpt55    openai-batch  gpt-5.5                        $2.50/$15
    gemini31 google-batch  gemini-3.1-pro-preview         $1/$6 ≤200k
    flash    google-batch  gemini-3.7-flash               $0.375/$1.875  [CANARY]
    live54   flex-openai   gpt-5.4 + web_search           $1.25/$7.50 + web

GATES, in order, all before any network call:
  1. --plan file must exist (call plan to disk before the first paid call);
  2. structural SECRETS scan of what every lane would carry, ALL lanes, no
     override at any setting — the same absolute rule as orchestrate.py
     (PRIVACY.md's «Secrets are blocked outright» must stay true here too);
  3. per-lane discount check via prices.py — «если скидки нет … писать, что
     скидки нет» (the operator): refuse, or --allow-nodiscount submits at sync
     price with the warning printed;
  4. OR lanes: structural PII scan of the COMPOSED text (A-numbers, SSN,
     receipt numbers, email, phone, plus an optional needle file kept OUTSIDE
     the tree) — identifiers never go to a broker (per-lane PII policy);
  5. worst-case arithmetic vs --ceiling: flex worst case counts max_tool_calls
     × ~25K fetched tokens billed as INPUT (Probe D shape), batch lanes count
     full output headroom. Mid-flight abort is impossible — the ceiling is
     enforced PRE-submit or not at all.

A submit is irreversible spend. Smoke first (--mode smoke: a tiny public-fact
brief through the SAME code path), and nothing here ever auto-retries.

PUBLISH-AUDIT: PATTERN-SOURCE — the PII/secret tables below are detectors
(regex definitions), not content.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


PR = _load("prices")
PY = sys.executable


def _spawn(argv: list[str]) -> int:
    sys.stdout.flush()  # keep parent narration ordered around child output
    return subprocess.run(argv).returncode

# ---- v0.3 lineup. Slugs/prices resolve from models_snapshot.json at run time;
# this table holds only lane WIRING. Override with --lanes-file (same JSON shape).
DEFAULT_LANES: list[dict] = [
    {"name": "solpro", "kind": "or-batch", "model": "openai/gpt-5.6-sol-pro:batch",
     "effort": "max", "max_output": 120000, "role": "vote"},
    {"name": "gpt55", "kind": "openai-batch", "model": "gpt-5.5",
     "effort": "xhigh", "max_output": 128000, "endpoint": "chat", "role": "vote"},
    {"name": "gemini31", "kind": "google-batch", "model": "gemini-3.1-pro-preview",
     "thinking_budget": 32768, "max_output": 65536, "role": "vote",
     "_tb_note": "pro-preview thinking ceiling NOT captured — 32768 is the "
                 "call-plan number; check thoughtsTokenCount on the smoke"},
    {"name": "flash", "kind": "google-batch", "model": "gemini-3.7-flash",
     "thinking_budget": 24576, "max_output": 65536, "role": "canary",
     "_note": "CANARY seat: detects lane/corpus breakage; findings reported in "
              "their own section, never counted into premium convergence "
              "(grokbuild canary-vote resolution, 2026-09-02)"},
    {"name": "live54", "kind": "flex-openai", "model": "gpt-5.4",
     "effort": "xhigh", "max_output": 128000, "web": "on", "role": "vote",
     "max_tool_calls": 12, "allowed_domains": ""},
]

PANEL_SCHEMA = """
Return ONLY a JSON object, no prose outside it, no markdown fence:

{
  "reviewer": "<the lane id you were given>",
  "verdict": "<one paragraph: overall judgement of the brief's question>",
  "findings": [
    {
      "severity": "fatal" | "serious" | "moderate" | "minor",
      "claim": "<the finding, 1-3 sentences, self-contained>",
      "where": "<the exact quoted words from the brief (or URL) this attacks>",
      "why": "<why this is true / why it matters — the reasoning, not a restatement>",
      "fix": "<the concrete correction or action>",
      "sources": ["<url or brief-section per load-bearing fact; a dated URL for every dated claim>"]
    }
  ],
  "sources_opened": ["<every URL you actually fetched, if you have web access; [] if none>"],
  "other_observations": [
    {"note": "<anything true and useful that no question invited>",
     "severity_if_it_were_a_finding": "fatal" | "serious" | "moderate" | "minor" | "informational"}
  ],
  "end_marker": "<the marker string you were given, verbatim>"
}

Zero findings is a permitted and respected answer — do not pad. A claim without
its `where` quote is worthless. Never present a guess as a citation: if you did
not open a source, say so in the claim itself.
"""

SMOKE_BRIEF = """SMOKE TEST — pipe validation only, not a real review.
Answer from general knowledge; web access is NOT required.

Question: name the two standard HTTP request methods most central to a REST
API (safe retrieval + resource creation), and state in one sentence what each
does.

Answer in the JSON schema below. Use severity "informational"-free fields as
specified; put the two methods into `findings` as two "minor" items.
"""

PII_PATTERNS = [
    ("a_number", re.compile(r"\bA[- ]?\d{8,9}\b")),
    ("ssn_dashed", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("receipt", re.compile(r"\b(?:MSC|SRC|IOE|EAC|WAC|LIN)[- ]?\d{10}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b")),
    ("us_phone", re.compile(r"\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")),
]

# Structural secrets scan, ALL lanes — the same shapes orchestrate.py refuses
# (private-key block, bearer token, labelled secret), and the same absoluteness:
# no override flag exists on purpose, because «the user meant to» is
# indistinguishable from «the user did not notice». The BEGIN...KEY marker is
# split across two literals so the assembled shape never sits in this source.
SECRET_PATTERNS = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE " r"KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("labelled_secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*\S{8,}")),
]


def secrets_scan(text: str) -> list[str]:
    """Kind names only — the matched value never reaches stdout or a log."""
    return [name for name, rx in SECRET_PATTERNS if rx.search(text)]


def pii_scan(text: str, needles_file: str) -> list[str]:
    hits = [f"{name}: {m.group(0)[:4]}…" for name, rx in PII_PATTERNS
            for m in [rx.search(text)] if m]
    if needles_file:
        np = pathlib.Path(needles_file)
        if not np.exists():
            raise SystemExit(f"REFUSING: --pii-needles {np} does not exist. A "
                             f"missing needle list scans nothing and prints a "
                             f"clean pass (the R09/redact lesson).")
        for line in np.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line.lower() in text.lower():
                hits.append(f"needle: {line[:3]}…")
    return hits


def compose(brief: str, posture: str, lane_name: str, marker: str) -> str:
    parts = []
    if posture.strip():
        parts.append(posture.rstrip())
    parts.append(brief.rstrip())
    parts.append(f"\nYour lane id: {lane_name}\nEnd marker: {marker}\n{PANEL_SCHEMA}")
    return "\n\n".join(parts)


def worst_case_usd(lane: dict, item_chars: int) -> float:
    est_in = item_chars / 3.5
    if lane["kind"] == "or-batch":
        p = PR.or_batch(lane["model"])
        return p.cost(int(est_in), lane.get("max_output", 120000))
    if lane["kind"] == "openai-batch":
        p = PR.openai_direct(lane["model"], "batch")
        return p.cost(int(est_in), lane.get("max_output", 128000))
    if lane["kind"] == "google-batch":
        p = PR.google_batch(lane["model"])
        return p.cost(int(est_in), lane.get("max_output", 65536))
    if lane["kind"] == "flex-openai":
        p = PR.openai_direct(lane["model"], "flex")
        web_input = lane.get("max_tool_calls", 12) * 25_000  # Probe D shape
        web_fee = lane.get("max_tool_calls", 12) * 10.00 / 1000
        return p.cost(int(est_in) + web_input, lane.get("max_output", 128000)) + web_fee
    raise SystemExit(f"REFUSING: unknown lane kind {lane['kind']!r}")


def discount_gate(lane: dict, allow: bool) -> None:
    p = {"or-batch": lambda: PR.or_batch(lane["model"]),
         "openai-batch": lambda: PR.openai_direct(lane["model"], "batch"),
         "google-batch": lambda: PR.google_batch(lane["model"]),
         "flex-openai": lambda: PR.openai_direct(lane["model"], "flex")}[lane["kind"]]()
    if not p.discount:
        msg = (f"🔴 СКИДКИ НЕТ на lane '{lane['name']}' ({p.name}): "
               f"{p.discount_reason or 'snapshot has no discounted tier'}")
        if not allow:
            raise SystemExit("REFUSING: " + msg + " — drop the lane or pass "
                             "--allow-nodiscount to submit at sync price.")
        print(msg + " — submitting at SYNC price on explicit --allow-nodiscount.")


def run_lane(lane: dict, item_file: pathlib.Path, rundir: pathlib.Path,
             mode: str, marker: str) -> tuple[str, list[str]]:
    """Returns (lane_name, argv) for the runner subprocess."""
    if lane["kind"] == "flex-openai":
        argv = [PY, str(HERE / "flex_lane.py"), "--path", "openai",
                "--model", lane["model"], "--tier", "flex",
                "--web", lane.get("web", "off"),
                "--brief", str(item_file), "--rundir", str(rundir),
                "--tag", lane["name"], "--marker", marker,
                "--effort", lane.get("effort", "xhigh"),
                "--max-output", str(lane.get("max_output", 128000)),
                "--max-tool-calls", str(lane.get("max_tool_calls", 12))]
        if lane.get("allowed_domains"):
            argv += ["--allowed-domains", lane["allowed_domains"]]
        return lane["name"], argv
    lane_map = {"or-batch": "or", "openai-batch": "openai", "google-batch": "google"}
    argv = [PY, str(HERE / "batch_one.py"), "--lane", lane_map[lane["kind"]],
            "--model", lane["model"].removesuffix(":batch")
            if lane["kind"] != "or-batch" else lane["model"],
            "--mode", mode, "--input-file", str(item_file),
            "--rundir", str(rundir), "--tag", lane["name"],
            "--effort", lane.get("effort", "xhigh"),
            "--max-output", str(lane.get("max_output", 120000))]
    if lane["kind"] == "google-batch":
        argv += ["--thinking-budget", str(lane.get("thinking_budget", 24576))]
        if lane.get("search"):
            argv += ["--search"]
    if lane["kind"] == "openai-batch" and lane.get("endpoint"):
        # batch_one's --endpoint flag: 'chat' pinned for gpt-5.5 (its /v1/responses
        # support inside batch is unverified — snapshot _gpt55_endpoint_note)
        argv += ["--endpoint", lane["endpoint"]]
    return lane["name"], argv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry", "smoke", "submit", "poll", "collect"],
                    required=True)
    ap.add_argument("--plan", required=True,
                    help="path to the CALL-PLAN file. Must exist — the plan goes "
                         "to disk BEFORE the first paid call, always.")
    ap.add_argument("--brief", default="", help="brief file (required for submit)")
    ap.add_argument("--posture-file", default="")
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--marker", default="PREMIUM-PANEL-DONE-01")
    ap.add_argument("--ceiling", type=float, default=0.0,
                    help="USD hard ceiling, enforced PRE-submit on worst-case "
                         "arithmetic (mid-flight abort is impossible)")
    ap.add_argument("--lanes-file", default="",
                    help="JSON list overriding the built-in v0.3 lineup")
    ap.add_argument("--only", default="", help="comma list of lane names to run")
    ap.add_argument("--allow-nodiscount", action="store_true")
    ap.add_argument("--pii-needles", default="",
                    help="file of name-needles (kept OUTSIDE the repo tree) "
                         "scanned before any OR lane submit")
    a = ap.parse_args()

    plan = pathlib.Path(a.plan)
    if not plan.exists():
        print(f"REFUSING: call plan {plan} does not exist. Write the plan file "
              f"first — lane, model, item count, est tokens, est cost, ceiling.",
              file=sys.stderr)
        return 2
    rundir = pathlib.Path(a.rundir); rundir.mkdir(parents=True, exist_ok=True)

    lanes = (json.loads(pathlib.Path(a.lanes_file).read_text(encoding="utf-8"))
             if a.lanes_file else DEFAULT_LANES)
    if a.only:
        want = {s.strip() for s in a.only.split(",")}
        lanes = [l for l in lanes if l["name"] in want]
        if not lanes:
            print(f"REFUSING: --only {a.only!r} matched no lane", file=sys.stderr)
            return 2

    # ---- collect/poll need no brief ---------------------------------------
    if a.mode == "poll":
        rcs = {}
        for lane in lanes:
            if lane["kind"] == "flex-openai":
                continue  # sync — already ran at submit time
            name, argv = run_lane(lane, rundir / f"item-{lane['name']}.txt",
                                  rundir, "poll", a.marker)
            print(f"\n== poll {name} ==")
            rcs[name] = _spawn(argv)
        still = [n for n, rc in rcs.items() if rc == 3]
        if still:
            print(f"\nstill running: {', '.join(still)} — re-run --mode poll "
                  f"(wrap in poll_loop.py for the polite cadence)")
            return 3
        return 0 if all(rc == 0 for rc in rcs.values()) else 1

    if a.mode == "collect":
        args = [PY, str(HERE / "aggregate_findings.py"), "--out",
                str(rundir / "PANEL-REPORT.md"), "--title",
                "Premium panel — 1 brief × N models"]
        missing = []
        for lane in lanes:
            p = rundir / f"{lane['name']}.parsed.json"
            if p.exists():
                prefix = "CANARY-" if lane.get("role") == "canary" else ""
                args += ["--lane", f"{prefix}{lane['name']}={p}"]
            else:
                missing.append(lane["name"])
        if missing:
            print(f"⚠️ no parsed output for: {', '.join(missing)} — aggregating "
                  f"the rest; the report will show the hole, not paper over it.")
        return _spawn(args)

    # ---- compose the item -------------------------------------------------
    if a.mode == "smoke":
        brief = SMOKE_BRIEF
    else:
        if not a.brief:
            print("REFUSING: --brief is required for submit/dry", file=sys.stderr)
            return 2
        bp = pathlib.Path(a.brief)
        if not bp.exists():
            print(f"REFUSING: brief {bp} does not exist", file=sys.stderr)
            return 2
        brief = bp.read_text(encoding="utf-8")
        if len(brief.strip()) < 500:
            print(f"REFUSING: brief is {len(brief)} chars (<500). An empty brief "
                  f"bills N lanes for a review of nothing.", file=sys.stderr)
            return 2
    posture = ""
    if a.posture_file:
        pp = pathlib.Path(a.posture_file)
        if not pp.exists():
            print(f"REFUSING: --posture-file {pp} does not exist", file=sys.stderr)
            return 2
        posture = pp.read_text(encoding="utf-8")

    # Secrets never leave, on ANY lane, at any setting. Scanned once on
    # everything a lane would carry (brief + posture; compose() only appends
    # this file's own constants), BEFORE the per-lane walk — so the refusal
    # does not depend on which lanes are selected or on any snapshot lookup.
    sec = secrets_scan(posture + "\n" + brief)
    if sec:
        print(f"REFUSING: secret-shaped content in the composed text "
              f"({', '.join(sec)}). Secrets are never sent, at any setting — "
              f"no override exists. Remove it, rotate anything real, re-run.",
              file=sys.stderr)
        return 2

    # one composed item per lane (lane id + marker differ); PII gate on OR text
    total_worst = 0.0
    item_files: dict[str, pathlib.Path] = {}
    print(f"lanes: {', '.join(l['name'] + (' [CANARY]' if l.get('role') == 'canary' else '') for l in lanes)}")
    for lane in lanes:
        discount_gate(lane, a.allow_nodiscount)
        text = compose(brief, posture, lane["name"], a.marker)
        if lane["kind"] == "or-batch":
            hits = pii_scan(text, a.pii_needles)
            if hits:
                print(f"REFUSING: OR lane '{lane['name']}' — identifiers in the "
                      f"composed text ({'; '.join(hits[:6])}). Identifiers never "
                      f"go to a broker: pseudonymise the brief or drop the lane.",
                      file=sys.stderr)
                return 2
        f = rundir / f"item-{lane['name']}.txt"
        f.write_text(text, encoding="utf-8")
        item_files[lane["name"]] = f
        wc = worst_case_usd(lane, len(text))
        total_worst += wc
        print(f"  {lane['name']:<10} {lane['kind']:<13} worst-case ${wc:.4f}")
    print(f"worst-case total: ${total_worst:.4f}  (ARITHMETIC, pre-submit gate)")

    if a.mode == "dry":
        for lane in lanes:
            if lane["kind"] == "flex-openai":
                continue
            name, argv = run_lane(lane, item_files[lane["name"]], rundir,
                                  "build", a.marker)
            print(f"\n== build {name} ==")
            _spawn(argv)
        print("\nDRY ONLY — nothing was submitted, nothing billed.")
        return 0

    if a.ceiling <= 0:
        print("REFUSING: smoke/submit needs an explicit --ceiling", file=sys.stderr)
        return 2
    if total_worst > a.ceiling:
        print(f"REFUSING: worst-case ${total_worst:.4f} exceeds ceiling "
              f"${a.ceiling:.2f}. Shrink lanes/brief/max_tool_calls or raise the "
              f"ceiling ON THE RECORD in the call plan.", file=sys.stderr)
        return 2

    # ---- submit: async batch lanes first, then the sync flex lane(s) ------
    rcs: dict[str, int] = {}
    for lane in lanes:
        if lane["kind"] == "flex-openai":
            continue
        name, argv = run_lane(lane, item_files[lane["name"]], rundir,
                              "submit", a.marker)
        print(f"\n== submit {name} ==")
        rcs[name] = _spawn(argv)
    for lane in lanes:
        if lane["kind"] != "flex-openai":
            continue
        name, argv = run_lane(lane, item_files[lane["name"]], rundir,
                              "submit", a.marker)
        print(f"\n== flex (sync) {name} ==")
        rcs[name] = _spawn(argv)
    bad = [n for n, rc in rcs.items() if rc not in (0,)]
    print(f"\nsubmitted: { {n: rc for n, rc in rcs.items()} }")
    print("next: poll_loop.py --cmd \"" + PY.replace("\\", "/") + " " +
          str(HERE / "premium_panel.py").replace("\\", "/") +
          f" --mode poll --plan {a.plan} "
          f"--rundir {a.rundir}\"  then --mode collect")
    if bad:
        print(f"⚠️ non-zero lanes: {', '.join(bad)} — NO auto-retry; a billable "
              f"error is retried only by a human decision.")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
