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
# 🔴 ADD is the "default set PLUS this one" mode, and it exists because opt-in channels do.
# Added 2026-08-14 (round 38) with orgpt56terrapro, the first channel that is off unless asked
# for by name. Igor: «если скажут используй все модели, ее не использовать, а если скажут и 5.6
# Terra Pro, то используй.» ONLY could not express that - "только terra pro" drops the other
# twelve - and without ADD the sentence he actually says was a hard ROUTE ERROR.
#
# 🔴 THE BARE "и" IS DELIBERATELY NOT A MARKER, and this is the whole design risk. It is the
# commonest word in Russian and appears in briefs, channel prose and ordinary conjunctions
# ("spark и codex"), so accepting it would turn half of every sentence into a selection verb -
# the same over-matching that made a bare `5.6` route to the wrong model in this very round.
# Only unambiguous ADDITIVE phrases are listed, and "и ещё" is included because the "ещё"
# carries the meaning that "и" alone does not.
# 🔴 «включая» WAS MISSING AND IT IS THE WORD IGOR ACTUALLY USES. R43, verbatim: «он не должен
# запускаться, без явного: "Запусти все, включая Terra pro"». That exact sentence was a hard
# ROUTE ERROR - the router recognised the channel, found no instruction word, and refused. So the
# one phrasing named in the instruction that AUTHORISES the channel was the one phrasing that
# could not authorise it. Found by running his sentence verbatim instead of a paraphrase of it,
# which is the only way this class is ever found: every ADD word already here was one someone
# imagined, and «и ещё»/«плюс» were imagined by the same person who then wrote «включая».
ADD = ["и ещё", "и еще", "а также", "плюс ", "добавь", "добавить", "дополнительно", "вместе с",
       "включая", "включительно", "в том числе", "and also", "plus ", "add ", "including"]


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
    _check_panels(reg)
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


def tier_alias_index(reg):
    """Every word that may follow `--tier`, mapped to the canonical tier it names.

    Includes each tier's own key, so this is the whole accepted vocabulary in one place.
    """
    out = {}
    for tname, t in (reg.get("tiers") or {}).items():
        if tname.startswith("_") or not isinstance(t, dict):
            continue
        out[tname.lower()] = tname
        for a in t.get("aliases") or []:
            out[str(a).lower().replace("ё", "е")] = tname
    return out


def canon_tier(reg, name):
    """
    Resolve a tier word to the tier it names, or raise with the accepted vocabulary.

    🔴 THIS EXISTS BECAUSE R43 COLLAPSED TWO TIERS INTO ONE AND `strategic`/`deep` HAD TO KEEP
    WORKING. They are now aliases of `max`. The alternative - deleting the words - would have
    broken every stored command in the other project, and the alternative to THAT - accepting
    them and silently resolving to the default - is the flag-accepted-but-not-applied class this
    repository has recorded five times. So they resolve, and the caller is told which word it
    honoured so the plan can print it.
    """
    if not name:
        return None
    idx = tier_alias_index(reg)
    key = str(name).lower().replace("ё", "е")
    if key in idx:
        return idx[key]
    raise RouteError(
        "unknown tier %r. Accepted: %s. (Since 2026-08-15 there is exactly ONE tier - every "
        "channel runs at the maximum depth its vendor accepts - and the old names strategic|deep "
        "are kept as aliases of it so nothing anyone typed before breaks.)"
        % (name, ", ".join(sorted(idx))))


def _check_panels(reg):
    """
    Validate the `panels` ladder and normalise every channel's membership.

    A PANEL is who is in the room; a TIER is how deep each of them goes. The two are separate
    flags on purpose, and this checker exists because the failure mode of getting a panel wrong
    is silent in the expensive direction: an unknown panel name that fell through to "run
    everything" would look exactly like a cheap run that happened to cost more.

    🔴 A MISSING `panel` DEFAULTS TO `default_panel` RATHER THAN RAISING, and the asymmetry is
    deliberate. Since 1.8.0 a user's own settings file can ADD a channel, and refusing to load a
    registry because someone's hand-written block omits one advisory word would break a working
    install over a formality. Defaulting to `standard` is also the SAFE direction: an
    undeclared channel is absent from `--panel cheap` (a cheap run stays cheap) and present in
    the default run (nothing changes). It is recorded in `_panel_defaulted` and printed, because
    the one thing it must not be is invisible. Channels shipped in THIS file are held to the
    stricter rule by selftest, where a missing declaration is a test failure rather than a
    silent default.
    """
    panels = {k: v for k, v in (reg.get("panels") or {}).items()
              if not k.startswith("_") and isinstance(v, dict)}
    reg["panels"] = panels
    if not panels:
        # A registry with no panels is legal and means "one room, everyone in it". The flag
        # then has nothing to offer and `--panel` is refused by argparse for lack of choices.
        reg["_panel_defaulted"] = []
        return
    default = reg.get("default_panel")
    if default not in panels:
        raise RouteError(
            "default_panel=%r is not one of the panels this registry defines (%s). A default "
            "that names nothing resolves to «no filter», which is the most expensive option "
            "wearing the name of a cheaper one."
            % (default, ", ".join(sorted(panels))))
    for pname, p in panels.items():
        inc = p.get("includes")
        if not isinstance(inc, list) or not inc:
            raise RouteError("panel %r has no `includes` list. A panel is defined by which "
                             "membership labels it admits; without one it admits nothing and "
                             "would run zero channels." % pname)
        unknown = [i for i in inc if i not in panels]
        if unknown:
            raise RouteError("panel %r includes %s, which is not a panel in this registry (%s)."
                             % (pname, ", ".join(repr(u) for u in unknown),
                                ", ".join(sorted(panels))))
        if pname not in inc:
            raise RouteError(
                "panel %r does not include its own label %r. `includes` is the ladder - a panel "
                "always admits the channels declared for it, plus anything cheaper it lists. "
                "Omitting itself makes the panel's own members invisible to it."
                % (pname, pname))
    defaulted = []
    for cname, ch in reg["channels"].items():
        want = ch.get("panel")
        if want is None:
            ch["panel"] = default
            defaulted.append(cname)
        elif want not in panels:
            raise RouteError(
                "channel %r declares panel=%r, which this registry does not define (%s). A "
                "membership label nothing admits means the channel silently never runs under "
                "any --panel, which reads as «it was not selected» rather than as a typo."
                % (cname, want, ", ".join(sorted(panels))))
    reg["_panel_defaulted"] = defaulted


