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
    _strip_comment_keys(reg)
    _check_channel_names(reg)
    _check_alias_collisions(reg)
    _check_channel_models(reg)
    return reg


def _strip_comment_keys(reg):
    """
    Every object in channels.json carries `_`-prefixed prose keys - that is the house idiom, and
    it is why the file can explain itself. Inside `channels` the same habit is a trap: the key
    would be ITERATED AS A CHANNEL, with a string where a dict belongs. Written exactly that way
    while adding the two agy channels, and caught only because the string happened to sit between
    two entries. So the convention is honoured where it is declared (drop `_` keys) and anything
    else that is not an object is refused BY NAME rather than crashing three functions later.
    """
    chans = reg.get("channels", {})
    for k in [k for k in chans if k.startswith("_")]:
        del chans[k]
    bad = sorted(k for k, v in chans.items() if not isinstance(v, dict))
    if bad:
        raise RouteError(
            "channels.json: %s under `channels` is not an object. Only channel definitions "
            "belong there; prose goes at the top level, or in a key starting with '_'."
            % ", ".join(repr(b) for b in bad))
    for k in [k for k in reg.get("groups", {}) if k.startswith("_")]:
        del reg["groups"][k]
    return reg


def _check_channel_models(reg):
    """
    A channel's default `model` must be one of its own `models`. Nothing checked this, and the
    consequence is not cosmetic: `_decorate` looks the model up to find its label and its data
    policy, so a default that is not in the table silently produces a plan with no label and no
    policy - the two fields that exist precisely so a human can see what is about to be spent.
    """
    for cname, ch in reg["channels"].items():
        known = ch.get("models") or {}
        if ch.get("model") and known and ch["model"] not in known:
            raise RouteError(
                "channel %r defaults to model %r, which is not in its own `models` table (%s). "
                "One channel = one model: list it, or point the default at one that is listed."
                % (cname, ch["model"], ", ".join(known) or "empty"))


# A channel name is now a FILESYSTEM PATH COMPONENT and a reported identity, not just a dict key:
# dispatch derives `<NAME>.md` and `<name>-ws` from it. Three independent reviewers flagged the
# same thing on 2026-08-06 - the key became a path input and nothing validated it. Checked at
# LOAD time, where it costs nothing, rather than at write time, where the run has already been
# paid for.
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
# Windows refuses these as filenames whatever the extension, so `CON.md` cannot be written.
_RESERVED = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6",
             "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6",
             "lpt7", "lpt8", "lpt9"}


def _check_channel_names(reg):
    seen = {}
    for cname in reg.get("channels", {}):
        if not _SAFE_NAME.match(cname):
            raise RouteError(
                "channel name %r is not usable: the dispatcher derives an output file "
                "(%s.md) and a workspace directory (%s-ws) from it, so it must match "
                "[a-z][a-z0-9_-]*. A name containing a separator, '..', a space or an "
                "upper-case letter can collide with another channel or escape --out."
                % (cname, cname.upper(), cname))
        if cname in _RESERVED:
            raise RouteError("channel name %r is a reserved Windows device name; %s.md "
                             "cannot be created." % (cname, cname.upper()))
        # Belt and braces: the regex already forbids upper case, so a case collision is
        # currently impossible. Kept because the regex is the kind of thing that gets relaxed.
        low = cname.lower()
        if low in seen:
            raise RouteError("channels %r and %r differ only by case; on Windows both would "
                             "write the same %s.md" % (seen[low], cname, cname.upper()))
        seen[low] = cname
    return seen


def _check_alias_collisions(reg):
    """
    Two entities sharing an alias makes every override that mentions it a coin flip. Catch it at
    load time, where it is a config typo, instead of at spend time, where it is the wrong model.

    Groups share this namespace with channels and models on purpose: `agy` naming a group while
    `agy31pro` names a channel is fine, but `agy` naming both a group and a model would make
    "не используй agy" mean two different things depending on which table was consulted first.
    """
    seen = {}

    def claim(alias, owner):
        # The test is "a DIFFERENT owner already has it", not "it was seen". A group listing its
        # own name among its aliases, or a model repeating a spelling, is harmless duplication;
        # only two distinct entities answering to one word makes an override a coin flip.
        key = alias.lower().replace("ё", "е")
        if seen.get(key, owner) != owner:
            raise RouteError("alias %r is claimed by both %s and %s" % (alias, seen[key], owner))
        seen[key] = owner

    for gname, g in (reg.get("groups") or {}).items():
        members = g.get("channels") or []
        missing = [m for m in members if m not in reg["channels"]]
        if missing:
            raise RouteError("group %r lists channels that do not exist: %s"
                             % (gname, ", ".join(missing)))
        if not members:
            raise RouteError("group %r is empty; a group that expands to nothing would silently "
                             "disable whatever mentions it" % gname)
        if gname in reg["channels"]:
            raise RouteError("group %r has the same name as a channel; one word cannot mean "
                             "both one channel and several" % gname)
        for al in [gname] + list(g.get("aliases", [])):
            claim(al, "group:" + gname)
    for cname, ch in reg["channels"].items():
        for al in [cname] + list(ch.get("aliases", [])):
            claim(al, "channel:" + cname)
        for mname, m in (ch.get("models") or {}).items():
            for al in m.get("aliases", []):
                claim(al, "model:%s:%s" % (cname, mname))
    return seen


