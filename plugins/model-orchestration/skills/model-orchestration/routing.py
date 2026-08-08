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
import hashlib
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(HERE, "channels.json")
# A byte-for-byte reference copy of the registry as it shipped, written by package.py into built
# trees only. Never loaded, never merged - it exists so that "has this been edited?" can be
# answered with the FIELD NAMES rather than a yes/no, both in the plan and by upgrade.py. It
# replaced `channels.sha256` in 1.8.0: a hash answers the smaller question and cannot answer the
# larger one, and two files answering one question is how the stale copy gets believed.
SHIPPED_REGISTRY_NAME = "channels.shipped.json"

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


# 🔴🔴 USER CONFIGURATION LIVES OUTSIDE THE SHIPPED TREE. THIS IS WHAT MAKES AN UPGRADE SAFE.
#
# INSTALL.md tells people, in so many words, to open `channels.json` and set `"enabled": true` on
# a channel. Every upgrade path this kit ships then destroyed that edit, and none of them said so:
# `install.ps1`/`install.sh` MOVE the old tree aside and copy a fresh one; "just copy the files"
# overwrites it; and the PLUGIN path - the one the docs recommend, the one that updates itself
# with nobody running anything - replaces the whole cached checkout. Verified on the 1.6.0 tree,
# 2026-08-08: the shipped registry was the only home for a setting the user was instructed to make.
#
# The fix is not a smarter merge, it is a smaller shipped file's worth of responsibility. An
# overlay outside the skill folder cannot be reached by ANY upgrade method, including the naive
# ones, so correctness stops depending on which method someone used. `upgrade.py --migrate` moves
# existing in-tree edits here; `doctor.py` reports the file, and `format_plan` prints every field
# it changed on every run - an invisible config file would be a worse trap than the one it fixes.
OVERLAY_ENV = "MODEL_ORCH_LOCAL"
OVERLAY_NAME = "model-orchestration.local.json"

# 🔴🔴 THE OVERLAY'S TRUST IS KEYED ON PROVENANCE, NOT ON WHICH FIELD IS BEING SET.
#
# Round 30 shipped a default-deny allowlist here on kimik3's finding: a file that survives every
# update, is merged BEFORE validation and can name a transport is update-proof redirection of where
# documents go - "and the per-run print of what the overlay changed only helps if a human reads it,
# which the plugin path specifically removes." The finding was real. The remedy was aimed at the
# wrong axis, and round 31 established why by looking rather than reasoning:
#
#   * `~/.claude/model-orchestration.local.json` and `<skill>/channels.json` have IDENTICAL write
#     permissions. Anything that can write the first can write the second. Refusing `model` here
#     never stopped an attacker; it sent them one file to the left.
#   * And that file is the QUIET one. `format_plan` prints every overlay change on every run,
#     while an in-place edit of channels.json was fingerprinted only by `doctor.py` - which nobody
#     runs before a round. So the allowlist pushed the most sensitive class of change into the
#     least visible place, which is the exact opposite of its purpose. Round 31 fixes that half
#     too: the plan now reports registry drift itself (see `registry_drift`).
#   * Meanwhile it fired on correct use. Igor, 2026-08-08: advanced users must be able to change
#     and improve this, and the hand on the keyboard is their Claude Code, not a novice. A gate
#     that fires on the intended workflow is a class this project has already measured twice - it
#     teaches people to switch the whole class off.
#
# What IS asymmetric is where the path came from. The home default can only be chosen by whoever
# owns the home directory. `MODEL_ORCH_LOCAL` can be chosen by a repository you cloned: verified
# 2026-08-08 at code.claude.com/docs/en/settings, `env` is "Environment variables applied to every
# session and to subprocesses Claude Code spawns from it", and `.claude/settings.json` is the file
# "checked into source control and shared with your team". A cloned repo can set that variable; a
# cloned repo cannot become your home directory.
#
# Hence two provenances, one rule each:
#   home       - everything is allowed. The trust is already there by construction, and every
#                change is printed before a penny is spent.
#   redirected - the fields that decide WHERE a document goes or WHAT is added to it are refused,
#                and the error says how to get them: move the file to the home path.
# Deliberately still no env var to bypass this. An escape hatch that can be set once and forgotten
# IS the default - measured in round 28, where a 161 KB case brief changed tier because the way
# round the gate was written down.
# 🔴🔴 AND THEN THREE REVIEWERS FOUND THE HOLE IN *THAT*, INDEPENDENTLY, AND THEY WERE RIGHT.
# kimik3, goog36flash and agy36flash each named the same asymmetry the permission-equivalence
# argument above misses: it is true for a RESIDENT attacker and false for a ONE-SHOT one.
#
#   `channels.json` is SELF-HEALING. The next update replaces it, which is exactly the defect
#   round 30 fixed - and simultaneously a security property nobody had named. The home overlay is
#   update-proof by construction. So opening it up handed the PERMANENT file the powers the
#   EPHEMERAL one had. kimik3, verbatim: "a single compromised assistant session writes
#   `model`/`provider` into the home overlay once, and every future run - including after the
#   skill updates - silently re-points a channel."
#
# That threat is not hypothetical here: this product's own premise is that an AI assistant edits
# the configuration on the user's behalf, so a one-shot write is the MOST likely compromise, not
# the least. The inline red marker does not answer it - the plugin path removes the human who
# would read it, which is the same sentence that killed the 1.7.0 design.
#
# THE FIX KEEPS BOTH REQUIREMENTS. A sharp change is still allowed at the home path - Igor's
# advanced user, and their Claude Code, can repoint a model or add a channel - but it is NOT
# applied to a paid run until it has been ACKNOWLEDGED once, by a command the user runs:
#
#     python routing.py --accept-settings
#
# The acknowledgement stores a digest of the sharp section. Change the sharp section and it stops
# matching, so the tool refuses again and prints what changed. A file write alone is no longer
# enough: the attacker would also have to make the human type a second command, having read a
# refusal that names the redirect. Quiet fields are untouched by any of this.
#
# Why not a per-run flag instead: it would put the friction on every legitimate run forever and
# teach people to paste it by reflex, which is this project's own measured definition of a dead
# gate. Why not simply refuse: because permanent transport changes now have a proper home -
# `channels.json`, which since 1.8.0 is reported field by field in the plan on EVERY run, and is
# wiped by updates, which is the right property for that class.
OVERLAY_TRUST_HOME = "home"
OVERLAY_TRUST_REDIRECTED = "redirected"
ACK_NAME = "model-orchestration.accepted.json"

