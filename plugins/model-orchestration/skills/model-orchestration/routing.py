#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
routing.py - decide WHICH channels run and WHICH model each one uses, from a config file plus
overrides, so that "don't use 5.6 Sol, use 5.5 instead" never requires touching code.

Three override sources:
    1. channels.json                      the registry - the only place a model name lives
    2. --route "<free text>"              what Igor actually types, in Russian or English
    3. --skip / --only / --set            explicit flags, applied on top

They do NOT have a precedence order, deliberately. Where the flags and the route AGREE or address
different channels they compose; where they CONTRADICT - a flag re-enabling a channel the route
excluded - it is a RouteError, not a winner. An earlier version applied flags last and therefore
let them win silently: `--only codex --route "не используй codex"` printed
`- route: excluded by name` on the line directly above `[RUN ] codex`, and spent the expensive
channel anyway. A safety printout that contradicts itself is worse than none.

The resolved plan is ALWAYS printed before anything is spent. A router that silently guesses
which expensive model to run is worse than no router: the whole point of the feature is that
weekly limits ran out on one model and the run must go to another one, on purpose.

    python routing.py --route "не использовать 5.6 Sol, а использовать вместо нее 5.5"
    python routing.py --route "не используй Spark" --skip agy
    python routing.py --set codex=gpt-5.4 --only codex spark

