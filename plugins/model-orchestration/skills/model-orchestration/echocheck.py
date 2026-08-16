# -*- coding: utf-8 -*-
"""
echocheck.py - prove a depth knob from the METER, not from having sent it.

    python echocheck.py --only goog36flash --samples 3
    python echocheck.py --only kimik3 --levels low,high
    python echocheck.py --all --dry-run          # free: prints the plan and the call count

WHY THIS EXISTS
---------------
Every other check in this kit answers "was the argument dispatched?". `selftest.py` asserts that
`thinking_level` reaches `call_gemini_direct`; the plan prints what the tier resolved to. None of
that is evidence that the VENDOR did anything with it. Two measured facts from this project's own
history say the gap is real, not theoretical:

  * 🔴 An HTTP 200 does not mean a parameter was applied. `api.x.ai` and `api.xiaomimimo.com` both
    accept an invented top-level field and answer normally, and MiMo's own documented
    `forced_search` is inert while the tool form works (2026-08-07).
  * 🔴 A knob can move the wrong thing entirely. Spark's tier was moving `thinking.budget_tokens`
    while the depth control on that endpoint is `output_config.effort` - resolved, printed, and
    doing nothing.

So this tool judges by a counter that comes BACK from the vendor: `reasoning_tokens` in
diagnostics.json, normalised across transports from `total_thought_tokens`,
`output_tokens_details.reasoning_tokens` and agy's `thinking_tokens`.

THE METHOD, AND WHY IT IS NOT ONE CALL PER ARM
----------------------------------------------
Round 29 measured `high` at 306 thought tokens against a control of 391 and concluded the knob was
inert. Round 30 re-measured the same knob on a harder question and got a clean monotone ladder.
The difference was noise: one sample per arm cannot tell a knob from the weather, and a question
too easy to need thinking makes every arm look the same.

Therefore:
  * every arm is sampled `--samples` times (default 3), and the arms are INTERLEAVED so that a
    vendor-side change of mood during the run hits both arms equally;
  * the verdict is CONFIRMED only when the two arms' ranges are DISJOINT - max(low) < min(high).
    Overlapping ranges are reported as UNPROVEN with both ranges printed, never rounded up into a
    conclusion;
  * identical values in both arms are reported as INERT, which is the finding worth having;
  * the probe is a question that actually requires work, with a checkable answer, so "cheap"
    cannot be mistaken for "good" - round 30's `low` arm answered correctly in FIVE output tokens.

WHAT IT CANNOT TELL YOU
-----------------------
That more thinking is better. Every arm of round 30's ladder answered correctly. This meters what
depth COSTS. Whether it BUYS anything is a different experiment, on a task hard enough to fail.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import routing                                                        # noqa: E402

MARKER = "ECHO-DONE"

# A question that needs real work and has one checkable answer. Congruences force a search rather
# than recall, and the digit-sum condition stops the first candidate being the answer.
# n ≡ 2 (mod 3), 3 (mod 5), 2 (mod 7) -> n ≡ 23 (mod 105); digit sums 5, 11, 8 -> 233.
PROBE = """Solve this exactly, showing no working in the final line.

Find the smallest positive integer n such that ALL of the following hold:
  n ≡ 2 (mod 3)
  n ≡ 3 (mod 5)
  n ≡ 2 (mod 7)
  the sum of the decimal digits of n is exactly 8