# Quiet fields: how hard a channel thinks, how much it may read, and bookkeeping. None of them can
# move a byte to a new destination, so they are accepted at either provenance and applied without
# ceremony. EVERYTHING NOT LISTED IS SHARP - a field added to the registry next month is sharp by
# default, which is the right way round for a list that will be edited by someone in a hurry.
OVERLAY_QUIET_FIELDS = frozenset({
    "enabled",          # the only field the docs ever told anyone to change
    "effort", "reasoning", "thinking_level", "max_tokens",   # how hard it thinks
    "fetch_tool", "web", "timeout",                          # how much it may read, for how long
    "label", "notes",                                        # genuinely cosmetic
})
# 🔴 `cost` WAS ON THAT LIST AS "cosmetic / bookkeeping" AND IT IS NOT COSMETIC. Found by taking
# a reviewer's general frame seriously and then checking it: kimik3 pointed out that every
# verification layer here is a report printed by the program whose configuration is under review,
# and offered `label` as the example. `label` turned out to be harmless - the plan prints the
# channel KEY and the model SLUG beside it, neither of which a redirected file can touch. `cost`
# is the one that bites: it decides whether the plan prints "EXPENSIVE channel" before you spend,
# and it decides which channels `--ask` fans out to, since that set is derived from `cost == free`.
# A cloned repository setting `cost: "free"` on an expensive channel would quietly add it to every
# one-shot question. Verify the FINDING, discard the PROOF.
OVERLAY_SHARP_HINT = (
    "Those fields decide WHERE your documents go or WHAT is added to them. This settings file was "
    "chosen by the %s environment variable, and a project's own .claude/settings.json can set that "
    "variable - so its provenance is weaker than your home directory's. Move the file to %s and "
    "the same fields are accepted, printed in full on every run." )


def overlay_home_path():
    """The one path whose provenance is the user's own home directory."""
    return os.path.join(os.path.expanduser("~"), ".claude", OVERLAY_NAME)


def overlay_path():
    """Where the user's own settings live. Never inside the skill directory - see above."""
    return os.environ.get(OVERLAY_ENV) or overlay_home_path()


def overlay_trust():
    """
    Who could have chosen this path. Not "is this file safe" - a file is not a provenance.

    Note the test is on the ENV VAR being set, not on where it points. Pointing the variable back
    at the home default is still a redirect, because we cannot see who set the variable; that is
    the whole asymmetry. Erring here costs a clear error message with the fix in it, and erring
    the other way costs a transport nobody printed.
    """
    return OVERLAY_TRUST_REDIRECTED if os.environ.get(OVERLAY_ENV) else OVERLAY_TRUST_HOME