def panel_members(reg, name):
    """The channel names a panel admits, by the `includes` ladder. Raises on an unknown name."""
    panels = reg.get("panels") or {}
    if name not in panels:
        raise RouteError("unknown panel %r. This registry defines: %s."
                         % (name, ", ".join(sorted(panels)) or "(none)"))
    admits = set(panels[name]["includes"])
    return {c for c, ch in reg["channels"].items() if ch.get("panel") in admits}


def channel_vendor(reg, cname):
    """
    Which COMPANY's weights this channel runs, falling back to the channel name.

    Not cosmetic and not derivable from `kind`: kind is the transport. Three Geminis through
    the agy CLI, two through Google directly and one through OpenRouter are six transports and
    ONE vendor - and a panel of six seats that all agree because they share a training corpus
    has produced one opinion, not six. The plan prints the tally so that «eleven reviewers
    agreed» can be read as what it is.
    """
    return (reg["channels"].get(cname) or {}).get("vendor") or cname


# The minimum a channel needs before the plan can honestly print a line for it. Checked at LOAD
# time because the alternative is what actually happened to an unknown `kind`: the plan printed
# [RUN ], money was budgeted for it, and the failure arrived from the dispatcher. Now that the
# overlay can ADD a channel, a half-written block is a thing a user will really produce.
#
# 🔴 `panel` AND `vendor` ARE DELIBERATELY NOT IN THIS TUPLE. Both have a safe fallback
# (`default_panel`, and the channel's own name) and both are advisory-to-a-human rather than
# load-bearing for the dispatcher, so refusing to load over a missing one would turn a
# formatting nicety into an outage. selftest requires them of every SHIPPED channel instead -
# strict where the author is this project, forgiving where the author is a user's settings file.
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
    # `default_enabled` is carried BESIDE `enabled`, never derived from it later, because the two
    # answer different questions and the answer to the first is destroyed by the time anyone needs
    # it: `enabled` is what will run after every flag and route word has been applied, while
    # `default_enabled` is what the registry shipped. The spend gate needs the difference - "this
    # channel is off unless someone asks" is only distinguishable from "this channel is on" once
    # both values exist. Deriving it downstream would mean re-reading the registry in a second
    # place, which is how the plan and the dispatcher came to disagree in round 23.
    return {c: {"enabled": ch.get("enabled", True), "model": ch.get("model"),
                "default_enabled": ch.get("enabled", True),
                "spend_guard": ch.get("spend_guard") or None,
                "panel": ch.get("panel"), "vendor": ch.get("vendor") or c,
                "explicit_only": bool(ch.get("explicit_only")),
                "kind": ch["kind"], "label": ch.get("label", c),
                "effort": ch.get("effort"), "agent": ch.get("agent"), "why": []}
            for c, ch in reg["channels"].items()}


# Panel words a human can type into a free-text route, resolved from the registry so the flag
# and the prose cannot drift apart. Longest first, for the same reason alias_index sorts that
# way: "дешевая панель" must win over "дешевая" or the leftover word confuses the entity scan.
def _panel_alias_index(reg):
    out = []
    for pname, p in (reg.get("panels") or {}).items():
        for al in [pname] + list(p.get("aliases") or []):
            out.append((al.lower(), pname))
    out.sort(key=lambda kv: -len(kv[0]))
    return out