Do not search the web; this is pure arithmetic.
Answer with the number alone on its own line, then the line %s
""" % MARKER
PROBE_ANSWER = "233"

# A system prompt of two sentences, not the 5 441-character review preset. The preset asks for
# citations, provenance tags and a structured report, all of which cost reasoning tokens that have
# nothing to do with the knob under test. It is constant across arms, so it would not bias the
# comparison - but it would bury the signal under a much larger constant.
PROBE_SYSTEM = ("You are answering a self-contained arithmetic question. Be exact. "
                "Do not use tools. End your reply with the line %s" % MARKER)


# 🔴 KEYED ON `kind`, like the dispatcher - never on channel names. Four literal channel names in
# a dispatch chain is the defect this project has now hit at six different layers. A kind that is
# not in this table gets the verdict NO KNOB, printed with its kind, rather than being skipped.
#
# Each entry: (where the knob lives, the ladder of values, how to build the overlay fragment).
# `tier` knobs are written under "tiers", `channel` knobs under "channels" - both of which the
# settings file has been able to carry since 1.8.0, which is what lets this tool drive the REAL
# product instead of calling a private function.
def _tier_frag(field, tier):
    return lambda ch, lvl: {"tiers": {tier: {field: lvl}}}


def _chan_frag(field):
    return lambda ch, lvl: {"channels": {ch: {field: lvl}}}


def _reasoning_frag(ch, lvl):
    return {"channels": {ch: {"reasoning": {"effort": lvl}}}}


def knob_for(cname, slot, tier):
    """(description, ladder, fragment-builder) for this channel, or (None, [], None)."""
    kind = slot.get("kind")
    if kind == "gemini":
        ladder = slot.get("thinking_levels") or []
        return ("tier %s.gemini_thinking_level" % tier, ladder,
                _tier_frag("gemini_thinking_level", tier))
    if kind in ("openrouter", "oai"):
        if not isinstance(slot.get("reasoning"), dict):
            return (None, [], None)
        # 🔴 THE LADDER COMES FROM THE CHANNEL NOW, NOT FROM A LITERAL. Until R43 this returned
        # ["low", "medium", "high"] for every OpenRouter channel, which meant the instrument
        # could never reach the rungs the registry actually uses: ordeepseekv4pro runs `xhigh`,
        # kimik3 runs `max`, and neither value was in the arms. So an A/B «is this knob real»
        # was being run at values the product does not send - the calibration equivalent of
        # testing a different build. Each channel now declares `supported_efforts` (highest
        # first, copied from the vendor catalogue), and the arms are its two ends.
        ladder = list(reversed(slot.get("supported_efforts") or [])) or ["low", "medium", "high"]
        return ("channel %s.reasoning.effort" % cname, ladder, _reasoning_frag)
    if kind == "http":
        # 🔴 `http_effort`, NOT `http_thinking_budget`. Meta's own documentation says
        # `thinking.budget_tokens` is "accepted for compatibility but not translated into an effort
        # value" - so the tier's budget is inert BY THE VENDOR'S OWN WORD, and `output_config.effort`
        # is the knob. That makes the budget this tool's best NEGATIVE CONTROL: point it there with
        # `--knob http_thinking_budget` and a working instrument must answer INERT. An instrument
        # that can only ever return CONFIRMED has not been calibrated, it has been trusted.
        # `max` is not legal here (probed: 400 on both Spark models), so the ladder stops at xhigh.
        return ("tier %s.http_effort" % tier, ["low", "xhigh"], _tier_frag("http_effort", tier))
    if kind == "agy":
        return ("tier %s.agy_effort" % tier, ["low", "high"], _tier_frag("agy_effort", tier))
    return (None, [], None)          # codex: timeout only. xai: this model refuses the field.


def _default_tier():
    """The registry's own default tier. See the note on --tier for why this is not a literal."""
    try:
        import routing
        reg = routing.load_registry()
        d = reg.get("default_tier")
        if d:
            return d
        return next(k for k in (reg.get("tiers") or {}) if not k.startswith("_"))
    except Exception:                                     # noqa: BLE001
        return "max"


# `or_reasoning_scale` and `fetch_scale` were REMOVED from the tier block in R43 when the second
# tier went away - a multiplier over per-channel values is an obfuscated constant once there is
# only one tier to multiply from. They are kept in this list on purpose: `--knob` is a
# calibration tool, and pointing it at a field the tier no longer carries is a legitimate way to
# ask "is this inert?", which is exactly the answer an honest instrument should give.
TIER_FIELDS = ("gemini_thinking_level", "http_effort", "http_thinking_budget", "http_floor",
               "agy_effort", "agy_timeout", "codex_timeout", "or_reasoning_scale", "fetch_scale")