def apply_overlay(reg, path=None, trust=None, data=None):
    """
    Merge the user's local settings over the shipped registry, recording every change.

    Deliberately strict. A typo'd channel name is REFUSED rather than ignored, because the failure
    mode of a config overlay is silence: `"goog36flah": {"enabled": true}` looks exactly like a
    channel that is off for some other reason, and the user would go looking in the wrong file.

    `data` skips the file entirely, for callers asking "would this payload load?" before writing
    it - see `validate_overlay_data`. Same code path, so the answer is about the real merge rather
    than about a re-implementation of it.
    """
    if path is None and data is None:
        path, trust = overlay_path(), (trust or overlay_trust())
    trust = trust or OVERLAY_TRUST_HOME
    info = {"path": path or "(in memory)", "trust": trust, "present": data is not None,
            "applied": [], "added": [], "renamed": [], "sharp": []}
    reg["_overlay"] = info
    if data is None:
        if not os.path.isfile(path):
            return reg
        info["present"] = True
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise RouteError(
                "your local settings file could not be read: %s\n  %s\n"
                "Fix it or rename it; it is not skipped silently, because a settings file that is "
                "ignored when malformed is worse than one that is missing." % (path, exc))
    if not isinstance(data, dict) or not (isinstance(data.get("channels"), dict)
                                          or isinstance(data.get("tiers"), dict)):
        raise RouteError(
            "%s must look like {\"channels\": {\"goog36flash\": {\"enabled\": true}}} - a top-level "
            "\"channels\" object, and/or a \"tiers\" object. Nothing else is read from it." % path)
    _apply_overlay_tiers(reg, data.get("tiers") or {}, path, trust, info)
    known = reg["channels"]
    for cname, over in (data.get("channels") or {}).items():
        if not isinstance(over, dict):
            raise RouteError("%s: channels.%s must be an object, not %s"
                             % (path, cname, type(over).__name__))
        # 🔴 RESOLVE THROUGH THE ALIAS TABLE BEFORE CALLING A NAME UNKNOWN. goog36flash raised
        # this one: strict rejection plus a rename upstream means every overlay naming the old
        # name STOPS THE TOOL STARTING, for everyone, on upgrade day - a hard failure produced by
        # a safety check, on machines whose owners did nothing wrong. Channels already carry
        # `aliases`, and this project has renamed all four of them once already (round 26).
        target = cname
        if target not in known:
            hits = [h for h in canon_channel_safe(reg, cname) if h in known]
            if len(hits) == 1:
                target = hits[0]
                info["renamed"].append((cname, target))
        # 🔴 ADDING A WHOLE CHANNEL IS THE POINT, NOT AN EDGE CASE - and until round 31 it was
        # impossible while `info["added"]` existed, was counted by doctor.py, and could never be
        # anything but empty. That is this project's signature defect (a reported field that no
        # code path can populate) sitting inside the instrument built to prevent it. `_new: true`
        # is required so a TYPO still fails loudly: without it, a misspelt name would silently
        # create a second channel instead of editing the one that was meant.
        if target not in known:
            if not over.get("_new"):
                raise RouteError(
                    "%s names a channel that does not exist: %r. Known channels: %s.\n"
                    "If you MEANT to add a new channel, add \"_new\": true to that block - it is "
                    "required so that a misspelt name cannot quietly become a second channel. If "
                    "that channel was removed or renamed in this version, delete the line; your "
                    "other settings still apply."
                    % (path, cname, ", ".join(sorted(known))))
            if trust != OVERLAY_TRUST_HOME:
                raise RouteError(
                    ("%s adds a new channel (%r), which needs the home settings file.\n"
                     % (path, cname))
                    + OVERLAY_SHARP_HINT % (OVERLAY_ENV, overlay_home_path()))
            known[cname] = {}
            target = cname
            info["added"].append(cname)
        # SHARP vs QUIET - see the block at the top of this file. At the home path nothing is
        # refused; a redirected path may not repoint a transport. Either way the change is
        # recorded, and `format_plan` prints the sharp ones under their own heading.
        sharp = sorted(f for f in over if not f.startswith("_")
                       and f not in OVERLAY_QUIET_FIELDS)
        if sharp and trust != OVERLAY_TRUST_HOME:
            raise RouteError(
                ("%s sets %s on channel %r.\n" % (path, ", ".join(repr(f) for f in sharp), cname))
                + OVERLAY_SHARP_HINT % (OVERLAY_ENV, overlay_home_path())
                + "\nAccepted from any path: %s." % ", ".join(sorted(OVERLAY_QUIET_FIELDS)))
        for field in sharp:
            # Only when it CHANGES something. A settings file that re-states the shipped value is
            # not a redirect, and demanding acceptance for it would be a false positive in the one
            # gate whose whole purpose is that a human reads it once and means it.
            if known[target].get(field) != over[field]:
                info["sharp"].append((target, field, known[target].get(field), over[field]))
        for field, value in over.items():
            if field.startswith("_"):
                continue
            before = known[target].get(field)
            if before != value:
                info["applied"].append((target, field, before, value))
            known[target][field] = value
    return reg


def _apply_overlay_tiers(reg, over, path, trust, info):
    """
    Tiers are settings too, and until 1.8.0 they were the one knob a user could not reach.

    That asymmetry had a concrete cost: `gemini_thinking_level` lives on the TIER and overrides the
    channel's own value, so a user lowering `goog36flash.thinking_level` in their settings file
    would have watched the tier silently put it back. A knob that resolves, prints and does nothing
    is the single most repeated defect in this project's history; here it would have been created
    by the safety rule rather than found by it.

    What counts as quiet is DERIVED, not listed: a field the shipped tier already has is a knob
    this release understands, and anything else is new, unrecognised, and therefore home-only.
    """
    if not over:
        return
    known = reg.get("tiers") or {}
    for tname, block in over.items():
        if tname.startswith("_"):
            continue
        if not isinstance(block, dict):
            raise RouteError("%s: tiers.%s must be an object, not %s"
                             % (path, tname, type(block).__name__))
        if tname not in known:
            if not block.get("_new"):
                raise RouteError(
                    "%s names a tier that does not exist: %r. Known tiers: %s. Add \"_new\": true "
                    "to define a new one." % (path, tname, ", ".join(sorted(
                        t for t in known if not t.startswith("_")))))
            if trust != OVERLAY_TRUST_HOME:
                raise RouteError(("%s adds a new tier (%r), which needs the home settings file.\n"
                                  % (path, tname))
                                 + OVERLAY_SHARP_HINT % (OVERLAY_ENV, overlay_home_path()))
            known[tname] = {}
            info["added"].append("tier:" + tname)
        quiet = {k for k in known[tname] if not k.startswith("_")}
        sharp = sorted(f for f in block if not f.startswith("_") and f not in quiet)
        if sharp and trust != OVERLAY_TRUST_HOME:
            raise RouteError(
                ("%s sets %s on tier %r, which this release's %r tier does not define.\n"
                 % (path, ", ".join(repr(f) for f in sharp), tname, tname))
                + OVERLAY_SHARP_HINT % (OVERLAY_ENV, overlay_home_path())
                + "\nAccepted from any path on this tier: %s." % (", ".join(sorted(quiet)) or "-"))
        for field, value in block.items():
            if field.startswith("_"):
                continue
            before = known[tname].get(field)
            if field in sharp:
                info["sharp"].append(("tier:" + tname, field, before, value))
            if before != value:
                info["applied"].append(("tier:" + tname, field, before, value))
            known[tname][field] = value
    reg["tiers"] = known