def extract_panel(reg, text):
    """
    Pull a panel word out of free text and hand back (panel, the rest of the text).

    🔴 THIS IS NOT PART OF THE ENTITY SCAN, AND IT MUST NOT BE. A group word expands into
    channel tokens that `--only` then resurrects from `enabled: false` - that is the documented
    opt-in path. A panel does the opposite: it filters DOWN and never turns a channel on. Two
    opposite semantics cannot share the alias namespace without one of them silently becoming
    the other, so panel words are consumed HERE, before `_scan` ever sees the string, and the
    remaining text is what gets parsed for channels. The alternative - registering `cheap` as a
    group - would have been one line and would have resurrected every kit-only twin.
    """
    if not text:
        return None, text
    t = " " + text.lower().replace("ё", "е") + " "
    found = []
    spans = []
    for al, pname in _panel_alias_index(reg):
        for m in re.finditer(r"(?<![\w\-])" + re.escape(al) + r"(?![\w\-])", t):
            if any(m.start() < e and m.end() > s for s, e in spans):
                continue                       # already consumed by a longer panel alias
            spans.append((m.start(), m.end()))
            found.append((m.start(), pname, al))
    if not found:
        # 🔴🔴 A PANEL WORD THAT ALMOST MATCHED IS THE EXPENSIVE FAILURE, AND IT WAS SILENT.
        # «дешовая панель без grok» - one transposed letter - found no alias, so the panel word
        # vanished, `apply_route` happily handled `grok`, and the round ran the DEFAULT panel:
        # a typo that costs money and prints nothing. Two reviewers found it on 2026-08-15 and
        # both ranked it BLOCKER, correctly: this project's rule is «keep failures LOUD, keep
        # spending silent-proof», and this was the inverse. The evidence that a panel was MEANT
        # is either the head noun («панель» / «panel») or a token that starts like one of the
        # aliases; the stems are derived from the alias table so they cannot drift away from it.
        heads = ("панель", "панели", "панелью", "панелях", "panel", "panels")
        stems = {a[:5] for a, _ in _panel_alias_index(reg) if " " not in a and len(a) >= 6}
        words = re.findall(r"[\w\-]+", t)
        near = [w for w in words
                if w in heads or (len(w) >= 5 and any(w.startswith(s) for s in stems))]
        if near:
            raise RouteError(
                "this route looks like it names a panel (%s) but nothing there matches one. A "
                "near miss is the expensive failure: the word would be ignored and the round "
                "would run the DEFAULT panel (%r) without saying so. The words that work: %s."
                % (", ".join("%r" % w for w in sorted(set(near))), reg.get("default_panel"),
                   ", ".join(sorted(a for a, _ in _panel_alias_index(reg)))))
        return None, text
    # 🔴 A NEGATED PANEL IS A SILENT INVERSION, AND THIS WAS A DEFECT WRITTEN IN THIS ROUND.
    # The first version of this function matched the alias wherever it appeared, so «не
    # используй дешевую панель» SELECTED the cheap panel - the exact opposite of the sentence -
    # and then produced a confusing "no channel matched" error from the leftover words, which
    # is the worst possible pairing: the wrong thing happens and the message is about something
    # else. The entity scanner handles negation because markers and entities travel in one
    # ordered stream; panel words are consumed before that stream exists, so negation has to be
    # checked here or not at all. Refusing rather than complementing is deliberate: with exactly
    # two panels "not cheap" has an obvious answer, and the code must not encode "exactly two".
    # 🔴 SUBSTITUTION IS NOT NEGATION, AND TREATING IT AS ONE REFUSED A SENTENCE THAT MEANS
    # EXACTLY ONE THING. «стандартная панель вместо дешевой» has a marker before the SECOND
    # panel word, so the first draft's single NEG+SUBST prefix test fired and answered «a panel
    # cannot be negated» - about the word the human was discarding. Three reviewers hit it
    # independently. The rule that resolves it is the one `apply_route` already uses for models:
    # after «вместо», the LATER name wins. So SUBST selects, NEG refuses, and the difference is
    # that «вместо X» tells you what to drop AND what is left, while «не X» tells you only the
    # first half - which with three panels would be a guess.
    subst_at = [m.start() for w in SUBST for m in re.finditer(re.escape(w), t)]
    for s, pname, word in found:
        prefix = t[max(0, s - 40):s].rstrip(" ,.:;-—")
        if next((w for w in SUBST if prefix.endswith(w.rstrip())), None):
            continue                           # «вместо <panel>» - handled below, not an error
        hit = next((w for w in NEG if prefix.endswith(w.rstrip())), None)
        if hit:
            raise RouteError(
                "%r negates the panel word %r, and a panel cannot be negated - it names which "
                "room the review happens in, so there is always exactly one. Say which panel "
                "you DO want: %s, or use the substitution form («%s вместо ...»), which says "
                "both halves. (Channels CAN be negated: «%s панель, без grok» works.)"
                % (hit.strip(), word, ", ".join(sorted(reg.get("panels") or {})), pname, pname))
    names = {p for _, p, _ in found}
    chosen = found[0][1]
    if len(names) > 1:
        # «A вместо B» = A wins, and A is the one that has a substitution marker AFTER it.
        # (Getting this backwards is easy and silent: the first draft kept B.) Without such a
        # marker, two panels named in one breath is a real ambiguity and stops the run.
        keeps = {p for s, p, _ in found if any(s2 > s for s2 in subst_at)}
        if len(keeps) == 1:
            chosen = keeps.pop()
        else:
            raise RouteError(
                "this route names more than one panel (%s). A panel is which reviewers are in "
                "the room and there can only be one room - say which: %s. If you meant one "
                "INSTEAD OF the other, write it that way: «<panel> вместо <panel>»."
                % (", ".join("%r" % w for _, _, w in sorted(found)),
                   " or ".join(sorted(names))))
        # 🔴 The substitution marker has to be cut out WITH the panel words it joined, or the
        # leftover is a bare «вместо» with nothing behind it - and resolve() refuses exactly
        # that shape, on purpose, because it usually means a misspelt channel name. Fixing the
        # selection without fixing the leftover would have turned a wrong answer into a
        # confident refusal, which is not obviously better.
        lo, hi = min(s for s, _, _ in found), max(s for s, _, _ in found)
        for w in SUBST:
            for m in re.finditer(re.escape(w), t):
                if lo < m.start() < hi:
                    spans.append((m.start(), m.end()))
    # Cut the matched words out so the entity scanner never sees them. `_scan` pads with spaces
    # too, so the offsets line up after dropping the leading pad.
    keep, prev = [], 0
    for s, e in sorted(spans):
        keep.append(t[prev:s])
        prev = e
    keep.append(t[prev:])
    return chosen, "".join(keep).strip()


def _scan(text, idx):
    """Produce an ordered stream of ('neg'|'subst'|'only'|entity) tokens with their positions."""
    t = " " + text.lower().replace("ё", "е") + " "
    marks = []
    for kind, words in (("neg", NEG), ("subst", SUBST), ("only", ONLY), ("add", ADD)):
        for w in words:
            for m in re.finditer(re.escape(w), t):
                # 🔴🔴 A NEGATED ADD IS A NEGATION, AND GETTING THIS WRONG PUT THE $12 CHANNEL IN
                # A ROUND THAT SAID «NOT». Found by a reviewer, by execution, in the round that
                # created it: «Запусти все, НЕ включая Terra pro» matched the ADD word «включая»
                # inside «не включая», the ADD branch enabled the channel, and the user's «не»
                # became decoration. The inverse of the very bug this ADD word was added to fix,
                # one sentence away from the phrasing that authorises the channel - so the two
                # sentences a human would actually write meant the SAME thing and one of them
                # was a lie. Checked here rather than in apply_route because a marker's polarity
                # is a property of the token, not of the branch that later consumes it.
                if kind in ("add", "only", "subst"):
                    before = t[max(0, m.start() - 14):m.start()]
                    if re.search(r"(?:^|\s)(?:не|нe|without|not|кроме|без)\s*$", before):
                        marks.append((m.start(), "neg", w))
                        continue
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