def knob_override(name, cname, tier):
    """`--knob <field>`: vary a named field instead of this channel's default depth control."""
    if name in TIER_FIELDS:
        return ("tier %s.%s" % (tier, name), _tier_frag(name, tier))
    if name == "reasoning.effort":
        return ("channel %s.reasoning.effort" % cname, _reasoning_frag)
    return ("channel %s.%s" % (cname, name), _chan_frag(name))


# Search and page-fetching are switched OFF for the probe, on the channels that have them. Two
# reasons, and the second is the important one: searching costs money per query (round 29 measured
# 128 searches at $0.32, sixty per cent of that round's whole bill), and a tool round re-sends the
# conversation, so the token counters would be dominated by retrieval noise instead of by the knob.
# Both fields are QUIET, which is the point - this tool runs through a redirected settings file and
# therefore proves that the quiet set is enough to drive the product's own instrumentation.
NO_TOOLS = {"web": None, "fetch_tool": None}


def with_no_tools(frag, cname, kind):
    if kind not in ("openrouter", "oai", "http"):
        return frag
    frag = json.loads(json.dumps(frag))
    frag.setdefault("channels", {}).setdefault(cname, {}).update(NO_TOOLS)
    return frag


def run_arm(cname, tier, fragment, outdir, brief_path, system_path, timeout_s):
    """One real run of the product, through its own CLI. Returns the result dict or None."""
    ov = os.path.join(outdir, "settings.json")
    with open(ov, "w", encoding="utf-8") as f:
        json.dump(fragment, f, ensure_ascii=False)
    env = dict(os.environ, MODEL_ORCH_LOCAL=ov, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, os.path.join(HERE, "orchestrate.py"),
           "--brief", brief_path, "--system", system_path, "--only", cname,
           "--tier", tier, "--marker", MARKER, "--out", outdir, "--no-citecheck"]
    try:
        subprocess.run(cmd, env=env, timeout=timeout_s, capture_output=True)
    except subprocess.TimeoutExpired:
        return {"error": "timed out after %ds" % timeout_s}
    diag = os.path.join(outdir, "diagnostics.json")
    if not os.path.isfile(diag):
        return {"error": "no diagnostics.json - the run did not get far enough to report"}
    with open(diag, encoding="utf-8") as f:
        res = (json.load(f).get("channels") or {}).get(cname) or {"error": "channel not in run"}
    # Was it RIGHT? A cheap arm that answers correctly is the finding; a cheap arm that answers
    # wrongly means the ladder is buying something and this tool is measuring only its price.
    answer = os.path.join(outdir, cname.upper() + ".md")
    if os.path.isfile(answer):
        res["correct"] = PROBE_ANSWER in open(answer, encoding="utf-8", errors="replace").read()
    return res