def ack_path():
    """Beside the settings file, never inside the skill folder - it must survive updates too."""
    return os.path.join(os.path.dirname(os.path.abspath(overlay_path())), ACK_NAME)


def sharp_digest(info):
    """
    A stable digest of every transport-affecting change the settings file makes.

    Keyed on (channel, field, new value) and sorted, so re-ordering the JSON, reformatting it, or
    editing a quiet field beside a sharp one does NOT invalidate the acknowledgement. Only a real
    change to what is sent, or where, does.
    """
    items = sorted((c, f, json.dumps(a, sort_keys=True, ensure_ascii=False))
                   for c, f, _b, a in (info.get("sharp") or []))
    if not items:
        return None
    return hashlib.sha256(json.dumps(items, ensure_ascii=False).encode("utf-8")).hexdigest()


def check_sharp_ack(reg):
    """Record on the overlay info whether its sharp section has been acknowledged."""
    info = reg.get("_overlay") or {}
    want = sharp_digest(info)
    info["sharp_digest"] = want
    info["sharp_acked"] = True
    if not want:
        return reg
    try:
        with open(ack_path(), encoding="utf-8") as f:
            info["sharp_acked"] = json.load(f).get("sharp_digest") == want
    except (OSError, ValueError):
        info["sharp_acked"] = False
    return reg


def accept_settings(reg):
    """Write the acknowledgement. Returns (path, digest, list of what was accepted)."""
    info = reg.get("_overlay") or {}
    want = sharp_digest(info)
    p = ack_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"sharp_digest": want,
                   "_what": "You accepted the transport-affecting settings listed below. This "
                            "file is not read for anything else. Change any of them and the tool "
                            "will refuse to spend money until you accept again.",
                   "_accepted": [[c, f_, a] for c, f_, _b, a in (info.get("sharp") or [])]},
                  f, indent=2, ensure_ascii=False)
    return p, want, info.get("sharp") or []


def validate_overlay_data(registry_path, data, trust=OVERLAY_TRUST_HOME):
    """
    Would this settings payload produce a registry that still loads? Returns None, or the reason.

    Exists so `upgrade.py` can carry a user's edits ONE AT A TIME and keep the ones that survive,
    instead of the 1.7.0 behaviour: carry `enabled` and leave everything else behind because a
    carried `model` *might* name something the new release renamed. "Might" is answerable - by
    asking the loader. A setting that is dropped is now dropped with the loader's own error next
    to it, which is the difference between a report and an apology.
    """
    try:
        with open(registry_path, encoding="utf-8") as f:
            reg = json.load(f)
        _strip_comment_keys(reg)
        apply_overlay(reg, trust=trust, data=data)
        _check_channel_names(reg)
        _check_alias_collisions(reg)
        _check_channel_models(reg)
        _check_channel_required(reg)
    except RouteError as exc:
        return str(exc)
    except (OSError, ValueError) as exc:
        return "registry could not be read: %s" % exc
    return None


def canon_channel_safe(reg, name):
    """`canon_channel` without its raise, for callers that have their own error to give."""
    try:
        return canon_channel(reg, name)
    except RouteError:
        return []


def registry_drift(path=DEFAULT_REGISTRY):
    """
    Has the SHIPPED registry been edited in place, and if so, where?

    Round 30 answered only "yes/no", only inside `doctor.py`, which nobody runs before a round.
    That left the two config files with opposite visibility: an overlay change was printed on
    every run, an in-place edit of channels.json was printed nowhere - so the strict overlay
    allowlist was steering people towards the silent file. A shipped tree now carries a reference
    copy of its own registry, so the answer is a LIST OF FIELDS rather than a boolean, and
    `upgrade.py` no longer has to infer what to carry across.

    Returns None in a source tree (no reference copy) - a check that always fires on the
    maintainer's machine is one the maintainer trains themselves to ignore.
    """
    ref = os.path.join(os.path.dirname(os.path.abspath(path)), SHIPPED_REGISTRY_NAME)
    if not os.path.isfile(ref) or not os.path.isfile(path):
        return None
    out = {"reference": ref, "pristine": True, "changed": [], "error": None}
    try:
        with open(ref, encoding="utf-8") as f:
            was = json.load(f)
        with open(path, encoding="utf-8") as f:
            now = json.load(f)
    except (OSError, ValueError) as exc:
        out["error"] = str(exc)
        return out
    a, b = (was.get("channels") or {}), (now.get("channels") or {})
    for cname in sorted(set(a) | set(b)):
        # `_`-prefixed keys inside `channels` are prose, not channels - the house idiom, and the
        # trap `_strip_comment_keys` exists for. Iterating one as a channel would `set()` a string
        # into its characters and report every letter as a changed field.
        if cname.startswith("_") or not isinstance(a.get(cname, b.get(cname)), dict):
            continue
        if cname not in a:
            out["changed"].append((cname, "*", "(absent)", "added by hand"))
            continue
        if cname not in b:
            out["changed"].append((cname, "*", "(shipped)", "deleted by hand"))
            continue
        for field in sorted(set(a[cname]) | set(b[cname])):
            if field.startswith("_"):
                continue
            if a[cname].get(field) != b[cname].get(field):
                out["changed"].append((cname, field, a[cname].get(field), b[cname].get(field)))
    out["pristine"] = not out["changed"]
    return out