def _route_has_entity(reg, text):
    """True when the text names at least one channel, model or group the registry knows."""
    if not text:
        return False
    return any(t[1] == "entity" for t in _scan(text, alias_index(reg)))


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
        if kind in ("neg", "subst", "only", "add"):
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

        elif mode == "add":
            # ADDITIVE: keep whatever the default set already is and switch this one ON. The
            # point of the mode is opt-in channels - anything already enabled is unaffected, so
            # "добавь kimi" on a default run is a no-op that still prints why, rather than an
            # error. Note this does NOT set _route_off anywhere: adding a channel excludes
            # nothing, which is exactly how it differs from `only`.
            plan[cname]["enabled"] = True
            plan[cname].pop("_route_off", None)
            if etype == "model":
                plan[cname]["model"] = mname
                plan[cname]["why"].append("route: added, model pinned to %s" % mname)
            else:
                plan[cname]["why"].append("route: added to the default set by name")

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
                # 🔴 THE MESSAGE LISTED THREE OF THE FOUR MODES AND OMITTED THE ONE THE READER
                # MOST OFTEN WANTS. ADD has existed since R38 and this sentence never mentioned
                # it, so «используй терра» - a natural way to ask for an opt-in channel - was
                # refused with a list of alternatives that did not contain the right answer.
                # A refusal is only as good as the instruction it gives.
                "%r mentions %s but no instruction word. Say what to do with it: «только %s» "
                "(that one and nothing else), «включая %s» / «плюс %s» (the usual set AND that "
                "one), «не используй %s» (drop it), or «X вместо %s» (swap). A bare name is not "
                "an instruction, and guessing between «only» and «also» is exactly the guess "
                "that would spend money on the wrong set."
                % (text, cname, cname, cname, cname, cname, cname))

    if only_list:
        for c in plan:
            if c not in only_list and plan[c]["enabled"]:
                plan[c]["enabled"] = False
                plan[c]["_route_off"] = True
                plan[c]["why"].append("route: not in the 'only' list")
            elif c in only_list and not plan[c]["enabled"]:
                # 🔴 NAMING A CHANNEL SELECTS IT, ON BOTH SELECTION PATHS. Until 2026-08-14 this
                # branch only turned channels OFF, so a route that named a default-OFF channel
                # produced "running 0 channel(s): NONE" - it removed everything else and then
                # left the one thing the human asked for still disabled. The `--only` FLAG had
                # always done the right thing (`else: plan[c]["enabled"] = True` in apply_flags),
                # so the two selection paths disagreed and only the prose one was wrong.
                #
                # Found the same day orgpt56terrapro became the first opt-in channel, which is
                # what made the gap reachable: with every channel enabled by default, "только X"
                # and "X is already on" were indistinguishable. Igor's rule - «по дефолту
                # отключена, только если явно скажут ее использовать» - is only true if saying it
                # in prose works as well as passing a flag.
                plan[c]["enabled"] = True
                plan[c].pop("_route_off", None)
                plan[c]["why"].append("route: named explicitly (overrides default-off)")

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
    # ё→е on BOTH sides (R74; orgemini37flash, R73): the key was normalised and the registry
    # side was not, so an alias spelled with ё in channels.json could never match. Latent
    # today - the one ё-word in the registry («всё») ships its е-twin - but every other
    # alias-matching site in this file already normalises both sides.
    for gname, g in (reg.get("groups") or {}).items():
        if (key == gname.lower().replace("ё", "е")
                or key in [str(a).lower().replace("ё", "е") for a in g.get("aliases", [])]):
            return list(g["channels"])
    for cname, ch in reg["channels"].items():
        if key in [str(a).lower().replace("ё", "е") for a in ch.get("aliases", [])]:
            return [cname]
    # 🔴 MODEL ALIASES ARE THE LAST RESORT, AND THEY WERE MISSING ENTIRELY UNTIL R43. The free-text
    # router resolves them (`_scan` indexes model aliases too), so «только 5.6 terra» worked while
    # `--only "5.6 terra"` died with «unknown channel» - the two selection paths accepting
    # different vocabularies, which is the same defect shape as `--only http` in July and as the
    # two paths disagreeing about default-off channels in August. Named by a reviewer this round.
    # Tried LAST so a channel or group word always wins: a model name is the more specific thing
    # to say, but it must never shadow the name of a channel.
    for cname, ch in reg["channels"].items():
        for mid, mv in (ch.get("models") or {}).items():
            if key == mid.lower() or key in [str(a).lower()
                                             for a in (mv or {}).get("aliases", [])]:
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
    # 🔴 --skip is applied AFTER --only (R74; orgemini37flash, R73): the old order ran skip
    # first, and the --only branch then re-enabled every member of a named GROUP - so
    # `--only grok --skip grok420` silently ran grok420, the flag the user typed precisely to
    # exclude it. An explicit exclusion beats an explicit inclusion in every ambiguous pair,
    # for the same reason a deny beats an allow (R57): the safe reading of a contradiction is
    # the one that does not spend.
    if only:
        for c in plan:
            if c not in only:
                plan[c]["enabled"] = False
                plan[c]["why"].append("--only excluded it")
            else:
                # 🔴 THE RESURRECTION WAS SILENT. This branch has always turned a default-OFF
                # channel back on - that is the documented opt-in path - but it wrote nothing into
                # `why`, so the one line in the plan that would have said «this channel does not
                # normally run and something asked for it» did not exist. The route path prints
                # exactly that sentence ("named explicitly (overrides default-off)"); the flag path
                # did not, and the flag path is the one an agent session uses. Same defect shape as
                # the two selection paths disagreeing on 2026-08-14, one field over: they agreed on
                # the ACTION and disagreed on the EXPLANATION, which is the half a human reads.
                if not plan[c]["enabled"] and not plan[c].get("default_enabled", True):
                    plan[c]["why"].append("--only named it explicitly (overrides default-off)")
                plan[c]["enabled"] = True
    for c in skip:
        plan[c]["enabled"] = False
        plan[c]["why"].append("--skip")
    return plan


def _group_alias_words(reg):
    """Every lowercase word that names a GROUP rather than a single channel."""
    out = set()
    for gname, g in (reg.get("groups") or {}).items():
        if gname.startswith("_") or not isinstance(g, dict):
            continue
        out.add(gname.lower())
        out.update(str(a).lower().replace("ё", "е") for a in g.get("aliases") or [])
    return out


def explicitly_named(reg, route=None, only=None, sets=None):
    """
    The channels the human named BY THEIR OWN NAME - never through a group.

    🔴 THIS INFORMATION IS DESTROYED EVERYWHERE ELSE, ON PURPOSE. `canon_channel` maps a group
    word to its member channels and `_expand_groups` rewrites a group token into channel tokens,
    both of them precisely so that every downstream branch can stop caring about the difference.
    By the time a plan exists, `--only openrouter` and `--only terra` are the same object. This
    function is the one place that still knows, and it therefore has to be computed from the
    WORDS rather than recovered from the plan.
    """
    named = set()
    gwords = _group_alias_words(reg)
    for spec in sets or []:
        nm = str(spec).split("=", 1)[0].strip()
        if nm.lower().replace("ё", "е") not in gwords:
            named.update(canon_channel_safe(reg, nm))
    for nm in only or []:
        if str(nm).strip().lower().replace("ё", "е") not in gwords:
            named.update(canon_channel_safe(reg, nm))
    if route:
        # Scanned WITHOUT _expand_groups: a group token stays a group token and is not counted.
        for tok in _scan(route, alias_index(reg)):
            if tok[1] != "entity":
                continue
            # 🔴 BOTH ENTITY KINDS CARRY THE CHANNEL IN SLOT 1; the model id is in slot 2.
            # ('model', 'orgpt56terrapro', 'openai/gpt-5.6-terra-pro'). The first draft here read
            # slot 1 as the model id for kind=='model' and searched the registry for it, which
            # found nothing - so «Запусти все, включая Terra pro», Igor's own authorising
            # sentence, resolved the channel and then did not count it as named, and Terra
            # stayed off. Naming a channel by its MODEL is naming it MORE precisely, not less.
            if tok[2][0] in ("channel", "model"):
                named.add(tok[2][1])
    return named