Standard library only.
"""

import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(HERE, "channels.json")

# Ordered longest-first at match time. Russian and English both, because the override is
# whatever Igor typed in chat, pasted verbatim.
NEG = ["не использовать", "не используй", "не использовать для", "не задействуй", "не надо",
       "не нужно", "исключи", "исключить", "убери", "убрать", "выключи", "выключить",
       "отключи", "отключить", "без ", "кроме ", "минус ",
       "do not use", "don't use", "dont use", "skip", "without", "except", "exclude", "no "]
SUBST = ["вместо", "взамен", "заменить на", "замени на", "instead of", "replace with", "->", "→"]
ONLY = ["только", "лишь", "исключительно", "only", "just use", "nothing but"]


class RouteError(Exception):
    """Raised instead of guessing. An ambiguous route must stop the run, not pick a model."""


def load_registry(path=DEFAULT_REGISTRY):
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
    _check_alias_collisions(reg)
    return reg


def _check_alias_collisions(reg):
    """
    Two entities sharing an alias makes every override that mentions it a coin flip. Catch it at
    load time, where it is a config typo, instead of at spend time, where it is the wrong model.
    """
    seen = {}
    for cname, ch in reg["channels"].items():
        for al in ch.get("aliases", []):
            key = al.lower()
            if key in seen:
                raise RouteError("alias %r is claimed by both %s and %s" % (al, seen[key], cname))
            seen[key] = "channel:" + cname
        for mname, m in (ch.get("models") or {}).items():
            for al in m.get("aliases", []):
                key = al.lower()
                if key in seen:
                    raise RouteError("alias %r is claimed by both %s and model:%s"
                                     % (al, seen[key], mname))
                seen[key] = "model:%s:%s" % (cname, mname)
    return seen


def alias_index(reg):
    """alias -> ("channel", name) | ("model", channel, model). Longest aliases first."""
    idx = []
    for cname, ch in reg["channels"].items():
        for al in ch.get("aliases", []):
            idx.append((al.lower(), ("channel", cname, None)))
        for mname, m in (ch.get("models") or {}).items():
            for al in m.get("aliases", []):
                idx.append((al.lower(), ("model", cname, mname)))
    idx.sort(key=lambda p: -len(p[0]))
    return idx


def initial_plan(reg):
    return {c: {"enabled": ch.get("enabled", True), "model": ch.get("model"),
                "kind": ch["kind"], "label": ch.get("label", c),
                "effort": ch.get("effort"), "agent": ch.get("agent"), "why": []}
            for c, ch in reg["channels"].items()}


def _scan(text, idx):
    """Produce an ordered stream of ('neg'|'subst'|'only'|entity) tokens with their positions."""
    t = " " + text.lower().replace("ё", "е") + " "
    marks = []
    for kind, words in (("neg", NEG), ("subst", SUBST), ("only", ONLY)):
        for w in words:
            for m in re.finditer(re.escape(w), t):
                marks.append((m.start(), kind, w))
    ents = []
    taken = []
    for al, ent in idx:                       # longest-first, so "5.6 sol" wins over "5.6"
        for m in re.finditer(r"(?<![\w\-])" + re.escape(al) + r"(?![\w\-])", t):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue                      # already consumed by a longer alias
            taken.append((m.start(), m.end()))
            ents.append((m.start(), "entity", ent))
    stream = sorted(marks + ents, key=lambda x: x[0])
    # A marker immediately followed by another marker ("не использовать ... а использовать
    # вместо") is one intent, not two; keep only the last marker before each entity run.
    cleaned = []
    for tok in stream:
        if cleaned and tok[1] != "entity" and cleaned[-1][1] != "entity":
            cleaned[-1] = tok
            continue
        cleaned.append(tok)
    return cleaned


def apply_route(plan, reg, text):
    """Interpret free text into plan mutations. Raises RouteError rather than guessing."""
    idx = alias_index(reg)
    stream = _scan(text, idx)
    if not any(t[1] == "entity" for t in stream):
        raise RouteError("no channel or model in this route matched the registry: %r. "
                         "Known aliases: %s" % (text, ", ".join(sorted(a for a, _ in idx))))

    mode = None
    pending_neg = None      # the entity a following "вместо" replaces
    subst_target = None
    only_list = []

    for _, kind, val in stream:
        if kind in ("neg", "subst", "only"):
            mode = kind
            if kind == "subst":
                subst_target = pending_neg      # "не 5.6 sol, вместо нее 5.5" -> anaphora
            continue
        etype = val[0]
        cname = val[1]
        mname = val[2]

        if mode == "only":
            only_list.append(cname)
            if etype == "model":
                plan[cname]["model"] = mname
                plan[cname]["why"].append("only: model pinned to %s" % mname)

        elif mode == "neg":
            if etype == "channel":
                plan[cname]["enabled"] = False
                plan[cname]["_route_off"] = True   # a later flag may not quietly undo this
                plan[cname]["why"].append("route: excluded by name")
                pending_neg = val
            else:
                pending_neg = val
                if plan[cname]["model"] == mname:
                    plan[cname]["_needs_replacement"] = mname
                    plan[cname]["why"].append("route: %s refused, replacement pending" % mname)
                else:
                    plan[cname]["why"].append("route: %s refused (was not selected anyway)"
                                              % mname)

        elif mode == "subst":
            if subst_target is None:
                # "вместо 5.6 sol возьми 5.5": first entity after the marker is the target,
                # the next one is the replacement.
                subst_target = val
                continue
            t_type, t_chan, t_model = subst_target
            if etype == "model" and t_type == "model" and cname == t_chan:
                plan[cname]["model"] = mname
                plan[cname]["enabled"] = True
                plan[cname].pop("_needs_replacement", None)
                plan[cname]["why"].append("route: %s -> %s" % (t_model, mname))
            else:
                plan[t_chan]["enabled"] = False
                plan[t_chan]["_route_off"] = True
                plan[t_chan]["why"].append("route: replaced by %s" % cname)
                plan[cname]["enabled"] = True
                plan[cname].pop("_route_off", None)   # the route itself re-enabled it
                if mname:
                    plan[cname]["model"] = mname
                plan[cname]["why"].append("route: replaces %s" % t_chan)
            subst_target = None
            pending_neg = None

        else:
            raise RouteError(
                "%r mentions %s but no instruction word (не использовать / вместо / только). "
                "Say what to do with it." % (text, cname))

    if only_list:
        for c in plan:
            if c not in only_list and plan[c]["enabled"]:
                plan[c]["enabled"] = False
                plan[c]["_route_off"] = True
                plan[c]["why"].append("route: not in the 'only' list")

    # A refused model with no stated replacement: fall back to the next model in registry order
    # and SAY SO in the plan. Registry order is curated, so this is deterministic - but it is
    # never silent, because picking an expensive model unannounced is the exact failure mode
    # this router exists to prevent.
    for cname, p in plan.items():
        refused = p.pop("_needs_replacement", None)
        if not refused:
            continue
        alts = [m for m in (reg["channels"][cname].get("models") or {}) if m != refused]
        if not alts:
            p["enabled"] = False
            p["_route_off"] = True
            p["why"].append("NOTE: %s refused and the registry lists no alternative - "
                            "channel disabled" % refused)
        else:
            p["model"] = alts[0]
            p["why"].append("NOTE: %s refused, no replacement named - fell back to %s "
                            "(first alternative in channels.json)" % (refused, alts[0]))
    return plan


def canon_channel(reg, name):
    """
    Map whatever the caller typed onto a registry channel name.

    The harness calls the HTTPS channel `http` internally and the registry calls it `spark`;
    SKILL.md documents `--only http` and argparse accepted it, but apply_flags did a literal
    dict lookup, so `--only http` and `--skip http` both died with `unknown channel 'http'` and
    exit 2 - two documented flags that could never work. Going through the same alias table the
    free-text router uses fixes that and makes `--skip gemini` / `--skip кодекс` work too, which
    is what anyone would expect after reading §0.1.
    """
    if name in plan_names(reg):
        return name
    key = str(name).lower().replace("ё", "е")
    for cname, ch in reg["channels"].items():
        if key in [a.lower() for a in ch.get("aliases", [])]:
            return cname
    raise RouteError("unknown channel %r. Channels: %s. Accepted aliases: %s"
                     % (name, ", ".join(plan_names(reg)),
                        ", ".join(sorted(a for ch in reg["channels"].values()
                                         for a in ch.get("aliases", [])))))


def plan_names(reg):
    return list(reg["channels"].keys())


def apply_flags(plan, reg, only=None, skip=None, sets=None):
    only = [canon_channel(reg, c) for c in (only or [])]
    skip = [canon_channel(reg, c) for c in (skip or [])]
    for spec in sets or []:
        if "=" not in spec:
            raise RouteError("--set needs channel=model, got %r" % spec)
        c, m = spec.split("=", 1)
        c = canon_channel(reg, c)
        known = reg["channels"][c].get("models") or {}
        if m not in known:
            raise RouteError("--set %s=%s: model not in registry. Known for %s: %s. "
                             "Add it to channels.json rather than passing it here."
                             % (c, m, c, ", ".join(known) or "(none)"))
        plan[c]["model"] = m
        plan[c]["enabled"] = True
        plan[c]["why"].append("--set %s" % m)
    for c in skip:
        plan[c]["enabled"] = False
        plan[c]["why"].append("--skip")
    if only:
        for c in plan:
            if c not in only:
                plan[c]["enabled"] = False
                plan[c]["why"].append("--only excluded it")
            else:
                plan[c]["enabled"] = True
    return plan


def resolve(reg, route=None, only=None, skip=None, sets=None, tier=None):
    plan = initial_plan(reg)
    if route:
        plan = apply_route(plan, reg, route)
    plan = apply_flags(plan, reg, only=only, skip=skip, sets=sets)

    # A flag may not silently overturn an exclusion the human stated in prose. Measured
    # 2026-07-31: `--only codex --route "не используй codex"` ran Codex - the EXPENSIVE channel -
    # and printed "route: excluded by name" on the line above "[RUN ] codex". Neither source
    # outranks the other, so a genuine contradiction stops the run and names both sides. This is
    # the same rule as an unparseable route: never guess which one was meant.
    clash = [c for c, p in plan.items() if p.pop("_route_off", False) and p["enabled"]]
    if clash:
        raise RouteError(
            "the route and the flags contradict each other on: %s. The route excluded %s; a "
            "--only/--set flag then re-enabled it. Neither wins by default - decide and pass one "
            "of them, not both. (Reasons recorded: %s)"
            % (", ".join(clash), " and ".join(clash),
               " | ".join("%s: %s" % (c, "; ".join(plan[c]["why"])) for c in clash)))

    if tier and tier in (reg.get("tiers") or {}):
        t = reg["tiers"][tier]
        if plan.get("agy"):
            want = t.get("agy_effort", plan["agy"].get("effort"))
            plan["agy"]["effort"] = _clamp_effort(reg, plan["agy"]["model"], want, plan["agy"])
            plan["agy"]["timeout"] = t.get("agy_timeout", "25m")
    return plan


# Ordered weakest to strongest, so clamping can pick the nearest available rung.
EFFORT_ORDER = ["low", "medium", "high"]


def _clamp_effort(reg, model, want, slot):
    """
    Not every model exposes every effort. `gemini-3.1-pro` has only low and high; asking it for
    medium is not ignored, it is a hard launch failure (exit 1, empty result, 3 seconds). The
    'standard' tier maps to medium, so an unclamped tier would silently kill every standard-tier
    agy run. Clamp to the nearest available rung and say so in the plan.
    """
    avail = ((reg["channels"]["agy"].get("models") or {}).get(model) or {}).get("efforts")
    if not avail or want in avail:
        return want
    want_i = EFFORT_ORDER.index(want) if want in EFFORT_ORDER else len(EFFORT_ORDER) - 1
    # Ties break UPWARD. This channel exists to produce a second opinion; an under-thought
    # review is confidently wrong, and that costs a bad decision, which is dearer than the extra
    # quota. The NOTE keeps the upgrade visible rather than silent.
    best = min(avail, key=lambda e: (abs(EFFORT_ORDER.index(e) - want_i),
                                     -EFFORT_ORDER.index(e)) if e in EFFORT_ORDER else (99, 0))
    slot["why"].append("NOTE: %s has no '%s' effort (available: %s) - clamped to '%s'"
                       % (model, want, ", ".join(avail), best))
    return best


def format_plan(plan, reg):
    lines = ["RESOLVED PLAN", "-" * 78]
    for c, p in plan.items():
        mark = "RUN " if p["enabled"] else "skip"
        cost = reg["channels"][c].get("cost", "?")
        lines.append("  [%s] %-6s %-28s model=%s%s" % (
            mark, c, p["label"], p["model"],
            ("  effort=%s" % p["effort"]) if p.get("effort") else ""))
        for w in p["why"]:
            lines.append("           - %s" % w)
        if p["enabled"] and cost == "expensive":
            lines.append("           - cost: EXPENSIVE channel")
    live = [c for c, p in plan.items() if p["enabled"]]
    lines.append("-" * 78)
    lines.append("  running %d channel(s): %s" % (len(live), ", ".join(live) or "NONE"))
    if not live:
        lines.append("  !! every channel is disabled - nothing would run")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--route", help="free-text override, e.g. 'не используй Spark'")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--skip", nargs="*")
    ap.add_argument("--set", dest="sets", nargs="*")
    ap.add_argument("--tier", default="strategic")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    reg = load_registry(a.registry)
    try:
        plan = resolve(reg, route=a.route, only=a.only, skip=a.skip, sets=a.sets, tier=a.tier)
    except RouteError as e:
        print("ROUTE ERROR: %s" % e)
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=1) if a.json else format_plan(plan, reg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