def load_registry(path=DEFAULT_REGISTRY, overlay=True):
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
    _strip_comment_keys(reg)
    if overlay:
        # BEFORE the validators, not after: an overlay can add a channel or repoint a model, and
        # those must face exactly the same checks as anything shipped. A validated-then-mutated
        # registry is how a config file becomes an unchecked code path.
        apply_overlay(reg)
    else:
        reg["_overlay"] = {"path": overlay_path(), "trust": overlay_trust(), "present": False,
                           "applied": [], "added": [], "renamed": [], "sharp": []}
    reg["_drift"] = registry_drift(path)
    check_sharp_ack(reg)
    _check_channel_names(reg)
    _check_alias_collisions(reg)
    _check_channel_models(reg)
    _check_channel_required(reg)
    _check_tiers(reg)
    return reg


def _check_tiers(reg):
    """
    A tier value that the vendor will reject costs a paid 400, so it is caught here for free.

    Only `gemini_thinking_level` is checkable this way today - it is the one tier field with a
    declared ladder to check against. The others are effort strings the dispatcher clamps, and
    timeouts, where any number is legal.
    """
    ladders = [set(ch["thinking_levels"]) for ch in reg["channels"].values()
               if isinstance(ch, dict) and ch.get("thinking_levels")]
    if not ladders:
        return
    legal = set().union(*ladders)
    for tname, t in (reg.get("tiers") or {}).items():
        if tname.startswith("_") or not isinstance(t, dict):
            continue
        lvl = t.get("gemini_thinking_level")
        if lvl and lvl not in legal:
            raise RouteError(
                "tier %r sets gemini_thinking_level=%r, which no channel declares. The levels "
                "this release knows about are: %s. Sending an unknown one costs a paid 400."
                % (tname, lvl, ", ".join(sorted(legal))))


# The minimum a channel needs before the plan can honestly print a line for it. Checked at LOAD
# time because the alternative is what actually happened to an unknown `kind`: the plan printed
# [RUN ], money was budgeted for it, and the failure arrived from the dispatcher. Now that the
# overlay can ADD a channel, a half-written block is a thing a user will really produce.
_REQUIRED_CHANNEL_FIELDS = ("kind", "label", "model")