def apply_explicit_only(plan, reg, named):
    """
    A channel marked `explicit_only` runs ONLY when the human named it. Never via a group.

    🔴 IGOR, R43: «Terra Pro не тестируй и он не должен запускаться, без явного ... только если
    явно назовут: Terra». `enabled: false` was ALREADY true of that channel and was not enough,
    and the gap is worth stating precisely because it is invisible from the channel's own block:
    `--only <group>` deliberately RESURRECTS a default-off channel - that is the documented
    opt-in path - and orgpt56terrapro sits in TWO groups, `openrouter` (aliases openrouter,
    опенроутер, **or**) and `openai` (aliases openai, gpt, гпт, chatgpt, чатгпт). So «--only
    openrouter», «--only gpt» or a route saying «только openrouter» would each have woken the one
    channel in this registry with a measured $12.08 runaway, and the plan would have called it an
    explicit opt-in because at that point in the pipeline it could no longer tell.

    Filtering DOWN is safe to do silently; this is not silent. The whole point of the change is
    that a word which used to include this channel now does not, and a human who typed that word
    is entitled to see the difference. Hence a line in `why` AND a warning collected for the plan.
    """
    for cname, p in plan.items():
        ch = reg["channels"].get(cname) or {}
        hard = bool(ch.get("explicit_only"))
        # 🔴🔴 THE SAME RULE, GENERALISED - AND IT CLOSED A MONEY BUG NOBODY HAD LOOKED FOR.
        # Testing Igor's own phrasings, «запусти только грок» resolved to TWO channels: grok420
        # (the direct xAI key) and orgrok420 (the OpenRouter twin, `enabled: false` here because
        # its `distribution` is `kit`). The group word woke a channel that exists for people who
        # do NOT have the direct key, so this machine would have paid OpenRouter's margin for a
        # second copy of an answer it was already buying directly. Same for «только mimo» and
        # «только gemini» (which also woke orgemini36flash).
        #
        # The registry already contains this exact argument - `_panels_doc` explains that a panel
        # must never resurrect, because `enabled` is what package.py flips per distribution and
        # resurrecting means «paying twice for one voice». That reasoning was written about
        # panels and never carried across to groups, which had the identical shape. Naming the
        # channel still works, in both flag and prose form; only the GROUP word stops doing it.
        off_by_default = not p.get("default_enabled", True)
        if not p.get("enabled") or not (hard or off_by_default):
            continue
        if cname in named:
            p["why"].append("named directly, which is the only way to start a channel that is "
                            "off by default")
            continue
        p["enabled"] = False
        p["why"].append(("explicit_only: " if hard else "")
                        + "NOT named directly - a group, a panel or a default cannot start "
                          "a channel that is off by default")
        reg.setdefault("_explicit_only_blocked" if hard else "_default_off_blocked",
                       []).append(cname)
    return plan


def apply_panel(plan, reg, panel):
    """
    Narrow the plan to one panel. FILTERS DOWN ONLY - it never enables a channel.

    🔴 THE ASYMMETRY WITH `--only` IS THE WHOLE POINT. `--only` resurrects a default-off channel
    by design; a panel must not, because `enabled` is precisely the field package.py flips when
    it generates the kit. A panel that could enable would mean `--panel cheap` runs the direct
    vendor channels in a kit where the user has no such keys, and runs the OpenRouter twins here
    where the direct ones are already running - paying twice for one voice. So this loop only
    ever turns things OFF, and a channel already off stays off with no extra `why` line, because
    the reason it is off is the one that was already recorded.
    """
    members = panel_members(reg, panel)
    for c, p in plan.items():
        if p["enabled"] and c not in members:
            p["enabled"] = False
            p["why"].append("outside the %r panel (this channel is declared %r)"
                            % (panel, p.get("panel")))
    return plan