def spread(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return {"n": len(vals), "min": min(vals), "max": max(vals),
            "mean": round(sum(vals) / float(len(vals)), 1), "all": vals}


def verdict(lo, hi, samples, lo_out=None, hi_out=None):
    """Deliberately conservative: three failures to prove are three different sentences."""
    if lo is None or hi is None:
        # 🔴 NO REASONING METER IS NOT NO EVIDENCE. Spark returns `redacted_thinking` blocks - the
        # trace is encrypted by the vendor, so no token count and no character count can exist on
        # that transport, ever. But depth still has to come out somewhere, and round 31 watched it
        # move between columns on Gemini: `minimal` spent 0 thought tokens and 439 OUTPUT tokens,
        # `high` spent 1 437 thought tokens and 8. So the OUTPUT counter is a real, weaker
        # instrument, and it is used HERE ONLY - labelled, never silently substituted, because it
        # measures verbosity and depth together and cannot tell them apart.
        if lo_out and hi_out and min(lo_out["n"], hi_out["n"]) >= 2 and \
                (lo_out["max"] < hi_out["min"] or hi_out["max"] < lo_out["min"]):
            return ("CONFIRMED (output tokens)",
                    "no reasoning counter exists on this transport, but the OUTPUT ranges are "
                    "disjoint: %d..%d vs %d..%d. Weaker evidence - output size mixes depth with "
                    "verbosity - and still a measured difference rather than an assumption"
                    % (lo_out["min"], lo_out["max"], hi_out["min"], hi_out["max"]))
        return ("NO METER", "this vendor returns no reasoning count of any kind on this "
                            "transport, and the output counts do not separate either, so the knob "
                            "cannot be verified from here - only by wall-clock or price")
    # 🔴 `min(n)`, NOT `--samples`. Written as `samples < 2` this read the REQUESTED count and
    # not the delivered one, and it announced CONFIRMED on an arm holding a single measurement -
    # in this tool's own first run, because a mid-run fix meant the earlier samples had no meter
    # to report. A guard that consults the configuration instead of the data is the same defect
    # this project has now named at several layers: an expectation that inherits its world.
    n = min(lo["n"], hi["n"])
    if n < 2:
        return ("UNPROVEN", "only %d usable sample(s) in the smaller arm (asked for %d). One "
                            "sample cannot tell a knob from the weather; round 29 called a "
                            "working knob inert on n=1" % (n, samples))
    if lo["all"] == hi["all"]:
        return ("INERT", "identical counts in both arms - the value was accepted and changed "
                         "nothing measurable")
    if lo["max"] < hi["min"]:
        return ("CONFIRMED", "ranges are disjoint: %d..%d vs %d..%d"
                % (lo["min"], lo["max"], hi["min"], hi["max"]))
    if hi["max"] < lo["min"]:
        return ("INVERTED", "the LOWER setting produced MORE reasoning: %d..%d vs %d..%d. Either "
                            "the ladder is the wrong way round or the field is not the knob"
                % (lo["min"], lo["max"], hi["min"], hi["max"]))
    return ("UNPROVEN", "ranges overlap (%d..%d vs %d..%d) - not separable at n=%d"
            % (lo["min"], lo["max"], hi["min"], hi["max"], samples))


def main():
    ap = argparse.ArgumentParser(
        description="Prove a channel's depth knob from the reasoning-token counter it returns.")
    ap.add_argument("--only", action="extend", nargs="*", default=None,
                    help="channels to test; any alias in channels.json works")
    ap.add_argument("--all", action="store_true", help="every enabled channel that has a knob")
    # 🔴 NOT A LITERAL. R43 collapsed strategic|deep into a single `max`, and a hard-coded
    # default here would have made every tier-scoped arm write an overlay fragment naming a tier
    # that no longer exists - which the overlay validator refuses, so this whole tool would have
    # died on 7 of 11 channels the moment the registry changed. The same "two homes for one list"
    # shape the tier list itself was fixed for on 2026-08-08.
    ap.add_argument("--tier", default=_default_tier(),
                    help="the tier the arms are written into (default: the registry's)")
    ap.add_argument("--levels", help="two values, comma separated (default: the ends of the "
                                     "channel's own ladder)")
    ap.add_argument("--knob", help="vary this field instead of the channel's default depth "
                                   "control. Needs --levels. The calibration case is "
                                   "`--knob http_thinking_budget --levels 4000,100000` on a Spark "
                                   "channel: the vendor documents that field as inert, so a "
                                   "working instrument must answer INERT")
    ap.add_argument("--samples", type=int, default=3,
                    help="runs per arm. 1 is refused a CONFIRMED verdict on purpose")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "echocheck"))
    ap.add_argument("--timeout", type=int, default=900, help="seconds per single run")
    ap.add_argument("--allow-expensive", action="store_true",
                    help="also test channels the registry prices `expensive`")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would run, and the number of paid calls, then stop")
    ap.add_argument("--json", metavar="PATH", help="also write the raw measurements here")
    a = ap.parse_args()

    reg = routing.load_registry()
    plan = routing.resolve(reg, tier=a.tier)
    if a.only:
        want = []
        for name in a.only:
            want.extend(routing.canon_channel(reg, name))
        want = sorted(set(want))
    elif a.all:
        want = sorted(c for c, p in plan.items() if p["enabled"])
    else:
        ap.error("pass --only <channel> or --all")

    jobs = []
    for cname in want:
        slot = plan.get(cname) or {}
        desc, ladder, frag = knob_for(cname, slot, a.tier)
        if a.knob:
            if not a.levels:
                ap.error("--knob needs --levels: nothing declares a ladder for an arbitrary field")
            desc, frag = knob_override(a.knob, cname, a.tier)
            ladder = []
        cost = (reg["channels"].get(cname) or {}).get("cost")
        if not desc:
            jobs.append((cname, None, None, None, None,
                         "NO KNOB - kind %r exposes no depth field this tool can vary" % slot.get("kind")))
            continue
        levels = [x.strip() for x in a.levels.split(",")] if a.levels else \
                 [ladder[0], ladder[-1]] if len(ladder) >= 2 else []
        if len(levels) != 2:
            jobs.append((cname, desc, None, None, None,
                         "NO LADDER - this channel declares no two values to compare"))
            continue
        levels = [int(x) if str(x).lstrip("-").isdigit() else x for x in levels]
        if cost == "expensive" and not a.allow_expensive:
            jobs.append((cname, desc, levels, None, None,
                         "SKIPPED - priced `expensive`; pass --allow-expensive"))
            continue
        jobs.append((cname, desc, levels, frag, slot, None))

    runnable = [j for j in jobs if j[5] is None]
    calls = len(runnable) * 2 * a.samples
    print("=" * 78)
    print("ECHO CHECK - a depth knob is judged by the counter that comes back")
    print("=" * 78)
    print("  tier    : %s" % a.tier)
    print("  samples : %d per arm  (%d paid calls in total)" % (a.samples, calls))
    print("  probe   : %d chars, answer is checked (%s)" % (len(PROBE), PROBE_ANSWER))
    for cname, desc, levels, frag, _slot, why in jobs:
        print("  %-16s %s" % (cname, why or "%s : %r vs %r" % (desc, levels[0], levels[1])))
    if a.dry_run:
        print("\n--dry-run: nothing was called, nothing was spent")
        return 0
    if not runnable:
        print("\nnothing to measure")
        return 2

    os.makedirs(a.out, exist_ok=True)
    brief_path = os.path.join(a.out, "probe.md")
    system_path = os.path.join(a.out, "system.md")
    open(brief_path, "w", encoding="utf-8").write(PROBE)
    open(system_path, "w", encoding="utf-8").write(PROBE_SYSTEM)

    # Interleaved and shuffled: a vendor that gets slower halfway through the run must not be able
    # to masquerade as a knob. Seeded from the channel names so a re-run is reproducible without
    # Math.random-style irreproducibility, and without pretending the ORDER is the measurement.
    rng = random.Random("|".join(c for c, *_ in runnable))
    schedule = [(c, lv, i) for c, _d, levels, _f, _s, _w in runnable
                for lv in levels for i in range(a.samples)]
    rng.shuffle(schedule)

    raw = {c: {} for c, *_ in runnable}
    outs = {c: {} for c, *_ in runnable}
    frags = {c: f for c, _d, _l, f, _s, _w in runnable}
    kinds = {c: (s or {}).get("kind") for c, _d, _l, _f, s, _w in runnable}
    units = {c: "reasoning_tokens" for c, *_ in runnable}
    answers = {c: [] for c, *_ in runnable}
    for n, (cname, lvl, i) in enumerate(schedule, 1):
        outdir = os.path.join(a.out, "%s-%s-%d" % (cname, str(lvl).replace("/", "_"), i))
        os.makedirs(outdir, exist_ok=True)
        print("\n[%d/%d] %s @ %r ..." % (n, len(schedule), cname, lvl), flush=True)
        res = run_arm(cname, a.tier,
                      with_no_tools(frags[cname](cname, lvl), cname, kinds[cname]),
                      outdir, brief_path, system_path, a.timeout)
        # 🔴 THE METER IS NAMED, NOT ASSUMED. Not every transport reports a reasoning-token count:
        # OpenRouter's channels return the reasoning TEXT, which the harness measures in
        # characters. That is a real meter in a different unit - and it is one WE count rather than
        # one the vendor asserts, which if anything is the stronger evidence. Falling back silently
        # would compare tokens with characters; naming the unit is the whole difference.
        rt = res.get("reasoning_tokens")
        unit = "reasoning_tokens"
        if rt is None and res.get("reasoning_chars") is not None:
            rt, unit = res["reasoning_chars"], "reasoning_chars (we counted the trace ourselves)"
        units[cname] = unit
        ok = res.get("ok")
        raw[cname].setdefault(str(lvl), []).append(rt)
        outs[cname].setdefault(str(lvl), []).append(res.get("out_tokens"))
        answers[cname].append((lvl, ok, res.get("correct"), res.get("error")))
        print("      reasoning_tokens=%s out=%s ok=%s correct=%s %s"
              % (rt, res.get("out_tokens"), ok, res.get("correct"), res.get("error") or ""))

    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    out = {}
    for cname, desc, levels, _f, _s, _w in runnable:
        lo = spread(raw[cname].get(str(levels[0]), []))
        hi = spread(raw[cname].get(str(levels[1]), []))
        lo_o = spread(outs[cname].get(str(levels[0]), []))
        hi_o = spread(outs[cname].get(str(levels[1]), []))
        v, why = verdict(lo, hi, a.samples, lo_o, hi_o)
        right = [c for _l, _ok, c, _e in answers[cname]]
        out[cname] = {"knob": desc, "levels": levels, "low": lo, "high": hi,
                      "low_out": lo_o, "high_out": hi_o, "verdict": v, "why": why,
                      "correct": right}
        out[cname]["meter"] = units[cname]
        print("\n  %s   %s" % (cname, v))
        print("      knob : %s" % desc)
        print("      meter: %s" % units[cname])
        print("      %-9r %s" % (levels[0], lo or "no meter"))
        print("      %-9r %s" % (levels[1], hi or "no meter"))
        print("      %s" % why)
        # 🔴 REPORT BOTH METERS. A single counter can be MOVED rather than reduced: the smoke run
        # of this very tool showed `minimal` at 0 thought tokens and 380 OUTPUT tokens against
        # `high` at 1367 thought tokens and 8 output tokens. Reading the reasoning column alone,
        # `minimal` looks like a channel that stopped thinking. It did not; it thought out loud,
        # in the column that is billed differently.
        print("      output tokens: %r %s | %r %s"
              % (levels[0], (lo_o or {}).get("all"), levels[1], (hi_o or {}).get("all")))
        wrong = [l for l, _ok, c, _e in answers[cname] if c is False]
        if wrong:
            print("      🔴 %d of %d answers were WRONG (arms: %s) - on this probe the ladder is "
                  "buying correctness, not only tokens" % (len(wrong), len(answers[cname]), wrong))
        elif all(c for c in right):
            print("      every arm answered correctly - this meters what depth COSTS, not what "
                  "it buys")
        bad = [l for l, ok, _c, _e in answers[cname] if not ok]
        if bad:
            print("      ⚠ %d of %d runs did not complete: %s"
                  % (len(bad), len(answers[cname]), bad))
    for cname, desc, levels, _f, _s, why in jobs:
        if why:
            print("\n  %s   %s" % (cname, why))
            out[cname] = {"verdict": why.split(" ", 1)[0], "why": why}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"tier": a.tier, "samples": a.samples, "raw": raw, "result": out},
                      f, indent=2, ensure_ascii=False)
        print("\n  raw measurements: %s" % a.json)
    print("\n  runs kept in %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