def _check_channel_required(reg):
    for cname, ch in reg["channels"].items():
        missing = [f for f in _REQUIRED_CHANNEL_FIELDS if not ch.get(f)]
        if missing:
            # The list of legal `kind` values is deliberately NOT repeated here. It lives beside
            # the dispatcher in orchestrate.py, which prints it when it meets one it cannot run;
            # a second copy in this file is the two-homes rot this project keeps measuring.
            raise RouteError(
                "channel %r is missing %s. A channel needs at least %s before a plan can honestly "
                "name what it is about to spend money on, and `kind` decides which dispatcher "
                "runs it at all."
                % (cname, ", ".join(repr(m) for m in missing),
                   ", ".join(_REQUIRED_CHANNEL_FIELDS)))


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
            # The fix, spelled out. Since 1.8.0 an advanced user can repoint a model from their own
            # settings file, and this is the guard they will meet first - so it has to hand them
            # the JSON rather than describe it. `label` is what the plan prints before money is
            # spent and `data_policy` is what it prints about the vendor; a model with neither is
            # a plan that cannot tell you what it is about to do.
            raise RouteError(
                "channel %r defaults to model %r, which is not in its own `models` table (%s).\n"
                "One channel = one model, and the table is where its label and data policy live. "
                "Add it in the same block:\n"
                '    "models": {"%s": {"label": "<what to print in the plan>",\n'
                '                      "data_policy": "<what the vendor may do with the payload>"}}'
                % (cname, ch["model"], ", ".join(known) or "empty", ch["model"]))


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

    # 🔴 DECORATE BEFORE APPLYING THE TIER, not after. The tier now SCALES per-channel values
    # (`reasoning.max_tokens`, `fetch_tool.max_calls`) and OVERRIDES one (`thinking_level`), and
    # all three arrive in the plan through _decorate. Run the other way round - which is how this
    # function read until 2026-08-08 - the tier would have scaled fields that were still None and
    # then had its own values silently overwritten by the registry defaults a line later: a knob
    # that resolves, prints and does nothing, which is the single most repeated defect in this
    # repository. Caught while writing it, by asking what _decorate does rather than assuming.
    plan = _decorate(plan, reg)

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
                p["_tier_note"] = ("effort %s (this model's ceiling), timeout %s"
                                   % (p["effort"], p["timeout"]))
        # 🔴 CODEX HAD NO TIER TIMEOUT AT ALL, AND NOTHING SAID SO. Only agy's was wired, so
        # `call_codex` fell through to the 3000-second default hard-coded in `_run` - and when a
        # run was killed the harness advised "raise the timeout for that channel", naming a lever
        # that did not exist. `limits` in channels.json was an empty object, and the
        # `timeout_seconds: 2400` that does exist belongs to a DIFFERENT channels.json in the
        # sibling project, which nothing here reads. Two files, one name, and the printed advice
        # pointed at the copy that was not in play.
            elif p.get("kind") == "codex":
                p["timeout"] = t.get("codex_timeout", "50m")
                p["_tier_note"] = ("timeout %s only - effort stays %s, pinned in this channel's "
                                   "own block because the subscription has no cheaper setting "
                                   "worth having" % (p["timeout"], p.get("effort")))
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
                p["_tier_note"] = ("thinking budget %s, output floor %s"
                                   % (t.get("http_thinking_budget"), t.get("http_floor")))
        # 🔴 THE TIER USED TO REACH 4 OF 11 RUNNING CHANNELS AND READ LIKE A GLOBAL CONTROL.
        # Igor, 2026-08-08: «не понятно визуально, чем отличается strategic от Deep» - and the
        # true answer was "a timeout", because these three families were never wired at all.
        # They are now, as MULTIPLIERS on each channel's own registry values rather than as
        # absolute numbers, so `strategic` (scale 1) is bit-for-bit what ran before and only
        # `deep` costs anything new. An absolute value here would have quietly DOWNGRADED
        # qwen38max, whose registry effort is already xhigh.
            elif p.get("kind") in ("openrouter", "oai"):
                changed = []
                rs = float(t.get("or_reasoning_scale") or 1)
                if rs != 1 and isinstance(p.get("reasoning"), dict):
                    r = dict(p["reasoning"])
                    if r.get("max_tokens"):
                        r["max_tokens"] = int(r["max_tokens"] * rs)
                        changed.append("reasoning cap %s" % r["max_tokens"])
                    p["reasoning"] = r
                fs = float(t.get("fetch_scale") or 1)
                if fs != 1 and isinstance(p.get("fetch_tool"), dict) \
                        and p["fetch_tool"].get("enabled"):
                    ftl = dict(p["fetch_tool"])
                    ftl["max_calls"] = int((ftl.get("max_calls") or 8) * fs)
                    p["fetch_tool"] = ftl
                    changed.append("page-fetch budget %s" % ftl["max_calls"])
                p["_tier_note"] = ", ".join(changed) if changed else \
                    "nothing this tier can raise on this channel"
            elif p.get("kind") == "gemini":
                # Believed impossible until 2026-08-08: the 08-07 probe sent `thinking_level` at
                # the top level, got "Unknown parameter", and concluded the knob did not exist.
                # It lives in `generation_config`. See the goog36flash block in channels.json.
                #
                # 🔴 THE NOTE USED TO PRINT THE VALUE AND CALL THAT A DIFFERENCE. With Igor's
                # 08-08 change both tiers sit at `high`, so "tier: thinking_level=high" would have
                # appeared under `deep` looking exactly like a raise, on a channel where the tier
                # now changes nothing. Same defect as the xai line the panel caught a day earlier:
                # a note that reports what was SENT rather than what was CHANGED. The ceiling is
                # read from the channel's own `thinking_levels`, so the sentence cannot go stale
                # if the vendor adds a level.
                before = p.get("thinking_level")
                lvl = t.get("gemini_thinking_level")
                if lvl:
                    p["thinking_level"] = lvl
                    ladder = p.get("thinking_levels") or []
                    if before and before != lvl:
                        p["_tier_note"] = "thinking_level %s -> %s" % (before, lvl)
                    elif ladder and lvl == ladder[-1]:
                        p["_tier_note"] = ("nothing this tier can raise on this channel - "
                                           "thinking_level is already %s, the top of %s"
                                           % (lvl, "|".join(ladder)))
                    else:
                        p["_tier_note"] = "thinking_level=%s (unchanged by this tier)" % lvl
            elif p.get("kind") == "xai":
                # 🔴 "no depth knob on this VENDOR" was the first wording and it was wrong in a
                # way that mattered: xAI's own docs document `reasoning_effort` on grok-4.5 and
                # `reasoning.effort` on grok-4.20-multi-agent. It is THIS MODEL that refuses it -
                # `400 Model grok-4.20-0309-reasoning does not support parameter reasoningEffort`,
                # returned identically for the top-level and the nested placement on
                # /v1/responses, while an invented sibling key returns 200 and is ignored. So the
                # refusal is model-level, not a placement mistake (which is what it WAS for
                # Gemini). Probed 2026-08-08 after qwen38max objected, correctly, that a single
                # 400 from one placement is the exact error this round had just fixed elsewhere.
                # It also gets the tier's TIMEOUT now; before 2026-08-08 the dispatcher passed
                # none, so `deep` was a literal no-op here while this line said "wall-clock".
                p["timeout"] = t.get("codex_timeout", "50m")
                p["_tier_note"] = ("no depth knob on THIS MODEL (other xAI models have one) - "
                                   "the tier buys wall-clock only: timeout %s" % p["timeout"])
    return plan


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
        # The slot travels alone into the dispatcher and into _system_for; anything that has to
        # answer "which channel is this" without a surrounding dict has to be IN it.
        p["_name"] = cname
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
        # 🔴 `tools` WAS MISSING FROM THIS LIST AND NOBODY COULD HAVE NOTICED. goog36flash declares
        # tools:["google_search","url_context"] and the dispatcher reads p.get("tools") - which was
        # always None, so call_gemini_direct fell through to its own hard-coded default. The
        # default happened to be the identical pair, so the registry entry produced the right
        # behaviour while being decorative: the exact shape of `channels.spark.model`, and the
        # exact reason that one survived for weeks. It would have become a real defect the first
        # time anyone edited the registry to drop url_context. Both homes still exist (the literal
        # is a deliberate fallback for a corrupt registry) but the registry now actually wins.
        for extra in ("reasoning", "max_tokens", "toolsets", "role", "fetch_tool", "tools",
                      "provider", "prompt_suffix", "distribution", "thinking_level",
                      "thinking_levels"):
            if ch.get(extra) is not None:
                p[extra] = ch[extra]
        # Hints are stored ONCE at top level and referenced, because the same 1.5 KB paragraph
        # belongs to both agy channels and a copy in each is a copy that drifts. Resolved here so
        # the dispatcher never re-opens the registry, and so --dry-run can show it.
        hint = ch.get("fetch_fallback_hint")
        ref = ch.get("fetch_fallback_hint_ref")
        if ref and not hint:
            hint = (reg.get("hints") or {}).get(ref)
            if hint is None:
                p["why"].append("⚠ fetch_fallback_hint_ref %r is not in `hints` - no hint sent"
                                % ref)
        if hint:
            p["fetch_fallback_hint"] = hint
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