def resolve(reg, route=None, only=None, skip=None, sets=None, tier=None, panel=None):
    plan = initial_plan(reg)
    # Computed BEFORE anything expands a group, and kept for the explicit_only gate below. The
    # route is re-scanned there rather than passed through, because extract_panel is about to
    # rewrite it and the panel word is not a channel name.
    named_directly = explicitly_named(reg, route=route, only=only, sets=sets)
    reg["_tier_asked"] = tier
    tier = canon_tier(reg, tier)
    reg["_tier_chosen"] = tier
    reg.pop("_explicit_only_blocked", None)
    # PANEL FIRST, so that a name given afterwards can still put a channel back. «дешевая
    # панель, плюс codex» is a sentence a human will write, and it can only mean what it says
    # if the narrowing happens before the naming; the reverse order would resolve --panel cheap
    # --only codex to an EMPTY round rather than to codex. Note --only stays exclusive - it
    # means "these and nothing else", so `--panel cheap --only codex` runs codex alone. The
    # additive form is the route's ADD mode, not a flag.
    # 🔴🔴 `--panel X` AGAINST A REGISTRY WITH NO PANELS WAS ACCEPTED AND IGNORED. argparse gets
    # its `choices` from the registry file and falls back to None when that file cannot be read,
    # so the flag stayed spellable while `if reg.get("panels")` skipped every filter - a flag
    # accepted, a narrowing not applied, and the round running EVERY channel. That is the
    # decorative-knob defect this repository has now recorded four times, written by the same
    # hand that documented it, in the same round. Two reviewers, independently. Refuse instead.
    if panel and not reg.get("panels"):
        raise RouteError(
            "--panel %r was given but this registry defines no panels at all, so there is "
            "nothing to narrow to. Accepting it silently would run EVERY enabled channel while "
            "looking like a restriction. Add a `panels` object to channels.json, or drop the "
            "flag." % panel)
    if reg.get("panels"):
        route_panel, route = extract_panel(reg, route)
        if route_panel and panel and route_panel != panel:
            raise RouteError(
                "the --panel flag says %r and the route says %r. Neither wins by default - the "
                "same rule as a route and a flag disagreeing about a channel. Pass one of them."
                % (panel, route_panel))
        chosen = panel or route_panel or reg.get("default_panel")
        plan = apply_panel(plan, reg, chosen)
        reg["_panel_chosen"] = chosen
        reg["_panel_from_route"] = bool(route_panel and not panel)
        # 🔴 «запусти на дешевой» IS A COMPLETE INSTRUCTION AND USED TO BE A ROUTE ERROR.
        # After the panel word is consumed the leftover is «запусти на» - filler, no channel -
        # and apply_route's job is to refuse a route that names nothing, so it refused. The
        # refusal was right for its own contract and wrong for the sentence. What distinguishes
        # the two cases is not whether an entity survived but whether an INSTRUCTION did: filler
        # after a panel word means nothing more was asked, while a marker («без …», «только …»)
        # with no entity behind it means a channel name was meant and was misspelled - and
        # swallowing that would silently run a channel the human just excluded. So: markers
        # still raise, filler does not.
        if route_panel and route and not _route_has_entity(reg, route):
            stream = _expand_groups(_scan(route, alias_index(reg)), reg)
            markers = [w for _, k, w in stream if k in ("neg", "subst", "only", "add")]
            if markers:
                raise RouteError(
                    "after the panel word, %r is left and it contains an instruction (%s) with "
                    "no channel behind it. Something was meant here - most likely a misspelt "
                    "channel name - and running the panel while ignoring it would silently "
                    "include or exclude the wrong reviewer."
                    % (route, ", ".join("%r" % m.strip() for m in markers)))
            route = None
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

    # AFTER every selection path has had its say, and before the tier decorates anything: a
    # channel that may only be started by name is either named or it is off. Placed here rather
    # than inside apply_flags/apply_route so that adding a THIRD selection path later cannot
    # reopen the hole - the gate does not care which path enabled the channel.
    plan = apply_explicit_only(plan, reg, named_directly)

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
            elif p.get("kind") == "grokcli":
                # Same shape as codex: a subscription CLI whose depth is pinned in its own block
                # at the top of the vendor's ladder, so the tier contributes wall-clock only.
                # Unlike codex, the flag here was proved to move the meter (low [828,1697] vs
                # xhigh [1918,4089] reasoning tokens, disjoint), so `effort` is load-bearing and
                # the note says which rung is being bought.
                p["timeout"] = t.get("grokcli_timeout", p.get("timeout") or "40m")
                p["_tier_note"] = ("timeout %s only - effort stays %s, the top of this model's "
                                   "own ladder and proved to move reasoning_tokens"
                                   % (p["timeout"], p.get("effort")))
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
        # 🔴 THE TIER STOPPED TOUCHING THESE CHANNELS IN R43, AND THE NOTE HAD TO CHANGE WITH IT.
        # It used to multiply `reasoning.max_tokens` and `fetch_tool.max_calls` by the tier's two
        # scales; with one tier a multiplier is an obfuscated constant, so both are gone and the
        # depth is declared in each channel's own block at that vendor's ceiling. What the note
        # prints now is therefore the RESOLVED DEPTH rather than a delta - because with nothing to
        # compare against, "nothing this tier can raise on this channel" is technically true, says
        # nothing a reader can act on, and reads like a limitation rather than like a ceiling.
            elif p.get("kind") in ("openrouter", "oai"):
                r = p.get("reasoning") if isinstance(p.get("reasoning"), dict) else {}
                if r.get("effort"):
                    depth = "reasoning effort %s (this model's declared ceiling)" % r["effort"]
                elif r.get("max_tokens"):
                    depth = "reasoning budget %s tokens" % r["max_tokens"]
                else:
                    depth = "no reasoning knob on this model"
                room = ("; %s tokens for reasoning+answer together" % p["max_tokens"]
                        ) if p.get("max_tokens") else ""
                p["_tier_note"] = depth + room
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
                        p["_tier_note"] = ("thinking_level %s, the top of %s - this channel's "
                                           "ceiling" % (lvl, "|".join(ladder)))
                    else:
                        p["_tier_note"] = "thinking_level=%s" % lvl
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
                p["_tier_note"] = ("no depth knob on THIS MODEL (other xAI models have one); "
                                   "%s tokens for reasoning+answer together, timeout %s"
                                   % (p.get("max_tokens"), p["timeout"]))
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
        # 🔴 fallback_model (SINGULAR, the Spark HTTP Contributor→Standard retry) was missing
        # from this tuple from the day R62 shipped the feature: the dispatcher reads
        # p.get("fallback_model"), the plan never carried it, so the registry's one documented
        # auto-fallback could never fire - decorative config in its purest form, found by
        # grokbuild in R73 (the R62 test called call_http_reviewer directly and so tested the
        # function, not the wiring). `read_order`/`reading_note`/`must_read` are deliberately
        # NOT here: those are reader-side fields stamped from the registry at the handoff site
        # (one home) - copying them into the plan too would be a second home that drifts.
        # 🔴 supported_efforts (R75): the THIRD field found dead at this allow-list in three
        # consecutive rounds - read_order (R73), fallback_model (R74), now the R43 effort
        # ladder, which echocheck reads from the PLAN slot. Without the copy its per-channel
        # arms silently degraded to the ["low","medium","high"] literal R43 existed to kill.
        # Caught by the R75 suite's derived-candidate test, not by a review.
        for extra in ("reasoning", "max_tokens", "toolsets", "role", "fetch_tool", "tools",
                      "provider", "provider_route", "prompt_suffix", "distribution",
                      "thinking_level", "thinking_levels", "fallback_models",
                      "fallback_model", "supported_efforts"):
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
    if kind == "grokcli":
        return ("web: the CLI's built-in web_search and web_fetch, ON by default - the flag is "
                "--disable-web-search, so access here is opt-OUT, not opt-in. The vendor runs the "
                "loop and opens the pages, so this channel's grounding is its own claim and the "
                "harness fetches nothing to check it against")
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
    # WHICH REVIEWERS ARE IN THE ROOM, printed above the list of them. Separate from `--tier`,
    # which is how deep each one goes: two axes, two lines, because collapsing them into one
    # word is what made `strategic` and `deep` indistinguishable until 2026-08-08.
    chosen = reg.get("_panel_chosen")
    panels = reg.get("panels") or {}
    if chosen and panels:
        admits = set(panels[chosen]["includes"])
        others = sorted(set(panels) - {chosen})
        how = (" (named in your route text)" if reg.get("_panel_from_route")
               else " (the default)" if chosen == reg.get("default_panel")
               else " (from --panel)")
        lines.append("  panel: %s%s - who is in the room. (--tier is how deep each of them goes.)"
                     % (chosen, how))
        for other in others:
            # What the OTHER panel would do, by name, every run. A cheaper option mentioned only
            # when someone already knows to ask for it is an option nobody takes; and the
            # reverse - what `cheap` gives up - has to be equally visible, because a panel that
            # silently reviews LESS is as much a defect as one that silently spends more.
            oadmits = set(panels[other]["includes"])
            gain = sorted(c2 for c2, p2 in plan.items()
                          if reg["channels"][c2].get("panel") in oadmits - admits
                          and p2.get("default_enabled"))
            lose = sorted(c2 for c2, p2 in plan.items()
                          if reg["channels"][c2].get("panel") in admits - oadmits
                          and p2.get("default_enabled"))
            if gain:
                lines.append("           - --panel %s would ALSO run: %s"
                             % (other, ", ".join(gain)))
            if lose:
                lines.append("           - --panel %s would DROP: %s" % (other, ", ".join(lose)))
        for cname in reg.get("_panel_defaulted") or []:
            lines.append("           - 🔴 %s declares no panel; defaulted to %r. Shipped channels "
                         "always declare one, so this is a channel your settings file added."
                         % (cname, reg.get("default_panel")))
        lines.append("-" * 78)
    # 🔴 THE TIER LINE PRINTS THE WORD THAT WAS TYPED AND THE TIER THAT RESOLVED, even when they
    # are the same. A collapse from two names to one is exactly the moment when a human keeps
    # typing the old word for months; saying nothing would let `--tier deep` look like it still
    # selects something. The second sentence is what stops the first from being a boast.
    if reg.get("_tier_chosen"):
        asked, got = reg.get("_tier_asked"), reg["_tier_chosen"]
        extra = ("" if str(asked).lower() == got.lower() else
                 " (you asked for %r; it is an alias)" % asked)
        lines.append("  tier: %s%s - how deep each reviewer goes. There is exactly ONE tier since "
                     "2026-08-15" % (got, extra))
        lines.append("           and it is every channel's own ceiling, so this is not a choice "
                     "and cannot be lowered.")
        lines.append("           A panel changes WHO is in the room; it never changes this.")
        lines.append("-" * 78)
    # Channels that a word ALMOST reached. Printed by name, because the whole point of the change
    # is that «--only openrouter» now means one channel fewer than it did, and a set that silently
    # shrinks is indistinguishable from a set that did not.
    blocked = reg.get("_explicit_only_blocked") or []
    if blocked:
        for cname in blocked:
            lines.append("  🔴 %s was NOT started: it runs only when named directly (%s), never "
                         "through a group," % (cname, "/".join(
                             (reg["channels"][cname].get("aliases") or [cname])[:3])))
            lines.append("     a panel or a default. A group word that used to include it - e.g. "
                         "`--only openrouter` -")
            lines.append("     silently woke it before 2026-08-15.")
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
        # 🔴 `role` WAS A DECORATIVE FIELD UNTIL 2026-08-15 (round 42). Four channels declare it
        # in channels.json, `_decorate` copied it into the plan, and NOTHING then read it - not
        # the dispatcher, not this printout, not report.py. The exact shape of `channels.spark.
        # model` and of the `tools` passthrough this file already records twice: a registry key
        # that reads as configuration and is prose. Printing it is the whole fix, because what
        # it was always FOR is a human deciding who should be in the room - and that decision
        # became a flag in this round, which is what made the omission finally cost something:
        # `--panel cheap` drops kimik3, the only `role: code` seat, and nothing said so.
        lines.append("  [%s] %-12s %-32s model=%s%s%s" % (
            mark, c, p["label"], shown,
            ("  effort=%s" % p["effort"]) if p.get("effort") else "",
            ("  role=%s" % p["role"]) if p.get("role") else ""))
        for w in p["why"]:
            lines.append("           - %s" % w)
        # 🔴 THE PLAN EXPLAINED EVERY SKIP EXCEPT THE ONES THAT MATTER MOST. Found R54 by reading
        # the printout rather than the code: a channel filtered out by a panel says so, a channel
        # blocked by explicit_only says so - but a channel that is simply `enabled: false` in the
        # registry accumulates NO `why` at all, because every writer of that list runs only over
        # channels that were still enabled when it ran. So three channels printed a bare `[skip]`
        # with no reason, one of them the most expensive in the registry, and a reader could not
        # tell "off by default" from "filtered out by your flags" from "broken". Printed here, at
        # display time rather than in the plan data, so no decision depends on it.
        if not p["enabled"] and not p["why"]:
            lines.append("           - off by default in the registry (`enabled: false`)%s"
                         % (" - and `explicit_only`, so ONLY its own name starts it: no group, "
                            "no panel, no default" if p.get("explicit_only")
                            else "; --only or a group word can still start it"))
        if p["enabled"] and cost == "expensive":
            lines.append("           - cost: EXPENSIVE channel")
        # 🔴 A FALLBACK NOBODY IS TOLD ABOUT IS A SILENT MODEL SUBSTITUTION, and this registry has
        # already paid for one: `--set spark12cont=muse-spark-1.2` ran a whole round on the wrong
        # checkpoint, and the only reason anyone ever found out was the «⚠ MODEL OVERRIDDEN» line.
        # A fallback chain is that same substitution with the vendor pulling the lever instead of a
        # human, so it is printed BEFORE the spend, by name, and the answer records which model
        # actually served. The money half matters too: on the one channel that has this today the
        # primary is free and the fallback is metered, so «it worked» and «it was free» stopped
        # being the same statement.
        if p["enabled"] and p.get("fallback_models"):
            lines.append("           - fallback: if %s errors, OpenRouter tries %s and bills for "
                         "whichever answers. `model_served` in the report says which one did."
                         % (p.get("model"), " then ".join(p["fallback_models"])))
        # 🔴 THE PRICE LINE USED TO KEY ON THE WORD `expensive`, WHICH ONLY CODEX CARRIES - so the
        # channel that actually ran away printed nothing at all. Measured 2026-08-14: orgpt56terrapro
        # is tagged `metered`, the same word as a $0.10 channel, and billed $12.08 in one round while
        # its plan block showed data/web/tier and no money. Keying on a declared `spend_guard`
        # instead of on a cost WORD is the fix: the guard is the thing that knows a number, and a
        # channel that declares one says so here whatever adjective it also carries.
        sg = p.get("spend_guard") or {}
        if p["enabled"] and sg:
            if sg.get("max_usd_per_review") is not None:
                # 🔴 THE CONDITION IS PART OF THE PROMISE. orgemini37flash, reviewing this the day
                # it shipped: the first wording said "enforced from the vendor's own returned cost
                # meter" for ANY channel declaring a guard, while the enforcement lives only in
                # call_oai_reviewer and only fires when the vendor actually returns `usage.cost`.
                # On a transport that reports no price the line would have promised a ceiling that
                # cannot exist - a plan that reassures is worse than a plan that says nothing. No
                # channel is in that state today; selftest now asserts it stays that way, and this
                # line states the dependency rather than relying on the assertion staying true.
                lines.append("           - spend: ceiling $%.2f per review - a STOP, not a depth "
                             "cap. Enforced only while the vendor returns a cost meter with each "
                             "response; a transport that reports no price cannot be stopped this "
                             "way." % float(sg["max_usd_per_review"]))
            if sg.get("measured_usd"):
                lines.append("           - spend: measured %s" % sg["measured_usd"])
            if sg.get("requires_ack"):
                lines.append("           - spend: THIS CHANNEL NEEDS --accept-spend %s (or "
                             "--accept-spend all). Selecting it is not the same act as "
                             "authorising its bill; --dry-run never needs the flag." % c)
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
    # 🔴 HOW MANY COMPANIES, NOT HOW MANY CHANNELS. A panel's product is disagreement between
    # independent voices, and this harness reaches ONE vendor by up to six transports - three
    # Geminis through the agy CLI, two through Google directly, one through OpenRouter. When
    # those six agree, that is one opinion reported six times, and a channel count presents it
    # as six. The tally is printed rather than described because the number moves whenever a
    # channel is skipped, and a paragraph in a config file cannot know what --skip was passed.
    if live:
        tally = {}
        for c in live:
            v = channel_vendor(reg, c)
            tally[v] = tally.get(v, 0) + 1
        ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("  from %d vendor(s): %s"
                     % (len(tally), ", ".join("%s %d" % kv for kv in ranked)))
        # 🔴 THE SHARE IS PRINTED ALWAYS; ONLY THE ALARM IS CONDITIONAL. First draft printed the
        # concentration line only above 50%, which meant moving from `cheap` (6/11 = 55%) to
        # `standard` (6/15 = 40%) made the warning DISAPPEAR while the same vendor still held
        # three times the next bloc - and a warning that vanishes reads as "fixed". A reviewer
        # named exactly that. So the number is always visible and only the 🔴 escalates.
        top, n = ranked[0]
        if n > 1:
            lines.append("           - largest bloc: %s holds %d of %d seats (%.0f%%)%s"
                         % (top, n, len(live), 100.0 * n / len(live),
                            ". 🔴 Where those agree, treat it as one voice repeated, not as "
                            "corroboration." if n * 2 >= len(live) else "."))
    if not live:
        lines.append("  !! every channel is disabled - nothing would run")
    return "\n".join(lines)