def group_members(reg, name):
    """The channels a group word expands to, or None if `name` is not a group."""
    g = (reg.get("groups") or {}).get(name)
    return list(g["channels"]) if g else None


def alias_index(reg):
    """alias -> ("channel"|"model"|"group", ...). Longest aliases first."""
    idx = []
    for gname, g in (reg.get("groups") or {}).items():
        for al in [gname] + list(g.get("aliases", [])):
            idx.append((al.lower(), ("group", gname, None)))
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


def _expand_groups(stream, reg):
    """
    Replace a group token with one token per member channel, in registry order, at the same
    position. Done here rather than inside apply_route so that every mode - neg, only, subst -
    keeps working unchanged: "не используй gemini" becomes two negations, "только agy" adds two
    names to the only-list. The alternative, teaching each branch about groups, is four places
    to forget one.
    """
    out = []
    for tok in stream:
        if tok[1] == "entity" and tok[2][0] == "group":
            for c in group_members(reg, tok[2][1]) or []:
                out.append((tok[0], "entity", ("channel", c, None)))
        else:
            out.append(tok)
    return out


def apply_route(plan, reg, text):
    """Interpret free text into plan mutations. Raises RouteError rather than guessing."""
    idx = alias_index(reg)
    stream = _expand_groups(_scan(text, idx), reg)
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

    Returns a LIST, because one word can legitimately name several channels: `--only agy` has to
    keep meaning "the Gemini channels" after that one channel became two, and `--skip spark` has
    to reach both Spark voices. Returning a bare string here is what would have re-created the
    `--only http` failure this function was written to fix - a documented flag dying with
    "unknown channel" the moment the thing it named stopped being exactly one entry.
    """
    if name in plan_names(reg):
        return [name]
    key = str(name).lower().replace("ё", "е")
    for gname, g in (reg.get("groups") or {}).items():
        if key == gname.lower() or key in [a.lower() for a in g.get("aliases", [])]:
            return list(g["channels"])
    for cname, ch in reg["channels"].items():
        if key in [a.lower() for a in ch.get("aliases", [])]:
            return [cname]
    raise RouteError("unknown channel %r. Channels: %s. Groups: %s. Accepted aliases: %s"
                     % (name, ", ".join(plan_names(reg)),
                        ", ".join(sorted(reg.get("groups") or {})),
                        ", ".join(sorted(
                            [a for ch in reg["channels"].values() for a in ch.get("aliases", [])]
                            + [a for g in (reg.get("groups") or {}).values()
                               for a in g.get("aliases", [])]))))


def plan_names(reg):
    return list(reg["channels"].keys())


def apply_flags(plan, reg, only=None, skip=None, sets=None):
    only = [c for name in (only or []) for c in canon_channel(reg, name)]
    skip = [c for name in (skip or []) for c in canon_channel(reg, name)]
    for spec in sets or []:
        if "=" not in spec:
            raise RouteError("--set needs channel=model, got %r" % spec)
        c, m = spec.split("=", 1)
        targets = canon_channel(reg, c)
        # A group names several channels, and one channel runs one model: `--set agy=<model>`
        # cannot mean anything except "give both of them the same model", which is precisely
        # what having two channels exists to avoid. Refuse rather than pick.
        if len(targets) > 1:
            raise RouteError("--set %s=%s names a GROUP (%s), and each channel runs exactly one "
                             "model. Name the channel: %s."
                             % (c, m, ", ".join(targets),
                                " or ".join("--set %s=%s" % (t, m) for t in targets)))
        c = targets[0]
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
        # Keyed on KIND, not on the channel name. Named lookups meant a second Gemini channel
        # (3.1 Pro and 3.6 Flash in one round, which is the only way to hear both - the CLI runs
        # one model per call) would silently get no effort and no timeout, i.e. the 3000-second
        # default that took 50 minutes to discover the last time it applied to codex.
        for cname, p in plan.items():
            if p.get("kind") == "agy":
                want = t.get("agy_effort", p.get("effort"))
                p["effort"] = _clamp_effort(reg, cname, p["model"], want, p)
                p["timeout"] = t.get("agy_timeout", "25m")
        # 🔴 CODEX HAD NO TIER TIMEOUT AT ALL, AND NOTHING SAID SO. Only agy's was wired, so
        # `call_codex` fell through to the 3000-second default hard-coded in `_run` - and when a
        # run was killed the harness advised "raise the timeout for that channel", naming a lever
        # that did not exist. `limits` in channels.json was an empty object, and the
        # `timeout_seconds: 2400` that does exist belongs to a DIFFERENT channels.json in the
        # sibling project, which nothing here reads. Two files, one name, and the printed advice
        # pointed at the copy that was not in play.
            elif p.get("kind") == "codex":
                p["timeout"] = t.get("codex_timeout", "50m")
        # 🔴 THE TIER DID NOTHING TO THE SPARK CHANNELS, and it looked like it did. The tier
        # varied `thinking.budget_tokens`, but Meta documents that field as "accepted for
        # compatibility but not translated into an effort value" - depth on this endpoint is set
        # by output_config.effort alone, and the code pinned that at xhigh for every tier. So
        # `--tier quick` and `--tier deep` bought the identical review at the identical depth.
        # Probed 2026-08-06 on both Spark models: low/medium/high/xhigh all return 200, `max`
        # returns 400 on both, and so does an invented value - the negative control that proves
        # the endpoint really validates it. xhigh is the true ceiling despite the vendor's own
        # OpenAPI enum listing `max`.
            elif p.get("kind") == "http":
                p["effort"] = t.get("http_effort", "xhigh")
    return _decorate(plan, reg)


def _decorate(plan, reg):
    """
    Attach everything the dispatcher and the printout need but must not look up by name.

    `model_label` exists because `gemini-3.6-flash` is a slug, not a name a human tracks across
    a five-channel plan; Igor asked for the readable form. It is resolved AFTER routing, because
    the model can still change during routing and a label captured earlier would describe the
    model that was NOT run - a caption disagreeing with its picture, which is the failure mode
    this project keeps measuring in prose.
    """
    for cname, p in plan.items():
        ch = reg["channels"].get(cname, {})
        m = (ch.get("models") or {}).get(p.get("model")) or {}
        p["model_label"] = m.get("label") or p.get("model")
        p["data_policy"] = m.get("data_policy")
        # 🔴 THE NAME IS NOT THE GUARANTEE. Igor asked for `Spark12Cont` so that running a
        # non-Contributor model would be visible - but a label is a string that asserts a mutable
        # value, which is the exact rot this project keeps measuring elsewhere. The label would
        # go on saying "Cont" after `--set spark12cont=muse-spark-1.2`. So the label is backed by
        # two mechanisms that cannot be ignored: each channel lists only the models it may run
        # (checked at load), and any departure from the registry default is flagged HERE and
        # printed in the plan before a penny is spent.
        p["model_default"] = ch.get("model")
        if ch.get("model") and p.get("model") != ch.get("model"):
            p["model_overridden"] = True
            p["why"].append("⚠ MODEL OVERRIDDEN: this channel is named for %s but is set to run "
                            "%s" % (ch["model"], p.get("model")))
        web = ch.get("web") or {}
        p["web"] = {k: v for k, v in web.items() if not k.startswith("_")} if web else None
        # Wire-level knobs that belong to the channel rather than to the tier. Passed through so
        # the dispatcher never has to re-open the registry, and so --dry-run shows them.
        for extra in ("reasoning", "max_tokens", "toolsets", "role", "fetch_tool"):
            if ch.get(extra) is not None:
                p[extra] = ch[extra]
    return plan


# Ordered weakest to strongest, so clamping can pick the nearest available rung.
EFFORT_ORDER = ["low", "medium", "high"]


def _clamp_effort(reg, cname, model, want, slot):
    """
    Not every model exposes every effort. `gemini-3.1-pro` has only low and high; asking it for
    medium is not ignored, it is a hard launch failure (exit 1, empty result, 3 seconds). The
    'standard' tier maps to medium, so an unclamped tier would silently kill every standard-tier
    agy run. Clamp to the nearest available rung and say so in the plan.

    Reads the efforts of the CHANNEL BEING CLAMPED. It used to read `reg["channels"]["agy"]`
    unconditionally, so a second Gemini channel would have been clamped against the first one's
    model table - the right answer for the wrong channel, which is invisible whenever the two
    happen to agree and wrong exactly when they do not.
    """
    avail = ((reg["channels"][cname].get("models") or {}).get(model) or {}).get("efforts")
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
        # The readable name first, the slug in brackets. A plan is read to catch the wrong model
        # before money is spent, and `gemini-3.1-pro` vs `gemini-3.6-flash` differ by one word in
        # the middle of a slug - which is precisely where the eye does not go.
        shown = p.get("model_label") or p.get("model")
        if shown != p.get("model"):
            shown = "%s [%s]" % (shown, p.get("model"))
        lines.append("  [%s] %-12s %-32s model=%s%s" % (
            mark, c, p["label"], shown,
            ("  effort=%s" % p["effort"]) if p.get("effort") else ""))
        for w in p["why"]:
            lines.append("           - %s" % w)
        if p["enabled"] and cost == "expensive":
            lines.append("           - cost: EXPENSIVE channel")
        # Printed for every enabled channel whose model declares one, not only for the alarming
        # ones: a policy line that appears only when something is wrong trains the eye to skip
        # the whole class. Spark's Contributor tier buys its ~12x discount with permission to
        # train on the payload, and that is a fact about the BRIEF, not about the budget.
        if p["enabled"] and p.get("data_policy"):
            lines.append("           - data: %s" % p["data_policy"])
        if p["enabled"] and (p.get("web") or {}).get("enabled"):
            w = p["web"]
            lines.append("           - web search ON via %s, max %s results - billed PER SEARCH "
                         "by the provider" % (w.get("engine", "default"),
                                              w.get("max_results", "default")))
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