# 🔴 "IS THE INTERNET ON FOR ALL OF THEM?" HAD TO BE ANSWERED BY A HUMAN READING SOURCE CODE.
# Igor asked it on 2026-08-08 and the honest reason he had to ask is that the plan printed a web
# line for exactly one FAMILY - the channels carrying a `web` object, i.e. openrouter and oai.
# grok420 (server-side agent tools), goog36flash (google_search + url_context), codex
# (-c tools.web_search=true) and both Spark channels (the web_search tool block) all had live
# web access and printed NOTHING about it, so the plan read as "these four are offline".
# A capability that is on but invisible gets asked about, doubted, and eventually re-implemented.
# Keyed on `kind`, like the dispatcher, so a new channel of a known kind inherits the line.
def _web_line(p):
    """One line describing this channel's live-web access, for every kind. None = no access."""
    kind = p.get("kind")
    if kind in ("openrouter", "oai"):
        w = p.get("web") or {}
        if not w.get("enabled"):
            base = None
        elif p.get("provider") == "mimo":
            base = ("web: the VENDOR's own search tool - it opens whole pages itself, so its "
                    "citations are page-level rather than search excerpts")
        else:
            base = ("web: OpenRouter search plugin via %s, max %s results - billed PER SEARCH"
                    % (w.get("engine", "provider default"), w.get("max_results", "default")))
        ft = p.get("fetch_tool") or {}
        if ft.get("enabled"):
            add = ("harness page-fetch tool, up to %s pages (WE run these, so they are the only "
                   "grounding this channel can prove)" % (ft.get("max_calls") or 8))
            base = base + " + " + add if base else "web: " + add
        return base
    if kind == "gemini":
        return ("web: Google's own retrieval - %s. url_context reaches pages a plain fetch is "
                "refused; google_search citations are redirect wrappers naming the publisher "
                "domain, resolved to real URLs by the citation audit"
                % ", ".join(p.get("tools") or ["google_search", "url_context"]))
    if kind == "xai":
        return ("web: xAI Agent Tools (%s) on /v1/responses - the vendor runs the loop and OPENS "
                "pages itself; chat/completions has had no search since live_search went 410"
                % ", ".join(p.get("tools") or ["web_search"]))
    if kind == "http":
        return ("web: the vendor's web_search tool is granted on every call - but a grant is "
                "PERMISSION, not instruction; the search count in the run log is the only proof "
                "it was used")
    if kind == "codex":
        return "web: built-in search via `-c tools.web_search=true`, plus its MCP page readers"
    if kind == "agy":
        return "web: the CLI's own agent tools plus its MCP servers (search, fetch, browser)"
    return None


def _short(value, cap=90):
    """A value a human can take in at a glance. The full object is in the file they are reading."""
    text = repr(value)
    return text if len(text) <= cap else text[:cap - 3] + "..."