def _cli_default(path, key, fallback=None):
    """A default declared in the registry, so the flag and the file cannot disagree."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get(key) or fallback
    except Exception:                                     # noqa: BLE001
        return fallback


def _cli_choices(path, key, with_aliases=False):
    """
    Names declared under `key` in the registry, for an argparse `choices`, or None.

    🔴 THIS EXISTS BECAUSE `--tier` HERE HAD NO `choices` AT ALL, AND THE REGISTRY CLAIMED
    OTHERWISE. `_tiers_doc` in channels.json says «`--tier quick` is now an argparse error naming
    the two that exist, because a silently-accepted dead tier is the decorative-knob defect this
    file keeps recording» - and that was true of orchestrate.py and false of this script, which
    accepted `--tier quick` and printed a plan resolved at the default. One claim, two programs,
    verified in one of them. Found 2026-08-15 by a reviewer who checked the sentence against
    both files instead of against the one it was written about.

    Returns None rather than raising when the registry cannot be read, so that a corrupt file
    still gets you a readable RouteError from the loader a moment later instead of an argparse
    traceback about choices.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh).get(key)
        if not isinstance(d, dict):
            return None
        names = [k for k in d if not k.startswith("_")]
        if with_aliases:
            # 🔴 WITHOUT THIS, R43's TIER COLLAPSE WOULD HAVE BROKEN EVERY STORED COMMAND.
            # `strategic` and `deep` became aliases of the single `max` tier; argparse validates
            # against `choices` BEFORE any of our code runs, so a choices list built from the
            # tier KEYS alone would reject `--tier deep` with a bare argparse error and no
            # explanation, in the other project's scripts, at the worst possible moment.
            for k in names[:]:
                for a in (d[k] or {}).get("aliases") or []:
                    if a not in names:
                        names.append(a)
        return sorted(names) or None
    except Exception:                                     # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--route", help="free-text override, e.g. 'не используй Spark'")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--skip", nargs="*")
    ap.add_argument("--set", dest="sets", nargs="*")
    ap.add_argument("--tier", default=_cli_default(DEFAULT_REGISTRY, "default_tier", "max"),
                    choices=_cli_choices(DEFAULT_REGISTRY, "tiers", with_aliases=True),
                    help="depth per channel. Since 2026-08-15 there is exactly ONE tier and it "
                         "is the ceiling; strategic|deep still parse as aliases of it. Choices "
                         "and the default both come from the registry")
    ap.add_argument("--panel", default=None,
                    choices=_cli_choices(DEFAULT_REGISTRY, "panels"),
                    help="which reviewers are in the room. Orthogonal to --tier, which is how "
                         "deep each of them goes. Filters DOWN only: unlike --only it never "
                         "enables a channel the registry has off")
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
        plan = resolve(reg, route=a.route, only=a.only, skip=a.skip, sets=a.sets, tier=a.tier,
                       panel=a.panel)
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