def format_plan(plan, reg):
    lines = ["RESOLVED PLAN", "-" * 78]
    # Printed BEFORE the channels, every run, whether or not it changed anything. A settings file
    # that is only mentioned when it acts is a file people forget they wrote; this project has
    # measured the same shape twice already (a `--set` that moved a tier, a `data_policy` nobody
    # re-read). The plan is the one screen a human is guaranteed to look at before spending.
    ov = reg.get("_overlay") or {}
    if ov.get("present"):
        via = ("  (path chosen by %s - a project's .claude/settings.json can set that variable, "
               "so transport fields are refused here)" % OVERLAY_ENV
               ) if ov.get("trust") == OVERLAY_TRUST_REDIRECTED else ""
        lines.append("  local settings: %s%s" % (ov["path"], via))
        sharp = {(c, f) for c, f, _b, _a in ov.get("sharp", [])}
        for cname in ov.get("added", []):
            lines.append("           - 🔴 %s is a channel YOUR settings file adds; it is not part "
                         "of this release" % cname)
        for cname, field, before, after in ov.get("applied", []):
            # 🔴 The sharp ones are marked in the SAME list rather than split into a second block.
            # A separate "dangerous changes" section is read as a section about someone else: what
            # has to be legible is that this line, among the ordinary ones, moves a document.
            mark = "🔴 " if (cname, field) in sharp else ""
            # 🔴 TRUNCATED, because the first version printed a whole `models` block - the shipped
            # one carries several hundred characters of prose - and the warning under it scrolled
            # off. A warning nobody can read is this project's own definition of a dead gate, and
            # it had just been rebuilt into the line meant to prevent a silent redirect.
            lines.append("           - %s%s.%s: %s -> %s (yours, not the shipped default)"
                         % (mark, cname, field, _short(before), _short(after)))
        for old, new in ov.get("renamed", []):
            lines.append("           - %r in your file resolved to the channel now called %r"
                         % (old, new))
        if not ov.get("applied") and not ov.get("renamed"):
            lines.append("           - present but changes nothing")
        if sharp:
            lines.append("           🔴 marked lines change WHERE a document goes or WHAT is added "
                         "to it.")
            if not ov.get("sharp_acked"):
                lines.append("           🔴🔴 NOT YET ACCEPTED. This file survives every update, so "
                             "one write to it")
                lines.append("              would otherwise re-point a channel forever, silently. "
                             "If you made these")
                lines.append("              changes, run once:   python \"%s\" --accept-settings"
                             % os.path.join(HERE, "routing.py"))
                lines.append("              If you did NOT, look at %s before anything else."
                             % ov["path"])
        lines.append("-" * 78)
    # The other config file. Until 1.8.0 this was checked only by `doctor.py`, so the strict file
    # printed itself every run and the file that can repoint a vendor printed nothing - which is
    # how a safety rule ends up steering people towards the quiet path.
    drift = reg.get("_drift")
    if drift and drift.get("changed"):
        lines.append("  🔴 channels.json has been edited since it was installed - %d field(s):"
                     % len(drift["changed"]))
        for cname, field, before, after in drift["changed"][:12]:
            lines.append("           - %s.%s: %r -> %r" % (cname, field, before, after))
        if len(drift["changed"]) > 12:
            lines.append("           - ... and %d more" % (len(drift["changed"]) - 12))
        lines.append("           That file is inside the folder an update replaces. Move it with:"
                     "  python upgrade.py --migrate")
        lines.append("-" * 78)
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
        if p["enabled"]:
            wl = _web_line(p)
            lines.append("           - %s" % wl if wl
                         else "           - web: NONE - this channel answers from training data "
                              "only, treat every dated claim as unverified")
            # What the chosen tier actually did to THIS channel. Igor: «не понятно визуально,
            # чем отличается strategic от Deep». It was not visible because it was not printed,
            # and on four of eleven channels there was nothing to print because the tier was
            # never wired to them. Both halves are fixed; where a tier still changes nothing,
            # the line says so rather than being omitted, because an omitted line reads as
            # "not applicable" and an absent lever reads the same way.
            if p.get("_tier_note"):
                lines.append("           - tier: %s" % p["_tier_note"])
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
    ap.add_argument("--accept-settings", action="store_true",
                    help="accept the transport-affecting changes your settings file makes. Needed "
                         "once after you add or repoint a channel there; a paid round refuses "
                         "until then, so that one write to a file that survives every update "
                         "cannot silently redirect a channel forever")
    a = ap.parse_args()
    # 🔴 LOADING WAS OUTSIDE THE HANDLER, so every refusal the loader is designed to produce came
    # out as a Python traceback. It never showed before because only a corrupt registry could
    # trigger it and nobody ships one; the local overlay made it reachable by a typo in a config
    # file - which is the case where a readable sentence matters most. Caught by running the
    # negative controls, not by reading the code: the three refusals fired correctly and were
    # unreadable, and "it refused" would have looked like a pass in a log.
    try:
        reg = load_registry(a.registry)
        plan = resolve(reg, route=a.route, only=a.only, skip=a.skip, sets=a.sets, tier=a.tier)
    except RouteError as e:
        print("ROUTE ERROR: %s" % e)
        return 2
    if a.accept_settings:
        info = reg.get("_overlay") or {}
        if not (info.get("sharp") or []):
            print("Nothing to accept: %s makes no transport-affecting change.\n"
                  "(Quiet settings - how hard a channel thinks, how much it may read, whether it "
                  "is enabled - never need accepting.)" % info.get("path"))
            return 0
        p, digest, items = accept_settings(reg)
        print("ACCEPTED, from %s:" % info.get("path"))
        for cname, field, before, after in items:
            print("  %s.%s: %r -> %r" % (cname, field, before, after))
        print("\nRecorded in %s\nChange any of those and a paid round will refuse again until you "
              "re-run this." % p)
        return 0
    print(json.dumps(plan, ensure_ascii=False, indent=1) if a.json else format_plan(plan, reg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
