#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py - does this harness BEHAVE correctly? Costs nothing; calls no vendor.

    python selftest.py            # all suites
    python selftest.py --quiet    # only the summary line

doctor.py answers "is my machine set up?". This answers "does the code still do what it
promises?" - which is the question that matters after an edit, an upgrade, or an AI-suggested
fix. Every check here exists because the corresponding behaviour broke at least once.

The three properties under test are the ones a user actually depends on:

  1. PARTIAL INSTALLS ARE NORMAL. Missing API key, missing CLI, missing both - the run must
     continue with whatever is left and say plainly what is unavailable. Never a traceback.
  2. "ONLY THESE MODELS" MUST BE OBEYED EXACTLY, including through aliases and free-text
     routing, and a contradiction must stop rather than guess.
  3. NOTHING SECRET-SHAPED MAY REACH THE CONSOLE, THE RUN LOG, OR diagnostics.json.

Exit 0 = all passed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PY = sys.executable
NOPE = str(Path(tempfile.gettempdir()) / "orch-selftest" /
           ("nonexistent.exe" if os.name == "nt" else "nonexistent"))
BRIEF = "Verify one trivial claim: the sky appears blue. One sentence.\n"

_results: list[tuple[bool, str, str]] = []
_quiet = False


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    if not _quiet:
        print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    if not _quiet:
        print("\n" + "=" * 78 + "\n" + title + "\n" + "=" * 78)


def run_cli(args, env_extra=None, timeout=180):
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "brief.md"
        bp.write_text(BRIEF, encoding="utf-8")
        cmd = [PY, str(HERE / "orchestrate.py"), "--brief", str(bp),
               "--out", str(Path(td) / "rev")] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)


def blob_of(p):
    return (p.stdout or "") + (p.stderr or "")


# =================================================================================================
def suite_degradation():
    section("1. A partial install must degrade, not crash")

    for chan in ("codex", "agy"):
        var = {"codex": "CODEX_BIN", "agy": "AGY_BIN"}[chan]
        p = run_cli(["--only", chan], {var: NOPE})
        b = blob_of(p)
        check("Traceback (most recent call last)" not in b, f"{chan} missing: no traceback")
        check("binary not found" in b, f"{chan} missing: the missing binary is named")
        check(p.returncode in (0, 1), f"{chan} missing: clean exit", f"exit={p.returncode}")

    p = run_cli(["--only", "codex", "agy"], {"CODEX_BIN": NOPE, "AGY_BIN": NOPE})
    b = blob_of(p)
    check("Traceback (most recent call last)" not in b, "both CLIs missing: no traceback")
    check(b.count("binary not found") >= 2, "both CLIs missing: both named")
    check(p.returncode in (0, 1), "both CLIs missing: clean exit", f"exit={p.returncode}")

    # The API key is read from the environment AND, on Windows, from the registry. Emptying the
    # env var alone is not enough to simulate its absence - the registry fallback would find the
    # real key and spend money. Both sources are stubbed out in a child process.
    probe = (
        "import sys, types, os\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "os.environ.pop('MODEL_API_KEY', None)\n"
        "fake = types.ModuleType('winreg')\n"
        "fake.HKEY_CURRENT_USER = 0\n"
        "def _boom(*a, **k): raise OSError('stubbed')\n"
        "fake.OpenKey = _boom; fake.QueryValueEx = _boom\n"
        "sys.modules['winreg'] = fake\n"
        "import orchestrate as o\n"
        "r = o.call_http_reviewer('brief', 'system', 'strategic', 'REVIEW-COMPLETE')\n"
        "print('OK=%r ERR=%r' % (r.get('ok'), r.get('error')))\n"
        "print('PRE=%r' % list(o.channel_preflight({'spark11'}, '.')))\n"
        # Same fault, a channel name the code has never seen - `newvoice` is in no registry and
        # never will be. The advice must name THAT channel: four channels now share two shared
        # transports, so "run with --skip spark" is actively wrong guidance when the one that
        # failed is a different voice on the same endpoint.
        "print('PRE2=%r' % list(o.channel_preflight({'newvoice'}, '.', {'newvoice': 'http'})))\n"
    )
    pf = Path(tempfile.gettempdir()) / "orch_selftest_nokey.py"
    pf.write_text(probe, encoding="utf-8")
    try:
        p = subprocess.run([PY, str(pf)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        b = blob_of(p)
        check("Traceback (most recent call last)" not in b, "no API key: does not raise")
        check("OK=False" in b, "no API key: returns a failed result instead of throwing")
        check("MODEL_API_KEY not set" in b, "no API key: the variable is named")
        check("--skip spark11" in b, "no API key: tells the user how to run without it")
        check("--skip newvoice" in b,
              "no API key: the advice names the channel that failed, not a hard-coded one")
        check("newvoice: MODEL_API_KEY is not set" in b,
              "preflight checks a channel it has never heard of, by kind")
    finally:
        pf.unlink(missing_ok=True)


def suite_routing():
    section("2. 'Only these models' must be obeyed exactly")

    # DERIVED from the registry, never listed here. This suite used to hardcode {spark, codex, agy};
    # when a fourth channel was added to channels.json on 2026-08-01 the four EXCLUSION cases went
    # red while the tool was working correctly, because "everything except spark" had been frozen
    # into the test as a literal pair. Channel names have one home - channels.json - and a test that
    # copies them is a second home that rots exactly like any other. An inclusion case (--only X)
    # can name X, because X is the input; an exclusion case must compute the complement.
    # `_`-prefixed keys are prose, not entries - the file's own convention, honoured here too
    # because this suite reads the raw JSON on purpose rather than through the loader it tests.
    with open(HERE / "channels.json", encoding="utf-8") as fh:
        _CHANS = {k: v for k, v in json.load(fh)["channels"].items() if not k.startswith("_")}
    # 🔴 `enabled` IS NOT ENOUGH - THE DEFAULT PANEL IS PART OF "WHAT A NO-FLAG RUN LAUNCHES",
    # and this set was `enabled` alone until 2026-08-16, when `default_panel` moved from
    # "standard" to "cheap" and twelve cases went red against correct code. They were not testing
    # the router; they were testing that nobody had changed a setting a human is expected to
    # change - this suite's own recurring defect. Derived from `default_panel` now, so the same
    # flip in either direction is a no-op here.
    _RAW = json.load(open(HERE / "channels.json", encoding="utf-8"))
    _DEFAULT_INCLUDES = set(_RAW["panels"][_RAW["default_panel"]]["includes"])
    ENABLED_ANY_PANEL = {k for k, v in _CHANS.items() if v.get("enabled", True)}
    ALL = {k for k, v in _CHANS.items()
           if v.get("enabled", True) and v.get("panel") in _DEFAULT_INCLUDES}

    def panel_set(name):
        """Enabled channels a NAMED panel admits - for cases whose route names one in prose."""
        inc = set(_RAW["panels"][name]["includes"])
        return {k for k, v in _CHANS.items()
                if v.get("enabled", True) and v.get("panel") in inc}

    EXISTS = set(_CHANS)
    check(len(ALL) >= 3, "the registry declares at least the three documented channels",
          f"registry={sorted(ALL)}")

    # 🔴 `distribution` MUST AGREE WITH `enabled`, OR IT IS A FIELD THAT DESCRIBES NOTHING.
    # Added 2026-08-08 with the local/kit split. Two fields naming one decision is how a decorative
    # field is born - `enabled` is what the dispatcher reads and `distribution` is what package.py
    # reads, so nothing would ever notice them disagreeing: the local run would quietly include a
    # channel meant for the kit, or drop one meant for here, and both look like a working config.
    # This is the error signal. distribution "local" => runs here; "kit" => does not.
    # 🔴 AND IT ONLY HOLDS IN ONE DIRECTION PER TREE. Found 2026-08-08 by running this suite
    # INSIDE the built kit for the first time: all six local/kit channels failed, against a build
    # that was doing exactly what it is supposed to do. `package.py` FLIPS these flags on the way
    # out - that flip is the feature - so the shipped tree's correct state is the mirror image of
    # the working copy's, and a check that knows only one of them calls the other broken. Same
    # shape as the `distribution` field it is guarding: an expectation with no way to tell which
    # world it is in. The tree says which world it is in - a shipped tree carries VERSION.
    kit_tree = os.path.isfile(os.path.join(HERE, "VERSION"))
    bad = []
    for c, v in sorted(_CHANS.items()):
        d = v.get("distribution", "both")
        on = v.get("enabled", True)
        want_on = {"both": None, "local": not kit_tree, "kit": kit_tree}.get(d, "?")
        if want_on == "?":
            bad.append("%s: unknown distribution %r" % (c, d))
        elif want_on is not None and on != want_on:
            bad.append("%s: distribution=%s but enabled=%s in %s tree"
                       % (c, d, on, "a shipped" if kit_tree else "the working"))
    check(not bad, "every channel's `distribution` agrees with the `enabled` for THIS tree",
          "; ".join(bad))

    # 🔴 OPT-IN CHANNELS: default-off must be INTENTIONAL, not an accident nobody notices.
    # The check above maps distribution "both" to None and therefore asserts NOTHING about
    # `enabled` for such channels - so a rationed channel could be flipped on (or a normal one
    # flipped off) in either tree and no test would object. Igor's rule for orgpt56terrapro,
    # 2026-08-14: «по дефолту отключена, только если явно скажут ее использовать … если скажут
    # используй все модели, ее не использовать». That is a POLICY with a measured price behind it
    # (~$1.80 a review, ~7x kimik3), so it gets a test rather than a paragraph: turning it on by
    # default should fail here, in both the working copy and a shipped kit.
    # 🔴 R54: the KEY moved with the seat when Igor said «Terra Pro меняем на Sol Pro». Worth one
    # line about why this dict is keyed on a channel name at all, given this file's own rule that a
    # test hard-coding the value a human is meant to change tests the human: the thing under test
    # here is not WHICH model sits in the seat, it is that SOMETHING rationed exists and stays off.
    # The rename made all three of this round's failures loud, which is the behaviour that was
    # wanted - a conditional `if "orgpt56terrapro" in _CHANS` would have skipped silently instead.
    OPT_IN = {"orgpt56solpro": "rationed: predecessor measured ~$1.80/review and this model costs "
                               "25% more per token; strategic questions only"}
    for c, why in sorted(OPT_IN.items()):
        if kit_tree:
            # 🔴 R47, Igor: «Terra Pro модель в репозитории вообще давай отключим (или удалим),
            # а то сотрудники все равно ее запускают. Только у меня локально ее оставь.» The
            # three lock rungs - enabled:false, explicit_only, requires_ack - were each walked
            # BY a determined user, because every rung leaves the channel present and nameable.
            # The only lock that survives naming is absence: package.py now deletes the entry
            # from the published registry (PUBLISH_EXCLUDE_CHANNELS), so in a shipped tree the
            # correct state is NOT THERE, and finding it is a build regression.
            check(c not in _CHANS,
                  "%s is ABSENT from the shipped registry (author-local channel, R47)" % c)
            continue
        if c not in _CHANS:
            check(False, "opt-in channel %s still exists" % c)
            continue
        check(_CHANS[c].get("enabled") is False,
              "%s stays OFF by default (%s)" % (c, why),
              "enabled=%r" % _CHANS[c].get("enabled"))
        # Off-by-default must not mean unreachable: the whole policy is that naming it works.
        #
        # 🔴🔴 `--dry-run` HERE IS A BUG FIX, NOT A STYLE CHOICE, AND IT IS THE SHARPEST THING THIS
        # ROUND FOUND ABOUT ITSELF. Written on 2026-08-14 without it, this line ran the real CLI
        # with a real brief and no dry-run against THE MOST EXPENSIVE CHANNEL IN THE REGISTRY - so
        # every `python selftest.py`, on a machine holding a live OpenRouter key, launched a paid
        # orgpt56terrapro round with web search and the page-fetch tool enabled. A test suite that
        # bills is not a test suite. It was invisible for the same reason the round-34 overspend
        # was: nothing printed a price, so a passing green line and a paid call looked identical.
        # Caught the next day by the spend gate refusing it - the guard fired on its own author.
        p = run_cli(["--only", c, "--dry-run"])
        b = blob_of(p)
        check(p.returncode == 0 and ("running 1 channel(s): %s" % c) in b,
              "%s is still REACHABLE by name despite being off by default" % c,
              b.strip().splitlines()[-1][:120] if b.strip() else "no output")
        check("--dry-run: nothing was called" in b,
              "%s reachability is proved WITHOUT paying for a round" % c)
        # ... and reachable is not the same as authorised. Two separate acts since 1.20.0.
        p = run_cli(["--only", c])
        b = blob_of(p)
        check(p.returncode == 2 and "REFUSING TO SPEND" in b,
              "%s selected but not authorised -> hard refusal, exit 2" % c,
              "rc=%s" % p.returncode)
        check("--accept-spend %s" % c in b,
              "the refusal names the exact flag that would authorise it")
        p = run_cli(["--only", c, "--accept-spend", c, "--dry-run"])
        b = blob_of(p)
        check(p.returncode == 0 and "REFUSING TO SPEND" not in b,
              "CONTROL: with --accept-spend the gate is satisfied", "rc=%s" % p.returncode)

    def without(*names):
        return ALL - set(names)

    # GROUPS are derived too. `--only agy` has to keep working now that one Gemini channel became
    # two, and the set it expands to is a registry fact - writing {agy31pro, agy36flash} here
    # would freeze today's membership into the test and go red the day a third one is added,
    # which is the same rot the exclusion cases above were rewritten to avoid.
    with open(HERE / "channels.json", encoding="utf-8") as fh:
        _groups_raw = {g: v for g, v in (json.load(fh).get("groups") or {}).items()
                       if not g.startswith("_")}
    GROUPS = {g: set(v["channels"]) for g, v in _groups_raw.items()}

    # 🔴 THE WORD -> GROUP MAP HAS TO BE DERIVED TOO, and not deriving it is what broke this file
    # on 2026-08-07. Membership was already read from the registry - the comment above says why -
    # but the CASES below still spelled out WHICH group a word belongs to: `gemini` was written as
    # GROUPS["agy"] because it was an alias of that group at the time. The day a Gemini appeared on
    # a second transport, `gemini` became its own group covering all three, both cases went red,
    # and the code under test was correct. Half a derivation freezes the other half.
    # 🔴 R43: A GROUP EXPANDS TO ITS MEMBERS AND THEN THE DEFAULT-OFF ONES ARE DROPPED. Until
    # 2026-08-15 `--only <group>` RESURRECTED every disabled member, and testing Igor's own
    # phrasing found what that cost: «запусти только грок» ran grok420 (the direct xAI key) AND
    # orgrok420 (the OpenRouter twin, off here because its `distribution` is `kit`) - two bills
    # for one voice, on a machine that already holds the cheaper key. The registry had the
    # argument written down for PANELS and nobody carried it to groups. Naming the channel still
    # wakes it; the group word no longer does. So the expected set is the members that are
    # ENABLED, which is a registry fact and stays derived.
    def group_of(word):
        """Which channels does a human word expand to, AND actually run? From the registry."""
        for g, v in _groups_raw.items():
            if word == g or word in (v.get("aliases") or []):
                # 🔴 INTERSECTED WITH `ENABLED_ANY_PANEL`, NOT WITH `ALL`. `ALL` is the DEFAULT
                # PANEL's enabled set, and naming a group is an explicit selection that overrides
                # the panel - `--only spark` runs both Sparks even though spark11 is standard-only
                # and the default panel is cheap. Using ALL here made this expect one Spark and
                # get two, i.e. it called correct behaviour a failure. The exclusion cases are
                # unaffected either way: `without()` subtracts from ALL, and subtracting a name
                # that was never in ALL is a no-op.
                return GROUPS[g] & ENABLED_ANY_PANEL
        raise AssertionError("no group answers to %r - this test names a word the registry lost"
                             % word)

    def group_members_all(word):
        """Every member the group NAMES, enabled or not - for the non-resurrection assertion."""
        for g, v in _groups_raw.items():
            if word == g or word in (v.get("aliases") or []):
                return GROUPS[g]
        raise AssertionError("no group answers to %r" % word)

    # Properties, not counts. `len(GROUPS["agy"]) == 2` was true for exactly one day.
    check(all(len(v) >= 2 for v in GROUPS.values()),
          "every group expands to at least two channels (a group of one is just a channel)",
          "sizes=%s" % {g: len(v) for g, v in GROUPS.items()})
    # 🔴 EXISTS, not ENABLED. This asserted membership of `ALL` (the enabled set) until
    # 2026-08-08, which was the same thing while every channel ran here. With the local/kit split
    # three channels are deliberately off locally and still belong to their groups - `--only
    # gemini` is precisely how a human turns one of them back on, so a group naming a disabled
    # channel is the feature, not the fault. The real invariant is that a group never names a
    # channel that does not exist, and that one still holds.
    check(all(c in EXISTS for v in GROUPS.values() for c in v),
          "every channel named by a group exists in the registry", f"groups={GROUPS}")
    # 🔴 THE NON-RESURRECTION INVARIANT, OVER EVERY GROUP x EVERY DEFAULT-OFF CHANNEL - not over
    # the one case that was found. A group word must never start a channel that ships disabled,
    # because `enabled` is what package.py flips per `distribution`: waking one here means paying
    # OpenRouter for a voice this machine already buys directly, and waking one in the kit means
    # calling a vendor whose key the user does not have. Naming the channel is tested separately
    # and must still work - a lock nobody can open is an outage, not a safeguard.
    # 🔴 «DEFAULT-OFF» MEANS `enabled: false`, NOT «outside the default panel». Written as
    # `EXISTS - ALL` until 2026-08-16, when ALL became panel-aware and this started claiming that
    # `--only spark` "resurrected" spark11 - a channel that is enabled, costs the same either
    # way, and is simply not on the cheap panel. The invariant being protected is about the
    # `enabled` flag package.py flips per `distribution`; panel membership is a different axis
    # and an explicit `--only` is supposed to cross it.
    _off = sorted(EXISTS - ENABLED_ANY_PANEL)
    for _g in sorted(_groups_raw):
        _resurrected = sorted(group_members_all(_g) & set(_off))
        if _resurrected:
            check(not (group_of(_g) & set(_off)),
                  "group %r does NOT resurrect its default-off members (%s)"
                  % (_g, ", ".join(_resurrected)))

    cases = [
        (["--only", "spark11"], {"spark11"}, "--only spark11"),
        (["--only", "http11"], {"spark11"}, "--only http11 (channel alias)"),
        (["--only", "codex"], {"codex"}, "--only codex"),
        # 🔴 A RENAME MUST NOT BREAK THE COMMANDS PEOPLE ALREADY HAVE. `qwen` and `kimi` were the
        # channel NAMES until 2026-08-07 and are now aliases of the model-bearing names, so these
        # two cases are the regression test for that promise, not decoration.
        (["--only", "qwen"], {"qwen38max"}, "--only qwen (OLD NAME, now an alias)"),
        (["--only", "kimi"], {"kimik3"}, "--only kimi (OLD NAME, now an alias)"),
        (["--only", "qwen38max"], {"qwen38max"}, "--only qwen38max"),
        (["--only", "agy36flash"], {"agy36flash"}, "--only agy36flash"),
        (["--only", "spark11", "codex"], {"spark11", "codex"}, "--only with two channels"),
        # The group cases. Each expands to SEVERAL channels from one word.
        (["--only", "agy"], group_of("agy"), "--only agy (GROUP -> the subscription transport)"),
        (["--only", "gemini"], group_of("gemini"), "--only gemini (GROUP -> the model family)"),
        (["--only", "spark"], group_of("spark"), "--only spark (GROUP -> both Spark)"),
        (["--skip", "spark"], without(*group_of("spark")), "--skip spark (GROUP)"),
        (["--skip", "codex", "agy"], without("codex", *group_of("agy")),
         "--skip codex + agy group"),
        (["--route", "только spark11"], {"spark11"}, "route: только spark11"),
        (["--route", "не используй codex"], without("codex"), "route: RU negation"),
        # The negative half of the ADD rule: no additive marker => the default set, unchanged.
        (["--route", "не используй gemini"], without(*group_of("gemini")),
         "route: a plain negation still leaves the opt-in channel OFF"),
        (["--route", "кроме gemini"], without(*group_of("gemini")), "route: кроме gemini (GROUP)"),
        (["--route", "не используй spark"], without(*GROUPS["spark"]),
         "route: RU negation of a GROUP"),
        (["--route", "only codex"], {"codex"}, "route: EN only"),
        ([], ALL, "no flags: every enabled channel runs"),
    ]
    if "orgpt56terrapro" in EXISTS:
        # 🔴 OPT-IN CHANNELS, round 38. Naming a default-OFF channel in prose must SELECT it -
        # until 2026-08-14 the route's only-branch could only turn things off, so "только 5.6
        # terra" removed the other twelve and left the named one disabled: "running 0 channel(s):
        # NONE". The --only FLAG was always right, so the two selection paths disagreed and the
        # prose one silently did nothing. Derived from the registry, not hard-coded to a count.
        # 🔴 R47: conditional on the channel EXISTING, because the shipped kit registry now
        # DELETES it (PUBLISH_EXCLUDE_CHANNELS in package.py). The absent-tree assertions live
        # in suite_explicit_only, which proves the words go dark rather than quietly empty.
        cases += [
            (["--route", "только 5.6 terra"], {"orgpt56terrapro"},
             "route: только 5.6 terra (names an OFF-by-default channel)"),
            (["--route", "только терра-про"], {"orgpt56terrapro"},
             "route: только терра-про (RU alias of an OFF-by-default channel)"),
            # ADD mode: default set PLUS the named channel. Igor's rule is that «используй все
            # модели» must NOT pull in the rationed channel while «и ещё 5.6 Terra Pro» must.
            # 🔴 «все модели» NAMES THE STANDARD PANEL IN PROSE - it is not a synonym for "the
            # default"; derived per case (standard panel here, the bare default below).
            (["--route", "используй все модели и ещё 5.6 Terra Pro"],
             panel_set("standard") | {"orgpt56terrapro"},
             "route: ADD keeps the default set and adds the opt-in one"),
            (["--route", "добавь терра-про"], ALL | {"orgpt56terrapro"},
             "route: добавь <opt-in channel>"),
        ]
    for args, expect, label in cases:
        p = run_cli(args + ["--dry-run"], timeout=90)
        ran = {ln.strip().split("]", 1)[1].strip().split()[0]
               for ln in (p.stdout or "").splitlines() if ln.strip().startswith("[RUN ]")}
        check(ran == expect and p.returncode == 0, f"{label:<28}",
              f"enabled={sorted(ran) or '-'}")

    p = run_cli(["--only", "codex", "--route", "не используй codex", "--dry-run"], timeout=90)
    check(p.returncode == 2 and "ROUTE ERROR" in blob_of(p),
          "a flag contradicting the route is a hard stop, not a guess", f"exit={p.returncode}")


def suite_redaction():
    section("3. Nothing secret-shaped may reach the console, the log or diagnostics.json")

    import orchestrate as o

    # Realistic SHAPES, deliberately non-functional values.
    samples = [
        ("MODEL_API_KEY=" + "L" * 48, "LABELLED_SECRET"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6", "BEARER_TOKEN"),
        ("sk-ant-" + "a" * 40, "ANTHROPIC_KEY"),
        ("ghp_" + "c" * 36, "GITHUB_TOKEN"),
        ("AKIAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY"),
        ("reach me at someone@example.com", "EMAIL"),
        ("A-123456789", "A_NUMBER"),
        ("ssn 123-45-6789", "SSN"),
        ("date of birth: 1988-04-12", "DATE_OF_BIRTH"),
    ]
    for raw, kind in samples:
        out = o.scrub(raw)
        core = raw.split("=")[-1].split()[-1]
        check(core not in out and "[REDACTED:" in out, f"scrub removes {kind:<16}")

    # The 2026-07-31 incident: a "mask" that kept 60 characters of a 48-character key kept all
    # of it. A substitution cannot fail that way; this asserts it never regresses to truncation.
    key = "LLM_" + "9" * 44
    check(key not in o.scrub("MODEL_API_KEY = " + key), "a 48-char key is removed, not truncated")

    nested = {"env": {"MODEL_API_KEY": "sk-" + "z" * 40},
              "l": ["mail bob@corp.io", {"t": "ghp_" + "q" * 36}]}
    b = json.dumps(o.scrub_deep(nested))
    check(not any(s in b for s in ("z" * 40, "bob@corp.io", "q" * 36)),
          "scrub_deep reaches nested lists, dicts and keys")
    check(o.scrub_deep({"n": 5, "b": True, "z": None}) == {"n": 5, "b": True, "z": None},
          "scrub_deep leaves non-strings untouched")

    # Ordinary prose must survive: a gate that cries wolf trains people to disable it.
    for text in ("Use Bearer authentication rather than a query parameter.",
                 "blocks a labelled date of birth unless you pass --allow-pii",
                 "do not paste a passport number into the brief",
                 "tokens in=27687 out=4646 | tool_calls=9"):
        check(o.scrub(text) == text, "clean prose is left alone", repr(text[:44]))

    # An exception carrying a credential must not print it. stdout is archived and replayed;
    # it is the same exfiltration surface as a file.
    out = Path(tempfile.gettempdir()) / "orch-selftest-crash"
    argv = sys.argv[:]
    main = o.main
    try:
        sys.argv = ["orchestrate.py", "--out", str(out)]
        secret = "LLM_" + "k" * 44

        def boom():
            raise ValueError("failure carrying MODEL_API_KEY=" + secret)

        o.main = boom
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = o._crash_handler()
        console = buf.getvalue()
        check(rc == 1, "a crash returns an exit code, not a raw traceback")
        check(secret not in console, "a crash does not print a credential to the console")
        dj = out / "diagnostics.json"
        check(dj.is_file(), "a crash still writes diagnostics.json")
        if dj.is_file():
            body = dj.read_text(encoding="utf-8")
            check(secret not in body, "diagnostics.json carries no credential")
            check("ValueError" in body, "diagnostics.json still carries the traceback")
    finally:
        sys.argv = argv
        o.main = main


def suite_prose_matches_behaviour():
    section("3b. The human-facing safety story is checked against the code, not against itself")
    # 🔴🔴 FOUND BY A REVIEWER, LIVE IN THE PUBLIC REPO, AND EXACTLY THE CLASS THIS PROJECT KEEPS
    # MEASURING. codex, round 32: "the public README says personal data is blocked by default,
    # while PRIVACY.md and orchestrate.py implement warn-and-send. The harness audits model
    # citations mechanically, but it has no equivalent audit for the human-facing safety story
    # that determines what the operator believes will be sent."
    #
    # It was true. The policy was inverted on 2026-08-07; PRIVACY.md was rewritten and README.md
    # was not, so for a day the published front page promised a block that the code does not
    # perform. PRIVACY.md even carries a warning about a document that "described [a gate] as
    # blocking by default months after that was inverted" - the file holding the lesson was the
    # one that got fixed, and its neighbour repeated the error verbatim.
    #
    # Every other check in this suite asks whether the CODE is right. This one asks whether the
    # SENTENCE is, because a reader's belief about what leaves their machine is set by the prose
    # and by nothing else.
    import orchestrate as o

    here = Path(__file__).resolve().parent
    # State the world rather than inheriting one tree: the documents live in `kit/` beside the
    # source and at the repository root once built, and the built layout nests the skill four
    # levels down. SEARCH for them rather than counting `.parents[n]` - the first version of this
    # counted, got the depth wrong, and the built kit failed. Which is the check doing its job:
    # "not vacuously green" is asserted precisely so a wrong path cannot read as a clean pass.
    #
    # 🔴 R60 (2026-08-20): the walk is BOUNDED. Before this, `list(here.parents)` reached the
    # filesystem root. Any ancestor happening to contain both README.md and PRIVACY.md - a
    # homedir dump, another checkout, a docs pack - would let `bool(docs)` become true against
    # documents THIS TOOL DOES NOT SHIP, and section 3b would then run its PII regex over the
    # wrong files. Caught by the R60 cheap panel (grokbuild's C3), which named it the same
    # "vacuous green" hole the check exists to close, pointing the other way. The bound stops
    # at the first ancestor carrying VERSION (a built/shipped tree) or `.git` (a source tree)
    # or `.claude-plugin` (a plugin-cache root) - the three markers this tool uses to mean
    # "here is one of our own layouts". If no marker is found, the walk stops before leaving
    # the immediate `.claude/` tree or the parent of the current package - never at C:\Users.
    def _bound_parents(start):
        stopped, out = False, []
        for p in start.parents:
            out.append(p)
            if (p / "VERSION").is_file() or (p / ".git").exists() or \
               (p / ".claude-plugin").is_dir():
                stopped = True
                break
            if p.name in (".claude", ""):
                break
        return out, stopped
    parents_bounded, _bound_hit = _bound_parents(here)
    roots = [here / "kit"] + parents_bounded
    docs, used = [], None
    for root in roots:
        found = sorted(root.glob("*.md")) if root.is_dir() else []
        names = {p.name for p in found}
        if {"README.md", "PRIVACY.md"} <= names:
            docs, used = found, root
            break
    check(bool(docs), "the shipped documents were located, so this check is not vacuously green",
          str(used or [str(r) for r in roots[:5]]))

    # Ask the CODE what the default is. Never a constant, never a doc.
    with contextlib.redirect_stdout(io.StringIO()):
        default_blocks = o.pii_gate([("brief", "reach me at probe@example.com")],
                                    strict_pii=False) != 0
        strict_blocks = o.pii_gate([("brief", "reach me at probe@example.com")],
                                   strict_pii=True) != 0
    check(not default_blocks and strict_blocks,
          "measured: personal identifiers are SENT by default and blocked only under --strict-pii",
          "default_blocks=%s strict_blocks=%s" % (default_blocks, strict_blocks))

    # Narrow on purpose. A false positive here trains someone to delete the check, which is the
    # disease this project already named: it must fire on a real contradiction and nothing else.
    claims_block = re.compile(
        r"(?is)(?:personal (?:data|identifier)|\bPII\b)[^.]{0,160}?"
        r"(?:is|are) blocked by default|"
        r"blocked by default[^.]{0,80}?(?:personal (?:data|identifier)|\bPII\b)")
    offenders = []
    for p in docs:
        body = p.read_text(encoding="utf-8", errors="replace")
        for m in claims_block.finditer(body):
            line = body[:m.start()].count("\n") + 1
            offenders.append("%s:%d" % (p.name, line))
    check(not (offenders and not default_blocks),
          "no shipped document promises a PII block the code does not perform",
          ", ".join(offenders) or "none")

    # The check must be able to FAIL, or it is decoration. Same reasoning as echocheck's
    # deliberately-inert calibration knob: a test that can only pass has not been calibrated.
    planted = "Personal data is blocked by default and requires a deliberate flag."
    check(bool(claims_block.search(planted)),
          "positive control: the detector fires on the exact sentence that was published")
    for benign in ("Secrets are blocked by default, with no override.",
                   "Personal data warns loudly and is SENT; --strict-pii makes it a hard stop.",
                   "Personal data - ID numbers, SSNs - is found, itemised and reported, then SENT."):
        check(not claims_block.search(benign),
              "negative control: no false positive on %r" % benign[:46])


def suite_contract():
    section("4. Run artefacts are produced and are useful")

    p = run_cli(["--only", "codex", "agy"], {"CODEX_BIN": NOPE, "AGY_BIN": NOPE})
    b = blob_of(p)
    check("diagnostics.json" in b, "the diagnostics path is printed on failure")
    check("-> " in b and "install it" in b.lower(),
          "each problem is reported with a plain-language fix")

    import orchestrate as o
    for sig, expect in (("MODEL_API_KEY not set", True), ("binary not found", True),
                        ("END MARKER ABSENT", True), ("HTTP 429 rate limit", True),
                        ("something nobody has ever seen", False)):
        cause, _fix = o.diagnose(sig)
        check(bool(cause) is expect, f"diagnose({sig[:30]!r})",
              (cause or "unrecognised")[:44])


def suite_dispatch():
    """
    Every channel the registry ENABLES is actually launched, and with its own settings.

    🔴 This suite exists because the opposite was true and nothing noticed. channels.json has
    always promised that "adding a channel is a change HERE, never in the code"; main() then
    dispatched on four literal names, so a fifth entry loaded, resolved, printed in the plan as
    [RUN ] - and was never launched. Every existing check passed throughout, because every one
    of them was written when there were exactly four channels. Correctness about the cases you
    thought of is not coverage.

    So the assertion is deliberately derived from the registry rather than from a list written
    here: a list written here would be updated by the same person who forgot the dispatcher.
    """
    section("6. Every channel the registry enables is really launched, with its own settings")
    probe = (
        "import json, os, sys, tempfile\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "import orchestrate as o, routing\n"
        "L = []\n"
        "def stub(kind):\n"
        "    def f(*a, **k):\n"
        # 🔴 CAPTURE WHAT THE TIER SCALES, NOT ONLY WHAT IT USED TO SET. The plan can print
        # `reasoning cap 48000` and the dispatcher can still hand the call something else - that
        # is exactly how `tools` stayed decorative on goog36flash for a day, agreeing with a
        # hard-coded default. Asserting the PLAN is asserting the printout; this asserts the CALL.
        "        L.append({'kind': kind, 'name': k.get('name'), 'model': k.get('model'),\n"
        "                  'effort': k.get('effort'), 'timeout': k.get('timeout'),\n"
        "                  'reasoning': k.get('reasoning'), 'fetch_tool': k.get('fetch_tool'),\n"
        "                  'thinking_level': k.get('thinking_level'),\n"
        "                  'provider_route': k.get('provider_route'),\n"
        "                  'web': bool((k.get('web') or {}).get('enabled'))})\n"
        "        return {'ok': True, 'text': 'stub\\nREVIEW-COMPLETE', 'seconds': 0.0}\n"
        "    return f\n"
        "o.call_http_reviewer = stub('http'); o.call_codex = stub('codex')\n"
        # 🔴 ONE FUNCTION SERVES BOTH `openrouter` AND `oai` SINCE 2026-08-08, so one stub covers
        # both kinds — and the launched-count assertion below is what would catch it if that ever
        # stopped being true. The old name `call_openrouter_reviewer` was patched here and the
        # rename would have left this line silently stubbing NOTHING: setattr on a module happily
        # creates an attribute that no longer exists, so the test would have made real paid calls
        # while reporting a clean pass. Same class as the flag rename that missed its own reporter.
        "o.call_agy = stub('agy'); o.call_oai_reviewer = stub('openrouter')\n"
        "o.call_xai_responses = stub('xai')\n"
        # Every entry in KNOWN_KINDS needs a stub here, or this very check reports the new
        # channel as unlaunched. It did exactly that on 2026-08-07, one minute after `kind:
        # gemini` was written — which is the check working. It is the regression test built
        # after four literal channel names silently swallowed a fifth channel, and it caught
        # the sixth kind by the same mechanism.
        "o.call_gemini_direct = stub('gemini')\n"
        "o.call_hermes = stub('hermes')\n"
        "o.call_grokcli = stub('grokcli')\n"
        "o.call_opencode = stub('opencode')\n"
        # 🔴 THE STUBS MUST REPLACE SOMETHING THAT EXISTS. Found while renaming
        # call_openrouter_reviewer -> call_oai_reviewer on 2026-08-08: `o.old_name = stub(...)`
        # does not fail on a name the module no longer has, it CREATES it. The dispatcher then
        # calls the real function, this suite makes real paid calls against live vendors, and
        # every check still passes. A test whose isolation can evaporate silently is worse than
        # no test. Asserted BEFORE assignment would need a different structure; asserted here it
        # still fires on the next rename, which is what matters.
        "for _n in ('call_http_reviewer','call_codex','call_agy','call_oai_reviewer',\n"
        "           'call_xai_responses','call_gemini_direct','call_hermes','call_opencode'):\n"
        "    assert callable(getattr(o, _n, None)), 'stub target missing: ' + _n\n"
        "t = tempfile.mkdtemp(prefix='orchdisp-')\n"
        "b = os.path.join(t, 'b.md')\n"
        "open(b, 'w', encoding='utf-8').write('Review.\\nREVIEW-COMPLETE\\n')\n"
        "sys.argv = ['o', '--brief', b, '--out', os.path.join(t, 'o'), '--tier', 'strategic',\n"
        "            '--no-citecheck', '--no-log']\n"
        "o.main()\n"
        "S = list(L); L.clear()\n"
        "sys.argv = ['o', '--brief', b, '--out', os.path.join(t, 'o2'), '--tier', 'deep',\n"
        "            '--no-citecheck', '--no-log']\n"
        "o.main()\n"
        "D = list(L)\n"
        f"reg = routing.load_registry(os.path.join(r'{HERE}', 'channels.json'))\n"
        # 'enabled' here means "would a no-flag run launch it", which is enabled AND in the
        # default panel - `o.main()` above is a no-flag run. Panel-filtered since 2026-08-16;
        # without it, flipping default_panel makes this compare 17 against 13 and call correct
        # code broken.
        "_inc = set(reg['panels'][reg['default_panel']]['includes'])\n"
        "en = sorted(c for c, ch in reg['channels'].items()\n"
        "            if ch.get('enabled', True) and ch.get('panel') in _inc)\n"
        "print('RESULT=' + json.dumps({'enabled': en, 'launched': S, 'deep': D}))\n"
    )
    pf = Path(tempfile.gettempdir()) / "orch_selftest_dispatch.py"
    pf.write_text(probe, encoding="utf-8")
    try:
        p = subprocess.run([PY, str(pf)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        b = blob_of(p)
        line = next((l for l in b.splitlines() if l.startswith("RESULT=")), None)
        check(bool(line), "the dispatch probe produced a result", b[-200:])
        if not line:
            return
        data = json.loads(line[len("RESULT="):])
        enabled, launched = data["enabled"], data["launched"]
        check(len(launched) == len(enabled),
              "every enabled registry channel was launched",
              "enabled=%s launched=%d" % (enabled, len(launched)))
        https = [r for r in launched if r["kind"] == "http"]
        if len(https) > 1:
            models = {r["model"] for r in https}
            check(len(models) == len(https),
                  "channels sharing one endpoint each ran their OWN model",
                  "models=%s" % sorted(models))
        for kind, field in (("codex", "timeout"), ("agy", "effort")):
            rows = [r for r in launched if r["kind"] == kind]
            if rows:
                check(all(r[field] for r in rows),
                      "the tier delivered %s to every %s channel" % (field, kind))
        # 🔴 R66: timeout must reach EVERY kind, not just codex/agy/grokcli/xai. Until R66 four
        # dispatch paths (http, openrouter, oai, gemini, hermes) silently dropped the registry's
        # timeout and fell back to the function's default. The stub captures timeout from kwargs;
        # _seconds(p.get("timeout"), 2400) ensures a non-None value even when the registry omits it.
        for r in launched:
            check(r.get("timeout") is not None,
                  "R66: timeout reached the %s (%s) call" % (r["name"], r["kind"]),
                  "timeout=%s" % r.get("timeout"))
        # 🔴 ONLY CHANNELS THAT ACTUALLY RUN. `web.enabled` is a property of the channel; "the
        # setting reached the call" is a property of a LAUNCH, and a channel that is disabled here
        # never makes one. Before the local/kit split every channel ran, so the two sets were the
        # same and the difference was invisible; on 2026-08-08 three kit-only channels turned this
        # into three red checks against working code. That is the same mistake as asserting group
        # membership against the enabled set, twenty lines up, and it is worth noticing that BOTH
        # were written by assuming "every channel in the registry runs here" - an assumption no
        # line of code stated and that stopped being true in one edit.
        # ...and the same argument applies one step further: a channel outside the DEFAULT PANEL
        # never launches on a no-flag run either, so it cannot have delivered its web setting to
        # a call. Added 2026-08-16 when default_panel moved to cheap and kimik3/qwen38max - both
        # standard-only - went red for not appearing in a run they were never part of.
        _reg_raw = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
        _inc = set(_reg_raw["panels"][_reg_raw["default_panel"]]["includes"])
        webbed = [c for c, ch in _reg_raw["channels"].items()
                  if not c.startswith("_") and ch.get("enabled", True)
                  and ch.get("panel") in _inc
                  and (ch.get("web") or {}).get("enabled")]
        for c in webbed:
            check(any(r["name"] == c and r["web"] for r in launched),
                  "the registry's web setting reached the %s call" % c)
        check(bool(webbed), "at least one launched channel has web search on",
              "webbed=%s" % webbed)
        # 🔴 R36 (2026-08-13): a registry `provider_route` block must REACH the call, not stay on
        # the printout. Same discipline as `web.enabled` above: the plan can print «provider pin»
        # and the dispatcher can still hand OpenRouter no `provider` field, which is the exact
        # rot this repository has recorded seven times for other fields. Derived from the registry
        # rather than named: any launched channel whose registry entry carries `provider_route`
        # must have received it in its call kwargs.
        # 🔴 AND THE SAME DEFAULT-PANEL FILTER AS `webbed` ABOVE - MISSING HERE UNTIL 2026-08-19.
        # That filter was added to `webbed` in R44 with three paragraphs explaining why a channel
        # outside the default panel cannot have delivered anything to a call, and this list
        # twenty lines below it was left alone. It went red the first time a pinned channel moved
        # out of the cheap panel (ordeepseekv4pro, R48) - red against working code, for exactly
        # the reason the comment above already gives. Third instance of class-fix-misses-a-member
        # found in this round, and the first one inside the test file: tests are code and rot the
        # same way. The registry is also read once now instead of three times in ten lines.
        pinned = [c for c, ch in _reg_raw["channels"].items()
                  if not c.startswith("_") and ch.get("enabled", True)
                  and ch.get("panel") in _inc
                  and ch.get("provider_route")]
        for c in pinned:
            row = next((r for r in launched if r["name"] == c), None)
            check(bool(row and row.get("provider_route")),
                  "provider_route from the registry REACHED the %s call" % c,
                  "registry=%s got=%s" % (_reg_raw["channels"][c].get("provider_route"),
                                          row and row.get("provider_route")))
        # 🔴 THE TIER MUST REACH THE CALL, NOT ONLY THE PRINTOUT. Compared arg-for-arg between a
        # strategic and a deep dispatch of the same registry: a knob that resolves and prints but
        # never reaches the function is the defect class this repository has now recorded seven
        # times (`channels.spark.model`, the four dispatch literals, the telemetry keyed on old
        # names, `tools` on goog36flash, the renamed flag that missed its own reporter, ...).
        # 🔴 SAME COMPARISON, OPPOSITE ASSERTION SINCE R43. The two dispatches are now made with
        # two ALIASES of one tier, so every argument that reaches the call must be IDENTICAL.
        # That is a stronger statement than the old «deep doubles it»: it catches a half-finished
        # re-split, where someone re-adds a second tier and only some kinds notice.
        deep = {r["name"]: r for r in data.get("deep") or []}
        for r in launched:
            d = deep.get(r["name"])
            if not d:
                continue
            if (r.get("fetch_tool") or {}).get("enabled"):
                check((d.get("fetch_tool") or {}).get("max_calls")
                      == (r["fetch_tool"].get("max_calls") or 8),
                      "'deep' and 'strategic' send the SAME fetch budget to %s" % r["name"],
                      "%s -> %s" % (r["fetch_tool"].get("max_calls"),
                                    (d.get("fetch_tool") or {}).get("max_calls")))
            if (r.get("reasoning") or {}).get("max_tokens"):
                check((d.get("reasoning") or {}).get("max_tokens")
                      == r["reasoning"]["max_tokens"],
                      "'deep' and 'strategic' send the SAME reasoning ceiling to %s" % r["name"],
                      "%s -> %s" % (r["reasoning"]["max_tokens"],
                                    (d.get("reasoning") or {}).get("max_tokens")))
            if (r.get("reasoning") or {}).get("effort"):
                check((d.get("reasoning") or {}).get("effort") == r["reasoning"]["effort"],
                      "'deep' and 'strategic' send the SAME effort to %s" % r["name"],
                      "%s -> %s" % (r["reasoning"]["effort"],
                                    (d.get("reasoning") or {}).get("effort")))
            if r["kind"] == "gemini":
                # 🔴 THIS USED TO ASSERT THE LITERALS "medium" AND "high", and it went red the
                # moment Igor raised strategic to `high` on 2026-08-08 - against code that was
                # working exactly as instructed. A test that hard-codes the value a human is
                # expected to change tests the human, not the code. What is actually invariant is
                # that whatever the registry's tier says arrives at the CALL, so that is what is
                # read and compared - the same derive-from-the-registry rule the channel set and
                # the tier list already follow, and the fourth place it has had to be applied.
                reg_t = json.loads(Path(HERE, "channels.json")
                                   .read_text(encoding="utf-8"))["tiers"]
                # 🔴 `reg_t["strategic"]` WAS A KeyError THE MOMENT THE TIER WAS RENAMED, and it
                # aborted the whole dispatch suite - 60-odd checks lost to one lookup. The two
                # words the CLI still accepts are ALIASES now, so the registry must be asked
                # which tier each word resolves to instead of being indexed by the word.
                sys.path.insert(0, HERE)
                import routing as _rt                                  # noqa: E402
                _reg = _rt.load_registry(str(Path(HERE, "channels.json")))
                for tier_name, row in (("strategic", r), ("deep", d)):
                    want = reg_t[_rt.canon_tier(_reg, tier_name)].get("gemini_thinking_level")
                    check(row.get("thinking_level") == want,
                          "the %s tier's thinking_level REACHES the %s call"
                          % (tier_name, r["name"]),
                          "registry=%s call=%s" % (want, row.get("thinking_level")))
    finally:
        pf.unlink(missing_ok=True)

    # --- One channel's failure must cost exactly one channel -------------------------------
    # Found by an external reviewer, 2026-08-06. `results = {k: f.result() ...}` re-raises, so
    # ONE channel throwing aborted the comprehension and discarded every other channel's result
    # - work that had already run and already been billed - leaving a traceback instead of four
    # good reviews. The sibling case: a registry `kind` the dispatcher cannot launch produced a
    # log line and no entry in `results`, which downstream is indistinguishable from a channel
    # nobody asked for, while the plan printed [RUN ] for it.
    probe2 = (
        "import json, os, shutil, sys, tempfile\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        f"REG = os.path.join(r'{HERE}', 'channels.json')\n"
        "shutil.copy(REG, REG + '.selftest-bak')\n"
        "try:\n"
        "    d = json.load(open(REG, encoding='utf-8'))\n"
        "    d['channels']['ghost'] = {'kind': 'https', 'enabled': True, 'label': 'Ghost',\n"
        "        'cost': 'cheap', 'model': 'nothing',\n"
        "        'models': {'nothing': {'aliases': ['nothing-model']}}, 'aliases': ['ghost']}\n"
        "    json.dump(d, open(REG, 'w', encoding='utf-8'), ensure_ascii=False)\n"
        "    import orchestrate as o\n"
        "    def ok(*a, **k): return {'ok': True, 'text': 'x\\nREVIEW-COMPLETE'}\n"
        "    def boom(*a, **k): raise RuntimeError('simulated wrapper crash')\n"
        "    o.call_http_reviewer = ok; o.call_codex = ok; o.call_oai_reviewer = ok\n"
        "    o.call_hermes = ok; o.call_gemini_direct = ok; o.call_xai_responses = ok\n"
        "    o.call_grokcli = ok; o.call_opencode = ok\n"
        "    o.call_agy = boom\n"
        "    t = tempfile.mkdtemp(); b = os.path.join(t, 'b.md')\n"
        "    open(b, 'w', encoding='utf-8').write('hi\\nREVIEW-COMPLETE\\n')\n"
        "    out = os.path.join(t, 'out')\n"
        "    sys.argv = ['o', '--brief', b, '--out', out, '--no-citecheck']\n"
        "    o.main()\n"
        "    ch = json.load(open(os.path.join(out, 'diagnostics.json'),\n"
        "                        encoding='utf-8'))['channels']\n"
        "    print('RESULT2=' + json.dumps({k: bool(v.get('ok')) for k, v in ch.items()}))\n"
        "finally:\n"
        "    shutil.move(REG + '.selftest-bak', REG)\n"
    )
    pf2 = Path(tempfile.gettempdir()) / "orch_selftest_dispatch2.py"
    pf2.write_text(probe2, encoding="utf-8")
    try:
        p = subprocess.run([PY, str(pf2)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        b = blob_of(p)
        line = next((l for l in b.splitlines() if l.startswith("RESULT2=")), None)
        check(bool(line), "the failure-isolation probe produced a result", b[-200:])
        if line:
            st = json.loads(line[len("RESULT2="):])
            check(st.get("agy31pro") is False,
                  "a channel that RAISES is recorded as a failed channel, not a traceback")
            check(st.get("ghost") is False,
                  "a channel with an undispatchable `kind` gets a failed RESULT, not only a log")
            # 🔴 COVERAGE, NOT JUST CORRECTNESS. `>= 3` was true with three channels and stayed
            # true at nine, so it kept passing while silently covering less and less: on
            # 2026-08-07 it read six survivors out of a possible seven and said PASS, because the
            # newly added `gemini` channel had no stub and failed for a reason that had nothing to
            # do with the crash being tested. A floor cannot detect a channel quietly dropping out
            # of the sample. Demand EVERY non-crashing channel, computed from the registry.
            # 🔴 THE CRASH-EXPECTED SET IS DERIVED FROM `kind == "agy"`, not hard-coded. Round 36
            # (2026-08-13) hit exactly the sibling defect this comment above documents: the list
            # was `("agy31pro", "agy36flash", "ghost")` while agy37flash was added the same day,
            # so the test failed for a reason that had nothing to do with the crash isolation
            # question - the exact silent-drop pattern the surrounding paragraph exists to warn
            # about. Fix: derive. `call_agy = boom` stubs the agy dispatcher, so every channel of
            # kind agy is expected to crash, and `ghost` is added by name because it is planted
            # by this test for the undispatchable-kind assertion.
            _reg = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
            _agy_chans = {k for k, v in _reg["channels"].items()
                          if not k.startswith("_") and isinstance(v, dict)
                          and v.get("kind") == "agy"}
            _crash_set = _agy_chans | {"ghost"}
            expected = {n for n in st if n not in _crash_set}
            survivors = {n for n in expected if st[n]}
            check(survivors == expected,
                  "one channel's crash does not discard ANY other paid-for result",
                  "survived=%s missing=%s" % (sorted(survivors), sorted(expected - survivors)))
    finally:
        pf2.unlink(missing_ok=True)


def suite_citations():
    section("5. The citation check must be honest about what it did and did not check")

    import citecheck
    import orchestrate as o

    # The network is STUBBED on purpose. This suite's promise is that it costs nothing and
    # contacts no vendor, and a test that reaches out to example.com breaks that promise and goes
    # flaky in CI besides. What is worth testing here is this project's own logic - deduplication,
    # punctuation, the cap, the never-raises contract - not whether urllib works.
    verdicts = {
        "https://real.example/ok":      ("LIVE", ""),
        "https://real.example/gone":    ("DEAD", "HTTP 404"),
        "https://real.example/wall":    ("BLOCKED", "HTTP 403 - existence not established"),
        "https://real.example/moved":   ("MOVED", "-> https://real.example/new"),
    }
    real_resolve = citecheck.resolve_all
    citecheck.resolve_all = lambda urls, workers=10: [verdicts.get(u, ("UNKNOWN", "stub"))
                                                      for u in urls]
    try:
        text = ("See https://real.example/ok, and https://real.example/gone. "
                "Also https://real.example/wall and https://real.example/moved; "
                "plus https://real.example/ok again.")
        e = o.citation_audit({"c": {"text": text}})["c"]

        check(e["cited"] == 4, "duplicate citations are probed once", str(e["cited"]))
        check(e["dead"] == 1, "a 404 is counted as DEAD", str(e))
        flagged = {d["url"] for d in e["flagged"]}
        check(not any(u.endswith((",", ".", ";")) for u in flagged),
              "trailing prose punctuation is not part of the URL", str(sorted(flagged)))

        # The rule this exists to enforce: a bot wall is not evidence of fabrication. Reporting
        # "could not check" as "fake" is the exact move the harness forbids the models, and it
        # would be worse coming from the harness, which is supposed to be the trustworthy part.
        check("https://real.example/wall" not in flagged,
              "a BLOCKED URL is not listed among the suspect citations")
        check(e["tally"].get("BLOCKED") == 1, "but it IS counted, so nothing disappears silently",
              str(e["tally"]))

        saved, o.CITECHECK_MAX_URLS = o.CITECHECK_MAX_URLS, 2
        try:
            capped = o.citation_audit({"c": {"text": text}})["c"]
        finally:
            o.CITECHECK_MAX_URLS = saved
        check(capped.get("not_probed") == 2 and bool(capped.get("not_probed_note")),
              "when the per-channel cap bites, the dropped count is stated",
              str(capped.get("not_probed")))
    finally:
        citecheck.resolve_all = real_resolve

    check("skipped" in o.citation_audit({"c": {"text": "x"}}, enabled=False),
          "--no-citecheck reports a stated skip rather than silently doing nothing")
    for shape in ({}, {"c": {}}, {"c": {"text": None}}, {"c": {"text": ""}}):
        try:
            o.citation_audit(shape)
            ok = True
        except Exception:                                # noqa: BLE001
            ok = False
        check(ok, f"citation_audit survives {shape!r}"[:70])
    for payload in ({}, {"skipped": "x"}, {"c": {"error": "URLError"}}, {"c": {"cited": 0}}):
        try:
            # Captured, because the assertion is "it does not raise", not "it prints" - and a
            # --quiet run that still prints is a flag that does not do what it says.
            with contextlib.redirect_stdout(io.StringIO()):
                o.log_citation_audit(payload)
            ok = True
        except Exception:                                # noqa: BLE001
            ok = False
        check(ok, f"the printer survives {payload!r}"[:70])

    # --- round 32: the citations that were never in the prose -------------------------------
    #
    # goog36flash returns its sources as structured annotations pointing at opaque vertexaisearch
    # wrappers. A regex over the answer text finds none of them, so for two rounds the audit
    # printed "cited no URLs" for a channel that had just cited six, and the registry recorded
    # the channel as unauditable. Every check below is against the shape MEASURED on the live
    # endpoint 2026-08-08, not against a shape the docs promise - the docs' own example shows
    # publisher URLs here and the endpoint returns wrappers.
    W = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/%s"

    def _ann(tok, title, s=0, e=1):
        return {"type": "url_citation", "url": W % tok, "title": title,
                "start_index": s, "end_index": e}

    spans = ([_ann("A", "wikipedia.org")] * 4 + [_ann("B", "thedailystar.net")] * 3
             + [_ann("C", "youtube.com")] * 3 + [_ann("D", "uefa.com")] * 2
             + [_ann("E", "wikipedia.org")] * 2)
    data = {"steps": [
        {"type": "google_search_call", "arguments": {"queries": ["q1", "q2"]}},
        {"type": "model_output", "content": [
            {"type": "text", "text": "Spain won.", "annotations": spans}]}]}
    p = o.parse_gemini_steps(data)
    check(p["n_annotations"] == 14, "annotation spans are counted as spans", str(p["n_annotations"]))
    check(len(p["redirect_urls"]) == 5, "and distinct SOURCES are counted separately - the two "
          "were one number, and it overstated this channel 3.5x", str(len(p["redirect_urls"])))
    check(p["redirect_domains"] == ["thedailystar.net", "uefa.com", "wikipedia.org",
                                    "youtube.com"],
          "the publisher domain is recovered from `title`, which used to be discarded",
          str(p["redirect_domains"]))
    check(p["queries"] == ["q1", "q2"] and p["text"] == "Spain won.",
          "text and queries still parse")

    # Negative controls. `title` is documented only as a display string, so the code must key on
    # the SHAPE. Naming a publisher we cannot actually name would be the same failure as the one
    # being fixed, pointing the other way.
    odd = {"steps": [{"type": "model_output", "content": [{
        "type": "text", "text": "x", "annotations": [
            _ann("F", "Spain wins Euro 2024 in a dramatic final"),   # a headline, not a domain
            _ann("G", ""),                                           # empty
            {"type": "url_citation", "title": "example.com"},        # no url at all
            _ann("H", "uefa.com")]}]}]}
    q = o.parse_gemini_steps(odd)
    check(q["redirect_domains"] == ["uefa.com"],
          "a headline in `title` is NOT reported as a publisher", str(q["redirect_domains"]))
    check(q["n_annotations"] == 3, "an annotation with no url is not counted at all",
          str(q["n_annotations"]))
    for shape in (None, {}, {"steps": None}, {"steps": [{"type": "model_output"}]},
                  {"steps": [{"type": "url_context_result", "result": "not-a-list"}]}):
        try:
            o.parse_gemini_steps(shape)
            ok = True
        except Exception:                                # noqa: BLE001
            ok = False
        check(ok, f"parse_gemini_steps survives {shape!r}"[:70])

    # The wrapper is resolved in its OWN hop, and only the publisher URL is probed. Doing both in
    # one pass lost data: probe_url follows the redirect and keeps going, so a slow publisher
    # (measured: uefa.com timed out) threw away the identity of the source with its existence.
    real_wrap, real_res = citecheck.resolve_wrappers, citecheck.resolve_all
    citecheck.resolve_wrappers = lambda urls, timeout=15: (
        {W % "A": "https://en.wikipedia.org/wiki/UEFA_Euro_2024"})
    citecheck.resolve_all = lambda urls, workers=10: [
        ("LIVE", "") if "wikipedia" in u else ("UNKNOWN", "TimeoutError") for u in urls]
    try:
        # 🔴 DEFAULT OFF, and the reason is Google's Grounding terms, not a technical limit - they
        # name "using Links to identify destination pages for crawling or scraping" as a violation
        # by example, and define Links to include the titles served with them. The default is what
        # strangers who install this kit run, so it must not be the named behaviour. The channel is
        # NOT thereby unreported: its publisher domains come from the response and cost no request.
        off = o.citation_audit({"g": {"text": "", "redirect_urls": [W % "A"],
                                      "cited_domains": ["uefa.com"]}})["g"]
        check(off["cited"] == 1 and off["probed"] == 0,
              "by default a grounding Link is COUNTED but not followed", str(off))
        check(off.get("domains") == ["uefa.com"],
              "and the publisher is still named, from the response, with no request", str(off))
        check("cited no URLs" not in str(off) and off["cited"] != 0,
              "'cited 0' and 'cited 6, none followed' stay different facts")
        check(off.get("links_followed") is False,
              "the record says the Links were not followed, so the printer cannot report our "
              "own decision as the vendor failing to answer")
        # The guard that rejects a non-domain title must COUNT what it rejected. A run reported
        # two wrappers and zero publishers with nothing saying which of the two causes it was.
        p2 = o.parse_gemini_steps({"steps": [{"type": "model_output", "content": [{
            "type": "text", "text": "x", "annotations": [
                _ann("Q", "Spain wins the final"), _ann("R", "uefa.com")]}]}]})
        check(p2["titles_unusable"] == 1 and p2["redirect_domains"] == ["uefa.com"],
              "a discarded title is counted, not silently dropped", str(p2["titles_unusable"]))
        # ABSENT and MALFORMED are different facts about the vendor, and the first version of this
        # counter reported both as "not domain-shaped" - which sends a reader to inspect a value
        # that does not exist.
        p3 = o.parse_gemini_steps({"steps": [{"type": "model_output", "content": [{
            "type": "text", "text": "x", "annotations": [
                _ann("S", None), _ann("T", ""), _ann("U", "Spain wins the final")]}]}]})
        check(p3["titles_missing"] == 2 and p3["titles_not_domain"] == 1,
              "a missing title and a malformed one are counted apart", str(p3))

        e = o.citation_audit({"g": {"text": "", "redirect_urls": [W % "A", W % "Z"]}},
                             resolve_links=True)["g"]
        probed = {d["url"] for d in e["flagged"]}
        check("https://en.wikipedia.org/wiki/UEFA_Euro_2024" in
              {d["url"] for d in e["flagged"]} or e["tally"].get("LIVE") == 1,
              "a resolved wrapper is probed as the PUBLISHER url, not as the opaque token")
        check(e["wrappers"] == 2 and e["wrappers_resolved"] == 1
              and e["wrappers_unresolved"] == 1,
              "resolved and unresolved wrappers are both counted, neither silently", str(e))
        check(any(W % "Z" == u for u in probed),
              "a wrapper Google would not resolve is still probed as-is rather than dropped",
              str(sorted(probed)))
        check(any("[recovered from a google_search wrapper]" in (d["note"] or "")
                  for d in e["flagged"]),
              "and a recovered URL says so, because its provenance is not the answer text")
    finally:
        citecheck.resolve_wrappers, citecheck.resolve_all = real_wrap, real_res


def suite_tiers_and_grounding():
    """
    Round 29's two structural changes, each guarded by the failure it would otherwise repeat.

    The tier had TWO homes - a literal in orchestrate.py feeding `--tier`'s choices, and the
    registry feeding the per-kind values - so deleting a tier from the registry would have left
    the flag accepting it and falling through to defaults. And `opened_urls` had TWO meanings,
    "pages we fetched" and "pages the vendor says it opened", which every cross-channel
    comparison silently added together.
    """
    section("7. One home for tiers; one meaning per grounding field")
    reg = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
    reg_tiers = set(reg.get("tiers") or {})

    # --- the tier list has ONE home -------------------------------------------------------
    probe = (
        "import json, sys\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "import orchestrate as o\n"
        "print('RESULT=' + json.dumps(sorted(o.load_tiers())))\n"
    )
    pf = Path(tempfile.gettempdir()) / "orch_selftest_tiers.py"
    pf.write_text(probe, encoding="utf-8")
    try:
        p = subprocess.run([PY, str(pf)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        line = next((l for l in blob_of(p).splitlines() if l.startswith("RESULT=")), None)
        code_tiers = set(json.loads(line[len("RESULT="):])) if line else set()
    finally:
        pf.unlink(missing_ok=True)
    check(code_tiers == reg_tiers,
          "orchestrate derives its tiers from the registry, with no second list",
          "registry=%s code=%s" % (sorted(reg_tiers), sorted(code_tiers)))

    # A tier the registry does not define must be REFUSED, not quietly defaulted.
    gone = subprocess.run([PY, str(Path(HERE, "orchestrate.py")), "--brief", os.devnull,
                           "--tier", "quick", "--dry-run"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    check(gone.returncode != 0 and "invalid choice" in blob_of(gone),
          "a tier that no longer exists is refused by name, not silently defaulted")

    # --- the tier reaches every kind that HAS a lever --------------------------------------
    sys.path.insert(0, HERE)
    import routing                                                       # noqa: E402
    r = routing.load_registry(str(Path(HERE, "channels.json")))
    strat = routing.resolve(r, tier="strategic")
    deep = routing.resolve(r, tier="deep")
    live = [c for c, p in strat.items() if p["enabled"]]
    # 🔴 Derived from the plan, not from a list written here: a hand-written list of "channels
    # the tier should reach" would be updated by the same person who forgot to wire the tier.
    missing = [c for c in live if not strat[c].get("_tier_note")]
    check(not missing,
          "every running channel says what the tier did to it (or that it did nothing)",
          "silent: %s" % missing)

    # 🔴 THIS USED TO ASSERT «deep doubles the page-fetch budget» AND IT NOW ASSERTS THE OPPOSITE,
    # on purpose. R43 collapsed the two tiers into one, so `strategic` and `deep` are aliases of
    # `max` and MUST resolve identically - including the fetch budget, which is deliberately not
    # doubled any more (reading is not thinking, and a doubled fetch budget is the one lever
    # measured to produce a token runaway). The check is kept rather than deleted because the
    # alias equivalence is exactly what would break silently if someone re-added a second tier.
    for c in live:
        ft_s = (strat[c].get("fetch_tool") or {})
        ft_d = (deep[c].get("fetch_tool") or {})
        if ft_s.get("enabled"):
            check((ft_d.get("max_calls") or 0) == (ft_s.get("max_calls") or 8),
                  "the retired tier names resolve to the SAME fetch budget on %s" % c,
                  "%s -> %s" % (ft_s.get("max_calls"), ft_d.get("max_calls")))
    # 🔴 THE OLD CHECK WAS "the depth knob MOVES with the tier", pinned to medium->high. On
    # 2026-08-08 Igor raised strategic to `high` and it went red against correct code. What is
    # worth asserting is not that a value changes - a maintainer decides that - but that the
    # NOTE tells the truth about whether it changed. That is the actual failure mode here: the
    # xai line the panel caught a day earlier printed "the tier buys wall-clock" while the tier
    # reached nothing, and a gemini line reprinting `thinking_level=high` under `deep` would read
    # as a raise on a channel where the tier now does nothing at all.
    for c in [c for c in live if strat[c].get("kind") == "gemini"]:
        ladder = strat[c].get("thinking_levels") or []
        check(bool(ladder) and strat[c].get("thinking_level") in ladder,
              "%s's thinking_level is one of its own declared levels" % c,
              "level=%s ladder=%s" % (strat[c].get("thinking_level"), ladder))
        for tier_name, p in (("strategic", strat[c]), ("deep", deep[c])):
            note, lvl = p.get("_tier_note") or "", p.get("thinking_level")
            # 🔴 WORDING CHANGED WITH THE TIER COLLAPSE AND THE ASSERTION HAD TO FOLLOW THE
            # INTENT, NOT THE STRING. The old note said «nothing this tier can raise on this
            # channel», which was informative while a second tier existed - it answered «would
            # deep help here?». With one tier that sentence answers a question nobody can ask,
            # and it reads as a limitation rather than as «you are at the ceiling». What still
            # has to be true is that the note names the level AND says it is the top.
            if ladder and lvl == ladder[-1]:
                check(str(lvl) in note and "ceiling" in note,
                      "%s on %s names the level and says it is the ceiling" % (tier_name, c),
                      "note=%r" % note)
            else:
                check("->" in note or str(lvl) in note,
                      "%s on %s reports the resolved level" % (tier_name, c),
                      "note=%r" % note)
    # 🔴 THE REGRESSION THAT MADE THIS CHECK NECESSARY. The tier used to be applied BEFORE
    # _decorate copied per-channel registry values into the plan, so it scaled fields that were
    # still None and then had its own value overwritten a line later. Both symptoms are
    # invisible in a passing run: the knob resolves, prints, and does nothing.
    check(all(strat[c].get("max_tokens") for c in live if strat[c].get("kind") in
              ("openrouter", "oai", "gemini", "xai")),
          "registry values still reach the plan after the tier is applied")

    # --- one meaning per grounding field ---------------------------------------------------
    src = Path(HERE, "orchestrate.py").read_text(encoding="utf-8")
    # Comment lines are stripped first. The retired name is quoted all over the prose that
    # explains WHY it was retired, and a check that cannot tell an explanation from a live
    # dict key fails on its own documentation - which is how a green suite gets edited into a
    # silent one. What must not exist is the KEY.
    code = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    offenders = [l.strip()[:90] for l in code if '"opened_urls":' in l]
    check(not offenders,
          "no channel returns `opened_urls` any more - the name that meant two things",
          "; ".join(offenders))
    fields = {"fetched_by_us", "fetched_urls", "vendor_opened", "vendor_opened_urls",
              "grounding_basis"}
    probe2 = (
        "import json, sys\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "import orchestrate as o\n"
        "ours = o._grounding(fetched=['https://a/x'])\n"
        "theirs = o._grounding(vendor_opened=['https://b/y'])\n"
        "both = o._grounding(fetched=['https://a/x'], vendor_opened=['https://b/y'])\n"
        "none = o._grounding()\n"
        "print('RESULT=' + json.dumps({'ours': ours, 'theirs': theirs, 'both': both,\n"
        "                              'none': none}))\n"
    )
    pf2 = Path(tempfile.gettempdir()) / "orch_selftest_grounding.py"
    pf2.write_text(probe2, encoding="utf-8")
    try:
        p2 = subprocess.run([PY, str(pf2)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120)
        line2 = next((l for l in blob_of(p2).splitlines() if l.startswith("RESULT=")), None)
        d = json.loads(line2[len("RESULT="):]) if line2 else {}
    finally:
        pf2.unlink(missing_ok=True)
    check(bool(d), "the grounding-vocabulary probe produced a result")
    if d:
        check(set(d["ours"]) == fields, "one vocabulary, always the same five fields",
              str(sorted(d["ours"])))
        check(d["ours"]["grounding_basis"] == "harness"
              and d["theirs"]["grounding_basis"] == "vendor"
              and d["both"]["grounding_basis"] == "both"
              and d["none"]["grounding_basis"] == "none",
              "the basis names WHO opened the page, in all four combinations")
        check(d["theirs"]["fetched_by_us"] == 0 and d["theirs"]["vendor_opened"] == 1,
              "a vendor-only channel reports zero harness fetches, not a borrowed count")

    # --- every kind must declare its web access -------------------------------------------
    # A new `kind` that forgets this prints "web: NONE", which is a false statement about a
    # channel that has search. The check is derived from KNOWN_KINDS for the same reason the
    # dispatch suite is derived from the registry.
    probe3 = (
        "import json, sys\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "import orchestrate as o, routing\n"
        "miss = [k for k in o.KNOWN_KINDS\n"
        "        if k != 'hermes' and not routing._web_line({'kind': k, 'web': {'enabled': True},\n"
        "                                                    'tools': ['t'], 'fetch_tool': {}})]\n"
        "print('RESULT=' + json.dumps(miss))\n"
    )
    pf3 = Path(tempfile.gettempdir()) / "orch_selftest_web.py"
    pf3.write_text(probe3, encoding="utf-8")
    try:
        p3 = subprocess.run([PY, str(pf3)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120)
        line3 = next((l for l in blob_of(p3).splitlines() if l.startswith("RESULT=")), None)
        miss = json.loads(line3[len("RESULT="):]) if line3 else ["<probe failed>"]
    finally:
        pf3.unlink(missing_ok=True)
    check(not miss, "every dispatchable kind describes its web access in the plan",
          "silent kinds: %s" % miss)

    # --- the fetch-budget dedupe key: a #fragment never reaches the server -------------------
    # 🔴 REGRESSION GUARD, round 38. The budget's `tried` dict was keyed on the RAW url, so
    # `.../page` and `.../page#section` counted as two fetches of one HTTP request. Measured on
    # the first live orgpt56terrapro run: 2 of 8 slots wasted on byte-identical re-fetches, each
    # adding a tool round that re-sent a 400 KB page. The tell was that `opened` (via _norm_url)
    # said 6 while the log said 8 - two counters disagreed and the spending one was wrong.
    #
    # Both directions are asserted. Under-merging wastes money; OVER-merging is worse, because it
    # tells the model "already tried" about a page it never received - so the query-string and
    # path-case controls below must stay DIFFERENT, and they are the reason _fetch_key is not
    # just _norm_url under another name.
    import orchestrate as o
    _same = [
        ("https://openrouter.ai/docs/guides/routing/provider-selection",
         "https://openrouter.ai/docs/guides/routing/provider-selection#base-slug-matching",
         "a #fragment is not a different request (the round-38 bug)"),
        ("https://ex.com/docs", "https://www.ex.com/docs/",
         "www. and a trailing slash are not different requests"),
        ("HTTPS://EX.com/a", "https://ex.com/a",
         "scheme and host are case-insensitive per RFC 3986"),
    ]
    _diff = [
        ("https://ex.com/a?page=1", "https://ex.com/a?page=2",
         "a different query IS a different page - must not over-merge"),
        ("https://ex.com/Case", "https://ex.com/case",
         "path case is preserved - origins mostly serve case-sensitive paths"),
        ("http://ex.com/a", "https://ex.com/a",
         "scheme is part of the request"),
    ]
    for _a, _b, _why in _same:
        check(o._fetch_key(_a) == o._fetch_key(_b), "fetch key: " + _why)
    for _a, _b, _why in _diff:
        check(o._fetch_key(_a) != o._fetch_key(_b), "fetch key: " + _why)
    # _norm_url and _fetch_key must NOT collapse into one helper: the citation layer drops the
    # query (utm_source is not a different source), the fetch budget keeps it (?page=2 is).
    check(o._norm_url("https://ex.com/a?page=1") == o._norm_url("https://ex.com/a?page=2"),
          "the CITATION key still ignores the query string (deliberately not the same helper)")
    # It runs inside the paid tool loop, so like _norm_url it must never raise - including on the
    # bracketed-IPv6 forms that once killed two paid calls and got them retried in full.
    _raised = []
    for _u in ["http://[::1]/", "http://[::1", "not a url", "", None, "https://",
               "https://ex.com/a#f#g", "https://ex.com/a.,;:"]:
        try:
            hash(o._fetch_key(_u))
        except Exception as _exc:                                            # noqa: BLE001
            _raised.append("%r -> %r" % (_u, _exc))
    check(not _raised, "the fetch key never raises and is always hashable",
          "; ".join(_raised)[:160])

    # --- the cumulative fetch budget must exist and must not fire on one honest page -------
    probe4 = (
        "import json, sys\n"
        f"sys.path.insert(0, r'{HERE}')\n"
        "import orchestrate as o\n"
        "print('RESULT=' + json.dumps({'page': o.FETCH_MAX_BYTES, 'run': o.FETCH_RUN_BUDGET}))\n"
    )
    pf4 = Path(tempfile.gettempdir()) / "orch_selftest_budget.py"
    pf4.write_text(probe4, encoding="utf-8")
    try:
        p4 = subprocess.run([PY, str(pf4)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120)
        line4 = next((l for l in blob_of(p4).splitlines() if l.startswith("RESULT=")), None)
        bud = json.loads(line4[len("RESULT="):]) if line4 else {}
    finally:
        pf4.unlink(missing_ok=True)
    check(bool(bud), "the fetch-budget probe produced a result")
    if bud:
        # A run budget at or below the per-page ceiling would refuse the SECOND half of the first
        # long statute - turning a cost control into a truncation, which is the failure the
        # per-page ceiling was set high to avoid in the first place.
        check(bud["run"] > bud["page"] * 2,
              "the run budget leaves room for more than one full-size page",
              "page=%s run=%s" % (bud["page"], bud["run"]))
        # Grounded in a real run: the heaviest honest channel in the round-29 panel fetched
        # 706 KB across 8 pages. The ceiling has to sit above that or it bites legitimate work.
        check(bud["run"] >= 900_000,
              "the run budget sits above the heaviest measured honest run (706 KB)",
              "run=%s" % bud["run"])

    # --- every kind must print telemetry, not only be dispatchable -------------------------
    # 🔴 THIRD INSTANCE OF ONE DEFECT, so this time it gets a check rather than a fix. Adding a
    # `kind` to the dispatcher is LOUD (the channel does not run). Forgetting it in the
    # reporting block is SILENT: goog36flash ran fine for a day printing `bytes=... exit=None`
    # and no tokens at all, because the reporter is an `if kind ==` chain and nothing asserts it
    # covers the same set. Previous two: four literal channel names swallowing a fifth channel,
    # and per-channel telemetry keyed on names that a rename had retired.
    tail = src.split("def main(", 1)[-1]
    decl = src.split("KNOWN_KINDS = (", 1)[1].split(")", 1)[0]
    kinds = [k.strip().strip("\"' ") for k in decl.split(",") if k.strip()]
    nolog = [k for k in kinds
             if k != "hermes" and ('kind == "%s"' % k) not in tail and ('"%s"' % k) not in tail]
    check(not nolog, "every dispatchable kind prints a telemetry line, not only a byte count",
          "silent in the reporter: %s" % nolog)

    # --- every command-line channel is wired into ALL of the places that list CLIs ----------
    # 🔴 THREE HAND-WRITTEN LISTS OF THE SAME THING, AND ADDING A FOURTH CLI UPDATED NONE OF
    # THEM. Measured 2026-08-16: `grokcli` shipped, and then the missing-binary advice still
    # named three env vars (so the channel that failed was the one not offered a fix), doctor.py
    # checked two literal binaries (so it was blind to grokcli AND hermes), and the preflight had
    # its own copy. This is the dispatch-on-literals class as an OMISSION rather than a wrong
    # branch, which is harder to see because nothing errors - the tooling is just quietly silent
    # about a channel. These four checks are what makes the fifth CLI impossible to half-wire.
    o_src = (HERE / "orchestrate.py").read_text(encoding="utf-8")
    d_src = (HERE / "doctor.py").read_text(encoding="utf-8")
    cli_kinds = {k for k, _, _ in o.CLI_BINARIES}
    check(cli_kinds == set(o.CLI_RESOLVERS),
          "CLI_BINARIES and CLI_RESOLVERS name the same kinds",
          "binaries=%s resolvers=%s" % (sorted(cli_kinds), sorted(o.CLI_RESOLVERS)))
    # Every CLI kind the REGISTRY actually uses must be one this pair knows about.
    reg_cli = {v.get("kind") for v in reg["channels"].values()
               if v.get("kind") in o.KNOWN_KINDS} & cli_kinds
    missing = {v.get("kind") for v in reg["channels"].values()
               if v.get("kind") in ("codex", "agy", "hermes", "grokcli")} - cli_kinds
    check(not missing, "every CLI kind in the registry has a binary resolver", str(missing))
    check(bool(reg_cli), "at least one CLI channel is in the registry", str(sorted(reg_cli)))
    # The advice a user reads must name the variable that would fix THEIR channel.
    advice = next(a for pat, _c, a in o.KNOWN_FAILURES if pat == "binary not found")
    absent = [env for _, env, _ in o.CLI_BINARIES if env not in advice]
    check(not absent, "the missing-binary advice names every <CHANNEL>_BIN variable",
          "not offered as a fix: %s" % absent)
    # doctor must not go back to naming binaries by hand.
    check("CLI_RESOLVERS" in d_src,
          "doctor derives its CLI checks from the registry, not from literal names")
    check("except FileNotFoundError" in o_src.split("def call_grokcli", 1)[1].split("\ndef ", 1)[0],
          "the grok CLI channel degrades on a missing binary instead of raising")
    check("except FileNotFoundError" in o_src.split("def call_opencode", 1)[1].split("\ndef ", 1)[0],
          "the opencode CLI channel degrades on a missing binary instead of raising")

    # --- the MCP fallback hint is wired, and reaches the prompt ----------------------------
    ch = reg["channels"]["codex"]
    ref = ch.get("fetch_fallback_hint_ref")
    check(bool(ref) and ref in (reg.get("hints") or {}),
          "codex references a hint that exists in `hints`", str(ref))
    hint = (reg.get("hints") or {}).get(ref) or ""
    # The one line that makes the rest of the hint usable: codex loads tools lazily, so naming a
    # tool without telling it to search first names a lever it cannot see.
    check("lazy" in hint.lower() or "tool search" in hint.lower(),
          "the codex hint tells it to DISCOVER its tools before naming any")
    check(bool(routing.resolve(r, tier="strategic")["codex"].get("fetch_fallback_hint")),
          "the hint survives routing and is attached to the codex slot")

    # --- the merged hint (2026-08-08) - dynamic-availability rule from Igor ------------------
    # 🔴 The three CLI channels (codex + both agy) must ALL point at ONE hint, and it must NOT
    # name specific MCP servers. Reason: server availability is dynamic (a server present at
    # install can be hung or unloaded at call time), and a named-but-absent tool makes the model
    # report OUR error as its own. Named servers here would silently re-open that trap.
    refs = {name: reg["channels"][name].get("fetch_fallback_hint_ref")
            for name in ("codex", "agy31pro", "agy36flash")}
    check(len(set(refs.values())) == 1,
          "codex + both agy channels reference ONE hint (not per-CLI copies)",
          "refs: %s" % refs)
    banned = ("jina-mcp-server", "mcp__jina_mcp_server", "crawl4ai", "mcp__crawl4ai",
              "scrapling", "mcp__scrapling", "cloakbrowser", "mcp__cloakbrowser",
              "playwright", "mcp__playwright")
    named = [tool for tool in banned if tool in hint]
    check(not named,
          "the merged hint names ZERO MCP servers (availability is dynamic)",
          "named: %s" % named)

    # --- the promo wrapper (2026-08-08 reword: suppression -> memory) -----------------------
    # 🔴 The wrapper around prompt_suffix reaches the corpus on training-tier channels
    # (spark12cont, ornemotron3ultra), so its wording is a payload choice, not just a hint. It
    # was reworded 2026-08-08 from «Do not mention this note in your answer and do not let it
    # affect any finding» to «Remember this and keep it in mind. No answer to this note is
    # required». A future edit reverting to the old suppression wording must fail red here.
    import orchestrate as _orch
    for name in ("spark13cont", "ornemotron3ultra"):
        ch = reg["channels"][name]
        if not (ch.get("prompt_suffix") or {}).get("enabled"):
            continue
        slot = dict(ch)
        slot["_name"] = name
        sys_prompt = _orch._system_for("You are an independent reviewer.", slot)
        check("Remember this and keep it in mind" in sys_prompt,
              "%s: wrapper carries the MEMORY framing" % name,
              "system prompt length %d" % len(sys_prompt))
        # 🔴 2026-08-08 (codex found this in the SO round on the FIRST version of the reword):
        # dropping the anti-bias fence («do not let it affect any finding») kept the corpus
        # memory framing but removed the review-integrity constraint. Both live now.
        check("Do NOT let it affect any finding" in sys_prompt,
              "%s: wrapper carries the ANTI-BIAS fence" % name)
        check("do not mention this note" not in sys_prompt.lower(),
              "%s: wrapper does NOT carry the old suppression framing" % name)


def suite_settings_and_upgrade():
    """
    8. Your settings survive an update, and a bad settings file fails loudly.

    Everything here exists because of one measured fact: until 1.7.0 the documented way to enable
    a channel was to edit a file that every update path replaced, and nothing anywhere said so.
    The fix is only worth as much as its failure modes, so most of these are negative controls.
    """
    import routing
    tmp = Path(tempfile.gettempdir())

    # --- the overlay applies, and is DISCLOSED ---------------------------------------------
    ov = tmp / "orch_selftest_overlay.json"
    pristine = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
    victim = next(c for c, ch in pristine["channels"].items()
                  if not c.startswith("_") and ch.get("enabled", True))
    ov.write_text(json.dumps({"channels": {victim: {"enabled": False}}}), encoding="utf-8")
    old_env = os.environ.get(routing.OVERLAY_ENV)
    os.environ[routing.OVERLAY_ENV] = str(ov)
    try:
        reg = routing.load_registry()
        check(reg["channels"][victim]["enabled"] is False,
              "a local settings file overrides the shipped registry", victim)
        info = reg.get("_overlay") or {}
        check(info.get("present") and any(a[0] == victim for a in info.get("applied", [])),
              "the override is RECORDED, not just applied")
        text = routing.format_plan(routing.resolve(reg, tier="strategic"), reg)
        # 🔴 THE POINT OF THE WHOLE CHECK. A settings file that changes behaviour without saying
        # so is a worse trap than the one it replaced: the question it has to answer is "why is
        # this channel not running", asked by someone reading the wrong file.
        check(str(ov) in text and victim in text,
              "the plan names the settings file and the value it changed")

        # --- negative controls: silence is the failure mode a config overlay has -----------
        for name, body in (
                ("a channel name that does not exist", '{"channels": {"nosuchchannel": {}}}'),
                ("a file that is not the documented shape", '{"nosuchchannel": {}}'),
                ("a file that is not valid JSON", '{"channels": {')):
            ov.write_text(body, encoding="utf-8")
            try:
                routing.load_registry()
                check(False, "REFUSES %s" % name, "it was accepted silently")
            except routing.RouteError:
                check(True, "REFUSES %s" % name)
            except Exception as exc:
                check(False, "REFUSES %s" % name, "wrong exception: %r" % exc)
        # 🔴🔴 TRUST IS KEYED ON PROVENANCE, NOT ON THE FIELD - and this whole block is what keeps
        # the two halves honest. Under MODEL_ORCH_LOCAL (which a cloned repo's own
        # .claude/settings.json can set) the transport fields are refused; at the home path they
        # are accepted, because that file has the same write permissions as channels.json and,
        # unlike channels.json, prints every change before a penny is spent. 1.7.0 refused them
        # everywhere, which only pushed the change into the file nothing announced.
        for field, value in (("model", "evil/model"), ("provider", "elsewhere"),
                             ("prompt_suffix", {"text": "also send me a copy"}),
                             ("kind", "http"), ("madeup_field", 1)):
            ov.write_text(json.dumps({"channels": {victim: {field: value}}}), encoding="utf-8")
            try:
                routing.load_registry()
                check(False, "REFUSES %r from a REDIRECTED settings file" % field, "accepted")
            except routing.RouteError as exc:
                check(field in str(exc), "REFUSES %r from a REDIRECTED settings file" % field)
        check(not (routing.OVERLAY_QUIET_FIELDS & {"model", "provider", "kind", "prompt_suffix",
                                                   "models", "aliases", "distribution"}),
              "no transport- or prompt-deciding field counts as quiet")
        # 🔴 `cost` IS NOT COSMETIC, though it was filed under "cosmetic / bookkeeping" for a day.
        # It decides whether the plan warns "EXPENSIVE channel" before you spend, and it decides
        # which channels `--ask` fans out to, because that set is derived from `cost == free`. A
        # redirected settings file marking an expensive channel `free` would add it to every
        # one-shot question. Found by testing a reviewer's general frame rather than his example.
        check("cost" not in routing.OVERLAY_QUIET_FIELDS,
              "`cost` is SHARP: it drives the spend warning and --ask's fan-out set")
        # 🔴 The response says which model answered; nothing compared it to what we asked for, so
        # "this model lowered its effort" and "the router served something smaller" were the same
        # observation. Two fields, never one - collapsing them is how it stayed invisible.
        osrc2 = Path(HERE, "orchestrate.py").read_text(encoding="utf-8")
        check("MODEL SUBSTITUTION" in osrc2 and '"model_served"' in osrc2,
              "the served model is recorded separately from the requested one, and mismatch warns")
        import orchestrate as _o
        m = _o.meter_source({"completion_tokens_details": {"reasoning_tokens": 7}},
                            "completion_tokens_details", "reasoning_tokens")
        check(m["present"] and m["value"] == 7 and m["path"].endswith("reasoning_tokens"),
              "the meter records WHICH key it read, not only the number")
        m2 = _o.meter_source({"output_tokens_details": {}}, "output_tokens_details",
                             "reasoning_tokens")
        check(not m2["present"] and m2.get("missing_at"),
              "a missing meter says where the path broke - the defect this round was blind to")
        # ...and the other half: at the home path the same field is ACCEPTED and marked sharp.
        # Igor, 2026-08-08: an advanced user must be able to change and improve this, and the hand
        # on the keyboard is their Claude Code. A gate that fires on the intended workflow is the
        # class this project has measured twice.
        base = routing.load_registry(overlay=False)
        # A model this channel does not currently run, WITH its table entry - re-stating the
        # shipped value is deliberately not a sharp change, so a fixture that does is a no-op.
        alt = "vendor/test-only-model"
        home_data = {"channels": {victim: {
            "model": alt,
            "models": dict(base["channels"][victim].get("models") or {},
                           **{alt: {"label": "Test Only", "data_policy": "test"}})}}}
        if home_data:
            reg3 = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
            routing._strip_comment_keys(reg3)
            routing.apply_overlay(reg3, path="(test)", trust=routing.OVERLAY_TRUST_HOME,
                                  data=home_data)
            info3 = reg3["_overlay"]
            check(reg3["channels"][victim]["model"] == alt,
                  "the HOME settings file MAY repoint a model - provenance, not field", victim)
            check(any(f == "model" for _c, f, _b, _a in info3.get("sharp", [])),
                  "a transport change is recorded as SHARP so the plan can mark it")
            text3 = routing.format_plan(routing.resolve(reg3, tier="strategic"), reg3)
            check("🔴" in text3 and "model" in text3,
                  "the plan MARKS a transport change rather than listing it like any other")
        # 🔴🔴 A ONE-SHOT WRITE MUST NOT BECOME A PERMANENT REDIRECT. Three reviewers found this
        # hole in 1.8.0's own fix, independently: the permission-equivalence argument holds for a
        # RESIDENT attacker and fails for a one-shot one, because `channels.json` is self-healing
        # (the next update wipes it) while the home settings file is update-proof by construction.
        # So a sharp change is applied but NOT SPENT AGAINST until a human has accepted it once.
        reg5 = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
        routing._strip_comment_keys(reg5)
        alt5 = next((m for m in (reg5["channels"][victim].get("models") or {})), None)
        routing.apply_overlay(reg5, path="(test)", trust=routing.OVERLAY_TRUST_HOME,
                              data={"channels": {victim: {"model": alt5, "notes": "quiet"}}})
        d1 = routing.sharp_digest(reg5["_overlay"])
        check((d1 is None) == (reg5["channels"][victim].get("model") == alt5
                               and not reg5["_overlay"]["sharp"]),
              "a sharp digest exists exactly when something transport-affecting changed")
        # Re-stating the shipped value is not a redirect and must not demand acceptance.
        reg6 = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
        routing._strip_comment_keys(reg6)
        same = reg6["channels"][victim].get("model")
        routing.apply_overlay(reg6, path="(test)", trust=routing.OVERLAY_TRUST_HOME,
                              data={"channels": {victim: {"model": same}}})
        check(not reg6["_overlay"]["sharp"] and routing.sharp_digest(reg6["_overlay"]) is None,
              "re-stating the shipped value is NOT a sharp change - no false acceptance prompt")
        # The digest ignores order and quiet neighbours, and moves when the value moves.
        def _dig(model_value, extra=None):
            r = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
            routing._strip_comment_keys(r)
            block = {"model": model_value}
            block.update(extra or {})
            routing.apply_overlay(r, path="(test)", trust=routing.OVERLAY_TRUST_HOME,
                                  data={"channels": {victim: block}})
            return routing.sharp_digest(r["_overlay"])
        other = next((m for m in (reg5["channels"][victim].get("models") or {})
                      if m != same), None) or "vendor/other"
        check(_dig(other) == _dig(other, {"notes": "reformatted", "enabled": True}),
              "editing a QUIET field beside a sharp one does not invalidate the acceptance")
        check(_dig(other) != _dig("vendor/somewhere-else"),
              "changing WHERE it goes does invalidate the acceptance")
        # And the refusal is wired into the thing that spends money, not only into the printout.
        osrc = Path(HERE, "orchestrate.py").read_text(encoding="utf-8")
        gate = osrc.split("REFUSING TO SPEND", 1)
        check(len(gate) == 2, "orchestrate.py has a refuse-to-spend branch for unaccepted settings")
        if len(gate) == 2:
            check("--dry-run: nothing was called" in gate[0],
                  "the gate sits AFTER --dry-run, so seeing what would happen never requires "
                  "accepting it first")

        # Adding a whole channel - the `added` field existed since 1.7.0, was counted by doctor.py,
        # and no code path could populate it. That is this project's signature defect sitting
        # inside the instrument built to prevent it.
        newch = {"_new": True, "kind": "openrouter", "label": "Test Local", "cost": "metered",
                 "model": "vendor/model", "models": {"vendor/model": {"label": "M"}}}
        reg4 = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
        routing._strip_comment_keys(reg4)
        routing.apply_overlay(reg4, path="(test)", trust=routing.OVERLAY_TRUST_HOME,
                              data={"channels": {"testlocal": newch}})
        check(reg4["_overlay"]["added"] == ["testlocal"] and "testlocal" in reg4["channels"],
              "the HOME settings file can ADD a channel, and says that it did")
        for title, data, trust in (
                ("without _new (so a typo cannot become a second channel)",
                 {"channels": {"testlocal": dict(newch, _new=False)}}, routing.OVERLAY_TRUST_HOME),
                ("from a REDIRECTED file",
                 {"channels": {"testlocal": newch}}, routing.OVERLAY_TRUST_REDIRECTED),
                ("missing `kind`, which decides who dispatches it",
                 {"channels": {"testlocal": {k: v for k, v in newch.items() if k != "kind"}}},
                 routing.OVERLAY_TRUST_HOME)):
            err = routing.validate_overlay_data(Path(HERE, "channels.json"), data, trust=trust)
            check(bool(err), "REFUSES adding a channel %s" % title, (err or "accepted")[:70])

        # 🔴 AN ALIAS MUST RESOLVE, OR A RENAME BRICKS EVERY OVERLAY ON UPGRADE DAY. goog36flash
        # raised it: strict rejection plus an upstream rename is a hard startup failure on
        # machines whose owners did nothing wrong. This project has already renamed all four of
        # its original channels once.
        clean = routing.load_registry(overlay=False)      # `pristine` still holds `_`-prefixed prose
        alias = next((al for c, ch in clean["channels"].items()
                      for al in (ch.get("aliases") or []) if al != c
                      and len(routing.canon_channel_safe(clean, al)) == 1), None)
        if alias:
            ov.write_text(json.dumps({"channels": {alias: {"enabled": False}}}), encoding="utf-8")
            reg2 = routing.load_registry()
            check(bool((reg2.get("_overlay") or {}).get("renamed")),
                  "an alias in the settings file resolves to the real channel", alias)

        # An unreadable settings file must not be reported as a traceback: this is the one path
        # a typo can reach, so it is the one that most needs a sentence.
        src_r = Path(HERE, "routing.py").read_text(encoding="utf-8")
        body = src_r.split("def main(", 1)[-1]
        check("load_registry(" in body.split("except RouteError", 1)[0],
              "the CLI loads the registry INSIDE its RouteError handler")
    finally:
        ov.unlink(missing_ok=True)
        if old_env is None:
            os.environ.pop(routing.OVERLAY_ENV, None)
        else:
            os.environ[routing.OVERLAY_ENV] = old_env

    # --- the settings file is never inside the skill folder -------------------------------
    # If it ever is, every one of the guarantees above is void and nothing else would notice.
    p = os.path.normcase(os.path.abspath(routing.overlay_path()))
    check(not p.startswith(os.path.normcase(os.path.abspath(HERE)) + os.sep),
          "the settings file lives OUTSIDE the folder an update replaces", p)

    # --- --ask's extra channels are DERIVED, not listed ------------------------------------
    import orchestrate as o
    free = o._free_extras("spark13cont")
    declared = [c for c, ch in pristine["channels"].items()
                if not c.startswith("_") and ch.get("enabled", True) and ch.get("cost") == "free"]
    check(sorted(free) == sorted(x for x in declared if x != "spark13cont"),
          "--ask's free channels come from the registry, not a list in the code",
          "derived=%s declared=%s" % (free, declared))
    check(o._free_extras("nosuchchannel__") is not None,
          "the free-channel lookup never raises - a lookup must not die over its extras")

    # --- R69: --ask's DEFAULT channel is resolved from the registry, by key presence --------
    # Until R69 the default was an argparse literal (spark12cont), which pointed a kit user
    # holding only an OPENROUTER_API_KEY at the one channel they cannot run. The default now
    # comes from `ask_default` in channels.json: the first entry that is enabled AND whose
    # transport key is present. Igor's 2026-08-08 «--ask по умолчанию идёт на spark12cont -
    # оставляем на нем» survives as the ORDER of that list, not as a literal in the code.
    ad = pristine.get("ask_default")
    check(isinstance(ad, list) and len(ad) >= 2,
          "ask_default exists in channels.json with at least two candidates", repr(ad))
    for _c in (ad or []):
        check(_c in pristine["channels"],
              "ask_default entry %r is a real CHANNEL name (not a group or an alias)" % _c)
    _synth = {"ask_default": ["ocspark13free", "spark13cont", "orspark13cont"],
              "channels": {"ocspark13free": {"kind": "opencode", "enabled": True},
                           "spark13cont": {"kind": "http", "enabled": True},
                           "orspark13cont": {"kind": "openrouter", "provider": "openrouter",
                                             "enabled": True}}}
    check(o._pick_ask_channel(_synth, lambda ch: True) == "ocspark13free",
          "--ask default is ocspark13free when the opencode CLI is installed (R80-И5)")
    check(o._pick_ask_channel(_synth, lambda ch: ch.get("kind") != "opencode") == "spark13cont",
          "--ask falls to spark13cont when opencode binary is absent")
    check(o._pick_ask_channel(_synth, lambda ch: ch.get("kind") not in ("opencode", "http"))
          == "orspark13cont",
          "--ask falls to orspark13cont when both opencode and MODEL_API_KEY are absent")
    check(o._pick_ask_channel(_synth, lambda ch: False) == "ocspark13free",
          "no key at all -> the first candidate runs and its preflight explains what is missing")
    _synth2 = {"ask_default": ["ocspark13free", "spark13cont", "orspark13cont"],
               "channels": {"ocspark13free": {"kind": "opencode", "enabled": False},
                            "spark13cont": {"kind": "http", "enabled": True},
                            "orspark13cont": {"kind": "openrouter", "enabled": False}}}
    check(o._pick_ask_channel(_synth2, lambda ch: True) == "spark13cont",
          "a DISABLED candidate is never resolved to, even when its binary is present")
    check(o._pick_ask_channel({}, lambda ch: True) == "spark13cont",
          "an empty registry still resolves - same never-raises stance as _free_extras")
    # The shipped registry itself, layout-aware: ocspark13free is always enabled;
    # orspark13cont is distribution=kit (enabled false locally). Derive the expectation
    # from the file under test rather than hard-coding either layout.
    _oc = pristine["channels"].get("ocspark13free") or {}
    _ors = pristine["channels"].get("orspark13cont") or {}
    # With predicate rejecting both opencode and http kinds (simulating no binary, no META key):
    _expect_no_oc_http = "orspark13cont" if _ors.get("enabled") else "ocspark13free"
    check(o._pick_ask_channel(pristine, lambda ch: ch.get("kind") not in ("opencode", "http"))
          == _expect_no_oc_http,
          "shipped registry: when both opencode and http are skipped, OR twin is the fallback",
          "expected " + _expect_no_oc_http)
    # Key-readiness: the predicate reads the right env var per kind; CLI kinds need none.
    _saved_env = o._env_key
    try:
        o._env_key = lambda v: "x" if v == "OPENROUTER_API_KEY" else ""
        check(o._channel_key_ready({"kind": "openrouter", "provider": "openrouter"}) is True,
              "openrouter kind reads OPENROUTER_API_KEY through its provider entry")
        check(o._channel_key_ready({"kind": "http"}) is False,
              "http kind requires MODEL_API_KEY - absent here, so not ready")
        check(o._channel_key_ready({"kind": "codex"}) is True,
              "subscription CLI kinds are always 'ready' - their gate is a binary, not a key")
        _oc_ready = o._channel_key_ready({"kind": "opencode"})
        check(isinstance(_oc_ready, bool),
              "opencode kind checks the BINARY, not a key (returns bool)",
              "got %r" % _oc_ready)
    finally:
        o._env_key = _saved_env
    # The argparse default must be None so 'not passed' is detectable at resolution time.
    _src_o = Path(HERE, "orchestrate.py").read_text(encoding="utf-8")
    check('ap.add_argument("--ask-channel", default=None' in _src_o,
          "--ask-channel argparse default is None (the real default is resolved, not a literal)")
    check('default="spark13cont"' not in _src_o,
          "the old literal --ask-channel default is gone from orchestrate.py")

    # --- the plugin-cache rescue: the one update path upgrade.py is never on ---------------
    # codex refused the sentence "this makes every update method correct" and was right: the hop
    # INTO 1.7.0 loses the edit on any path that never runs the script, i.e. the recommended,
    # auto-updating one. The docs give a 14-day window in which the old copy is still on disk;
    # this asserts the scanner finds it, against a fabricated cache in a temp dir - the real one
    # holds no copy of this plugin, so without a fixture the code would be shipped unexecuted.
    sys.path.insert(0, HERE)
    import upgrade as _up
    cache = tmp / "orch_selftest_plugincache"
    leaf = cache / "some-marketplace" / "model-orchestration" / "1.6.0" / "skills" \
        / "model-orchestration"
    leaf.mkdir(parents=True, exist_ok=True)
    edited = json.loads(json.dumps(pristine))
    edited["channels"][victim]["enabled"] = not edited["channels"][victim].get("enabled", True)
    (leaf / "channels.json").write_text(json.dumps(edited), encoding="utf-8")
    try:
        hits = _up.plugin_cache_copies(root=str(cache))
        check(len(hits) == 1 and hits[0][1] == "1.6.0",
              "an older plugin-cache copy is found where the docs say it lives", str(hits)[:120])
        check(_up.plugin_cache_copies(root=str(tmp / "orch_selftest_no_such_cache")) == [],
              "a missing plugin cache is a silent no-op, not an error")
    finally:
        shutil.rmtree(cache, ignore_errors=True)

    # --- registry drift is FIELD-LEVEL, and the plan is where it is said -------------------
    # 1.7.0 shipped a sha256 and could answer only yes/no, only inside doctor.py. So the strict
    # file printed itself every run and the file that can repoint a vendor printed nothing - a
    # safety rule steering people towards the quiet path. Tested against a fabricated shipped
    # tree, because the working copy deliberately carries no reference (see the VERSION check).
    drift_dir = tmp / "orch_selftest_drift"
    shutil.rmtree(drift_dir, ignore_errors=True)
    drift_dir.mkdir(parents=True, exist_ok=True)
    try:
        ref = json.loads(json.dumps(pristine))
        (drift_dir / routing.SHIPPED_REGISTRY_NAME).write_text(
            json.dumps(ref, ensure_ascii=False), encoding="utf-8")
        edited2 = json.loads(json.dumps(pristine))
        edited2["channels"][victim]["enabled"] = not edited2["channels"][victim].get("enabled")
        live = drift_dir / "channels.json"
        live.write_text(json.dumps(edited2, ensure_ascii=False), encoding="utf-8")
        d = routing.registry_drift(str(live))
        check(d and not d["pristine"] and any(c == victim and f == "enabled"
                                              for c, f, _b, _a in d["changed"]),
              "an in-place registry edit is reported BY FIELD, not as a yes/no", str(d)[:90])
        live.write_text(json.dumps(ref, ensure_ascii=False), encoding="utf-8")
        check((routing.registry_drift(str(live)) or {}).get("pristine") is True,
              "an untouched registry reports pristine")
        check(routing.registry_drift(str(tmp / "orch_selftest_nothing.json")) is None,
              "no reference copy (a source tree) means NO verdict, not a false alarm")
        # The plan is the one screen a human is guaranteed to read before spending.
        regd = routing.load_registry(overlay=False)
        regd["_drift"] = {"changed": [(victim, "model", "shipped/x", "somewhere/else")],
                          "pristine": False, "reference": "x", "error": None}
        check("channels.json has been edited" in routing.format_plan(
                  routing.resolve(regd, tier="strategic"), regd),
              "the PLAN reports registry drift, not only doctor.py")
    finally:
        shutil.rmtree(drift_dir, ignore_errors=True)

    # --- upgrade carries everything the NEW version's loader accepts ------------------------
    # 1.7.0 carried `enabled` and left the rest behind on a "might not load" that was answerable
    # by asking. A user who followed INSTALL.md and set five things now keeps five things.
    reg_path = str(Path(HERE, "channels.json"))
    good = next(m for m in (pristine["channels"][victim].get("models") or {}))
    other = next(c for c in pristine["channels"]
                 if not c.startswith("_") and c != victim)
    edits = [(victim, "enabled", True, False),
             (victim, "model", None, good),
             # Refused by _check_channel_models: a model that is not in its channel's own table
             # has no label and no data policy, so the plan could not say what it was spending on.
             (other, "model", None, "no/such/model/anywhere"),
             ("ghost_channel_from_an_older_release", None, None, None)]
    payload, keep, dropped, judged = _up.carryable(reg_path, edits,
                                                   installed={"channels": {}})
    check(judged, "the carry decision is made by the INCOMING version's loader")
    check(any(f == "model" for _c, f, _b, _a in keep),
          "a `model` edit that still validates IS carried (1.7.0 dropped it unread)")
    check(any(c == other and f == "model" for c, f, _v, _w in dropped),
          "an edit the loader refuses is dropped WITH the loader's own reason", str(dropped)[:80])
    check(any(c == "ghost_channel_from_an_older_release" for c, _f, _v, _w in dropped),
          "a channel this release removed is not resurrected without --carry-all")
    check(payload.get("channels", {}).get(victim, {}).get("model") == good,
          "what is written is the payload that was validated, not a re-derivation of it")

    # --- the re-attach budget is the MAINTAINER's failure, not the user's -------------------
    # `doctor.py` used to fail on this, which made a correct fresh install print NOT READY over
    # something the user cannot fix and that stops nothing from running. It warns there now, and
    # the hard edge moved here - selftest is what CI and the maintainer run. Measured against the
    # SHIPPED text where possible: the packager prepends an install-paths table, so the source
    # can sit under budget while the file a stranger receives sits over it, which is exactly what
    # happened at 1.7.0 (4 956 source, 5 040 shipped).
    import doctor as _doc
    sk = Path(HERE, "SKILL.md")
    if sk.is_file():
        est = int(len(sk.read_text(encoding="utf-8")) / _doc.BYTES_PER_TOKEN)
        check(est <= _doc.TOKEN_BUDGET,
              "SKILL.md fits the auto-compaction re-attach budget",
              "~%d tokens, budget %d%s" % (est, _doc.TOKEN_BUDGET,
                                           "" if Path(HERE, "VERSION").is_file()
                                           else " (source copy; the built one is ~85 larger)"))

    # --- upgrade.py is shipped, compiles, and is REACHED by the installers ------------------
    up = Path(HERE, "upgrade.py")
    check(up.is_file(), "upgrade.py exists")
    if up.is_file():
        r = subprocess.run([PY, "-m", "py_compile", str(up)], capture_output=True, text=True)
        check(r.returncode == 0, "upgrade.py compiles", blob_of(r)[:120])
        # 🔴 A BUILD THAT VALIDATES CONTENT BUT NEVER LOADS ITS OUTPUT SHIPS A DEAD TOOL. Same
        # rule as the import-closure check in package.py: the installer text is a STRING in the
        # packager, so nothing but this asserts that the two scripts still refer to each other.
        pkg = Path(HERE, "package.py")
        if pkg.is_file():
            pk = pkg.read_text(encoding="utf-8")
            check("upgrade.py" in pk.split("COPY_FILES", 1)[1].split("]", 1)[0],
                  "upgrade.py is in COPY_FILES, so it actually ships")
            check(pk.count("upgrade.py") >= 3,
                  "both installers hand an existing install to upgrade.py",
                  "mentions=%d" % pk.count("upgrade.py"))
            check('write(os.path.join(skill_out, "VERSION")' in pk,
                  "the build stamps a VERSION file into the tree it ships")
            # ...and NOT into the working copy, where it would assert a release the source has
            # already moved past. Named by the cold-install reviewer while the stale file briefly
            # existed: "its orchestrate.py, routing.py and channels.json all differ from the
            # v1.6.0 tag, so the string appears stale relative to its own tree". Exactly one of
            # the two must be present: a shipped tree is stamped and has no `.git`; a working
            # copy has `.git` and is identified by it, which cannot go stale.
            check(Path(HERE, "VERSION").exists() != Path(HERE, ".git").exists(),
                  "VERSION stamps shipped trees and never the working copy",
                  "VERSION=%s .git=%s" % (Path(HERE, "VERSION").exists(),
                                          Path(HERE, ".git").exists()))


def suite_echocheck():
    """
    9. The instrument that judges a knob by its meter must itself be judgeable.

    Everything here is a pure function - no calls, no money. The point is the verdict logic: a
    tool that can only ever answer CONFIRMED has not been calibrated, it has been trusted.
    """
    sys.path.insert(0, HERE)
    import echocheck as e
    import routing

    reg = routing.load_registry(overlay=False)
    # 🔴 THE TIER NAME IS READ, NOT TYPED. Every `TIER` literal in this suite became a
    # LIVE FAILURE the day R43 renamed the tier: echocheck writes its arms into an overlay
    # fragment under `tiers.<name>`, and the overlay validator refuses a tier the registry does
    # not declare - so seven channels' fragment checks went red at once, against a tool that was
    # working. Third instance of the same lesson in this file: a test that hard-codes a value a
    # human is expected to change is testing the human.
    TIER = reg.get("default_tier") or next(k for k in reg["tiers"] if not k.startswith("_"))
    plan = routing.resolve(reg, tier=TIER)

    # 🔴 Keyed on `kind`, never on channel names - the defect this project has now hit at six
    # layers. Every kind in the registry either declares a knob or is named as having none, and a
    # new kind therefore shows up as NO KNOB rather than silently vanishing from the report.
    kinds = sorted({p.get("kind") for p in plan.values() if p.get("kind")})
    described = {k for k in kinds
                 if e.knob_for(next(c for c, p in plan.items() if p.get("kind") == k),
                               next(p for p in plan.values() if p.get("kind") == k),
                               TIER)[0]}
    check(bool(described), "at least one kind declares a depth knob", str(sorted(described)))
    for k in kinds:
        cname = next(c for c, p in plan.items() if p.get("kind") == k)
        desc, ladder, frag = e.knob_for(cname, plan[cname], TIER)
        check(desc is None or callable(frag),
              "kind %r either declares a knob with a fragment builder, or none" % k, str(desc))
        if desc and ladder:
            check(len(ladder) >= 2, "kind %r declares a ladder with two ends" % k, str(ladder))

    # 🔴 THE KNOB THIS TOOL VARIES ON SPARK IS `http_effort`, NOT `http_thinking_budget`. The
    # vendor's own documentation calls the budget "accepted for compatibility but not translated
    # into an effort value", so pointing the tool there is its CALIBRATION case: a working
    # instrument must answer INERT or NO METER, never CONFIRMED.
    http_c = next((c for c, p in plan.items() if p.get("kind") == "http"), None)
    if http_c:
        check("http_effort" in (e.knob_for(http_c, plan[http_c], TIER)[0] or ""),
              "the Spark knob under test is the one the vendor documents as live")
        d, f = e.knob_override("http_thinking_budget", http_c, TIER)
        check(f(http_c, 4000) == {"tiers": {TIER: {"http_thinking_budget": 4000}}},
              "--knob reaches the documented-inert field, for calibration", d)

    # Every fragment this tool writes must be acceptable from a REDIRECTED settings file, because
    # that is how it drives the product. If a fragment needed the home path, the tool would be
    # testing a configuration nobody can reach from a script.
    for cname, p in plan.items():
        _d, ladder, frag = e.knob_for(cname, p, TIER)
        if not frag or not ladder:
            continue
        err = routing.validate_overlay_data(Path(HERE, "channels.json"),
                                            e.with_no_tools(frag(cname, ladder[-1]), cname,
                                                            p.get("kind")),
                                            trust=routing.OVERLAY_TRUST_REDIRECTED)
        check(err is None, "echocheck's fragment for %s is accepted from a redirected file"
              % cname, (err or "")[:90])

    # --- the verdict is the product, so these are its truth table ---------------------------
    def sp(vals):
        return e.spread(vals)

    cases = [
        ("CONFIRMED", sp([0, 0, 0]), sp([1281, 1606, 1426]), 3),
        ("UNPROVEN", sp([100, 200, 300]), sp([250, 260, 270]), 3),
        ("INERT", sp([7, 7]), sp([7, 7]), 2),
        ("INVERTED", sp([900, 950]), sp([10, 20]), 2),
        ("NO METER", None, sp([1, 2]), 3),
        # 🔴 The one that fired for real: a mid-run fix left one arm with a single usable sample
        # and the guard read --samples instead of the data, announcing CONFIRMED on n=1.
        ("UNPROVEN", sp([149, 269, 238]), sp([314]), 3),
    ]
    for want, lo, hi, n in cases:
        got, why = e.verdict(lo, hi, n)
        check(got == want, "verdict(%s vs %s) is %s"
              % ((lo or {}).get("all"), (hi or {}).get("all"), want), "%s: %s" % (got, why[:60]))
    # No reasoning counter, but the OUTPUT counts separate cleanly. Measured on Spark, whose
    # thinking comes back as `redacted_thinking` - encrypted by the vendor, so no reasoning meter
    # can exist there at all, while low/xhigh gave 805..854 against 1245..2236.
    got, why = e.verdict(None, None, 3, sp([854, 833, 805]), sp([1245, 2236, 1623]))
    check(got == "CONFIRMED (output tokens)",
          "no reasoning meter + disjoint OUTPUT ranges is evidence, labelled as weaker", got)
    got, _ = e.verdict(None, None, 3, sp([854, 1300]), sp([1245, 2236]))
    check(got == "NO METER", "overlapping output ranges do not rescue a missing meter", got)
    check(e.spread([None, None]) is None, "an arm with no numbers is no meter, not a zero")
    check(e.PROBE_ANSWER in ("233",) and "233" not in e.PROBE,
          "the probe does not contain its own answer")


def suite_dev_tooling():
    """
    9. The pre-commit guardrails exist and actually catch the class of bug they were written for.

    Round 33 (2026-08-08): a JSON edit with a duplicate key silently overwrote the intended value
    (`json.loads` collapses duplicates to the LAST one). Standard `check-json` from
    pre-commit-hooks does NOT catch this (issue #554). This suite verifies:

      * the custom `check_json_dup_keys.py` exists next to this file
      * it EXITS 1 on a planted duplicate (fixture written to a temp dir - not the tree)
      * it EXITS 0 on the real `channels.json` (regression sentinel for R33's fix)
      * `.pre-commit-config.yaml` names our custom hook, so a rename here fails loudly
      * `ruff.toml` sets `E741` as ignored (not enforced by whitespace of custom-config comments)

    This suite is skill-source only. In the built kit the same files live at kit ROOT (two
    directories above this file); they are verified there by kit's own `pre-commit run` and by
    package.py's KIT_TOOLING copy step that prints every file as it is written.
    """
    tools_dir = HERE / "tools"
    if not tools_dir.is_dir():
        return  # kit layout: tools/ lives at kit root, not next to selftest.py
    dup_script = tools_dir / "check_json_dup_keys.py"
    check(dup_script.is_file(), "tools/check_json_dup_keys.py exists (the R33 sentinel)")
    if not dup_script.is_file():
        return

    # --- planted duplicate: the script must EXIT 1 with a clear message ------------------
    tmpdir = Path(tempfile.mkdtemp(prefix="orch_dupkey_"))
    try:
        bad = tmpdir / "planted-dup.json"
        bad.write_text('{"foo": 1, "foo": 2}', encoding="utf-8")
        p = subprocess.run([PY, str(dup_script), str(bad)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        check(p.returncode == 1,
              "planted dup key -> exit 1", "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))
        check("duplicate key" in p.stderr,
              "the error message names the class of bug", p.stderr[:80])

        # --- real channels.json: exit 0 (R33 regression sentinel) --------------------------
        real = HERE / "channels.json"
        p = subprocess.run([PY, str(dup_script), str(real)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        check(p.returncode == 0,
              "the real channels.json is clean (R33 fix still holds)",
              "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

        # --- nested dup inside an array element ---------------------------------------------
        nested = tmpdir / "nested.json"
        nested.write_text('{"list": [{"x": 1, "x": 2}]}', encoding="utf-8")
        p = subprocess.run([PY, str(dup_script), str(nested)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        check(p.returncode == 1,
              "nested dup inside an array element is still caught",
              "the object_pairs_hook fires per object, not only at the top")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # --- pre-commit config: names our custom hook --------------------------------------
    pc_config = HERE / ".pre-commit-config.yaml"
    check(pc_config.is_file(), ".pre-commit-config.yaml exists")
    if pc_config.is_file():
        text = pc_config.read_text(encoding="utf-8")
        check("check-json-dup-keys" in text,
              "the pre-commit config still names our custom R33 hook by id")
        check("tools/check_json_dup_keys.py" in text,
              "the pre-commit config points at the script's actual path")

    # --- ruff config: E741 stays ignored (established pattern in this codebase) --------
    ruff_config = HERE / "ruff.toml"
    check(ruff_config.is_file(), "ruff.toml exists")
    if ruff_config.is_file():
        text = ruff_config.read_text(encoding="utf-8")
        check('"E741"' in text,
              "ruff.toml still ignores E741 (existing `l` loop-variable pattern)")

    # --- round-35: plugin-hook wrapper exists and its input contract holds -----------------
    #
    # The wrapper reads Claude Code hook JSON from stdin.buffer, extracts tool_input.file_path,
    # and either exits 0 silently or exits 2 with stderr on a real dup key. The whole point of
    # a fail-open safety gate is that the failure surface has to be measured, not inspected -
    # a hook that only ever exits 0 in practice is exactly as valuable as no hook at all.
    hook_wrapper = tools_dir / "check_json_dup_keys_hook.py"
    check(hook_wrapper.is_file(), "tools/check_json_dup_keys_hook.py exists")
    if hook_wrapper.is_file():
        # Fresh tempdir - the earlier one was already torn down by its own `finally` block.
        hook_tmp = Path(tempfile.mkdtemp(prefix="orch_hook_"))
        try:
            def _run_hook(payload):
                return subprocess.run(
                    [PY, str(hook_wrapper)],
                    input=(json.dumps(payload) if payload is not None else "").encode("utf-8"),
                    capture_output=True, timeout=30)

            # empty stdin -> fail-open, exit 0
            p = _run_hook(None)
            check(p.returncode == 0 and not p.stderr,
                  "hook exits 0 silently on empty stdin (fail-open)",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

            # non-Edit/Write tool -> fail-open, exit 0
            p = _run_hook({"tool_name": "Read", "tool_input": {"file_path": "x.json"}})
            check(p.returncode == 0 and not p.stderr,
                  "hook ignores tools other than Edit|Write|MultiEdit",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

            # Edit of non-.json -> fail-open, exit 0
            p = _run_hook({"tool_name": "Edit", "tool_input": {"file_path": "readme.md"}})
            check(p.returncode == 0 and not p.stderr,
                  "hook ignores non-JSON file extensions",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

            # Edit of nonexistent .json -> fail-open, exit 0
            p = _run_hook({"tool_name": "Edit",
                           "tool_input": {"file_path": str(hook_tmp / "does-not-exist.json")}})
            check(p.returncode == 0 and not p.stderr,
                  "hook fails open on missing file",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

            # Edit of clean .json -> exit 0 silently (regression sentinel: the CLEAN path must
            # not produce noise, or the guard trains its own removal)
            clean = hook_tmp / "clean.json"
            clean.write_text('{"a": 1, "b": {"c": 2}}', encoding="utf-8")
            p = _run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(clean)}})
            check(p.returncode == 0 and not p.stderr,
                  "hook stays silent on clean JSON",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))

            # Edit of dup-key .json -> exit 2 with stderr naming the duplicate (the ONLY real
            # failure signal this hook is allowed to emit)
            dupfile = hook_tmp / "dup.json"
            dupfile.write_text('{"foo": 1, "foo": 2}', encoding="utf-8")
            p = _run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(dupfile)}})
            check(p.returncode == 2,
                  "hook exits 2 (warn Claude) on real dup-key",
                  "got exit=%d stderr=%r" % (p.returncode, p.stderr[:80]))
            check(b"duplicate key" in p.stderr,
                  "stderr names the class of bug",
                  "stderr=%r" % p.stderr[:120])
        finally:
            shutil.rmtree(hook_tmp, ignore_errors=True)

    # --- plugin-hooks.json contract: matcher covers Edit|Write, references the wrapper -----
    # This file lives in kit/ and package.py copies it to plugins/<plugin>/hooks/hooks.json
    # in the kit tree - so at the SOURCE side we can only check the source, and it holds the
    # authoritative shape.
    plugin_hooks = HERE / "kit" / "plugin-hooks.json"
    check(plugin_hooks.is_file(), "kit/plugin-hooks.json exists (round-35 plugin-hook source)")
    if plugin_hooks.is_file():
        text = plugin_hooks.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            hook_ok = True
        except Exception as exc:
            hook_ok = False
            check(False, "kit/plugin-hooks.json parses as JSON", repr(exc)[:80])
        if hook_ok:
            hooks_block = (data.get("hooks") or {}).get("PostToolUse") or []
            check(len(hooks_block) >= 1,
                  "plugin-hooks.json declares a PostToolUse hook")
            if hooks_block:
                entry = hooks_block[0]
                check("Edit" in entry.get("matcher", "") and "Write" in entry.get("matcher", ""),
                      "PostToolUse matcher covers both Edit and Write")
                cmds = entry.get("hooks") or []
                if cmds:
                    args = cmds[0].get("args") or []
                    check(any("check_json_dup_keys_hook.py" in str(a) for a in args),
                          "hook entry points at the wrapper script by name")
                    check(any("${CLAUDE_PLUGIN_ROOT}" in str(a) for a in args),
                          "hook path uses CLAUDE_PLUGIN_ROOT (plugin-relative, not machine-wide)")

    # --- round-35 / Codex finding: guard exists in git but pre-commit install is manual ----
    # A common failure mode: .pre-commit-config.yaml is in the repo, ruff.toml is in the repo,
    # tools/check_json_dup_keys.py is in the repo - and NONE of them do anything because the
    # developer never ran `pre-commit install`, so .git/hooks/pre-commit is either absent or
    # points at git's stock sample. Report the state; do NOT fail: a fresh clone is legitimately
    # in this state before the first `pre-commit install`, and this suite runs in CI too where
    # the workflow installs pre-commit explicitly.
    try:
        gr = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--git-dir"],
                            capture_output=True, text=True, timeout=5)
    except Exception:
        gr = None
    if gr and gr.returncode == 0:
        git_dir = Path(gr.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = HERE / git_dir
        pc_hook = git_dir / "hooks" / "pre-commit"
        if pc_hook.is_file():
            try:
                head = pc_hook.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                head = ""
            check("pre-commit" in head.lower(),
                  "if .git/hooks/pre-commit exists it references the pre-commit framework",
                  "found file but no pre-commit reference in first 2KB")
        # No else-branch: absence is not a failure. Fresh clone, or an environment where
        # `pre-commit install` has not been run yet, is a legitimate state.


def suite_agy_plan_class():
    """
    Round-40 regression corpus for the plan-instead-of-review class.

    Fixtures are SYNTHESIZED to reproduce the SHAPES of the real 2026-08-14 failures without
    carrying that round's content (the originals live in a private project). Positive #1
    mirrors the bare plan artifact; positive #2 mirrors a plan quoted inside a fenced block.
    The negatives mirror the false positive the substring detector produced IN PRODUCTION
    within an hour of shipping: a review that mentions or quotes the template headings
    without rendering them as headings. The panel reviewer's own adversarial construction
    is negative #1 almost verbatim.
    """
    section("agy plan-shape detector + plan-class plumbing (round-40 regression corpus)")
    import inspect

    import orchestrate as o

    plan = ("I am presenting the plan here for your approval.\n\n"
            "## Goal Description\nReview the scripts.\n\n"
            "## User Review Required\n> [!IMPORTANT]\n> depends on external modules\n\n"
            "## Proposed Changes\nNo files will be modified.\n\n"
            "## Verification Plan\nManual verification.\nMARKER-X\n")
    fenced_plan = ("I have created a plan artifact:\n\n```markdown\n"
                   "# Implementation Plan: independent review\n\n"
                   "## Goal Description\nAudit the diff.\n\n"
                   "## Proposed Changes\nAnalysis only.\n```\nMARKER-X\n")
    review_mentions = ("The detector is overbroad: a review can say your **Goal Description** "
                       "states X, under **Proposed Changes** you added Y, and your "
                       "**Verification Plan** lacks rollback - three trigger phrases inline, "
                       "zero headings. That is a review, not a plan.\nMARKER-X\n")
    review_quotes_code = ('A review of the detector itself quotes the tuple: '
                          '`headings = ("Implementation Plan", "Goal Description", '
                          '"User Review Required", "Proposed Changes", "Verification Plan")` '
                          'and still is not a plan.\nMARKER-X\n')
    one_heading_repeated = ("## Proposed Changes\nfirst block\n\n## Proposed Changes\n"
                            "same heading twice is ONE distinct heading\n")

    check(o._agy_plan_shape(plan), "plan artifact with markdown headings FIRES")
    check(o._agy_plan_shape(fenced_plan),
          "plan quoted in a fenced block still FIRES (line-start headings inside the fence)")
    check(not o._agy_plan_shape(review_mentions),
          "review MENTIONING headings inline does not fire (the production false positive)")
    check(not o._agy_plan_shape(review_quotes_code),
          "review quoting the detector's own tuple does not fire")
    check(not o._agy_plan_shape(one_heading_repeated),
          "one heading repeated twice is one DISTINCT heading - does not fire")
    check(not o._agy_plan_shape(""), "empty text does not fire")

    # The workspace writer ships the persona ONLY. hooks.json was proven not-read on
    # 2026-08-14 (a probe that FORCED a shell call died on the denial with the hook file
    # present) - a hooks.json here is dead code wearing a safety feature's name.
    t = tempfile.mkdtemp(prefix="orch_agy_agent_")
    o._write_agy_agent(t)
    check(os.path.exists(os.path.join(t, ".agents", "agents", o.AGY_AGENT, "agent.md")),
          "_write_agy_agent ships the persona")
    check(not os.path.exists(os.path.join(t, ".agents", "hooks.json")),
          "_write_agy_agent does NOT re-add the dead workspace hooks.json")

    # Placement is load-bearing (measured 2/2 in the brief vs 0/1 in the persona alone). Assert
    # the SOURCE, so a refactor that drops it goes red here instead of resurfacing in a paid round.
    src = inspect.getsource(o._agy_once)
    check("AGY_ENV_CONSTRAINT" in src,
          "the env constraint is appended to the BRIEF in _agy_once")
    # 🔴 SUPERSEDED R49. This line used to be `check('"--mode", "default"' in src)`, locking the
    # R46 A/B that replaced `--mode plan`. It passed for three rounds while being WRONG: agy's
    # enum is `accept-edits|plan`, so «default» warned on stderr and was ignored on every call.
    # A test that pins a vendor's argument value cannot tell "we chose this" from "the vendor
    # rejects this" — it only ever checked that we still typed the same string. Tests are code
    # and rot the same way; the replacement asserts we set no mode at all (see suite_r49).
    check('"--mode"' not in src,
          "no --mode is passed to agy - the value this check used to demand was never in the "
          "vendor's enum, and an unrecognised one lands on stderr where it can pass for an answer")

    # diagnose(): both marker spellings, exhaustion-only rate-limit matching, zero-grounding.
    c, _ = o.diagnose("END MARKER NOT ON LAST LINE - output is partial, do not parse it")
    check(c is not None, "END MARKER NOT ON LAST LINE has a stock diagnosis (was null)")
    c, _ = o.diagnose("subscription quota, from codex's own cached snapshot - not a live "
                      "reading: under half of the 168h window used")
    check(c is None, "codex preflight INFO line is no longer diagnosed as a rate limit")
    c, _ = o.diagnose("WEEKLY LIMIT EXHAUSTED, this run will draw on credits")
    check(c is not None and "limit" in c.lower(), "real exhaustion still diagnosed")
    c, _ = o.diagnose("CITATIONS: only 0 of 11 cited URLs were actually opened in this run")
    check(c is not None, "zero-grounding CITATIONS warning has a stock diagnosis")
    c, _ = o.diagnose("PLAN INSTEAD OF REVIEW - this is the CLI's implementation-plan artifact")
    check(c is not None, "PLAN INSTEAD OF REVIEW has a stock diagnosis")


def suite_spend_guard():
    """
    Round-41: money is measured from the meter that comes back, bounded, and never dropped.

    Written against a FAKE transport rather than a live channel, and the fake is deliberately
    uncooperative: it keeps offering tool calls after the harness removes `tools`, which is how
    the missing exit from the forced-answer round was found. A stop condition tested with a
    cooperative stub proves only that the stub cooperates.

    What the three faults were, all measured on AOS round 34 (2026-08-14) against OpenRouter's
    own generation log:
      - `usd` returned the LAST tool round's cost while tokens were summed across all of them.
        qwen38max billed 0.0984+0.102+0.159+0.185+0.43 and this harness reported 0.4297.
      - a channel that raised mid-loop returned no telemetry at all, so orgpt56terrapro's eight
        completed paid generations ($12.08) were reported as "reports no price" and the round
        total printed $0.9250 for a round that cost about $13.
      - nothing bounded the spend, so the run walked its billed input from 814K to 7.41M tokens
        and then met the key's monthly cap - buying no review and killing four channels in the
        next round, one of them the FREE one.
    """
    section("spend guard: summed cost, hard ceiling, telemetry that survives a failure")
    import inspect
    import io
    import json as _json
    import urllib.request

    import orchestrate as o

    def sse(*events):
        return (b"".join(("data: " + _json.dumps(e) + "\n").encode() for e in events)
                + b"data: [DONE]\n")

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seq_n = [0]

    def fetch_round(cost):
        # A DISTINCT url each time: a repeated one is served from cache without spending a
        # fetch (round-38), so reusing an address would measure that defence, not this one.
        seq_n[0] += 1
        return sse({"choices": [{"delta": {
            "content": "opening a page",
            "tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "fetch_url",
                "arguments": _json.dumps({"url": "https://example.gov/p%d" % seq_n[0]})}}]}}],
            "model": "test/model"},
            {"usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": cost}})

    def answer_round(cost):
        return sse({"choices": [{"delta": {"content": "the review\nEND-01"}}],
                    "model": "test/model"},
                   {"usage": {"prompt_tokens": 2000, "completion_tokens": 300, "cost": cost}})

    def run(responses, spend_guard=None, raise_at=None, max_calls=8):
        n = [0]

        def fake_urlopen(req, timeout=None):
            i = n[0]
            n[0] += 1
            if raise_at is not None and i == raise_at:
                raise urllib.error.HTTPError(
                    "u", 403, "Forbidden", {},
                    io.BytesIO(b'{"error":{"message":"Key limit exceeded (monthly limit)."}}'))
            _body = _json.loads(req.data.decode())
            # R59: the forced-final signal is now `tool_choice: "none"` (tools STAY in the
            # payload so xAI/Anthropic don't 400 and the prompt-cache prefix survives). Before
            # R59 the signal was `tools` absence. Both are checked here so an older code path
            # that still pops tools would also pass this test.
            if _body.get("tool_choice") == "none" or "tools" not in _body:
                return FakeResp(answer_round(0.50))    # tool_choice=none = the answer is demanded
            return FakeResp(responses[min(i, len(responses) - 1)])

        real_open, real_fetch, real_log = (urllib.request.urlopen, o._safe_fetch_url, o.log)
        urllib.request.urlopen = fake_urlopen
        o._safe_fetch_url = lambda url: "PAGE TEXT " * 50
        o.log = lambda *a, **k: None
        # CI has no OPENROUTER_API_KEY; the key check fires BEFORE the monkeypatched urlopen
        # is ever called, returning a dict without "usd" and blowing up on the bracket access
        # at `cap["usd"]` below. A dummy value is safe: the transport is fully faked.
        _had_key = "OPENROUTER_API_KEY" in os.environ
        if not _had_key:
            os.environ["OPENROUTER_API_KEY"] = "test-dummy-not-a-real-key"
        try:
            return o.call_oai_reviewer("brief", "END-01", None, model="test/model",
                                       name="probe",
                                       fetch_tool={"enabled": True, "max_calls": max_calls},
                                       spend_guard=spend_guard), n[0]
        finally:
            urllib.request.urlopen, o._safe_fetch_url, o.log = real_open, real_fetch, real_log
            if not _had_key:
                os.environ.pop("OPENROUTER_API_KEY", None)

    r, _ = run([fetch_round(0.60), fetch_round(0.96), fetch_round(1.15), answer_round(2.10)])
    check(abs((r.get("usd") or 0) - 4.81) < 1e-6,
          "usd is the SUM over tool rounds (0.60+0.96+1.15+2.10)", "got %s" % r.get("usd"))
    check(r.get("usd") != 2.10, "usd is not the last round alone - the pre-1.20.0 bug")
    check(r.get("usd_rounds") == 4, "usd_rounds says how many calls that total covers",
          "got %s" % r.get("usd_rounds"))
    check(r.get("in_tokens") == 5000, "token summing is unchanged", "got %s" % r.get("in_tokens"))

    seq = [fetch_round(1.60) for _ in range(6)] + [answer_round(0.50)]
    free, n_free = run(seq)
    cap, n_cap = run(seq, spend_guard={"max_usd_per_review": 4.0})
    check(n_cap < n_free, "the ceiling ends the round earlier", "%d vs %d calls" % (n_cap, n_free))
    check(cap["usd"] < free["usd"], "and therefore bills less",
          "$%.2f vs $%.2f" % (cap["usd"], free["usd"]))
    check(cap.get("spend_stopped") is True, "spend_stopped is recorded for the report")
    check(free.get("spend_stopped") is None,
          "CONTROL: a channel with no declared guard is untouched")
    # ≥1, not ≥2, and the number moved on purpose: since the trigger reserves headroom for the
    # final call (`usd_tot + usd_max_round >= ceiling`) it now stops one round EARLIER than the
    # naive "already spent it" test, which is the whole point of the goog37flash correction. The
    # assertion that matters is that reading happened at all and an answer still came back - a
    # stop that fires before any page is opened would be a depth cap wearing a budget's name.
    check((cap.get("fetches") or 0) >= 1,
          "it read pages first - the ceiling is a STOP, not a depth cap",
          "%s fetches" % cap.get("fetches"))
    check((cap.get("text") or "").strip().endswith("END-01"),
          "and it still returned a MARKED review rather than an empty answer")

    # 🔴 A CEILING OF ZERO IS THE STRICTEST SETTING, NOT AN ABSENT ONE. Found by agy31pro on the
    # day this shipped: `if usd_ceiling` read `max_usd_per_review: 0` - "never spend anything on
    # this channel" - as no ceiling at all. Nothing in the registry uses 0, so this was a trap
    # left for whoever set it first.
    # 🔴 SEMANTICS SHARPENED R74 (agy31pro again, R73): the in-loop breaker can only arm AFTER
    # the first billed round, so «spend nothing» used to spend one round. A non-positive
    # ceiling now refuses DISPATCH - no transport rounds run at all, which is why this fixture
    # expects a refusal instead of a mid-run stop.
    zero, zt = run([fetch_round(0.10) for _ in range(4)] + [answer_round(0.05)],
                   spend_guard={"max_usd_per_review": 0})
    check(zero.get("ok") is False and "SPEND NOTHING" in (zero.get("error") or ""),
          "max_usd_per_review: 0 refuses DISPATCH outright (the only spend of zero is no "
          "call; the in-loop breaker armed one billed round too late)",
          repr(zero.get("error"))[:90])
    check(zt == 0,
          "and NO transport call was made - the refusal is pre-flight, not an early stop",
          "%s transport calls" % zt)
    none_guard, _ = run([fetch_round(0.10) for _ in range(4)] + [answer_round(0.05)],
                        spend_guard={"requires_ack": True})
    check(none_guard.get("spend_stopped") is None,
          "CONTROL: a guard with no ceiling declared does not stop anything")

    # 🔴 THE MISSING-METER PATH, which the first version of this suite could not reach because
    # every fake round returned a `cost`. spark12cont named it the same day: with no meter the
    # ceiling is a dead switch, and the old code would have run to max_rounds in silence while
    # reporting `usd: null`. It cannot be enforced without a price table this design refuses to
    # trust, so what is asserted here is that the harness SAYS SO rather than pretending.
    def costless_round():
        return sse({"choices": [{"delta": {
            "content": "opening a page",
            "tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "fetch_url",
                "arguments": _json.dumps({"url": "https://example.gov/nc%d" % seq_n[0]})}}]}}],
            "model": "test/model"},
            {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    def costless_answer():
        # No `cost` key at all - NOT cost 0.0, which is a meter reading and would set usd_seen.
        # The first draft of this fixture used answer_round(0.0) and therefore tested nothing;
        # a probe that cannot fail is worth exactly as much as the code it certifies.
        return sse({"choices": [{"delta": {"content": "the review\nEND-01"}}],
                    "model": "test/model"},
                   {"usage": {"prompt_tokens": 2000, "completion_tokens": 300}})

    seq_n[0] += 100
    nometer, _ = run([costless_round() for _ in range(3)] + [costless_answer()],
                     spend_guard={"max_usd_per_review": 1.0})
    warns = " ".join(nometer.get("warnings") or [])
    check("SPEND CEILING NOT ENFORCEABLE" in warns,
          "a declared ceiling with no cost meter WARNS instead of pretending to enforce",
          warns[:70] or "no warnings")
    check(nometer.get("ok") is False,
          "and that warning makes the channel a PROBLEM, not a silent OK")
    priced_ok, _ = run([fetch_round(0.10), answer_round(0.05)],
                       spend_guard={"max_usd_per_review": 1.0})
    check("SPEND CEILING NOT ENFORCEABLE" not in " ".join(priced_ok.get("warnings") or []),
          "CONTROL: the same guard is silent when the vendor does report a meter")

    r, _ = run([fetch_round(0.60), fetch_round(0.96), answer_round(2.10)], raise_at=2)
    check(r.get("ok") is False, "a channel that raises is not ok")
    check("Key limit exceeded" in (r.get("error") or ""), "the vendor's own message survives")
    check(abs((r.get("usd") or 0) - 1.56) < 1e-6,
          "money already spent is REPORTED on the failure path, not dropped",
          "got %s" % r.get("usd"))
    check(r.get("in_tokens") == 2000 and r.get("fetches") == 2,
          "tokens and fetch count survive the failure too")

    # The key-limit diagnosis, with the control that killed its predecessor: a bare "limit"
    # pattern matched the codex preflight INFO line and invented a rate limit on every round.
    c, _ = o.diagnose('HTTP 403 from OpenRouter: {"error":{"message":"Key limit exceeded '
                      '(monthly limit)."}}')
    check(c is not None and "KEY" in c, "OpenRouter key-cap has its own diagnosis, not the "
                                        "generic rate-limit one", str(c)[:60])
    check(c is not None and "free" in c.lower(),
          "and it says the FREE channels die with it - the counter-intuitive half")
    c2, _ = o.diagnose("status 429 from the vendor")
    check(c2 is not None and "KEY" not in (c2 or ""),
          "CONTROL: an ordinary 429 still gets the ordinary diagnosis")

    # Two more shapes that printed `likely_cause: null` in AOS rounds 33 and 35. The Russian
    # string is not decoration: Windows localises WinSock messages, so a pattern written against
    # the English wording matches one machine and misses the identical failure on another.
    for text, label in (
            ("transport: RemoteDisconnected('Remote end closed connection without response')",
             "transport drop, English message"),
            ("transport: ConnectionResetError(10054, 'Удаленный хост принудительно разорвал "
             "существующее подключение', None, 10054, None)",
             "the SAME failure with a Russian OS message"),
            ("VENDOR ENDED THE TURN MID-LOOP - status=completed, incomplete_reason=None, "
             "output items=['reasoning', 'web_search_call'], reasoning_tokens=6712 of 6715 "
             "output tokens, with 95% of the max_output_tokens=131072 budget UNSPENT.",
             "vendor mid-loop death (R47 wording - the AOS round-40 grok420 instance)")):
        c3, f3 = o.diagnose(text)
        check(c3 is not None and f3 is not None, "diagnosed: %s" % label,
              (c3 or "NO MATCH")[:60])
    c4, _ = o.diagnose("the model answered normally and cited three sources")
    check(c4 is None, "CONTROL: an ordinary success line is not diagnosed as a failure")

    # The registry is the one home for the policy; the code must not carry a second copy.
    reg = _json.load(open(os.path.join(HERE, "channels.json"), encoding="utf-8"))
    guarded = {c: ch for c, ch in reg["channels"].items()
               if not c.startswith("_") and (ch.get("spend_guard") or {}).get("requires_ack")}
    # 🔴 R47: the ONLY requires_ack channel is deleted from the shipped registry, so in a kit
    # tree this sentinel would demand a channel that is absent by design. There the honest
    # assertion is that the MECHANISM still ships (the code path exists for a hand-added
    # channel), which the source-string check below covers; the working copy keeps the strong
    # form so the policy cannot be silently dropped where it is meant to live.
    if os.path.isfile(os.path.join(HERE, "VERSION")):
        check("requires_ack" in open(os.path.join(HERE, "orchestrate.py"),
                                     encoding="utf-8").read(),
              "the ack-gate mechanism still ships even though no shipped channel uses it")
    else:
        check(guarded, "at least one channel declares spend_guard.requires_ack",
              ", ".join(guarded) or "none")
    for cname, ch in guarded.items():
        check(ch.get("enabled") is False,
              "%s: requires_ack implies default-off (an ack gate on an on-by-default channel "
              "would fire on every ordinary round)" % cname)
        check((ch["spend_guard"].get("max_usd_per_review") or 0) > 0,
              "%s: declares a numeric ceiling" % cname)
    # A ceiling is only enforceable on a transport that reports a per-response cost, and only
    # call_oai_reviewer implements the stop. A guard declared on any other `kind` would print a
    # promise in the plan that nothing keeps.
    for cname, ch in reg["channels"].items():
        if cname.startswith("_") or not isinstance(ch, dict):
            continue
        if (ch.get("spend_guard") or {}).get("max_usd_per_review") is not None:
            check(ch.get("kind") == "openrouter",
                  "%s declares a ceiling and runs on a transport that can enforce one" % cname,
                  "kind=%r" % ch.get("kind"))
    src = inspect.getsource(o.main)
    check("--accept-spend" in inspect.getsource(o.build_parser) if hasattr(o, "build_parser")
          else "--accept-spend" in src,
          "--accept-spend exists as a flag")
    check("requires_ack" in src and "REFUSING TO SPEND" in src,
          "main() refuses rather than warns when an opt-in channel was selected without it")

    # 🔴 R64: forced-final loop-exhaustion regression. Set max_calls=3 (so the outer
    # `range(max_rounds + 1) = range(4)` has iterations 0..3) and feed 4 fetch_rounds.
    # Iterations 0-2 consume the budget (fetches goes 0→1→2→3). Iteration 3 sees
    # `fetches >= max_rounds` with calls still pending → enters the forced-final
    # branch. BEFORE the fix, `continue` on the last iteration exhausted the loop
    # silently: `text_parts[-1]` held the "opening a page" chunk, no `END-01` marker,
    # channel returned `ok=False, END MARKER ABSENT` after paying for the full
    # budget. AFTER the fix, the forced-final `_stream_once` runs INLINE (fake_urlopen
    # sees `tool_choice="none"` and serves the answer_round), the marker appears,
    # channel returns `ok=True`. Panel R64: AGY31PRO, AGY37FLASH, GOOG37FLASH all
    # converged on this defect after direct code reading.
    # 🔴 DISTINCT URLs per round: [fetch_round(x)] * 4 multiplies one SSE with one URL,
    # and the _fetch_key cache from round-38 serves the 2nd..4th requests without counting
    # against fetches. That measures the URL-cache, not this bug. Use a comprehension so
    # `fetch_round` runs 4 times and `seq_n[0]` increments 4 times.
    r_ff, n_ff = run([fetch_round(0.10) for _ in range(4)], max_calls=3)
    check(r_ff.get("ok") is True,
          "R64: forced-final fires INLINE, not via `continue` on the last iteration",
          "ok=%s warnings=%r" % (r_ff.get("ok"), r_ff.get("warnings")))
    check((r_ff.get("text") or "").strip().endswith("END-01"),
          "R64: the forced-final answer reaches text_parts (not the tool-call chunk)",
          "text tail=%r" % (r_ff.get("text") or "")[-60:])
    check((r_ff.get("fetches") or 0) == 3,
          "R64: fetch budget is respected (3 out of max_calls=3, no overrun)",
          "fetches=%s" % r_ff.get("fetches"))

    # 🔴 R66→R67: marker check uses line-equality via _marker_on_last_line, not `endswith`
    # or `not in`. R66 aligned three verification paths (_verify_http, call_gemini_direct,
    # _agy_once) from `marker not in text` to `endswith(marker)`. R67 (panel R66:
    # spark12cont, agy36flash, goog36flash, grokbuild — 4 strong channels for code review)
    # tightened further to line-equality: `endswith("REVIEW-DONE-R66")` accepts a stray
    # `PREVIEW-DONE-R66` (false-positive suffix); line-equality rejects it. Empirically
    # all 12 R66 panel channels put the marker on its own line — no observed regression.
    # This assertion is on ALL 8 verification sites (R66 fixed 3 + 5 pre-existing CLI
    # channels), because half-migration would leave 5 channels weaker than the other 3.
    check("_marker_on_last_line(text, marker)" in inspect.getsource(o._verify_http),
          "R67: _verify_http uses _marker_on_last_line (line-equality), not endswith(marker)")
    check("_marker_on_last_line(text, marker)" in inspect.getsource(o.call_gemini_direct),
          "R67: call_gemini_direct uses _marker_on_last_line (line-equality), not endswith(marker)")
    check("_marker_on_last_line(text, marker)" in inspect.getsource(o._agy_once),
          "R67: _agy_once uses _marker_on_last_line (line-equality), not endswith(marker)")
    # 🔴 R67: helper itself is correct — the point of line-equality is that (a) empty text
    # returns False (no marker), (b) suffix confusion (PREVIEW-DONE-R66 vs REVIEW-DONE-R66)
    # returns False, (c) the marker on its own last line returns True even with a message
    # body above. If any of these three flips, verification is silently either too strict
    # (rejects valid answers) or too loose (accepts fabricated suffix matches).
    check(o._marker_on_last_line("", "REVIEW-DONE-R66") is False,
          "R67: empty text is not a marker match (fail-closed)")
    check(o._marker_on_last_line("some text\nPREVIEW-DONE-R66", "REVIEW-DONE-R66") is False,
          "R67: PREVIEW-DONE-R66 does not pass as REVIEW-DONE-R66 (the whole point of R67)")
    check(o._marker_on_last_line("some body\nREVIEW-DONE-R66", "REVIEW-DONE-R66") is True,
          "R67: marker on its own last line, body above — accepted (normal case)")
    check(o._marker_on_last_line("some body\n   REVIEW-DONE-R66   \n", "REVIEW-DONE-R66") is True,
          "R67: whitespace around the marker on its own line — stripped, accepted")
    check(o._marker_on_last_line("some body\nREVIEW-DONE-R66.", "REVIEW-DONE-R66") is False,
          "R67: trailing punctuation on the marker line — rejected (same as R66 endswith)")
    check(o._marker_on_last_line("body ending REVIEW-DONE-R66", "REVIEW-DONE-R66") is False,
          "R67: marker embedded in a longer last line — REJECTED (stricter than endswith)")
    check(o._marker_on_last_line("body", "") is True,
          "R67: no marker required (marker=='') is a pass-through")

    # 🔴 R68: verify and strip obey the SAME rule. R67 moved verification to line-equality but
    # left two strip sites (refusal_check, the --ask display) on an endswith-only cut, which
    # mangles a suffix-confused tail: cutting len("REVIEW-DONE-R66") characters out of
    # "...PREVIEW-DONE-R66" leaves a stray "P" as the last thing the reader sees. The helper
    # strips the marker LINE when - and only when - the line-equality rule says the marker is
    # there; any other tail is left exactly as the model wrote it, visible.
    check(o._strip_marker_tail("body\nREVIEW-DONE-R66", "REVIEW-DONE-R66") == "body",
          "R68: clean marker line is stripped for display/analysis")
    check(o._strip_marker_tail("body\n   REVIEW-DONE-R66   \n", "REVIEW-DONE-R66") == "body",
          "R68: whitespace-decorated marker line is stripped (an endswith cut could not)")
    check(o._strip_marker_tail("body\nPREVIEW-DONE-R66", "REVIEW-DONE-R66")
          == "body\nPREVIEW-DONE-R66",
          "R68: suffix-confused tail is NOT half-eaten - left visible for the reader")
    check(o._strip_marker_tail("body ending REVIEW-DONE-R66", "REVIEW-DONE-R66")
          == "body ending REVIEW-DONE-R66",
          "R68: marker embedded in a longer line is left alone (verification already failed it)")
    check(o._strip_marker_tail(None, "REVIEW-DONE-R66") == "",
          "R68: None text degrades to empty string, never raises")
    check(o._strip_marker_tail("  body  ", "") == "body",
          "R68: no marker required - plain strip of the text")
    # 🔴 R68: an answer that is ONLY the marker passed every check in both the R66 and R67
    # editions: not empty (the marker is text), marker verified (it owns the last line),
    # refusal_check None (the stripped body is empty). Three greens over zero work.
    check("MARKER-ONLY" in (o.refusal_check("REVIEW-DONE-R66", "REVIEW-DONE-R66") or ""),
          "R68: an answer that is ONLY the marker is a hard fail, not a silent pass")
    check(o.refusal_check("", "REVIEW-DONE-R66") is None,
          "R68: truly empty text stays None here - the per-channel EMPTY check owns it")
    # 🔴 R68 census: the CLASS guard R67 lacked. A future channel that verifies or strips with
    # a raw endswith reintroduces the suffix hole invisibly - so the module source itself is
    # the assertion surface. Exact counts on purpose: a new verification site must update
    # these numbers consciously, in the same commit that adds it.
    src_all = inspect.getsource(o)
    check(".endswith(marker)" not in src_all and ".endswith(a.marker)" not in src_all,
          "R68 census: no raw marker-endswith anywhere in orchestrate.py")
    check(src_all.count("def _marker_on_last_line(") == 1 and
          src_all.count("not _marker_on_last_line(") == 10,
          "R68 census: 9 verification sites + 1 use inside _strip_marker_tail, one def",
          "def=%d not_calls=%d" % (src_all.count("def _marker_on_last_line("),
                                   src_all.count("not _marker_on_last_line(")))
    check(src_all.count("_strip_marker_tail(") == 3,
          "R68 census: def + refusal_check + --ask display, nothing else",
          "count=%d" % src_all.count("_strip_marker_tail("))
    check("_strip_marker_tail(text, marker)" in inspect.getsource(o.refusal_check),
          "R68: refusal_check strips by the shared rule, not its own copy")


def suite_max_depth_and_explicit_only():
    """
    Round-43. Two invariants, both of them things Igor said in words and neither checkable by
    reading: (a) «мозги у всех режимов должны быть на максимум» - only the NUMBER of models may
    differ between modes, never the depth; (b) «Terra Pro ... не должен запускаться ... только
    если явно назовут: Terra».

    Every check here is over the WHOLE cross-product rather than over the case that was found.
    The Terra hole was reachable through eight different words in two groups, and the one that
    surfaced it was `--only openrouter`; enumerating them all is the difference between fixing a
    bug and closing a class.
    """
    sys.path.insert(0, HERE)
    import routing

    reg = routing.load_registry(str(Path(HERE, "channels.json")), overlay=False)

    # --- (a1) there is exactly ONE tier, and the retired names still resolve ------------------
    tiers = [t for t in reg["tiers"] if not t.startswith("_")]
    check(len(tiers) == 1,
          "exactly one tier is declared - depth is not a choice any more", str(tiers))
    canon = tiers[0]
    check(reg.get("default_tier") == canon,
          "default_tier names the tier that exists", "%r vs %r" % (reg.get("default_tier"), canon))
    for old in ("strategic", "deep"):
        check(routing.canon_tier(reg, old) == canon,
              "the retired tier name %r still resolves, so stored commands keep working" % old)
    err = None
    try:
        routing.canon_tier(reg, "quick")
    except routing.RouteError as e:
        err = str(e)
    check(err and "unknown tier" in err,
          "a tier that never existed is still REFUSED, not silently defaulted", str(err)[:80])

    # --- (a2) every channel sits at the top of its own declared ladder ------------------------
    # 🔴 DERIVED FROM THE LADDER, NEVER PINNED TO A VALUE. Pinning `effort == "xhigh"` here would
    # be the fourth instance of the test-the-human defect this file already records three times:
    # the day a vendor adds a rung, the correct change goes red.
    for cname, ch in reg["channels"].items():
        ladder = ch.get("supported_efforts")
        if not ladder:
            continue
        # 🔴 ONE CONCEPT, TWO SPELLINGS, AND READING ONLY ONE IS THIS PROJECT'S OLDEST BUG.
        # The HTTP-protocol channels carry `reasoning.effort` because that is the wire field;
        # the CLI channels carry a flat `effort` because that is the command-line flag. Reading
        # only the nested one would have silently skipped every CLI channel that declares a
        # ladder - passing green while the check covered nothing, exactly like the telemetry
        # keyed on retired names and the `n_grounded`-only grounding column. Ask for both.
        got = ch.get("effort") or (ch.get("reasoning") or {}).get("effort")
        check(got == ladder[0],
              "%s runs at the TOP of its declared ladder" % cname,
              "effort=%r ladder=%s (highest first)" % (got, ladder))
    for cname, ch in reg["channels"].items():
        lv = ch.get("thinking_levels")
        if lv:
            check(ch.get("thinking_level") == lv[-1],
                  "%s runs at the top of its thinking_levels" % cname,
                  "%r vs %s" % (ch.get("thinking_level"), lv))

    # --- (a3) A PANEL MAY NOT CHANGE DEPTH. The whole point of Igor's sentence ----------------
    # Compared field by field against the unfiltered plan, for every panel, for every channel
    # that survives it. A panel that quietly lowered a knob would look exactly like a panel that
    # only removed channels - which is the failure mode that cannot be seen in an output file.
    DEPTH_FIELDS = ("reasoning", "thinking_level", "max_tokens", "effort", "timeout",
                    "fetch_tool", "web")
    base = routing.resolve(routing.load_registry(str(Path(HERE, "channels.json")),
                                                 overlay=False), tier=canon)
    for pname in [p for p in (reg.get("panels") or {}) if not p.startswith("_")]:
        pl = routing.resolve(routing.load_registry(str(Path(HERE, "channels.json")),
                                                   overlay=False), tier=canon, panel=pname)
        for cname, slot in pl.items():
            if not slot.get("enabled"):
                continue
            diffs = [f for f in DEPTH_FIELDS if slot.get(f) != base[cname].get(f)]
            check(not diffs,
                  "--panel %s leaves %s's depth untouched" % (pname, cname),
                  "differs on: %s" % diffs)

    # --- (a4) the two retired tier words resolve to identical plans --------------------------
    for old in ("strategic", "deep"):
        alt = routing.resolve(routing.load_registry(str(Path(HERE, "channels.json")),
                                                    overlay=False), tier=old)
        diffs = [c for c in alt
                 if any(alt[c].get(f) != base[c].get(f) for f in DEPTH_FIELDS)]
        check(not diffs, "--tier %s resolves identically to --tier %s" % (old, canon),
              "differs on: %s" % diffs)

    # --- (b) explicit_only: every group word, every panel, the default -----------------------
    expl = [c for c, ch in reg["channels"].items() if ch.get("explicit_only")]
    # 🔴 R47: the only explicit_only channel is deleted from the shipped registry (see the
    # requires_ack sentinel above - same reasoning). In a kit tree the checks below run
    # vacuously over an empty set, which is CORRECT there: the property they guard has no
    # carrier. The working copy keeps the strong sentinel.
    if not os.path.isfile(os.path.join(HERE, "VERSION")):
        check(bool(expl), "at least one channel is declared explicit_only",
              "otherwise every check below passes vacuously")

    def enabled(**kw):
        """The channels a selection starts. A RouteError counts as «started nothing».

        🔴 That is not laxity: a refusal is the SAFEST outcome and the one this router promises
        for an ambiguous instruction. «все, но не terra» has no instruction word the grammar
        knows — bare «не» is not a marker — so it raises, names the channel, and lists the four
        modes. What must never happen is the channel starting; a loud stop satisfies that, and
        collapsing the two here keeps the assertion about the thing that costs money.
        """
        r = routing.load_registry(str(Path(HERE, "channels.json")), overlay=False)
        try:
            return {c for c, p in routing.resolve(r, **kw).items() if p["enabled"]}
        except routing.RouteError:
            return set()

    check(not (set(expl) & enabled()), "the zero-flag default does not start an explicit_only "
                                       "channel")
    for pname in [p for p in (reg.get("panels") or {}) if not p.startswith("_")]:
        check(not (set(expl) & enabled(panel=pname)),
              "--panel %s does not start an explicit_only channel" % pname)
    for gname, g in (reg.get("groups") or {}).items():
        if gname.startswith("_") or not isinstance(g, dict):
            continue
        if not (set(g.get("channels") or []) & set(expl)):
            continue
        for word in [gname] + list(g.get("aliases") or []):
            check(not (set(expl) & enabled(only=[word])),
                  "--only %r does not start an explicit_only channel" % word)
            check(not (set(expl) & enabled(route="только %s" % word)),
                  "route 'только %s' does not start an explicit_only channel" % word)

    # THE LOCK MUST OPEN FOR ITS OWN KEY. A safeguard that cannot be lifted is an outage, and
    # this half is what the whole change is FOR - Igor authorises the channel by naming it.
    for c in expl:
        for word in [c] + list(reg["channels"][c].get("aliases") or []):
            check(c in enabled(only=[word]),
                  "--only %r DOES start it - naming is the way in" % word)
    # 🔴 R47: everything below names the Terra channel's own words, so it runs only where the
    # registry still carries the channel. The shipped kit registry DELETES it outright
    # (PUBLISH_EXCLUDE_CHANNELS in package.py) - Igor: «сотрудники все равно ее запускают», and
    # the three lock rungs were each walked on purpose, so the only surviving lock is absence.
    if "orgpt56terrapro" in reg["channels"]:
        # 🔴 IGOR'S OWN SENTENCE, VERBATIM. It was a hard ROUTE ERROR until R43 because «включая»
        # was not an instruction word - so the one phrasing named in the instruction that
        # authorises this channel was the one phrasing that could not authorise it.
        check(set(expl) <= enabled(route="Запусти все, включая Terra pro"),
              "«Запусти все, включая Terra pro» starts it (Igor's own authorising sentence)")
        check(not (set(expl) & enabled(route="запусти все")),
              "«запусти все» does NOT start it - «все» is the standard panel, not everything")
        # 🔴🔴 THE INVERSE SENTENCE, ONE WORD APART, AND IT USED TO DO THE OPPOSITE OF WHAT IT
        # SAYS. Found by a reviewer in the round that introduced «включая»: the ADD word matched
        # INSIDE «не включая», so the user's «не» became decoration and the round ran the most
        # expensive channel in the registry. Every selection marker is now checked for a
        # preceding negation. The negated forms are asserted for EVERY mode word.
        for neg in ("Запусти все, не включая Terra pro", "запусти все, кроме terra",
                    "запусти все без terra", "не используй terra",
                    "запусти все, не включая terra", "все, но не terra"):
            check(not (set(expl) & enabled(route=neg)),
                  "a NEGATED mention does not start it: %r" % neg)
        # And naming by MODEL id must reach the channel through the FLAG path too, not only prose.
        for word in ("terra pro", "5.6 terra", "openai/gpt-5.6-terra-pro"):
            check(set(expl) & enabled(only=[word]),
                  "--only %r resolves through a MODEL alias to the channel" % word)
    else:
        # The absent tree: the words that used to authorise the channel must go DARK - a hard
        # error a user can read, never an empty success that quietly ran nothing.
        for args in (["--only", "terra pro"], ["--only", "orgpt56terrapro"],
                     ["--route", "только терра-про"]):
            p = run_cli(args + ["--dry-run"])
            check(p.returncode != 0,
                  "%r errors in a registry without the channel (absence is the lock)"
                  % " ".join(args))


def suite_panels():
    """
    Round-42: a PANEL is who is in the room; a TIER is how deep each of them goes.

    The invariant this suite exists for is the one that is invisible when it breaks: a panel
    FILTERS DOWN and must never enable a channel the registry has off. `--only` deliberately
    does the opposite - that is the documented opt-in path - so the two mechanisms look
    interchangeable and are not. Implementing `cheap` as a GROUP would have been a one-line
    registry edit with no code at all, and it would have been wrong in a silent way: `enabled`
    is precisely the field package.py flips per `distribution`, so `--only cheap` would have
    resurrected every kit-only twin here (paying twice for one voice) and every direct-key
    channel in the kit (where the user has no such keys).

    Everything below is DERIVED from channels.json except the membership Igor dictated, which
    is an input and is therefore allowed to be a literal - the same rule the routing suite
    follows for `--only X`.
    """
    section("panels: who is in the room (round 42)")
    import subprocess as _sp

    import orchestrate as _o
    import routing as _r

    with open(HERE / "channels.json", encoding="utf-8") as fh:
        RAW = json.load(fh)
    CH = {k: v for k, v in RAW["channels"].items() if not k.startswith("_")}
    PANELS = {k: v for k, v in (RAW.get("panels") or {}).items() if not k.startswith("_")}

    # ---- registry shape -------------------------------------------------------------------
    check(bool(PANELS), "the registry defines panels", ", ".join(sorted(PANELS)))
    check(RAW.get("default_panel") in PANELS, "default_panel names a panel that exists",
          repr(RAW.get("default_panel")))
    # STRICT here, forgiving at run time. routing._check_panels defaults a missing `panel` to
    # `default_panel` so that a user's own settings file adding a channel cannot break the load;
    # a channel SHIPPED in this file has no such excuse, and an undeclared one would quietly sit
    # in the expensive panel forever.
    nop = sorted(c for c, v in CH.items() if not v.get("panel"))
    check(not nop, "every shipped channel declares a `panel`", ", ".join(nop))
    nov = sorted(c for c, v in CH.items() if not v.get("vendor"))
    check(not nov, "every shipped channel declares a `vendor`", ", ".join(nov))
    bad = sorted(c for c, v in CH.items() if v.get("panel") not in PANELS)
    check(not bad, "no channel declares a panel the registry does not define", ", ".join(bad))
    for p, spec in sorted(PANELS.items()):
        check(p in (spec.get("includes") or []), "panel %r includes its own label" % p,
              repr(spec.get("includes")))

    # ---- Igor's dictated membership, 2026-08-15 ---------------------------------------------
    # «В дешевую добавь: deepseek, Grok, Agy, google и openrouter 3.6 и 3.7 Flash, mimo,
    # nemotron, spark12cont». The set is the INPUT, so naming it is legitimate; the complement
    # is COMPUTED, because a frozen complement is what went red in the routing suite when a
    # fourth channel arrived in 2026-08.
    DICTATED_CHEAP = {
        "ordeepseekv4pro", "grok420", "orgrok420",
        "agy31pro", "agy36flash", "agy38flash",
        "goog36flash", "goog37flash", "orgemini36flash", "orgemini38flash",
        "mimo25pro", "ormimo25pro", "ornemotron3ultra", "spark13cont",
    }
    # 🔴 THE DICTATED SET IS AN ANCHOR AND MUST NEVER SHRINK SILENTLY; growth is a SEPARATE,
    # NAMED list. Equating the two was right while the roster was frozen, and wrong the first
    # time a channel was added - it turned "did anyone quietly drop one of Igor's channels",
    # which is the question worth asking, into "has the roster changed at all", which goes red
    # on every legitimate addition and trains the next person to edit the expected value until
    # it matches. Splitting them keeps the alarm on the half that matters: a name leaving
    # DICTATED_CHEAP is still a hard failure, while an addition costs one deliberate line here
    # that has to say WHY the channel is cheap.
    # 🔴🔴🔴 R56 — FOURTH INSTANCE OF ONE CLASS IN ONE FUNCTION, AND THE PANEL NAMED THE CURE.
    #
    # The history, because the pattern is the point and each step looked like a fix:
    #   1. `actual_cheap == DICTATED_CHEAP` went red on every legitimate ADDITION.
    #      Fix: a second table, ADDED_TO_CHEAP_SINCE.
    #   2. A legitimate REMOVAL had nowhere to go, so the only green edit was to delete a name
    #      from the anchor. Fix: a third table, REMOVED_FROM_CHEAP_SINCE.
    #   3. R55: a channel added as a trial and removed when the trial answered no landed in BOTH
    #      tables and tripped a disjointness assertion. I deleted the assertion. The r55 review
    #      panel was asked whether that was a rationalisation and NINE OF ELEVEN said yes -
    #      grokbuild: «the replacement is ALSO a normalisation rule in a safety-check's
    #      clothing». Fix: assert the end state AND require the removal reason to name the
    #      addition it cancels.
    #   4. That fix has its own hole, and THREE channels found it independently (grokbuild,
    #      agy37flash, goog37flash): ADD -> REMOVE -> ADD. A re-admitted channel is in both books
    #      AND in the panel, so `not (_both & actual_cheap)` goes red, and the only green edit is
    #      to delete a ledger line. That is step 2's defect with the arrow reversed.
    #      🔴 Worse, the R55 fix was PROSE-ENFORCED: it required a human to type the words
    #      "ADDED_TO_CHEAP_SINCE" into a removal reason. This project's own hard rule is that
    #      prose enforces nothing.
    #
    # Two sets cannot express a sequence, and four rounds of predicates over two sets is four
    # rounds of rearranging the furniture. So: ONE ORDERED, APPEND-ONLY EVENT LOG, and the sets
    # are DERIVED by folding it. Add, remove, and re-add are all sayable; nothing has to be
    # deleted to go green; and «no silent churn» stops being a sentence somebody must remember to
    # write and becomes a structural property - two consecutive events of the same kind for one
    # channel is a contradiction the fold can see.
    #
    # Append at the bottom. Never edit or delete a line above: this is the record of what was
    # decided and why, and every defect in the list above came from a table that made deleting
    # history the tidiest-looking edit.
    PANEL_EVENTS = [
        # (when, ADD|REMOVE, channel, why - who decided and on what evidence)
        ("R44 2026-08-16", "ADD", "grokbuild",
         "Igor, in the same message that asked for the model: subscription CLI, free at the "
         "margin exactly like the agy seats. NOTE it carries `enabled: false` - panel membership "
         "and enablement are different questions, and filing it as cheap means turning it on "
         "later needs no second decision about which room it belongs in."),
        ("R44 2026-08-16", "ADD", "orglm52",
         "Igor, same message: $0.308/M in, cheaper than ordeepseekv4pro which he did name as "
         "cheap."),
        ("R48 2026-08-19", "REMOVE", "ordeepseekv4pro",
         "Igor by name: «перенеси его в дорогую панель». It was the most expensive member of the "
         "cheap panel by a wide margin - $0.7732 of the $3.97 R43 round, $0.3653 in R42 - and "
         "the panel keeps two other `role: code` voices (grokbuild, orglm52), so nothing is "
         "orphaned. Still reachable: standard INCLUDES cheap, so `--panel standard` runs it."),
        ("R54 2026-08-19", "ADD", "orgpt56lunapro",
         "Igor: «Добавь так же временно Luna Pro для теста, посмотрим как быстро будет отвечать, "
         "не будет ли тормозить других в cheap panel». He put it in the cheap panel himself and "
         "the price agreed: $0.20/M in, $1.20/M out, read live - a TENTH of its Sol Pro sibling. "
         "TEMPORARY by his own word; the channel's `_temporary` key says what ends the trial."),
        ("R55 2026-08-19", "REMOVE", "orgpt56lunapro",
         "Igor by name: «Luna pro перенеси из cheap panel в standart». The R54 trial returned a "
         "NO: 604 s and $0.8629 in its first real round - the last channel to return and the "
         "highest single-channel spend of fourteen, on the CHEAPEST metered rate card in the "
         "registry. That pair is the finding: A RATE IS NOT A BILL, so `cost` and `panel` are "
         "allowed to disagree, and here they did."),
        ("R61 2026-08-21", "REMOVE", "orglm52",
         "RETIRED — replaced by orglm53 (GLM 5.3). Igor: «orglm53 free давай удалим, а 5.2 "
         "заменим на 5.3 и оставим только платный». The free tier kept falling back to paid "
         "anyway (429 rate limits from Decart's shared pool). GLM 5.3 released 2026-08-18."),
        ("R61 2026-08-21", "ADD", "orglm53",
         "Same instruction. z-ai/glm-5.3, $1.40/M in, $4.40/M out, reasoning always on. "
         "Replaces orglm52 in the cheap panel — the price went up but the model is stronger."),
        ("R62 2026-08-22", "REMOVE", "orglm53",
         "Igor: moved to standard panel. Solo run failed — model burned all 11 fetches on "
         "irrelevant content (Python docs, 124K chars) and generation timed out (exit 255). "
         "At $1.40/M input it belongs in standard, not cheap. Still runs on --panel standard."),
        ("R69 2026-08-30", "ADD", "orspark13cont",
         "Igor's R69 scenario: a first-time kit user with ONLY an OPENROUTER_API_KEY had no "
         "Spark voice at all. Same Contributor checkpoint as spark13cont (already in "
         "DICTATED_CHEAP), same $0.10/M price, reached through the reseller. Kit-distribution, "
         "so locally it carries enabled:false — panel membership and enablement are different "
         "questions (the grokbuild precedent above). 🔴 R80: channel renamed from orspark12cont "
         "to orspark13cont (Spark 1.3 upgrade); event updated in place."),
        # R80: agy37flash→agy38flash and orgemini37flash→orgemini38flash are RENAMES (same panel
        # slot, model upgraded 3.7→3.8). spark12cont→spark13cont likewise renamed in
        # DICTATED_CHEAP. orspark12cont→orspark13cont renamed in the R69 ADD event above.
        # Not ADD/REMOVE events because neither left nor entered
        # the cheap panel; DICTATED_CHEAP was updated in place to carry the new key names.
        ("R80 2026-09-04", "ADD", "ocspark13free",
         "Igor R80-И5: free Spark 1.3 Contributor via opencode CLI. No API key needed — "
         "opencode/ prefix models are free. Same Spark 1.3 weights as spark13cont (already "
         "in DICTATED_CHEAP). Primary in ask_default when the opencode CLI is installed; "
         "fallback to paid spark13cont/orspark13cont. distribution: both."),
    ]
    # The fold. Last event per channel wins; order is the file's order, which is why the list is
    # append-only. `ADDED_TO_CHEAP_SINCE` / `REMOVED_FROM_CHEAP_SINCE` keep their names because
    # the checks below and the round notes both refer to them - but they are now COMPUTED, so
    # they cannot drift from the record they summarise.
    _net = {}
    for _when, _act, _ch, _why in PANEL_EVENTS:
        _net[_ch] = _act
    ADDED_TO_CHEAP_SINCE = {c: w for _t, a, c, w in PANEL_EVENTS
                            if a == "ADD" and _net[c] == "ADD"}
    REMOVED_FROM_CHEAP_SINCE = {c: w for _t, a, c, w in PANEL_EVENTS
                                if a == "REMOVE" and _net[c] == "REMOVE"}

    check(all(a in ("ADD", "REMOVE") for _t, a, _c, _w in PANEL_EVENTS),
          "every panel event is an ADD or a REMOVE",
          "bad=%s" % sorted({a for _t, a, _c, _w in PANEL_EVENTS} - {"ADD", "REMOVE"}))
    check(all(w and t for t, _a, _c, w in PANEL_EVENTS),
          "every panel event states when it happened and who decided it, and why")
    # 🔴 THIS IS THE NO-SILENT-CHURN PROPERTY, AND IT IS NOW STRUCTURAL RATHER THAN A SENTENCE.
    # spark12cont named the property and mimo25pro named the scenario: a channel entering the
    # ledger twice through churn - two additions with no removal between them - rather than
    # through one deliberate trial. Under two sets that was invisible. Under a sequence it is a
    # contradiction: you cannot add what is already in, or remove what is already out.
    _seq_bad = []
    _state = {}
    for _t, a, c, _w in PANEL_EVENTS:
        if _state.get(c) == a:
            _seq_bad.append("%s %s %s (already %sED)" % (_t, a, c, a))
        _state[c] = a
    check(not _seq_bad,
          "no channel is ADDed twice running or REMOVEd twice running - a repeat with nothing "
          "in between is churn or a copy-paste, not a decision, and a sequence can see it where "
          "two sets could not",
          "; ".join(_seq_bad))
    # ---- R54: a declared fallback chain that can never fire ------------------------------------
    # 🔴 MEASURED, NOT READ: with `provider.allow_fallbacks: false`, a chain declared here does not
    # survive an upstream failure. Three arms holding the provider pin constant, the primary
    # genuinely failing (a real 429 from the free tier's shared pool) in each: with `false` the 429
    # came back and the fallback model was NEVER tried; with `true`, and with the flag omitted, the
    # fallback answered.
    #
    # 🔴 THE CLAIM THIS ASSERTION ORIGINALLY CARRIED WAS TOO BROAD - «false suppresses model-level
    # fallback» - and another arm of the same probe refutes it: with the pin set to the paid
    # model's providers only, the free model was dropped at ROUTING time and the paid one answered,
    # flag false. So model fallback is not suppressed in general; what the flag stops is any
    # further attempt after a DISPATCHED request fails. The assertion is unchanged, because
    # rate-limiting and downtime - the failures a fallback exists for - are all runtime ones.
    #
    # This is the [[depth-knobs-judged-by-meter]] shape one level up: a field that is SET, parses,
    # costs nothing, and silently does not act. Left untested it would have failed on exactly one
    # day - the day the free tier was down, which is the only day the fallback matters.
    for cname, ch in sorted(CH.items()):
        fb = ch.get("fallback_models")
        if not fb:
            continue
        pr = ch.get("provider_route") or {}
        check(pr.get("allow_fallbacks") is not False,
              "%s declares fallback_models AND does not pin allow_fallbacks:false - measured R54: "
              "that flag suppresses model-level fallback too, so the chain would never fire"
              % cname, "provider_route=%r" % pr)
        # Every model this channel can actually run must be describable: label, data_policy,
        # aliases. A fallback target missing from `models` is a model the plan cannot name.
        missing = [m for m in fb if m not in (ch.get("models") or {})]
        check(not missing,
              "%s declares every fallback target in its `models` table, so each one has a label "
              "and a data_policy the plan can print" % cname, "missing=%s" % missing)
        # 🔴 The free tier's data terms differ from the paid one's; if the plan prints only the
        # primary's policy, a reader is told about the wrong one. Both must be stated.
        for m in [ch.get("model")] + list(fb):
            check((ch.get("models") or {}).get(m, {}).get("data_policy"),
                  "%s: model %s states a data_policy - the free and paid tiers of one model do "
                  "NOT share terms, and the fallback can serve either" % (cname, m))

    # --- HTTP channel fallback_model (harness-level, singular) — R62
    for cname, ch in sorted(CH.items()):
        fb = ch.get("fallback_model")
        if not fb:
            continue
        check(ch.get("kind") == "http",
              "%s: fallback_model is only meaningful for kind:http channels (Meta API); "
              "OpenRouter channels use fallback_models (plural) which the vendor handles"
              % cname, "kind=%s" % ch.get("kind"))
        check(fb in (ch.get("models") or {}),
              "%s: fallback_model %r must be in the channel's `models` table so it has a "
              "label and data_policy the plan can print" % (cname, fb),
              "models=%s" % sorted((ch.get("models") or {}).keys()))
        check(fb != ch.get("model"),
              "%s: fallback_model must differ from the primary model — falling back to "
              "yourself is a no-op that wastes a retry" % cname)
        check((ch.get("models") or {}).get(fb, {}).get("data_policy"),
              "%s: fallback model %s states a data_policy — the Contributor and Standard "
              "tiers have DIFFERENT data terms" % (cname, fb))

    actual_cheap = {c for c, v in CH.items() if v.get("panel") == "cheap"}
    check(not (DICTATED_CHEAP - actual_cheap - set(REMOVED_FROM_CHEAP_SINCE)),
          "every channel Igor named as cheap is STILL cheap, or its removal is recorded here",
          "dropped without a record=%s"
          % sorted(DICTATED_CHEAP - actual_cheap - set(REMOVED_FROM_CHEAP_SINCE)))
    check(actual_cheap == (DICTATED_CHEAP | set(ADDED_TO_CHEAP_SINCE))
          - set(REMOVED_FROM_CHEAP_SINCE),
          "the cheap panel is what Igor dictated, plus the additions and minus the removals "
          "recorded here",
          "unrecorded=%s" % sorted(actual_cheap - DICTATED_CHEAP - set(ADDED_TO_CHEAP_SINCE)))
    check(all(ADDED_TO_CHEAP_SINCE.values()),
          "every later addition to the cheap panel states why it is cheap")
    check(all(REMOVED_FROM_CHEAP_SINCE.values()),
          "every removal from the cheap panel states who decided it and why")
    # A name cannot be in both books, and a channel that was demoted out of cheap must still
    # exist somewhere in the registry - "removed from cheap" is a move, not a deletion, and
    # spelling the two the same way is how a channel disappears while a test stays green.
    #
    # 🔴 BUT AS WRITTEN THAT MADE RETIREMENT IMPOSSIBLE, which grokbuild caught reviewing R48:
    # «`REMOVED_FROM_CHEAP_SINCE` can only mean "demoted", never "deleted". That is a
    # zombie-registry rule.» Correct, and it is the R48 defect one level up - the removals book
    # was added because additions had a home and removals had none, and it shipped with the same
    # asymmetry inside it: a demotion could be recorded and a retirement could not. Vendors
    # deprecate models; the first genuinely dead channel would have gone red forever against
    # working code, and the only way out would have been to delete the record - the exact edit
    # both books exist to forbid.
    #
    # A retirement is now sayable, and it costs the same one deliberate line: prefix the reason
    # with RETIRED. That word is the difference between "this channel moved rooms" and "this
    # channel no longer exists", and it has to be written by a human either way.
    _retired = {c for c, why in REMOVED_FROM_CHEAP_SINCE.items()
                if str(why).strip().upper().startswith("RETIRED")}
    # 🔴 R60 (2026-08-20): kit-excluded channels are LEGITIMATELY absent from the shipped
    # registry - PUBLISH_EXCLUDE_CHANNELS in package.py deletes them at build time. They are
    # not retired (still live on the maintainer's machine), so the RETIRED prefix does not fit;
    # not present on shipped installs, so `c in CH` is false. Before R60 the shipped selftest
    # went red on every install for orgpt56lunapro (demoted cheap→standard in R55 AND excluded
    # from distribution because it is a running experiment), presenting a real drift signal as
    # a broken record. `_kit_excluded_channels` is written into the shipped channels.json by
    # package.py; on the dev tree the key is absent → empty set → the check runs unchanged.
    _kit_excluded = set(RAW.get("_kit_excluded_channels", []))
    # 🔴🔴 THIRD INSTANCE OF THE CLASS THIS FUNCTION HAS NOW RECORDED TWICE ABOVE, and it was
    # mine. This used to assert `not (REMOVED & ADDED)` - "no channel in both books". R55 hit it
    # honestly: orgpt56lunapro was ADDED to cheap in R54 as a timed trial and REMOVED in R55 when
    # the trial answered no. That is two real events about one channel, and the check made them
    # unsayable - the only way to green it was to delete the addition, i.e. to erase Igor's R54
    # decision and its reason. Exactly what the comment forty lines up calls «the exact edit both
    # books exist to forbid».
    #
    # It also guarded nothing. Everything it was standing in for is asserted elsewhere and more
    # directly: the set arithmetic by the DICTATED|ADDED-REMOVED equality above, continued
    # existence and retirement by the two checks below. Disjointness was a NORMALISATION rule -
    # "don't record a no-op pair, delete the addition instead" - wearing a safety check's clothes.
    #
    # What a reader actually wants asserted is the property, so assert that: if a name is in both
    # books it must really be out of the cheap panel now. A removal recorded on paper while the
    # channel still sits in the panel is a lying record, and THAT is worth failing on.
    # 🔴🔴 AND THE r55 PANEL SAID I WAS RATIONALISING - 9 of 11 reviewers, independently, on the
    # paragraph above. They were right, and the sharpest form of it (grokbuild) is that my
    # replacement «is ALSO a normalisation rule in a safety-check's clothing»: it checks the
    # CURRENT state and says nothing about the ledger's coherence. mimo25pro named the concrete
    # scenario I had asked for and could not think of - a channel entering both books through
    # CHURN rather than through one deliberate trial, which the current-state check cannot see
    # because the end state looks identical. spark12cont named the property in one phrase: the
    # books encode an audit trail, and what disjointness bought was NO SILENT CHURN.
    #
    # 🔴 SUPERSEDED IN R56, BY THE SAME PANEL, ONE LEVEL DEEPER. The two checks that stood here
    # asserted (a) the end state and (b) that a removal reason contains the literal string
    # "ADDED_TO_CHEAP_SINCE". (b) was the no-silent-churn property enforced BY PROSE - a human
    # had to remember to type a magic phrase - and this project's own hard rule is that prose
    # enforces nothing. Both are now structural consequences of PANEL_EVENTS being a sequence
    # instead of two sets: churn is a repeated action with nothing between it, caught at the fold
    # above, and the end state is the fold itself, checked by the set-equality assertion higher
    # up. The re-admission hole (ADD -> REMOVE -> ADD, which check (a) made unsayable and which
    # grokbuild, agy37flash and goog37flash each found independently) is gone by construction.
    #
    # What survives here is the half that is genuinely about the REGISTRY rather than the ledger:
    # a channel written out of the cheap panel must still exist somewhere, unless its reason says
    # it is dead.
    check(all(c in CH for c in set(REMOVED_FROM_CHEAP_SINCE) - _retired - _kit_excluded),
          "a channel DEMOTED out of the cheap panel still exists in the registry; one that is "
          "gone for good says RETIRED in its reason (kit-excluded channels are OK - see "
          "PUBLISH_EXCLUDE_CHANNELS in package.py and _kit_excluded_channels in channels.json)",
          "vanished without a RETIRED-or-kit-excluded note=%s"
          % sorted(c for c in set(REMOVED_FROM_CHEAP_SINCE) - _retired - _kit_excluded
                   if c not in CH))
    # And the mirror: RETIRED must mean retired. A name marked retired that is still in the
    # registry is a record that has drifted from the thing it describes - the same defect,
    # pointing the other way, and the cheaper one to leave rotting because nothing breaks.
    check(not (_retired & set(CH)),
          "a channel recorded as RETIRED is really gone from the registry",
          "still present=%s" % sorted(_retired & set(CH)))
    standard_only = {c for c in CH} - actual_cheap
    check({c for c, v in CH.items() if v.get("panel") == "standard"} == standard_only,
          "everything outside the cheap panel is standard-only", ", ".join(sorted(standard_only)))
    # The ladder is NESTED, not a partition: «стандартная» has to mean "what normally runs".
    reg = _r.load_registry()
    check(_r.panel_members(reg, "cheap") < _r.panel_members(reg, "standard"),
          "standard is a strict SUPERSET of cheap - the ladder is nested, not a partition",
          "%d < %d" % (len(_r.panel_members(reg, "cheap")),
                       len(_r.panel_members(reg, "standard"))))
    # What a cheap round ACTUALLY runs here: membership intersected with `enabled`. Several
    # later checks derive their expected numbers from this rather than pinning a literal.
    cheap_live = [c for c in _r.panel_members(reg, "cheap") if CH[c].get("enabled", True)]

    # ---- THE CORE INVARIANT: a panel never resurrects -----------------------------------------
    off = sorted(c for c, v in CH.items() if not v.get("enabled", True))
    check(bool(off), "there is at least one default-off channel to test resurrection against",
          ", ".join(off))
    for pname in sorted(PANELS):
        plan = _r.resolve(_r.load_registry(), panel=pname)
        woke = sorted(c for c in off if plan[c]["enabled"])
        check(not woke, "--panel %s enables nothing that the registry has off" % pname,
              "woke: " + ", ".join(woke))
        live = {c for c, p in plan.items() if p["enabled"]}
        outside = live - _r.panel_members(reg, pname)
        check(not outside, "--panel %s runs nothing outside its own membership" % pname,
              ", ".join(sorted(outside)))
    # ...and the ASYMMETRY that makes the invariant meaningful: --only still resurrects.
    plan = _r.resolve(_r.load_registry(), panel="cheap", only=[off[0]])
    check(plan[off[0]]["enabled"],
          "CONTROL: --only still resurrects a default-off channel (panels are not --only)",
          off[0])

    # ---- alias namespace ----------------------------------------------------------------------
    # Panel words are consumed BEFORE the entity scan, so a word that is both a panel alias and
    # a channel alias would mean two different things depending on which consumer ran first.
    ent = {a for a, _ in _r.alias_index(reg)}
    pal = {a for a, _ in _r._panel_alias_index(reg)}
    check(not (ent & pal), "no panel alias collides with a channel/model/group alias",
          ", ".join(sorted(ent & pal)))

    # ---- route parsing ------------------------------------------------------------------------
    def route_err(text, **kw):
        try:
            _r.resolve(_r.load_registry(), route=text, **kw)
            return None
        except _r.RouteError as e:
            return str(e)

    check(route_err("не используй дешевую панель"),
          "a NEGATED panel word is refused, not silently obeyed backwards",
          (route_err("не используй дешевую панель") or "")[:70])
    check(route_err("дешевая и стандартная"), "naming two panels is refused")
    check(route_err("дешевая панель, без грокк"),
          "a marker with no channel behind it is refused even after a panel word")
    p = _r.resolve(_r.load_registry(), route="запусти на дешевой")
    check(sum(1 for v in p.values() if v["enabled"]) == len(
        [c for c in _r.panel_members(reg, "cheap") if CH[c].get("enabled", True)]),
        "filler after a panel word is NOT an error - «запусти на дешевой» resolves")
    check(route_err("дешевая", panel="standard"),
          "route and --panel disagreeing about the panel is a hard stop")
    # Unchanged behaviour when no panel word is present.
    p = _r.resolve(_r.load_registry(), route="не используй gemini")
    check(not any(p[c]["enabled"] for c in _r.group_members(reg, "gemini")),
          "CONTROL: a route with no panel word behaves exactly as before")

    # ---- what the plan prints -----------------------------------------------------------------
    reg2 = _r.load_registry()
    txt = _r.format_plan(_r.resolve(reg2, panel="cheap"), reg2)
    check("panel: cheap" in txt, "the plan names the panel it resolved")
    check("--panel standard would ALSO run" in txt,
          "a cheap plan says what the other panel would ADD, by name")
    # DERIVED, not pinned to 6: the number moves the moment a channel is added or skipped, and
    # a test that froze it would go red against correct code - the failure this suite's own
    # complement rule exists to avoid.
    want_v = len({CH[c]["vendor"] for c in cheap_live})
    check(("from %d vendor(s)" % want_v) in txt,
          "the plan counts VENDORS, not just channels",
          next((ln.strip() for ln in txt.splitlines() if "vendor(s)" in ln), "(no line)"))
    check("role=code" in txt,
          "`role` is printed - it was a registry field nothing read until round 42")
    reg3 = _r.load_registry()
    dflt = _r.format_plan(_r.resolve(reg3), reg3)
    # 🔴 THE ASSERTION IS «THE DEFAULT PLAN NAMES THE OTHER PANEL», NOT «IT SAYS THE WORD DROP».
    # Pinned to "--panel cheap would DROP" until 2026-08-16, which tested that the default was
    # `standard` rather than that the printer is symmetric - so flipping the default turned a
    # correct plan red. Which direction the sentence runs is derived from `default_panel`: on a
    # standard default the cheap panel DROPS channels, on a cheap default the standard one ADDS
    # them, and the same `gain`/`lose` computation prints both.
    _other = next(p for p in reg3["panels"] if p != reg3["default_panel"])
    check(("--panel %s would ALSO run" % _other) in dflt
          or ("--panel %s would DROP" % _other) in dflt,
          "the DEFAULT plan names the other panel and what changing to it would do",
          "default_panel=%r other=%r" % (reg3["default_panel"], _other))
    # The concentration warning is derived from the resolved set, so it has to move when the
    # set does. Two arms, because a line that is always printed is not evidence of anything.
    reg4 = _r.load_registry()
    only_two = _r.format_plan(_r.resolve(reg4, only=["spark11", "codex"]), reg4)
    check("seats are" not in only_two,
          "CONTROL: no concentration warning when no vendor holds half the seats")
    check("largest bloc: google" in txt,
          "and it names the largest bloc on the cheap panel by vendor",
          next((ln.strip() for ln in txt.splitlines() if "largest bloc" in ln), "(no line)"))

    # ---- the cheap panel keeps a code voice ---------------------------------------------------
    # The reason ordeepseekv4pro was added in this round. Derived: if the only `role: code`
    # channel is standard-only, the cheap panel has no code seat and this check says so.
    check(any(CH[c].get("role") == "code" for c in cheap_live),
          "the cheap panel has at least one `role: code` seat",
          ", ".join(sorted(c for c in cheap_live if CH[c].get("role") == "code")))
    check(not any(CH[c].get("cost") == "expensive" for c in _r.panel_members(reg, "cheap")),
          "no channel tagged cost=expensive sits in the cheap panel")

    # ---- deepseek, the round's new channel ----------------------------------------------------
    ds = CH.get("ordeepseekv4pro") or {}
    check(ds.get("model") == "deepseek/deepseek-v4-pro",
          "ordeepseekv4pro runs the id the live catalogue returned", repr(ds.get("model")))
    pin = (ds.get("provider_route") or {}).get("only") or []
    check(len(pin) >= 2, "it is pinned to an allow-list, not left to route across 18 providers "
                         "at a 5.5x price spread", repr(pin))
    # 🔴 THE ONE PROVIDER THAT MUST NOT COME BACK. The first draft of this channel pinned
    # `only: [deepseek]` - cheapest non-quantised, best uptime, the only reachable endpoint with
    # implicit caching - and it is 404 on this account: «No endpoints available matching your
    # guardrail restrictions and data policy». The catalogue lists what EXISTS; the account
    # decides what is REACHABLE, and no API call shows the difference. Measured 2026-08-15.
    check("deepseek" not in pin,
          "and NOT to the first-party endpoint, which this account's data policy refuses (404)",
          repr(pin))
    sg = ds.get("spend_guard") or {}
    check(sg.get("max_usd_per_review") is not None and not sg.get("requires_ack"),
          "it declares a ceiling and NO ack gate (an ack gate on a default-on channel would "
          "refuse every ordinary round)", "ceiling=%r" % sg.get("max_usd_per_review"))
    # 🔴 It was null through the whole build, on purpose, and is filled from the first REVIEW
    # (not from a probe, and never from a price × a token count). The check moved with it: what
    # it asserts now is that the string carries a round's own number and says what produced it.
    mu = sg.get("measured_usd") or ""
    check("0.1334" in mu and "brief" in mu,
          "measured_usd carries a REVIEW's own cost, with what produced it", mu[:90])
    check("_measured_usd_was_null_until_the_first_real_round" in sg,
          "and the note explaining why it was null survives the fill - deleting it would erase "
          "the rule that a probe price is not a review price")

    # ---- what the round-42 panel of twelve reviewers found ------------------------------------
    # Each of these is a regression test for a defect that was SHIPPED in the first draft of this
    # round and caught by an external reviewer. Kept as behaviour checks (route in, panel out)
    # rather than as assertions about the alias table, so the fix can be implemented differently
    # and the test still means the same thing.
    def resolved_panel(text):
        r = _r.load_registry()
        _r.resolve(r, route=text)
        return r.get("_panel_chosen")

    # 🔴 «A вместо B» is SUBSTITUTION, not negation. Three reviewers independently hit the first
    # draft's single NEG+SUBST prefix test, which answered «a panel cannot be negated» - naming
    # the word the human was DISCARDING. `apply_route` has always resolved «вместо» this way for
    # models; the panel extractor now does too.
    check(resolved_panel("стандартная панель вместо дешевой") == "standard",
          "«A вместо B» selects A, the panel BEFORE the marker",
          repr(resolved_panel("стандартная панель вместо дешевой")))
    check(resolved_panel("дешевая панель вместо стандартной") == "cheap",
          "...and the mirror image selects the other one - the rule is positional, not a "
          "preference for either panel",
          repr(resolved_panel("дешевая панель вместо стандартной")))
    check(route_err("дешевая и стандартная"),
          "CONTROL: two panels with NO substitution marker is still a hard stop")

    # 🔴 A word that could name the OTHER AXIS must not select a panel. `full`, `полная`,
    # `полную`, `полной` were aliases; «run a full analysis» is asking for DEPTH (--tier deep),
    # and the first draft silently set panel=standard AND swallowed the word, so the leftover
    # produced an error about a channel. Two reviewers found it independently.
    check(resolved_panel("run a full analysis, но без grok") == reg.get("default_panel"),
          "a depth word («full») no longer hijacks the panel",
          repr(resolved_panel("run a full analysis, но без grok")))
    check(resolved_panel("полная панель") == "standard",
          "...while the disambiguated noun phrase still works («полная панель»)")
    pal_bare = {a for a, _ in _r._panel_alias_index(reg)}
    depth_words = {"full", "полная", "полную", "полной", "deep", "глубокая", "глубоко"}
    check(not (pal_bare & depth_words),
          "no bare panel alias is a word that answers «how deep»",
          ", ".join(sorted(pal_bare & depth_words)))

    # Case endings, checked by BEHAVIOUR. A missing ending is silent in the expensive direction:
    # with a channel elsewhere in the sentence the round just runs the default panel.
    for phrase, want in (("запусти на дешевом", "cheap"), ("запусти на дешевой", "cheap"),
                         ("экономную панель", "cheap"), ("стандартном", "standard"),
                         ("стандартные", "standard")):
        check(resolved_panel(phrase) == want, "route %r resolves to %s" % (phrase, want),
              repr(resolved_panel(phrase)))

    # 🔴 THE REGISTRY CLAIMED «--tier quick is now an argparse error» AND IT WAS TRUE IN ONE OF
    # THE TWO PROGRAMS. orchestrate.py had `choices`; routing.py did not, and printed a plan
    # resolved at the default. One sentence, two files, verified against the one it was written
    # about. Found by a reviewer who checked it against both.
    for script, flag, bad in (("routing.py", "--tier", "quick"), ("routing.py", "--panel", "zz"),
                              ("orchestrate.py", "--tier", "quick")):
        p = _sp.run([PY, str(HERE / script), flag, bad] +
                    ([] if script == "routing.py" else ["--brief", str(HERE / "SKILL.md"),
                                                        "--dry-run"]),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=120)
        check(p.returncode != 0 and "invalid choice" in blob_of(p),
              "%s %s %s is refused by argparse, listing what exists" % (script, flag, bad),
              "exit=%d" % p.returncode)
    ok = _sp.run([PY, str(HERE / "routing.py"), "--tier", "deep"], capture_output=True, text=True,
                 encoding="utf-8", errors="replace", timeout=120)
    check(ok.returncode == 0, "CONTROL: a tier that DOES exist is still accepted",
          "exit=%d" % ok.returncode)

    # 🔴 THE PROVIDER ROUTE NEEDS ALL THREE FIELDS, AND EACH WAS MEASURED SEPARATELY:
    # `only` bounds (only:[anthropic] -> 404, no leak), `order` pins the primary (Baidu 3/3
    # against StreamLake x3 / Baidu x1 without it, which matters because a provider swap between
    # tool rounds discards the prompt cache), `allow_fallbacks:false` is belt-and-braces.
    pr = ds.get("provider_route") or {}
    check(pr.get("order") and set(pr["order"]) <= set(pr.get("only") or []),
          "the provider `order` is a subset of `only` - a preference cannot widen the allow-list",
          repr(pr))
    check(pr.get("allow_fallbacks") is False,
          "and fallbacks are explicitly off, so the doubt a reader has is answered in the file",
          repr(pr.get("allow_fallbacks")))

    # 🔴🔴 THE TWO SILENT-EXPENSIVE PATHS THE LATE REVIEWERS FOUND. Both had the same shape: the
    # flag or the word was accepted, the narrowing was not applied, and the round ran EVERY
    # channel while looking restricted. That is the inverse of this project's own rule («keep
    # failures LOUD, keep spending silent-proof») and it was written by the hand that documented
    # the rule, in the same round.
    check(route_err("дешовая панель без grok"),
          "a near-miss panel word is refused, not silently ignored (a typo cost the default "
          "panel before this)", (route_err("дешовая панель без grok") or "")[:80])
    check(route_err("run the cheep panel"),
          "...in English too - a stem that almost matches an alias is a near miss")
    p = _r.resolve(_r.load_registry(), route="дешевая панель, без grok")
    check(sum(1 for v in p.values() if v["enabled"]) < len(cheap_live),
          "CONTROL: a CORRECT panel word plus a channel exclusion still works")
    empty = _r.load_registry()
    empty["panels"] = {}
    try:
        _r.resolve(empty, panel="cheap")
        woke = "accepted and ignored"
    except _r.RouteError as e:
        woke = None if "no panels" in str(e) else "raised the wrong error: %s" % e
    check(woke is None,
          "--panel against a registry with NO panels is REFUSED, not accepted-then-ignored",
          woke or "")

    # The concentration line is printed for every set, and only the 🔴 escalates - a warning
    # that disappears when you move from cheap (6/11) to standard (6/15) reads as «fixed».
    reg5 = _r.load_registry()
    std = _r.format_plan(_r.resolve(reg5), reg5)
    check("largest bloc:" in std and "largest bloc:" in txt,
          "the largest-vendor share is printed on BOTH panels, not only the alarming one")
    # 🔴 DERIVED FROM THE SEATS, NOT PINNED TO A PANEL. This used to assert literally «cheap
    # escalates and standard does not», which was true at 6/11 vs 6/15 and became false the
    # moment R44 added two non-Google channels to the cheap panel and diluted that bloc to
    # 6/13 = 46%. The escalation correctly stopped firing, and a test pinned to the old roster
    # called the correct behaviour a regression - the test-the-human defect this file records
    # four times elsewhere. What the rule actually says is «escalate iff the largest bloc holds
    # at least half the seats», so compute the share and assert the marker tracks it.
    def _bloc_share(reg_, **kw):
        pl = _r.resolve(reg_, **kw)
        live_ = [c for c, v in pl.items() if v.get("enabled")]
        t = {}
        for c in live_:
            t[pl[c].get("vendor") or c] = t.get(pl[c].get("vendor") or c, 0) + 1
        top = max(t.values()) if t else 0
        return top, len(live_)

    for label, kw, text in (("cheap", {"panel": "cheap"}, txt), ("standard", {}, std)):
        top, seats = _bloc_share(_r.load_registry(), **kw)
        want = seats and top * 2 >= seats
        check(("🔴 Where those agree" in text) == bool(want),
              "the 🔴 escalation on the %s panel tracks the actual bloc share" % label,
              "largest bloc %d of %d seats; marker %s" % (
                  top, seats, "present" if "🔴 Where those agree" in text else "absent"))

    # ---- the flag reaches the CLI --------------------------------------------------------------
    check(sorted(_o.load_panels()) == sorted(PANELS),
          "orchestrate.load_panels() derives --panel's choices from the registry",
          repr(_o.load_panels()))
    h = _sp.run([PY, str(HERE / "orchestrate.py"), "--help"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120).stdout or ""
    check("--panel" in h, "--panel is a documented flag on orchestrate.py")
    # 🔴 --dry-run, ALWAYS. The 1.19.0 version of the reachability check ran the real CLI with
    # no --dry-run against the most expensive channel and asserted returncode == 0 - which only
    # passes if a paid call SUCCEEDED. One live call per `python selftest.py`, invisible because
    # nothing printed a price. Found in round 41 when the new ack gate refused the test suite.
    r = run_cli(["--dry-run", "--panel", "cheap", "--marker", "X"])
    blob = blob_of(r)
    check(r.returncode == 0 and "panel: cheap" in blob,
          "orchestrate --dry-run --panel cheap resolves the cheap panel", "exit=%d" % r.returncode)
    check("kimik3" not in blob.split("running")[-1],
          "and the standard-only channels are not in its running list")
    r = run_cli(["--dry-run", "--panel", "nosuchpanel", "--marker", "X"])
    check(r.returncode != 0 and "invalid choice" in blob_of(r),
          "an unknown --panel is refused by argparse, listing the ones that exist",
          "exit=%d" % r.returncode)


def suite_refs_and_meters():
    section("R46. Attachments by reference, and the meters that answer Igor's questions")
    sys.path.insert(0, str(HERE))
    import orchestrate as o
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")

    # ---- refs mode: who reads from disk --------------------------------------------------------
    check(set(o.REFS_KINDS) <= set(o.KNOWN_KINDS), "REFS_KINDS is a subset of KNOWN_KINDS",
          repr(o.REFS_KINDS))
    check("hermes" not in o.REFS_KINDS,
          "hermes is NOT a refs kind - its toolset grant is `web` only, so a path would name a "
          "capability it does not have")
    check(not {"http", "openrouter", "oai", "xai", "gemini"} & set(o.REFS_KINDS),
          "no API kind is a refs kind - a remote endpoint cannot read this disk")

    atts = [(r"C:\x\doc.md", "SECRET-FREE CONTENT")]
    refs = o._attach_refs(atts, [r"C:\x\sub"])
    inline = o._attach_inline(atts, [r"C:\x\sub"])
    check(r"C:\x\doc.md" in refs and "SECRET-FREE CONTENT" not in refs,
          "_attach_refs sends the PATH and never the file text")
    check("READ ONLY" in refs and "no write tools" in refs,
          "_attach_refs carries the read-only contract in the brief (R40: the brief is the "
          "strong position, the persona the weak one)")
    check("SECRET-FREE CONTENT" in inline and "NOT" in inline and "folder" in inline.lower(),
          "_attach_inline carries the file text and names the folders as ABSENT rather than "
          "letting an API reviewer imagine reading them")

    # grok build: refs grants read_file+list_dir and nothing else; no write tool in any mode
    check('"web_search,web_fetch,todo_write,read_file,list_dir" if file_refs' in src,
          "grok build gains read_file/list_dir ONLY in refs mode")
    grok_src = src.split("def call_grokcli")[1].split("def neutral_cwd")[0]
    for bad in ("edit_file", "write_file", "create_file", "run_command"):
        check(bad not in grok_src,
              "no write/shell tool is ever granted to grok build (%s absent)" % bad)
    agy_src = src.split("def _agy_once")[1].split("\ndef ")[0]
    check('"--add-dir", d' in agy_src,
          "agy gains --add-dir per attachment folder in refs mode")

    # end to end, free: --dry-run with an attachment
    with tempfile.TemporaryDirectory() as td:
        doc = Path(td) / "doc.md"
        doc.write_text("attached document body", encoding="utf-8")
        r = run_cli(["--dry-run", "--only", "spark11", "--marker", "X",
                     "--attach", str(doc)])
        blob = blob_of(r)
        check(r.returncode == 0 and "attachments: 1 file(s)" in blob,
              "--attach shows up in the plan on --dry-run", "exit=%d" % r.returncode)
        check("refs mode TRUSTS the attached material" in blob,
              "the plan prints the refs-mode trust caveat before anything is spent")
        # the gate covers the attachment: a planted key inside it must refuse the round (exit 3)
        leak = Path(td) / "leak.md"
        leak.write_text("api_key = sk-FAKE" + "A" * 32, encoding="utf-8")
        r2 = run_cli(["--dry-run", "--only", "spark11", "--marker", "X",
                      "--attach", str(leak)])
        check(r2.returncode == 3 and "SECRETS IN THE PAYLOAD" in blob_of(r2),
              "a secret inside an ATTACHED file refuses the round with no override",
              "exit=%d" % r2.returncode)
        # a folder with a binary: skipped LOUDLY, round not blocked
        sub = Path(td) / "sub"
        sub.mkdir()
        (sub / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
        r3 = run_cli(["--dry-run", "--only", "spark11", "--marker", "X",
                      "--attach-dir", str(sub)])
        check(r3.returncode == 0 and "excluded from the vetted copy" in blob_of(r3),
              "a binary in --attach-dir is excluded LOUDLY (R75: named as outside the "
              "vetted copy), round not blocked", "exit=%d" % r3.returncode)
        r4 = run_cli(["--dry-run", "--only", "spark11", "--marker", "X",
                      "--attach", str(Path(td) / "nope.md")])
        check(r4.returncode == 2 and "file not found" in blob_of(r4),
              "--attach with a missing file refuses before anything runs",
              "exit=%d" % r4.returncode)

    # ---- meters --------------------------------------------------------------------------------
    check("fetches if fetch_on else None" in src
          and '"fetches": fetches or None' not in src,
          "fetches distinguishes 0 (tool offered, unused) from None (not offered) - R45's "
          "diagnostics could not answer «может не настроен поиск?» because it did not")
    check("ZERO WEB GROUNDING IN THE TEXT" in src,
          "an OAI channel that cites nothing despite web access says so in its notes")
    check("openrouter_key_meter" in src and "GET /credits" in src,
          "the OpenRouter key ledger cross-meter is wired into the round and diagnostics")
    env = o.environment_report()
    missing = [k for k in o.CLI_RESOLVERS if k + "_installed" not in env]
    check(not missing,
          "environment_report derives its CLI list from CLI_RESOLVERS (R45.1's class fix, "
          "applied to the member it missed)", repr(missing))

    # ---- the depth-default class is LOCKED, not patched per instance ---------------------------
    # grokbuild and qwen38max converged on the same R46 panel finding: measuring grok-4.20's
    # default_enabled:false and fixing that one channel leaves the CLASS open - the next model
    # whose vendor defaults reasoning OFF ships "fast, confident, wrong" until someone asks why
    # it answered in 0.6 s. Every enabled OpenRouter channel must therefore DECLARE its depth;
    # silence in the registry would mean the vendor decides. (kind `oai`/mimo is exempt: its
    # provider table hardcodes thinking:enabled unconditionally.)
    regj = json.loads((HERE / "channels.json").read_text(encoding="utf-8"))
    naked = [c for c, ch in regj["channels"].items()
             if not str(c).startswith("_") and isinstance(ch, dict)
             and ch.get("kind") == "openrouter" and ch.get("enabled")
             and not ch.get("reasoning")]
    check(not naked,
          "every ENABLED openrouter channel declares a `reasoning` block - a vendor default "
          "can be «do not think» (grok-4.20: 0 tokens + wrong arithmetic, measured R46)",
          repr(naked))

    # ---- R47: the discretionary section, and the rotated-key divergence warning ----------------
    check("titled UNASKED" in src
          and '"UNASKED: nothing beyond the questions."' in src,
          "every round appends the UNASKED section to the system layer - «иная информация на "
          "твое усмотрение» is structural, not a brief-writing habit (R46's hand-written "
          "version returned four findings that shipped)")
    check("if not ask_mode:" in src.split("titled UNASKED")[0][-1500:],
          "and --ask (a lookup, not a review) does not get the UNASKED section")
    check("_ENV_KEY_DIVERGENCE_WARNED" in src,
          "_env_key warns once per variable when the process env and HKCU disagree - a key "
          "rotated with setx is otherwise masked by the stale process copy (measured R47: a "
          "fresh GEMINI_API_KEY produced 429s that read as a quota wall)")
    check('winreg.QueryValueEx(reg, "MODEL_API_KEY")' not in src
          and 'winreg.QueryValueEx(reg, "GEMINI_API_KEY")' not in src,
          "no inline winreg key-readers remain beside _env_key - two readers of one variable "
          "is the two-homes rot, key-shaped")

    # ---- report.py renders a depth for every kind's spelling of the knob -----------------------
    import report as _rep
    fake = {"plan": {"g": {"model": "m", "thinking_level": "high", "kind": "gemini"},
                     "b": {"model": "m2", "kind": "openrouter",
                           "reasoning": {"max_tokens": 48000}}},
            "channels": {"g": {"ok": True}, "b": {"ok": True}},
            "invocation": {"tier": "max"}}
    rows = {r["name"]: r for r in _rep._rows(fake)}
    check(rows["g"]["effort"] == "high",
          "report.py shows thinking_level as the depth for gemini channels (was '-')")
    check(rows["b"]["effort"] == "budget:48000",
          "report.py shows a reasoning budget as the depth for budget-form channels (was '-')")


def suite_r47_causes():
    section("R47. The cause the vendor states, not the one the canned text assumes")
    sys.path.insert(0, str(HERE))
    import orchestrate as o
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")

    # ---- finish_reason is read off the stream and recorded -------------------------------------
    check('ch.get("finish_reason")' in src and 'ch.get("native_finish_reason")' in src,
          "both spellings of the provider's stop reason are captured (OpenRouter normalises; "
          "the vendor's own spelling rides in native_finish_reason)")
    oai_tail = src.split("def call_oai_reviewer")[1].split("\nXAI_RESPONSES_URL")[0]
    check('"finish_reason": last_finish.get("finish")' in oai_tail,
          "the channel record carries finish_reason - it was discarded on every chunk before, "
          "so a provider-cut answer (nemotron, AOS R40: 32 KB ending mid-heading, no marker) "
          "was indistinguishable from a model that chose to stop")
    check("PROVIDER LENGTH CEILING CUT THE ANSWER" in oai_tail,
          "finish_reason=length beside a missing marker names the cutter, not just the symptom")

    # ---- the two xai empty-output shapes meet different causes ---------------------------------
    xai_src = src.split("def call_xai_responses")[1].split("\nAGY_AGENT")[0]
    check("VENDOR ENDED THE TURN MID-LOOP" in xai_src
          and "OUTPUT BUDGET EXHAUSTED - status=" in xai_src,
          "the empty xai answer splits on budget share: exhausted vs vendor mid-loop death "
          "(AOS R40: 95% of the budget UNSPENT while the canned cause said it was gone)")
    check('"response_id": resp_obj.get("id")' in xai_src,
          "the vendor's response id is recorded for post-mortem retrieval")

    # ---- diagnose() has a cause for each new warning - EXECUTED, not read ----------------------
    for w, frag in (
            ("VENDOR ENDED THE TURN MID-LOOP - status=completed, x", "server-side"),
            ("PROVIDER LENGTH CEILING CUT THE ANSWER - finish_reason=length: x", "provider's"),
            ("OUTPUT BUDGET EXHAUSTED - status=completed, output items=['reasoning']",
             "CUT OFF")):
        cause, fix = o.diagnose(w)
        check(bool(cause) and bool(fix) and frag.lower() in ((cause or "") + (fix or "")).lower(),
              "diagnose() maps %r... to its own cause" % w[:40])
    check("reasoned until its output budget was gone" not in src,
          "the wrong one-size cause (budget-was-gone printed over a 95%-unspent budget) is gone")

    # ---- doctor: keys derived from the registry, resolver shared with the harness --------------
    dsrc = (HERE / "doctor.py").read_text(encoding="utf-8")
    check('os.environ.get("MODEL_API_KEY")' not in dsrc,
          "doctor no longer keeps a third private copy of the process-env/HKCU key resolver")
    check("check_key(r, mod)" in dsrc and "_env_key" in dsrc,
          "doctor's key check goes through the harness's _env_key, divergence warning included")
    check("kind_var" in dsrc and "key_env" in dsrc,
          "doctor derives WHICH keys matter from the registry and the provider table - it used "
          "to report the Spark key and stay silent about the only key a kit user has")

    # ---- Terra: held back from the published registry ------------------------------------------
    # package.py is the BUILDER and does not ship; in the kit tree the equivalent assertion is
    # the registry-absence check in suite_routing's kit_tree branch. (This suite raised
    # FileNotFoundError on the first kit run - the world-awareness rule, applied to its author.)
    pkg = HERE / "package.py"
    if pkg.is_file():
        psrc = pkg.read_text(encoding="utf-8")
        # 🔴 R54: THIS USED TO ASSERT THE EXACT SOURCE LINE, LITERAL NAME AND ALL, and that is the
        # defect this file has recorded twice under its own name - «a test hard-coding the value a
        # human is meant to change tests the human». It went red the moment the seat was re-pointed
        # from Terra Pro to Sol Pro, which was CORRECT behaviour, but the only repair it offered
        # was «paste the new name in», so it would have passed again while asserting nothing about
        # whether the exclusion still covers what needs excluding. Now it DERIVES: whatever the
        # registry marks `explicit_only` is what the published tree must not contain.
        excl = re.search(r"PUBLISH_EXCLUDE_CHANNELS\s*=\s*\[([^\]]*)\]", psrc)
        shipped_excl = set(re.findall(r'"([^"]+)"', excl.group(1))) if excl else set()
        _reg54 = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
        _chans54 = {k: v for k, v in _reg54["channels"].items() if not k.startswith("_")}
        rationed = {c for c, v in _chans54.items() if v.get("explicit_only")}
        check(bool(excl), "package.py declares PUBLISH_EXCLUDE_CHANNELS at all")
        check(rationed and rationed <= shipped_excl,
              "every explicit_only channel is DELETED from the shipped registry (R47: employees "
              "walked every lock rung on purpose - absence is the only lock that survives naming)",
              "explicit_only=%s excluded=%s missing=%s"
              % (sorted(rationed), sorted(shipped_excl), sorted(rationed - shipped_excl)))
        # An exclusion for a channel that no longer exists is dead weight that reads as protection.
        check(shipped_excl <= set(_chans54),
              "every excluded name is a channel that actually exists - a stale exclusion looks "
              "exactly like a live one and protects nothing",
              "unknown=%s" % sorted(shipped_excl - set(_chans54)))


def suite_dedup_scripts():
    """
    Two byte-identical copies of `check_json_dup_keys.py` ship: one at the repo root (used by
    the published repo's own `.pre-commit-config.yaml`) and one inside the plugin's `tools/`
    (shipped to end users, referenced by the wrapper `check_json_dup_keys_hook.py`). A fix to
    one that skips the other is a drift pattern no other suite catches, because both copies
    pass `suite_dev_tooling` independently.

    🔴 AUTHORED BY IGOR, IN THE GENERATED TREE, AND PORTED HERE 2026-08-19 (R48). This is the
    second round running in which a hand edit landed in `model-orchestration-kit` - which
    `package.py` regenerates from this directory, so the next clean build would have deleted it
    silently. The check itself is good and is kept verbatim in substance; only its home moved.
    Same lesson as R47.1's SUPPORT.md/CODE_OF_CONDUCT adoption: **a generated tree accepts an
    edit and forgets it, and nothing warns you at the moment you make it.**

    Runs only from the repo layout; a user's kit or plugin install has one copy.
    """
    section("Shipped-script duplication (Igor, T61 audit — ported from the generated tree)")
    root = HERE.parent.parent.parent.parent  # skill -> plugin -> plugins -> repo root
    a = root / "tools" / "check_json_dup_keys.py"
    b = root / "plugins" / "model-orchestration" / "tools" / "check_json_dup_keys.py"
    if not (a.is_file() and b.is_file()):
        return                       # not the repo layout - nothing to compare
    import hashlib
    ha = hashlib.sha256(a.read_bytes()).hexdigest()
    hb = hashlib.sha256(b.read_bytes()).hexdigest()
    check(ha == hb,
          "check_json_dup_keys.py: repo-root and plugin copies are byte-identical",
          "root=%s plugin=%s" % (ha[:12], hb[:12]))


def suite_r49_record_integrity():
    """
    R49. The record survives being interrupted, and says so when it is half-written.

    R48 made the record crash-proof by writing it twice and, in the same stroke, made a crash
    invisible: a death during the citation audit now leaves a complete-LOOKING diagnostics.json
    and REPORT.md. NINE of that round's twelve reviewers raised it independently - the highest
    convergence any finding has reached here - and every one of them named the same two missing
    pieces: a completeness flag, and an atomic write so the second pass cannot destroy the first.

    The other checks lock things this round found in its own code rather than in a reviewer's
    imagination: two parameters that were passed to `write_handoff` and never read, and a
    removals register that could record a demotion but not a retirement.
    """
    section("R49. A half-written record announces itself; a passed parameter is read")
    sys.path.insert(0, str(HERE))
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")
    rep = (HERE / "report.py").read_text(encoding="utf-8")

    # ---- atomic writes -------------------------------------------------------------------------
    check("def _atomic_write(" in src and "os.replace(tmp, path)" in src,
          "there is one atomic-write helper and it renames rather than truncates in place")
    check("os.fsync(f.fileno())" in src,
          "the temp file is fsynced before the rename - a rename is atomic, the CONTENT still "
          "has to have reached the disk")
    for artefact in ("diagnostics.json", "REPORT.md", "HANDOFF.md"):
        check('_atomic_write(os.path.join(outdir, "%s")' % artefact in src
              or (artefact == "diagnostics.json" and "_atomic_write(path, json.dumps" in src),
              "%s is written atomically - a reader between the two passes gets the old complete "
              "file or the new one, never a truncated one" % artefact)
    check('open(os.path.join(outdir, "REPORT.md"), "w"' not in src
          and 'open(os.path.join(outdir, "HANDOFF.md"), "w"' not in src,
          "no in-place truncating write survives for the round's own artefacts")

    # ---- the completeness flag -----------------------------------------------------------------
    check('"record_status"' in src and '"complete": audit is not None' in src,
          "the early write SAYS it is the early write - before R49 a crash during the citation "
          "audit left a record that looked finished, which is worse than the missing file it "
          "replaced")
    check('_rs.get("complete") is False' in rep and "EARLY WRITE" in rep,
          "REPORT.md declares a partial record at the TOP, where Igor reads - a true verdict "
          "three screens down is what round 48 was about")

    # ---- parameters that are passed must be read -----------------------------------------------
    _handoff = src.split("def write_handoff(")[1].split("\ndef ")[0]
    check("os.path.getmtime(p) < started" in _handoff,
          "write_handoff READS `started` - it was in the signature and unused, so a previous "
          "panel left in the same --out folder was billed to this round's read cost")
    check("if panel" in _handoff and "панели `%s`" in _handoff,
          "write_handoff READS `panel` - the fresh context cannot otherwise tell a voice that "
          "was excluded from one that failed")

    # ---- the class test, asked for by orglm52 reviewing R48 -------------------------------------
    # «The only test that catches the CLASS is one that asserts every dispatcher writes
    # answer_bytes.» It cannot be asserted per dispatcher without running them all; the stronger
    # statement is that no dispatcher CAN write it, because exactly one line in the file does.
    check(src.count('r["answer_bytes"] =') == 1,
          "`answer_bytes` is assigned in exactly ONE place in the whole file - the class fix is "
          "that a dispatcher cannot forget a field it is not allowed to set",
          "assignments=%d" % src.count('r["answer_bytes"] ='))
    check('encoding="utf-8", newline="\\n"' in src,
          "answer files pin their line ending, so the same review is the same bytes on every "
          "platform instead of differing by exactly its newline count on Windows")

    # ---- a flag value the vendor never accepted -------------------------------------------------
    # agy 1.1.15 `--help`: «--mode  Set the agent execution mode for this session (accept-edits,
    # plan)». R46 replaced `--mode plan` with `--mode default` to stop this channel returning a
    # plan; «default» is not in the enum, so every agy call since printed
    # `warning: unrecognized --mode value "default"` and exited 0 — and in the R49 panel that
    # warning was agy37flash's entire 74-byte answer file. Asserting ABSENCE rather than the legal
    # values on purpose: an enum copied into a test is a vendor fact that rots, and the point is
    # that we have no business setting this knob at all.
    check('"--mode"' not in src,
          "the agy call passes no --mode: «default» was never one of its values, and an "
          "unrecognised one warns on stderr where it can be mistaken for the answer")

    # ---- the token estimate, kept BECAUSE it was measured ---------------------------------------
    check(getattr(__import__("orchestrate"), "CHARS_PER_TOKEN", None) == 4,
          "the read-cost divisor stays 4: three R48 reviewers called it a 2-3x underestimate on "
          "Cyrillic and a measurement over 17 real files put the true ratio at 3.48-4.64 B/token, "
          "Russian and English alike")
    check("EST_BAND" in src and "tiktoken" in src,
          "the estimate ships its measured error band instead of the word «estimate»")

    # ---- the manifest no longer overstates its own guarantee ------------------------------------
    #
    # 🔴 FIFTH INSTANCE OF «NAME THE SHAPE, NEVER SPELL IT», and this time in the check itself.
    # Written first as `"never from the run's own records" not in _handoff`, it went red against
    # the fixed code, because the docstring that RETRACTS the sentence has to quote it to retract
    # it. A check that cannot tell a claim from a discussion of that claim is the same defect as
    # the R45 bracket detector that could not tell a damaged answer from an answer about damage.
    # The claim lives in the emitted string, so the docstring is cut off before matching.
    _handoff_code = _handoff.split('"""')[2] if _handoff.count('"""') >= 2 else _handoff
    check("never from the run's own records" not in _handoff_code,
          "the manifest no longer TELLS ITS READER it ignores the records it demonstrably joins "
          "against - four R48 reviewers refuted that sentence using its own second half")
    check("never a projection of what the harness believed it wrote" in _handoff_code,
          "and it still states the half that IS true: the file list is never derived from the "
          "run's beliefs, which is the property that stopped round 46 recurring")


def suite_r56_agy_concurrency_and_permissions():
    """R56. Three ways this channel loses a whole run, and none of them is the model's fault.

    All three were measured this round, on the same brief through the same code path:

      1  CONCURRENT STARTS RACE ON A SHARED TOOL CACHE. Solo 2/2 ok; beside its two siblings,
         one channel dies at ~4 s with 0 output tokens. 2 of 6 concurrent launches, and the
         victim MOVES (agy31pro once, agy36flash once) - which is why R55 wrote it off as
         transient. `being used by another process` appears 0x in both solo logs and 1-2x in
         every concurrent one.
      2  AN UNLISTED TOOL CANCELS THE TURN; AN EXPLICITLY DENIED ONE DOES NOT. Measured in
         three arms: denied -> ordinary tool error, model recovers and answers; unlisted ->
         `Print mode: soft-denying tool confirmation`, status CANCELED, everything discarded.
      3  THE DEFAULT LOG PATH IS A WALL CLOCK, so simultaneous starts share one file and the
         failing channel's record is the one that gets overwritten.

    What is asserted here is that each fix REACHES THE CALL - the R31 rule, because a knob that
    is only configured is not a knob.
    """
    print("\n" + "=" * 78)
    print("R56. Concurrent agy starts, and the difference between denied and unlisted")
    print("=" * 78)
    src = open(os.path.join(HERE, "orchestrate.py"), encoding="utf-8").read()

    # 1 - the stagger, and that it is applied BEFORE the launch rather than merely defined
    check("_agy_stagger()" in src and "def _agy_stagger" in src,
          "the agy launch is spaced by _agy_stagger() - concurrent starts corrupt a shared MCP "
          "tool-schema cache and the loser's run is discarded before its first token")
    i_call, i_run = src.find("waited = _agy_stagger()"), src.find("p, secs = _run(cmd, timeout=3600")
    check(0 < i_call < i_run,
          "the stagger is taken BEFORE the subprocess starts, not after it - the race is in the "
          "first seconds of startup",
          "stagger@%d launch@%d" % (i_call, i_run))
    import orchestrate as _o
    prev = os.environ.get("AGY_START_SPACING")
    try:
        os.environ["AGY_START_SPACING"] = "0"
        _o._agy_stagger()
        check(_o._agy_stagger() == 0.0,
              "AGY_START_SPACING=0 disables the wait, so a single-channel run pays nothing and "
              "the value is not frozen into the code")
    finally:
        if prev is None:
            os.environ.pop("AGY_START_SPACING", None)
        else:
            os.environ["AGY_START_SPACING"] = prev

    # 2 - the warning must distinguish the two permission states, because they need opposite fixes
    check("soft-denying tool confirmation" in src,
          "the harness reads the CLI log for a SOFT-DENY - that event appears in neither "
          "stream-json nor stderr, so without this a cancelled run has no recorded cause")
    check("not a denial - a MISSING RULE" in src,
          "the soft-deny warning says the tool was UNLISTED, not denied - they look identical "
          "in the result frame and need opposite fixes, and calling the wrong one sent two AOS "
          "rounds to a patch script that could not apply")
    # 🔴 R57 REWROTE THESE THREE CHECKS, AND THE REASON IS THE POINT. They used to read the
    # patch file as TEXT and assert that `mcp(firecrawl/*)` was ABSENT from it. R57 denies
    # Firecrawl wholesale - which is strictly stronger than not wildcarding it - and the string
    # `mcp(firecrawl/*)` therefore now appears in the file, in the DENY list. The old assertion
    # would have gone RED on more correct code, purely because it keyed on a spelling instead of
    # on the decision. That is this project's own recurring shape (R45: a test that hard-codes the
    # value a human is meant to change tests the human), and the fix is to ask the MODULE what it
    # decided rather than to grep its source.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_perms", os.path.join(HERE, "patch_agy_permissions.py"))
    _perms = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_perms)
    allow, deny = set(_perms.ALLOW), set(_perms.DENY)

    check("mcp(*)" in allow,
          "every MCP server is allowed by ONE wildcard - a per-server wildcard covers a tool the "
          "server gains, but not a SERVER added later, which is the same fatal unlisted state one "
          "level up. Measured R57 arm T with a valid control")
    # 🔴🔴 THE CHECK THAT WOULD HAVE CAUGHT MY OWN OVER-REACH, AND DID NOT EXIST UNTIL IT HAD
    # ALREADY COST SOMETHING. R57 first shipped `deny mcp(firecrawl/*)`, reasoning that under
    # `mcp(*)` a tool the server gains later could bill. The reasoning was sound and the rule was
    # wrong, because a wildcard deny takes the WHOLE server (arm S) and the owner's policy is
    # *scrape and map are allowed*. Within the hour the verification panel showed agy31pro getting
    # `Permission denied for mcp(firecrawl/firecrawl_scrape)` - a tool it was supposed to have -
    # and the round's own author read that as the fix working.
    #
    # A safety rule that also removes a SANCTIONED capability is not a stricter version of the
    # policy, it is a different policy. So the assertion is two-sided: the expensive tools must be
    # denied AND the sanctioned ones must NOT be. A one-sided safety test can only ever fail in
    # the direction of too little safety, which is why it never objects to over-reach.
    for tool in ("firecrawl_crawl", "firecrawl_agent", "firecrawl_search",
                 "firecrawl_monitor_create"):
        check("mcp(firecrawl/%s)" % tool in deny,
              "firecrawl/%s is denied by NAME - it is metered, recurring or duplicates a free "
              "tool" % tool)
    for tool in ("firecrawl_scrape", "firecrawl_map"):
        check("mcp(firecrawl/%s)" % tool not in deny and "mcp(firecrawl/*)" not in deny,
              "firecrawl/%s stays REACHABLE - it is 1 credit and it is the sanctioned last "
              "resort for a bot-protected page. Neither a direct deny nor a wildcard over the "
              "server may take it away, because a wildcard deny cannot be rescued by a more "
              "specific allow (arm S)" % tool)
    check("mcp(playwright/*)" not in deny,
          "the playwright server is not denied wholesale - the owner's call, 2026-08-20, "
          "re-confirmed knowingly 2026-08-31 after a full risk briefing (CLOSED, not "
          "pending). A denial here is free in run terms, which is exactly why it is "
          "tempting to add one nobody asked for")
    check("mcp(jina-mcp-server/show_api_key)" in deny,
          "wildcarding every server pulled the credential-revealing tool into reach, and the SAME "
          "change denies it - a widened allow and its matching deny belong in one commit")

    # 3 - the per-channel log, which is what made 1 and 2 diagnosable at all
    check('"--log-file", agy_log' in src,
          "each agy channel writes its own CLI log - the default path is timestamped to the "
          "SECOND, so a panel's simultaneous children shared one file and the failing channel's "
          "record was the one overwritten")
    check("_AGY_LOG_NOISE" in src and "not logged into Antigravity" in src,
          "the log reader excludes the lines present in EVERY log - «not logged into "
          "Antigravity» appears 20-52 times in all 89 logs on this machine, successes included, "
          "and reading it as the cause is a root cause with a perfect citation and no control")


def suite_r57_agy_capability_model():
    """R57. agy's permission language is CAPABILITY-based, and two of its rules cannot be written.

    Everything below was measured against agy 1.1.16 with a valid control on every arm; the raw
    logs are under `runs/r57/`. The grammar itself is undocumented - `agy --help` covers flags,
    and the vendor's reference page is slash-commands only - so it was read out of the store the
    product writes for itself, `~/.gemini/config/config.json`:

        command(...)  mcp(server/tool)  read_file(path)  write_file(path)
        read_url(domain)  execute_url(domain)

    Six kinds, no bare-tool-name form (`run_command` and `RunCommand` match nothing in either
    list). The 49 tools all map onto those six, so "every tool" is a closed set.

    THE TWO THINGS THAT CANNOT BE WRITTEN, and both shape what this file may promise:

      * "allow everything except deleting files". `*` is an ALL-TOKEN, not a glob: with
        `allow command(*)` + `deny command(*del*)` the canary file WAS DELETED and no deny fired.
        And there is nothing else to deny instead - agy has no file-deletion tool at all; the
        `DeleteFileOrDirectory` symbols in its binary belong to an IDE-facing gRPC service. So the
        shell is the only route to deletion, and denying it is the only way to close that route.

      * "allow the shell safely". `command()` is EXACT-match despite its own help string saying
        "matches commands by prefix", so an allow-list would have to predict the exact command
        line the model composes. `--sandbox` cancels an ALLOW anyway.

    What DOES work, and is therefore what the patch ships: deny beats allow in every combination
    tried, a deny is survivable (the model gets an ordinary error and finishes), and `mcp(*)`
    covers every server including ones added later.
    """
    section("R57. agy capability model: what can and cannot be expressed")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("_perms57",
                                        os.path.join(HERE, "patch_agy_permissions.py"))
    perms = _ilu.module_from_spec(spec)
    spec.loader.exec_module(perms)

    check(perms.SHELL_DENY in perms.DENY,
          "the shell is EXPLICITLY DENIED, not left unlisted. Unlisted is the fatal state - the "
          "turn is discarded and reported as an empty answer with status SUCCESS and exit 0 - "
          "while a denial is an ordinary tool error the model recovers from. This is also the "
          "only way to stop file deletion, because the shell is the only route to it")
    check(not any(r.startswith("command(") for r in perms.ALLOW),
          "no command() ALLOW rule is shipped: matching is exact, so an allow-list would have to "
          "predict the model's exact command line, and --sandbox cancels such an allow anyway")

    # The two checks below are about a promise this file must NOT make. A reader who sees a
    # deletion-shaped deny rule will reasonably conclude deletion is blocked by it; the canary
    # says otherwise, so the rule must not exist to be misread.
    glob_shaped = [r for r in perms.DENY if "*" in r and r != "command(*)" and "/" not in r]
    check(not glob_shaped,
          "no deny rule pretends to pattern-match part of a command line - `*` does not glob "
          "(measured: the canary file was deleted under `deny command(*del*)`), so such a rule "
          "would be decoration that reads as protection",
          "found=%r" % (glob_shaped,))

    # missing() is the single source of truth shared with doctor.py and the run-time preflight.
    # If it stops reporting a stale config, every downstream warning goes quiet at once - the
    # failure mode this project keeps meeting: dispatch fails loudly, reporting fails silently.
    empty_missing = perms.missing({})
    check(empty_missing[0] == perms.ALLOW and empty_missing[1] == perms.DENY,
          "missing() on an empty config asks for the FULL rule set - doctor.py and the run-time "
          "preflight both derive their idea of 'current' from this one function, so a config "
          "that predates the fix cannot be certified green by either of them")
    current = {"permissions": {"allow": list(perms.ALLOW), "deny": list(perms.DENY)}}
    check(perms.missing(current) == ([], []),
          "missing() on a fully-patched config asks for nothing - otherwise doctor.py would nag "
          "for ever and the warning would be trained away")
    check(perms.missing(current, keep_shell=True) == ([], []),
          "--keep-shell is a real variant, not a flag that changes nothing: it drops exactly the "
          "shell rule and demands the rest")
    kept = perms.missing({"permissions": {"allow": list(perms.ALLOW),
                                          "deny": [d for d in perms.DENY
                                                   if d != perms.SHELL_DENY]}},
                         keep_shell=True)
    check(kept == ([], []),
          "with --keep-shell, a config that has everything EXCEPT the shell deny is current - so "
          "someone who needs the interactive TUI's shell is not nagged for ever either")


def suite_r58_update_check():
    """R58. Update check: local-only for the plugin hook, full for doctor.

    Two-sided per property (R57 rule): every safety-shaped assertion has both a positive form
    (X happens when it should) AND a negative form (X does NOT happen when it should not). A
    one-sided check can only fail towards too little safety, which is exactly why the R57
    over-reach shipped.
    """
    section("R58. update_check: local-only hook, full doctor path, no phones-home surprises")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("_uc58", os.path.join(HERE, "update_check.py"))
    uc = _ilu.module_from_spec(spec)
    spec.loader.exec_module(uc)

    # Semver comparison — the trap the panel flagged, and the reason string compare is wrong.
    check(uc.is_newer("1.10.0", "1.9.0"),
          "1.10.0 > 1.9.0 as version tuple (string compare would say the opposite)")
    check(not uc.is_newer("1.9.0", "1.10.0"),
          "1.9.0 is NOT newer than 1.10.0 — the reverse direction is also right")
    check(not uc.is_newer("1.33.1", "1.33.1"),
          "equal is NOT newer — otherwise every session fires the notice")
    check(not uc.is_newer(None, "1.33.1"),
          "None on either side is NOT newer — silent, never crash the hook")

    # The User-Agent must NOT carry the installed version. Panel: with a small user base,
    # version + IP + time is a fingerprint. The two-sided form: it must be exactly this
    # string, and it must never grow to include a digit.
    check(uc._user_agent() == "ai-second-opinion-update-check",
          "User-Agent is exactly the versionless string — the panel's privacy floor")
    import re
    check(not re.search(r"\d", uc._user_agent()),
          "no digit in the UA at all — a future edit that pastes VERSION in would be caught here")

    # The stamp path is per-user (expanduser) and outside any tree an upgrade could replace.
    check(uc.STAMP_PATH.startswith(os.path.expanduser("~")),
          "stamp path is under the user's home directory")
    check("skills" not in uc.STAMP_PATH,
          "stamp is NOT inside the skill folder — an upgrade must not lose the snooze")

    # Backoff on network failure: with 1 failure the fresh window shrinks from 168h to 1h so
    # the next session actually retries. Two-sided: 0 failures still uses the full window.
    long_stamp = {"consecutive_failures": 0,
                  "last_check_utc":
                      (uc._now_utc() - __import__("datetime").timedelta(hours=2)).strftime(
                          "%Y-%m-%dT%H:%M:%SZ")}
    check(uc.stamp_is_fresh(long_stamp),
          "2h old with 0 failures: fresh (168h window is intact)")
    short_stamp = dict(long_stamp, consecutive_failures=1)
    check(not uc.stamp_is_fresh(short_stamp),
          "2h old with 1 failure: STALE (backoff shortened the window to 1h)")

    # Clock skew — a `last_check_utc` in the future must be treated as stale, not as fresh.
    # Panel: SPARK12CONT called this out for VM resume / dual-boot.
    future_stamp = {"last_check_utc":
                    (uc._now_utc() + __import__("datetime").timedelta(hours=48)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ")}
    check(not uc.stamp_is_fresh(future_stamp),
          "clock in the past: a future last_check is treated as stale (do not sleep for a week)")

    # The disable env vars — one positive and one negative per gate.
    for var in ("MODEL_ORCH_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"):
        os.environ.pop(var, None)
    check(not uc.is_check_disabled(),
          "no env vars set: check is enabled (the default is ON, which is why the LOCAL-only "
          "hook design matters — the network path is opt-in via --install-hook only for "
          "Method-2/3 users)")
    os.environ["MODEL_ORCH_UPDATE_CHECK"] = "0"
    check(uc.is_check_disabled(), "our own env=0 disables")
    os.environ["MODEL_ORCH_UPDATE_CHECK"] = ""
    os.environ["NO_UPDATE_NOTIFIER"] = "1"
    check(uc.is_check_disabled(), "NO_UPDATE_NOTIFIER=1 disables (ecosystem convention)")
    os.environ["NO_UPDATE_NOTIFIER"] = ""
    os.environ["CI"] = "true"
    check(uc.is_check_disabled(), "CI=true disables (every CI system sets it)")
    os.environ["CI"] = "false"
    check(not uc.is_check_disabled(),
          "CI=false does NOT disable — the two-sided form of the CI check")
    os.environ.pop("CI", None)

    # The tags URL, not /releases/latest — measured live 2026-08-20, this repo's Releases
    # stopped at v1.27.0 while tags climbed to v1.33.1. The panel would have caught it too
    # (GROKBUILD). Two-sided: the correct endpoint IS used, and the broken one is NOT.
    check("/tags" in uc.GITHUB_TAGS_URL, "the checker uses /tags, which is authoritative")
    check("releases/latest" not in uc.GITHUB_TAGS_URL,
          "the checker does NOT use /releases/latest, which returned v1.27.0 on 2026-08-20 "
          "while the highest tag was v1.33.1")

    # pick_latest_tag picks by tuple, not by list order. The API returns them in some order
    # (usually reverse chronological, but that is not documented). The picker must not trust
    # it, so a shuffled list still returns the max.
    shuffled = [{"name": "v1.32.0"}, {"name": "v1.9.0"}, {"name": "v1.33.1"},
                {"name": "v1.33.0"}, {"name": "not-a-tag"}]
    check(uc.pick_latest_tag(shuffled) == "v1.33.1",
          "pick_latest_tag returns the max version tuple regardless of list order — "
          "1.33.1 > 1.32.0 > 1.9.0 in numeric tuple, would be 1.33.0 > 1.32.0 > 1.9.0 in "
          "string order")

    # The hook mode's message MUST NOT be emitted on a first-ever run — we do not know if the
    # user has been on this version for a year or installed it yesterday, and nagging on
    # first run is what teaches people to ignore the nag. Simulated via a temp install +
    # empty stamp.
    import tempfile
    d = tempfile.mkdtemp(prefix="uc_selftest_")
    (open(os.path.join(d, "VERSION"), "w", encoding="utf-8")
        .write("1.33.1\n"))
    stamp_path = os.path.join(d, ".stamp.json")
    saved_here, saved_stamp = uc.HERE, uc.STAMP_PATH
    try:
        uc.HERE = d
        uc.STAMP_PATH = stamp_path
        action, msg = uc.do_local_delta()
        check(action == "no-change" and msg is None,
              "first-ever run seeds the stamp but does NOT nag — otherwise every fresh install "
              "shows a bogus 'update' notice on session start")
        # Second call with the same VERSION also stays silent.
        action, msg = uc.do_local_delta()
        check(action == "no-change" and msg is None,
              "same version on a subsequent run is silent — the two-sided form of the above")
        # Now bump the VERSION and expect exactly one notice.
        with open(os.path.join(d, "VERSION"), "w", encoding="utf-8") as f:
            f.write("1.34.0\n")
        action, msg = uc.do_local_delta()
        check(action == "local-update" and msg and "1.33.1" in msg and "1.34.0" in msg,
              "a bumped VERSION file fires the notice exactly once, naming both versions")
        action, msg = uc.do_local_delta()
        check(action == "no-change" and msg is None,
              "the notice fires ONCE — the second call is silent (stamp was updated)")
    finally:
        uc.HERE, uc.STAMP_PATH = saved_here, saved_stamp


def suite_r55_child_env_and_first_error():
    """R55. A tool whose BINARY is missing, and an instrument that named the last frame."""
    section("R55. The child's PATH is a dependency, and the first error is the cause")
    sys.path.insert(0, str(HERE))
    import orchestrate as o
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")

    # ---- the knob has to reach the CALL --------------------------------------------------------
    # This project's own rule, learned the expensive way: a knob you only DEFINED is not a knob.
    # `_agy_env` existing proves nothing; what matters is that the agy subprocess is launched
    # with it. Assert the dispatched call, not the helper.
    agy_call = [ln for ln in src.splitlines()
                if "_run(cmd" in ln and "stdout_path=ndjson" in ln]
    check(len(agy_call) == 1 and "env=_posix_child_env()" in agy_call[0],
          "the agy subprocess is launched WITH _posix_child_env() - without it grep_search "
          "cannot resolve its binary from a PowerShell parent, and the model's fallback for a "
          "broken search tool is a shell command, which is denied and discards the run",
          "call=%r" % (agy_call[0].strip() if agy_call else None))
    # 🔴 THE CLASS, NOT THE CHANNEL. agy is the one that lost rounds, but all three agentic-CLI
    # children inherit the parent shell's PATH. Counting the call sites is the check: this
    # project's recurring defect is a guard that runs on one branch of six.
    # 🔴 READ THE WHOLE CALL, NOT ITS FIRST LINE. The first version of this check scanned
    # line by line and reported "1 of 6", because `env=` sits on a CONTINUATION line in most
    # of these calls. A checker that reads one line while the fact spans several is the same
    # shape as the bug this suite exists for, committed inside the test for it.
    cli_launches = []
    for m in re.finditer(r"(?:p, secs = _run\(cmd|p = subprocess\.run\(cmd)", src):
        depth, i = 0, m.end() - len("cmd")
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        cli_launches.append(src[m.start():i + 1])
    with_env = [c for c in cli_launches if "env=" in c]
    check(len(with_env) >= 3,
          "every agentic-CLI child (agy, kimi/hermes, grokcli) is launched with an explicit "
          "environment, so none of them depends on which shell started python",
          "%d of %d whole call expressions pass env=" % (len(with_env), len(cli_launches)))
    check("_agy_env" not in src.replace("`_agy_env`", ""),
          "the helper is named for the CLASS, not for the channel that found it - a fix called "
          "_agy_env is a fix nobody applies to kimi")

    # ---- APPEND, never prepend -----------------------------------------------------------------
    # Git\usr\bin also ships find.exe and sort.exe. Prepending would shadow the Windows built-ins
    # of those names for everything else the child runs - a fix that quietly breaks its neighbours.
    fn = src.split("def _posix_child_env(")[1].split("\ndef ")[0]
    check('env["PATH"] = env.get("PATH", "") + os.pathsep + d' in fn,
          "the POSIX toolset is APPENDED to the child PATH, so Windows `find`/`sort` still win")
    check("if shutil.which(\"grep\"):" in fn,
          "when grep already resolves (a Git-Bash parent) the environment is left alone entirely")

    # ---- no machine's layout baked into a shipped default --------------------------------------
    pt = src.split("def posix_tools_dir(")[1].split("\ndef ")[0]
    check("POSIX_TOOLS_DIR" in pt, "an env var overrides the search, so no path is mandatory")
    check(pt.count('os.path.isfile(os.path.join(d, "grep.exe"))') == 1,
          "the directory is accepted only if grep.exe is actually IN it - existence of the "
          "folder is not evidence that the binary is there")

    # ---- it resolves on THIS machine, or says why ----------------------------------------------
    # Executed, not read: a helper that returns None everywhere would pass every check above.
    if os.name == "nt":
        d = o.posix_tools_dir()
        check(d is None or os.path.isfile(os.path.join(d, "grep.exe")),
              "posix_tools_dir() returns a directory that really holds grep.exe, or None",
              "resolved=%r" % d)
        env = o._posix_child_env()
        path = (env or os.environ).get("PATH", "")
        check(shutil.which("grep", path=path) is not None or o.agy_grep_warning(),
              "either grep is resolvable in the environment agy will actually get, or "
              "agy_grep_warning() explains why it is not - never silently neither",
              "which=%r" % shutil.which("grep", path=path))

    # ---- a registry annotation must never reach the wire ---------------------------------------
    # 🔴🔴 MEASURED, NOT REASONED: `orglm52` died in 0.1 s with `HTTP 400 ... provider:
    # Unrecognized key: "order_reason"`. That key is PROSE - it explains why the provider order is
    # not price order - and it lived in the same dict as the vendor's own parameters, so the vendor
    # was asked to honour a comment. The channel was dead from the moment the note was written, and
    # nothing caught it because the note was added AFTER that round's panel had already run.
    check('wire = {k: v for k, v in provider_route.items() if not k.startswith("_")}' in src,
          "underscore-prefixed keys are stripped from the provider block before it is sent - "
          "every other annotation in this registry already marks itself that way")
    _regpr = json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
    _OR_PROVIDER_KEYS = ("only", "order", "ignore", "allow_fallbacks", "sort", "quantizations",
                         "require_parameters", "data_collection", "zdr", "max_price")
    stray = {}
    for cname, chn in (_regpr.get("channels") or {}).items():
        if cname.startswith("_"):
            continue
        pr = chn.get("provider_route") or {}
        odd = [k for k in pr if not k.startswith("_") and k not in _OR_PROVIDER_KEYS]
        if odd:
            stray[cname] = odd
    check(not stray,
          "no channel's provider_route carries a key that is neither a documented OpenRouter "
          "provider parameter nor an underscore annotation - the vendor 400s on those instantly, "
          "for the whole round", "stray=%r" % stray)

    # ---- a canned cause may not contradict its own numbers -------------------------------------
    check("It produced NOTHING" in src and "if not text.strip() and (spent or calls):" in src,
          "the «it had already done the work» sentence is gated on the meter it describes - "
          "agy31pro printed it over 0 tokens and 0 tool calls, where its advice is backwards")

    # ---- the instrument reports the FIRST error ------------------------------------------------
    # 🔴 AOS R52 and R53 were lost to the same missing binary and reported as two different
    # failures, because the summary read the terminal frame. In R52 grep_search failed three
    # times, the model fell back to a raw PowerShell pipeline, that was denied, and our warning
    # named the denial and prescribed patch_agy_permissions.py - which cannot install grep.
    check('"error_seq": []' in src and 'out["error_seq"].append((name, err))' in src,
          "the agy stream parser keeps (tool, message) in the ORDER errors happened - `errors` "
          "is a de-duplicated set with no tool name and cannot answer «which failed first»")
    check('ev["errors"][-1]' not in src,
          "nothing reports the LAST error as the cause any more")
    check("FIRST tool error" in src,
          "the END-MARKER-ABSENT warning names the first error, not the terminal one")
    check("was NOT the first failure" in src,
          "the permission-denial warning says so when an earlier error preceded it, instead of "
          "sending the reader to a fix that cannot apply")
    check("A TOOL'S BINARY IS MISSING FROM THE CHILD'S PATH" in src,
          "a missing binary is reported as a CAUSE in its own right, with the tool that hit it")

    # ---- the persona's ban names a category the MODEL does not use -----------------------------
    # Kept as a check rather than a rewrite: the ban is about run_command and is correct. What was
    # wrong was expecting it to cover `grep_search`, which is neither a terminal nor a shell in
    # any wording the model sees. Prose does not decide tool access; this file says so already.
    check("Permission rules are the only thing that decides tool access. Prose does not." in src,
          "the measured lesson stays next to the persona that keeps tempting the next reader to "
          "solve a tool problem with a sentence")


def suite_r48_visibility():
    """R48. What the run PRODUCED, told truthfully - the round Igor read a table and it lied."""
    section("R48. The artifact measures itself, and a heading may not outrun its rows")
    sys.path.insert(0, str(HERE))
    import orchestrate as o
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")
    rep = (HERE / "report.py").read_text(encoding="utf-8")

    # ---- the codex-in-the-report defect --------------------------------------------------------
    check("ran_rows = [r for r in rows if r[\"ran\"]]" in rep
          and "for r in ran_rows:" in rep,
          "the model table iterates only channels that RAN - it used to walk the whole registry, "
          "so a cheap-panel run printed «Which model actually answered | codex | GPT-5.4»")
    check("NOT RUN in this round" in rep,
          "channels that did not run are named once, in a line whose words say they did not run")
    check("for r in rows:" not in rep.split("## Which model actually answered")[1]
          .split("## Citations")[0],
          "neither of the two telemetry tables walks the full registry any more")

    # ---- bytes: the artifact measures itself ---------------------------------------------------
    check('r["answer_bytes"] = os.path.getsize(_answer_path)' in src,
          "the answer's size is read off the FILE, at the single write site - it used to be set "
          "independently in eight dispatchers and call_http_reviewer (spark) never set it, so "
          "spark12cont showed `-` in six consecutive panels while returning 45 KB reviews")
    http_fn = src.split("def call_http_reviewer")[1].split("\ndef transport_damage")[0]
    check('"bytes"' not in http_fn,
          "call_http_reviewer still does not set `bytes` itself - the fix is the central one, "
          "not a ninth copy of the same promise")
    check('r["answer_file"] = os.path.basename(_answer_path)' in src
          and "answer=%s" in src,
          "the run log names the answer FILE for every channel, so a reading list can be built "
          "from the log instead of from memory (R46 lost 317 KB to a hand-built list)")

    # ---- the manifest is a listdir, not a projection of the records ----------------------------
    check(hasattr(o, "write_handoff"), "write_handoff exists")
    hand = src.split("def write_handoff")[1].split("\n# How many cited URLs")[0]
    check("os.listdir(outdir)" in hand,
          "the manifest is built from a DIRECTORY LISTING - a manifest derived from `results` "
          "would reproduce exactly the blind spot it exists to catch")
    check("orphans" in hand and "ran_without_file" in hand,
          "the manifest names files no record claims, and channels that ran and wrote nothing")
    check("est_tokens" in hand and "CHARS_PER_TOKEN" in hand,
          "the manifest prices the READ, which is the number the defer-or-not decision turns on")

    # ---- the record is not hostage to what runs after the round --------------------------------
    check(src.index("diag = write_diagnostics(a.out, _diag_payload(None))")
          < src.index("audit = citation_audit(results"),
          "diagnostics are written BEFORE the citation audit: the AOS R45 panel spent $3.97, "
          "wrote 17 answers, and died in the gap - leaving no diagnostics.json and no REPORT.md")
    check("except BaseException as exc:" in src.split("audit = citation_audit(results")[1][:1200],
          "the audit is guarded even though its docstring promises it never raises - prose does "
          "not enforce anything")

    # ---- the two-counters family, fourth member ------------------------------------------------
    m = o.meter_source({"completion_tokens_details": {"reasoning_tokens": 40}},
                       "completion_tokens_details", "reasoning_tokens", summed=562)
    check(m.get("value") == 562 and m.get("last_round_value") == 40 and m.get("rounds_summed"),
          "reasoning_meter reports the SUMMED value beside the last-round one - R47 summed "
          "reasoning_tokens and left this meter, one line below it, reading the last round "
          "(measured disagreement up to 4.4x across nine channels)",
          repr(m)[:160])
    m2 = o.meter_source({"output_tokens_details": {"reasoning_tokens": 4901}},
                        "output_tokens_details", "reasoning_tokens")
    check(m2.get("value") == 4901 and "rounds_summed" not in m2,
          "the single-response xAI shape is unchanged - a class fix applied where the class does "
          "not hold is its own defect")

    # ---- diagnoses that read the fields the harness captured -----------------------------------
    oai = src.split("def call_oai_reviewer")[1].split("\nXAI_RESPONSES_URL")[0]
    check("NO ANSWER TURN - finish_reason=tool_calls" in oai,
          "an empty answer whose finish_reason is tool_calls is named, not reported as «gave no "
          "reason» - the R43 nemotron record carried that field while the warning denied it")
    check("gave no reason" not in oai or "finish_reason was absent from every chunk" in oai,
          "«gave no reason» is now claimed only when the reason field really was absent")
    check("TOOL-CALL MARKUP IN PLACE OF AN ANSWER" in oai,
          "raw <tool_call> syntax saved as a review is named - R45's orglm52 got a SHORT ANSWER "
          "note and a transport-corruption note, and neither described what happened")
    check("SUBSTANTIAL TEXT, MARKER MISPLACED" in src
          and "unverified_but_substantial" in src and "unverified_but_substantial" in rep,
          "a complete review that misplaced its end marker is distinguished from an empty one - "
          "«do not parse it» was printed over a 46 473 B answer with 11 citations")

    # ---- the money line states an observation, not an invariant --------------------------------
    check("They MATCH THIS ROUND" in src and "The two meters agree." not in src,
          "the ledger says the meters matched THIS round rather than that they agree - they "
          "disagreed in three of the four measured rounds, once by $1.18")

    # ---- doctor re-reads prices, because a pinned order is a snapshot --------------------------
    doc = (HERE / "doctor.py").read_text(encoding="utf-8")
    check("def check_provider_prices_live" in doc and "check_provider_prices_live(r, mod)" in doc,
          "doctor --online re-reads provider prices and compares them against the pinned order")
    reg = json.loads((HERE / "channels.json").read_text(encoding="utf-8"))
    for name in ("ordeepseekv4pro", "orglm53"):
        pr = (reg["channels"][name].get("provider_route") or {})
        check("baidu" not in [p.lower() for p in pr.get("only", [])],
              "%s no longer routes to the undiscounted provider the pin had put first" % name,
              repr(pr))


def suite_r59_grokbuild_cyrillic_and_recitation():
    """R59. Grokbuild survives a Cyrillic-path workdir; ornemotron3ultra has more fetch
    headroom; goog37flash has a real diagnosis when the recitation filter fires.

    Two-sided per property (R57 rule), and each check names the AOS Round 55 defect it exists
    to prevent so a future edit that reverts the fix hits a failing test that describes what
    the code used to do wrong.
    """
    section("R59. grokbuild Cyrillic + ornemotron3ultra budget + goog37flash recitation")

    sys.path.insert(0, HERE)
    import orchestrate as _o
    import json as _j

    # Fix 1: _ascii_safe_workdir helper — extracted from _agy_once R37, now used by grokbuild
    # too. Two-sided: ASCII passes through unchanged, non-ASCII is deterministically mirrored
    # to an ASCII sibling under %TEMP%.
    p_ascii = os.path.join(tempfile.gettempdir(), "r59-ascii-test")
    check(_o._ascii_safe_workdir(p_ascii, "grokbuild", "grokbuild") == p_ascii,
          "R59: ASCII workdir passes through _ascii_safe_workdir unchanged")

    p_cyr = os.path.join("D:", "Claude Projects", "AOS 2026",
                         "07-Исследования-и-рецензии", "reviews", "grokbuild-ws")
    m1 = _o._ascii_safe_workdir(p_cyr, "grokbuild", "grokbuild")
    check(m1 != p_cyr, "R59: Cyrillic workdir IS mirrored — the fix triggers")
    check(_ascii_ok(m1),
          "R59: mirrored workdir is ASCII-clean — grokcli's Node runtime will not stumble")
    check("orch-grokbuild-ws" in m1,
          "R59: tag_prefix 'grokbuild' produces %TEMP%/orch-grokbuild-ws/ — the helper "
          "parameter is not decorative")

    # Determinism WITHIN A PROCESS: same input twice must land in the same directory so
    # retries reuse it. A UUID-based mirror would fail this. R75 added the pid to the name
    # (concurrent PROCESSES must not share a mirror) - retries stay in-process, so this
    # property survives; the cross-process half is asserted in suite_r75_backlog.
    m2 = _o._ascii_safe_workdir(p_cyr, "grokbuild", "grokbuild")
    check(m1 == m2,
          "R59: _ascii_safe_workdir is deterministic — retries land in the same directory "
          "so files inside survive across invocations")

    # Distinct inputs -> distinct mirrors. Without this, collision-by-basename could serve
    # channel B's PROMPT.md to channel A.
    m3 = _o._ascii_safe_workdir(p_cyr + "-other", "grokbuild", "grokbuild")
    check(m3 != m1,
          "R59: distinct workdirs map to distinct mirrors — no cross-channel collision")

    # tag_prefix parameterisation: agy and grokbuild get different subroots. Without this,
    # concurrent runs of the two channels sharing one mirror root would race on temp files.
    m_agy = _o._ascii_safe_workdir(p_cyr, "agy", "agy")
    check("orch-agy-ws" in m_agy and "orch-agy-ws" not in m1,
          "R59: tag_prefix separates agy and grokbuild mirror roots — no cross-channel race")

    # Fix 2: ornemotron3ultra fetch budget raised from 11 to 16, per-channel in the registry.
    # Two-sided: nemotron gets the bump, other paid openrouter channels do NOT.
    reg = _j.load(open(os.path.join(HERE, "channels.json"), encoding="utf-8"))
    nemo = reg["channels"]["ornemotron3ultra"]["fetch_tool"]
    check(nemo["max_calls"] == 16,
          "R59: ornemotron3ultra.fetch_tool.max_calls == 16 (raised from 11 for the free "
          "channel where wall clock is the only cost)")

    # The bump is CHANNEL-SPECIFIC. Every OTHER openrouter channel that carries a fetch_tool
    # must keep the default 11 - a bump-everywhere would multiply cost on paid channels.
    for name, ch in reg["channels"].items():
        if name == "ornemotron3ultra":
            continue
        ft = (ch or {}).get("fetch_tool") or {}
        if ft.get("enabled") and ft.get("max_calls") is not None:
            check(ft["max_calls"] <= 11,
                  "R59: %s keeps the default max_calls ceiling — the bump is deliberately "
                  "confined to the free channel" % name,
                  "actual: %d" % ft["max_calls"])

    # The registry entry also carries a `_raised_from_11_2026_08_20` explainer key so a
    # future audit can find the reason without reading git blame.
    check("_raised_from_11_2026_08_20" in nemo,
          "R59: the raise carries an inline explanation naming the round it exists to fix")

    # Fix 2b: `tool_choice="none"` is set on the forced-final round, AND `tools` stays in the
    # payload (not popped). The panel reversed the initial R59 draft that popped tools - three
    # providers 400/422 on the pop (xAI, Anthropic-via-OpenRouter, plus prompt-cache breakage).
    # Grep the source: tool_choice must be present, AND the pop line must be gone.
    src = open(os.path.join(HERE, "orchestrate.py"), encoding="utf-8").read()
    check('body["tool_choice"] = "none"' in src,
          "R59: tool_choice='none' is set on the forced-final turn (explicit instruction to "
          "the model that supplements the built-in behaviour when tools stay present)")
    check('body.pop("tools", None)' not in src,
          "R59: `tools` is NOT popped on the forced-final turn - popping busts prompt-cache "
          "and 400s xAI/Anthropic. Keep tools + set tool_choice='none'.")

    # Fix 1c: The workdir passed to call_grokcli is absolutized BEFORE the mirror is applied.
    # This is the ACTUAL root cause of AOS R55 - grokcli resolves relative --prompt-file
    # against `--cwd neutral_cwd()` rather than the parent's cwd, so a relative path always
    # points nowhere. Without abspath, an ASCII path fails just as reliably as a Cyrillic one,
    # which the R59 panel proved live in this project's `runs/r59/` (ASCII) directory.
    check('workdir = os.path.abspath(workdir)' in src,
          "R59: call_grokcli absolutizes workdir before use - grokcli resolves relative "
          "--prompt-file against --cwd, not the parent cwd, so an unabsolutized relative "
          "path always points nowhere")

    # Fix 1d: The ASCII-mirror helper uses sha256, not md5. FIPS-enabled Python raises
    # `ValueError: [Beyond FIPS] md5 is not allowed`. sha256[:12] gives the same 48 bits of
    # entropy for a directory-disambiguator use.
    orch_src = src
    check("hashlib.sha256(workdir.encode" in orch_src,
          "R59: _ascii_safe_workdir uses sha256, not md5 - md5 raises on FIPS-enabled "
          "Python builds (measured by 3 of 9 R59 panellists)")
    check("hashlib.md5(workdir.encode" not in orch_src,
          "R59: no md5 lingers in the workdir-mirror path - md5 was replaced, not shadowed")

    # Fix 3: goog37flash recitation - a dedicated KNOWN_FAILURES entry that matches the
    # 400 error string and gives a specific fix. The generic REFUSAL row must NOT win the
    # match on this error, otherwise the advice ("rewrite the brief as verification") is
    # actively wrong for this class - the filter is not tuneable.
    cause, fix = _o.diagnose(
        'HTTP 400: {"error":{"message":"Request blocked due to copyright/recitation content..."}}')
    check(cause is not None and "recitation" in cause.lower(),
          "R59: the recitation-filter cause is diagnosed by name, not left as unrecognised",
          repr(cause)[:120])
    check(fix is not None and "goog36flash" in fix,
          "R59: the fix recommends the sister channel that has been observed to handle the "
          "same brief - route the round, don't rewrite the brief",
          repr(fix)[:180])
    # Negative control: the RECITATION diagnosis must NOT fire on unrelated 400 errors.
    unrelated_cause, _ = _o.diagnose("HTTP 400: bad request")
    check(unrelated_cause is None or "recitation" not in (unrelated_cause or "").lower(),
          "R59: an unrelated 400 error does NOT falsely surface the recitation diagnosis")

    # Order matters: our new pattern must be BEFORE the generic REFUSAL entry so it wins
    # the match on a record that carries both words. The `diagnose()` function returns the
    # first match. This checks the KNOWN_FAILURES list order directly.
    patterns = [row[0] for row in _o.KNOWN_FAILURES]
    idx_rec = next((i for i, p in enumerate(patterns) if "recitation" in p.lower()), -1)
    idx_ref = next((i for i, p in enumerate(patterns) if p == "REFUSAL"), -1)
    check(idx_rec != -1, "R59: recitation pattern is in KNOWN_FAILURES", "patterns=%d" % len(patterns))
    check(idx_ref != -1, "R59: REFUSAL pattern is still in KNOWN_FAILURES (control)")
    check(idx_rec < idx_ref,
          "R59: recitation entry is placed BEFORE the generic REFUSAL - the more specific "
          "cause wins the match on the record that carries both words",
          "recitation@%d vs REFUSAL@%d" % (idx_rec, idx_ref))


def _ascii_ok(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def suite_r60_shipped_docs_and_kit_exclusion():
    """R60. The shipped tree self-checks pass from an install location.

    Reported by an employee on 1.24.1 who could not get past a red selftest: running
    `python selftest.py` from `~/.claude/skills/model-orchestration/` failed
    "the shipped documents were located" on every install.ps1 install since 1.9.1 (12+ days).
    Reproduced on 1.35.0 here. And a second failure of the same class - `orgpt56lunapro` was
    demoted cheap->standard AND excluded from published distribution, so on shipped trees the
    "REMOVED-not-marked-RETIRED" check flagged it as a lying record when the real cause was a
    build-time hold-back the shipped selftest had no way to know about.

    The class the colleague named: a red indicator people learn to ignore is a disabled
    indicator. Both defects fired only against installs, and both installs are what employees
    receive, so nobody in CI ever saw either red. Two two-sided fixes; a third check that would
    have caught the class at build time before it reached anyone (this suite).

    Two-sided against a real BUILD of the shipped kit, not a hand-crafted mock: the check that
    made the shape possible was the one nobody thought to run against a fresh install directory.
    """
    section("R60. shipped-docs check + kit-excluded channel drift")

    # This suite requires a source tree with kit/ and package.py to build a real kit. Silently
    # skip on installed trees (which are what this test is DEFENDING) - they have no package.py.
    pkg_py = HERE / "package.py"
    if not pkg_py.is_file():
        return

    # --- Fix 1a: KIT_PLUGIN_FILES ships the two docs the shipped-doc check requires ---------
    # The check at suite_prose_matches_behaviour looks for README.md + PRIVACY.md at
    # `here / "kit"` first. package.py must copy them there at build time.
    pkg_text = pkg_py.read_text(encoding="utf-8")
    check('"_OUT/README.md":' in pkg_text and '"kit", "README.md")' in pkg_text,
          "R60: KIT_PLUGIN_FILES ships README.md into plugins/*/skills/*/kit/ - the fastest "
          "path for the shipped-doc check to find it (install.ps1 users had no other route)")
    check('"_OUT/PRIVACY.md":' in pkg_text and '"kit", "PRIVACY.md")' in pkg_text,
          "R60: KIT_PLUGIN_FILES ships PRIVACY.md alongside README.md - the check requires "
          "BOTH; shipping only one would still fail 'not vacuously green'")
    # Reads from _OUT/ (already substituted), not HERE/kit/ (substitution source with `Igor` in
    # it) - the leak sweep would catch a pre-substitution copy at build time and refuse to ship.
    check('_OUT/README.md' in pkg_text,
          "R60: reads from _OUT/README.md (build-output, substituted) not HERE/kit/README.md "
          "(pre-substitution source) - the leak sweep would refuse the raw copy")

    # --- Fix 1b: end-to-end. Build the kit to a temp dir, verify the docs are in place ------
    # This is what the reporter's own probe was: run the check from an install layout. Now that
    # the shipped tree PUTS the docs there, the check finds them at `here / "kit"` immediately.
    build_dir = Path(tempfile.mkdtemp(prefix="orch-r60-build-"))
    try:
        p = subprocess.run([PY, str(pkg_py), "--out", str(build_dir), "--check"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        # --check builds and removes the output. To VERIFY files we need to build without --check.
        # Do that in a second temp dir so a slow build failure does not double the time cost.
        pass
    finally:
        shutil.rmtree(str(build_dir), ignore_errors=True)

    # Real build (no --check) so we can inspect what would be shipped.
    build_dir = Path(tempfile.mkdtemp(prefix="orch-r60-build2-"))
    try:
        p = subprocess.run([PY, str(pkg_py), "--out", str(build_dir)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        check(p.returncode == 0, "R60: package.py builds cleanly",
              "exit=%d stderr=%r" % (p.returncode, (p.stderr or "")[:200]))
        if p.returncode == 0:
            skill_out = build_dir / "plugins" / "model-orchestration" / "skills" / "model-orchestration"
            kit_dir = skill_out / "kit"
            check(kit_dir.is_dir(),
                  "R60: shipped plugin skill has a kit/ subdir (the check's first lookup)")
            readme = kit_dir / "README.md"
            privacy = kit_dir / "PRIVACY.md"
            check(readme.is_file(),
                  "R60: shipped README.md lives at plugins/*/skills/*/kit/README.md so the "
                  "check's `here / 'kit'` lookup succeeds on every install method")
            check(privacy.is_file(),
                  "R60: shipped PRIVACY.md lives alongside README.md - both are required")
            if readme.is_file():
                body = readme.read_text(encoding="utf-8", errors="replace")
                # Substitution SANITY: the shipped README must not carry the raw operator name
                # or an author-only path. The leak sweep would refuse a raw copy, so this is a
                # defense-in-depth check.
                check("C:\\Users\\igors" not in body and "Igor Saevets" in body,
                      "R60: shipped README carries the intended maintainer credit "
                      "('Igor Saevets') but NOT the author's home path (defence in depth)")
            # --- Fix 2: the built channels.json advertises kit-excluded channels --------------
            shipped_ch = skill_out / "channels.json"
            if shipped_ch.is_file():
                reg = json.loads(shipped_ch.read_text(encoding="utf-8"))
                excl = reg.get("_kit_excluded_channels")
                check(isinstance(excl, list) and len(excl) >= 1,
                      "R60: shipped channels.json declares _kit_excluded_channels - a list, so "
                      "the shipped selftest can distinguish 'held back' from 'drift'",
                      "value=%r" % excl)
                # The check that used to fail on Luna Pro. Simulate its condition: any channel
                # in REMOVED_FROM_CHEAP_SINCE that is not in the shipped registry MUST be in
                # _kit_excluded_channels (otherwise the check would go red on install).
                for c in excl or []:
                    check(c not in reg["channels"],
                          "R60: excluded channel %r is actually absent from the shipped "
                          "registry (not just declared)" % c)
            # --- Fix 1c: run the SHIPPED selftest from the shipped location ------------------
            # This is what the reporter did, and what nobody in CI ever did. On the fixed build
            # it must exit 0 with 0 failures. If it does not, ANY new bug of the "check works
            # on dev, breaks on install" class is caught right here at build time, before push.
            sh_selftest = skill_out / "selftest.py"
            if sh_selftest.is_file():
                p2 = subprocess.run([PY, str(sh_selftest), "--quiet"],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=300, cwd=str(skill_out))
                # Extract the summary line. On failure, its last line is the tail of the failure
                # list; on success, it says "N/N checks passed".
                out = (p2.stdout or "") + (p2.stderr or "")
                last_lines = "\n".join(out.strip().splitlines()[-8:])
                check(p2.returncode == 0,
                      "R60: the SHIPPED selftest passes from the SHIPPED install location "
                      "(the check nobody had ever run before this round)",
                      "exit=%d tail=%s" % (p2.returncode, last_lines[:400]))
    finally:
        shutil.rmtree(str(build_dir), ignore_errors=True)


def suite_r70_transport_retry_and_timeout():
    """R70. The transport survives what the vendor rejects, and cuts what hangs.

    Functional checks against real loopback sockets, not mocks of our own code —
    both still cost nothing and call no vendor (127.0.0.1 only).

    (a) A5: one 429 used to kill the WHOLE review on the OAI transport, while
        call_http_reviewer had retried that class for weeks. A loopback HTTP
        server answers 429, 429, then a well-formed completion: the channel must
        recover on the third request, honouring Retry-After. The control that can
        fail: a 400 on the same server must NOT be retried — an unconditional
        retry passes the first arm and fails this one.

    (b) The functional timeout. Every urlopen in orchestrate.py passes timeout=,
        but a value passed is not a value enforced (the depth-knob lesson applied
        to our own code): a server that accepts the connection and never answers
        must cut _post at roughly the requested seconds — not at the 2400s
        default, and not never.
    """
    section("R70. transport retry (A5) + functional timeout")
    import http.server
    import socket
    import threading
    import time
    import orchestrate as o

    # ---- (a) 429, 429, completion -> ok=True on the third request -------------------
    hits: list[str] = []
    completion = {
        "choices": [{"message": {"content": "Verified, trivially.\nR70-DONE-MARK"},
                     "finish_reason": "stop"}],
        "model": "selftest/echo",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    class _Seq(http.server.BaseHTTPRequestHandler):
        codes = [429, 429, 200]

        def do_POST(self):
            n = len(hits)
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            code = self.codes[min(n, len(self.codes) - 1)]
            payload = (json.dumps(completion) if code == 200
                       else '{"error": "slow down"}').encode("utf-8")
            self.send_response(code)
            if code == 429:
                self.send_header("Retry-After", "3")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass                        # keep the suite's stdout to check() lines

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Seq)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]

    sleeps: list[float] = []
    real_sleep, real_envkey = o.time.sleep, o._env_key
    o.OAI_PROVIDERS["_selftest"] = {
        "key_env": "SELFTEST_NOT_A_KEY", "url": url, "depth": "reasoning",
        "search": "plugin", "usage_request": None, "label": "selftest loopback",
        "streaming": False,
    }
    outdir = tempfile.mkdtemp(prefix="orch-r70-")
    try:
        # Capturing sleep does double duty: the suite stays fast AND the waits are
        # asserted as VALUES — a retry that slept the wrong amount would still "work".
        o.time.sleep = lambda s: sleeps.append(s)
        o._env_key = lambda name: "selftest-dummy"
        res = o.call_oai_reviewer(
            BRIEF, "R70-DONE-MARK", os.path.join(outdir, "a.md"),
            model="selftest/echo", name="_selftest", provider="_selftest",
            fetch_tool={"enabled": False}, max_tokens=200, timeout=30)
        check(res.get("ok") is True and len(hits) == 3,
              "R70/A5: two 429s then a completion - the channel recovers on the 3rd "
              "request instead of failing the review",
              "ok=%r hits=%d err=%s" % (res.get("ok"), len(hits),
                                        str(res.get("error"))[:120]))
        check(sleeps == [3, 4],
              "R70/A5: the waits are Retry-After-aware - 3s (header beats the 2s "
              "floor), then 4s (exponential floor beats the header)",
              "sleeps=%r" % (sleeps,))

        hits.clear()
        sleeps.clear()
        _Seq.codes = [400]
        res = o.call_oai_reviewer(
            BRIEF, "R70-DONE-MARK", os.path.join(outdir, "b.md"),
            model="selftest/echo", name="_selftest", provider="_selftest",
            fetch_tool={"enabled": False}, max_tokens=200, timeout=30)
        check(res.get("ok") is False and len(hits) == 1 and not sleeps,
              "R70/A5: a 400 is NOT retried - one request, no sleeps, channel FAILED "
              "(the control an unconditional retry cannot pass)",
              "ok=%r hits=%d sleeps=%r" % (res.get("ok"), len(hits), sleeps))
    finally:
        o.time.sleep = real_sleep
        o._env_key = real_envkey
        o.OAI_PROVIDERS.pop("_selftest", None)
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(outdir, ignore_errors=True)

    # ---- (b) a server that never answers: _post is cut at the requested timeout ----
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    conns = []          # keeps the accepted socket referenced, so it stays OPEN

    def _sink():
        try:
            c, _ = lst.accept()
            conns.append(c)
            c.recv(65536)               # read the request, answer NOTHING
        except OSError:
            pass

    threading.Thread(target=_sink, daemon=True).start()
    t0 = time.time()
    exc = None
    try:
        o._post("http://127.0.0.1:%d/x" % lst.getsockname()[1],
                {"probe": 1}, "selftest-dummy", 2, False)
    except Exception as e:                                        # noqa: BLE001
        exc = e
    elapsed = time.time() - t0
    # The lower bound is the control-that-can-fail: an instant ConnectionRefused
    # (server not really listening) would also "not hang", without ever exercising
    # the timeout. It must have WAITED for roughly the 2s asked for.
    check(exc is not None and 1.5 <= elapsed < 30,
          "R70: _post(timeout=2) against a hung server is cut by the socket layer "
          "in ~2s - neither hangs toward the 2400s default nor fails instantly",
          "exc=%r elapsed=%.1fs" % (exc, elapsed))
    for c in conns:
        try:
            c.close()
        except OSError:
            pass
    lst.close()

    # ---- census: a timeout must be PASSED everywhere before it can be enforced -----
    src = (HERE / "orchestrate.py").read_text(encoding="utf-8")
    bare = [ln.strip()[:90] for ln in src.splitlines()
            if ("urlopen(" in ln or "opener.open(" in ln)
            and not ln.strip().startswith("#") and "timeout" not in ln]
    check(not bare,
          "R70: every urlopen/opener.open call site in orchestrate.py passes "
          "timeout= on the same line (if a refactor wrapped a call across lines, "
          "keep `timeout=` on the line with the open call - this census reads lines)",
          "; ".join(bare))


def suite_r71_bom_payload_and_gate_signature():
    """R71 (D1 harvest). A BOM dies at the file boundary; the gate refuses positional flags.

    (a) BOM poisoning: a brief, --system file or attachment saved by Notepad or
        PowerShell 5 `Out-File` starts with U+FEFF, and a plain utf-8 read used
        to ship it to every vendor as the payload's first character — the class
        a sister project's audit panel named 2026-08-17; its runner has read
        briefs with utf-8-sig since, ours had not. Functional, through
        main(--dry-run): the fixture control proves each file REALLY starts
        with a BOM under plain utf-8, then the parts captured at pii_gate prove
        none of it survived into the payload. --dry-run returns after the gate,
        so nothing is dispatched and no vendor is called.

    (b) pii_gate flags are keyword-only. The 2026-08-07 allow_pii→strict_pii
        inversion promised "a stale positional caller fails visibly" and
        delivered it for ONE polarity: True hit a loud refusal, False silently
        inherited send-by-default (measured R71 on a sister project's runner,
        still positional eleven days after the inversion). A positional call
        must now die with TypeError at the gate, before anything is sent.
    """
    section("R71. BOM at the payload boundary + gate signature")
    import orchestrate as o

    d = tempfile.mkdtemp(prefix="orch-r71-")
    try:
        bom = "\ufeff"
        paths = {}
        for name, body in (("brief", "R71-BRIEF body; nothing here calls a vendor."),
                           ("system", "R71-SYS reviewer framing from a user file."),
                           ("attach", "R71-ATT attached document text.")):
            p = os.path.join(d, name + ".md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(bom + body)
            paths[name] = p
        # The control that can fail: prove the fixtures actually carry a BOM under plain
        # utf-8 — otherwise every assertion below is vacuously green.
        with open(paths["brief"], encoding="utf-8") as f:
            check(f.read().startswith(bom),
                  "fixture control: a plain utf-8 read DOES see the BOM")

        captured = {}

        def _capture_gate(parts, *, strict_pii=False, warn_pii=False):
            captured["parts"] = [(label, text) for label, text in parts]
            return 0

        real_gate, real_argv = o.pii_gate, sys.argv[:]
        o.pii_gate = _capture_gate
        try:
            sys.argv = ["orchestrate.py", "--brief", paths["brief"],
                        "--system", paths["system"], "--attach", paths["attach"],
                        "--marker", "R71-DONE-MARK",
                        "--out", os.path.join(d, "reviews"), "--dry-run"]
            with contextlib.redirect_stdout(io.StringIO()):
                rc = o.main()
        finally:
            o.pii_gate, sys.argv = real_gate, real_argv
        check(rc == 0, "--dry-run with BOM'd brief/system/attach returns 0", "rc=%r" % rc)
        parts = captured.get("parts") or []
        by = dict(parts)
        check("brief" in by and "system" in by,
              "the gate saw brief and system parts", str(sorted(by)))
        check(by.get("brief", "").startswith("R71-BRIEF"),
              "the brief reaches the gate WITHOUT its BOM as first character")
        check("R71-ATT" in by.get("brief", ""),
              "the attachment is inline in the gated brief (so the gate scans it)")
        check(not by.get("system", "").startswith(bom) and "R71-SYS" in by.get("system", ""),
              "a user-authored --system file reaches the gate WITHOUT its BOM")
        joined = "".join(text for _label, text in parts)
        check(bom not in joined,
              "no U+FEFF anywhere in the gated payload parts (attachment BOM included)")

        # ---- (b) the signature IS the guard now ------------------------------------------
        try:
            o.pii_gate([("brief", "clean text")], True)
            check(False, "positional strict_pii must raise TypeError", "no exception")
        except TypeError:
            check(True, "a positional flag dies loudly at the gate (TypeError)")
        with contextlib.redirect_stdout(io.StringIO()):
            kw_ok = o.pii_gate([("brief", "clean text")], strict_pii=True) == 0
        check(kw_ok, "the keyword form still works and clean text passes strict mode")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def suite_r72_reading_protocol():
    """R72 (Igor). The panel's answers become readable by the session that ordered them.

    (a) --answer-cap plumbing, functionally: with a cap the payload carries the length
        discipline WITH the exact number; with --answer-cap 0 it carries none. Captured at
        pii_gate through main(--dry-run) — the same zero-vendor capture point as R71.

    (b) read_order is a REGISTRY census, derived — every channel carries 1, 2 or 3. No
        assertion pins WHICH tier a channel sits in: that value is Igor's to re-tier as models
        rotate, and a test that hard-codes it tests the human (the R45 lesson).

    (c) write_handoff sorts by read_order (not alphabetically — the fixture names invert the
        two orders on purpose), meters the cap in CHARS, flags declared truncations, and
        carries the reading protocol plus the ordered resume-prompt line.
    """
    section("R72. reading protocol: answer cap + smart-first order")
    import orchestrate as o

    d = tempfile.mkdtemp(prefix="orch-r72-")
    try:
        # ---- (a) the cap reaches the payload, and 0 disables it --------------------------
        bp = os.path.join(d, "brief.md")
        with open(bp, "w", encoding="utf-8") as f:
            f.write("R72-BRIEF: no vendor is called by this test.")
        captured = {}

        def _capture_gate(parts, *, strict_pii=False, warn_pii=False):
            captured["brief"] = dict(parts).get("brief", "")
            return 0

        real_gate, real_argv = o.pii_gate, sys.argv[:]
        o.pii_gate = _capture_gate
        try:
            sys.argv = ["orchestrate.py", "--brief", bp, "--marker", "R72-DONE-MARK",
                        "--out", os.path.join(d, "r1"), "--dry-run",
                        "--answer-cap", "12321"]
            with contextlib.redirect_stdout(io.StringIO()):
                rc1 = o.main()
            capped = captured.get("brief", "")
            sys.argv = ["orchestrate.py", "--brief", bp, "--marker", "R72-DONE-MARK",
                        "--out", os.path.join(d, "r2"), "--dry-run", "--answer-cap", "0"]
            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = o.main()
            uncapped = captured.get("brief", "")
        finally:
            o.pii_gate, sys.argv = real_gate, real_argv
        check(rc1 == 0 and rc2 == 0, "both --dry-run runs return 0", "rc=%r/%r" % (rc1, rc2))
        check("12321" in capped and "TRUNCATED-BY-LIMIT" in capped,
              "--answer-cap N puts the length discipline, with N itself, into the gated payload")
        check("TRUNCATED-BY-LIMIT" not in uncapped and "12321" not in uncapped,
              "--answer-cap 0 sends no length instruction — the control that can fail")

        # ---- (b) registry census: every channel has a read_order in {1,2,3} --------------
        reg = json.load(open(os.path.join(HERE, "channels.json"), encoding="utf-8"))
        bad = sorted(n for n, ch in reg.get("channels", {}).items()
                     if ch.get("read_order") not in (1, 2, 3))
        check(not bad,
              "every registry channel carries read_order 1, 2 or 3 (derived census; no test "
              "pins WHICH tier — that value is Igor's to re-tier)", ", ".join(bad))

        # ---- (c) the handoff sorts, meters the cap, and states the protocol --------------
        out = os.path.join(d, "h")
        os.makedirs(out)
        mk = "R72-H-MARK"
        specs = {"BBB.md": ("smart answer.\n" + mk, "chan_b", 1),
                 "AAA.md": ("mid answer.\n" + mk, "chan_a", 2),
                 "CCC.md": ("flash answer " + "x" * 300 + "\ndropped: one item\n"
                            "TRUNCATED-BY-LIMIT\n" + mk, "chan_c", 3)}
        results = {}
        for fn, (body, cn, ro) in specs.items():
            with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
                f.write(body)
            results[cn] = {"answer_file": fn, "read_order": ro, "seconds": 1.0}
        # A standing registry note («NEVER read Nemotron» is the live instance) must surface
        # UNDER the table, next to the file it advises about - not only in the run tail.
        results["chan_c"]["reading_note"] = "NEVER read me (fixture note)"
        with contextlib.redirect_stdout(io.StringIO()):
            h = o.write_handoff(out, results, marker=mk, answer_cap=200)
        check(bool(h) and h.get("read_order_files") == ["BBB.md", "AAA.md", "CCC.md"],
              "the manifest is sorted smart-first (1, 2, 3), not alphabetically",
              repr((h or {}).get("read_order_files")))
        check(h.get("over_cap") == ["CCC.md"] and h.get("truncated") == ["CCC.md"],
              "the cap meter names the over-cap answer and the declared truncation",
              repr((h.get("over_cap"), h.get("truncated"))))
        text = open(os.path.join(out, "HANDOFF.md"), encoding="utf-8").read()
        check("Reading order — smartest voices first" in text and "колонка `read`" in text,
              "the protocol section and the ordered resume-prompt line are both in HANDOFF.md")
        check(text.index("`BBB.md`") < text.index("`AAA.md`") < text.index("`CCC.md`"),
              "the printed table itself is in reading order")
        check("NEVER read me (fixture note)" in text and "(chan_c)" in text,
              "a registry reading_note surfaces UNDER the handoff table, next to its file")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def suite_r74_panel_fixes():
    """R74 — the fixes bought by the R73 full-code panel, each with the control that can fail.

    (a) fallback_model reaches the PLAN (grokbuild: the registry's one auto-fallback was
        decorative from birth - the R62 test called call_http_reviewer directly and so tested
        the function, not the wiring).
    (b) --skip beats a group --only (orgemini37flash: skip ran first and the only-branch
        re-enabled the skipped member).
    (c) ★ must_read census + the handoff sorts the ★ row first within its tier and states
        the mandatory-minimum rule (Idea 3, Igor 2026-09-01).
    (d) --ask defaults the cap OFF and drops the «no length requirement» sentence when a cap
        is explicitly given; a review payload carries the cap AFTER the attachments and
        BEFORE the marker (agy31pro: an instruction a megabyte before the end is dropped).
    (e) citecheck: bracketed-IPv6 URL_RE, never-raising normalise, DNS-class refusal in
        probe_url, single-word FR slug threshold.
    (f) XAI_KEY secret pattern, with negative control.
    (g) update_check: single-command hook shape; malformed settings.json refuses instead of
        wiping; legacy args-shape uninstall; upgraded stamp does not serve the stale nag.
    (h) alias words survive in BOTH worlds: openai/gpt/chatgpt resolve on this registry and
        on the kit's folded one (package.py moves a dropped group's words to the survivor).
    (i) a non-positive spend ceiling refuses DISPATCH (agy31pro: the in-loop breaker armed
        only after the first billed round).
    """
    section("R74. panel fixes: fallback wiring, skip-beats-only, ★ minimum, gates")
    import orchestrate as o
    import routing as r
    import citecheck as cc
    import update_check as uc

    reg = r.load_registry(overlay=False)

    # ---- (a) fallback_model is IN the plan ------------------------------------------------
    # only=[...] so the check holds in BOTH worlds: the kit ships spark13cont disabled
    # (distribution: local) and --only is the documented resurrection path.
    plan = r.resolve(reg, only=["spark13cont"])
    want_fb = (reg["channels"].get("spark13cont") or {}).get("fallback_model")
    check(bool(want_fb), "the registry still declares the Spark HTTP fallback (precondition)")
    check(plan.get("spark13cont", {}).get("fallback_model") == want_fb,
          "the PLAN carries fallback_model - the dispatcher reads p.get(), so this wiring is "
          "the feature", repr(plan.get("spark13cont", {}).get("fallback_model")))

    # ---- (b) --skip beats a group --only --------------------------------------------------
    # apply_flags directly, not resolve(): the ORDERING of the two flags is the unit under
    # test, and resolve()'s later layers (panel filter, ack gates) differ between the source
    # world and the shipped kit - a world-dependent expectation is the R45 class.
    p2 = r.apply_flags(r.initial_plan(reg), reg, only=["grok"], skip=["grok420"])
    check(p2["grok420"]["enabled"] is False and "--skip" in p2["grok420"]["why"],
          "--only grok --skip grok420 leaves grok420 OFF (an explicit exclusion beats an "
          "explicit inclusion)", repr(p2["grok420"]["why"]))
    p3 = r.apply_flags(r.initial_plan(reg), reg, only=["grok"])
    check(p3["grok420"]["enabled"] is True,
          "the control that can fail: without --skip the group member runs")

    # ---- (c) ★ must_read: census + handoff placement --------------------------------------
    musts = sorted(n for n, ch in reg.get("channels", {}).items() if ch.get("must_read"))
    check(bool(musts), "at least one channel carries must_read (Idea 3 has a bearer)")
    bad = [n for n in musts
           if reg["channels"][n].get("read_order") != 1
           or reg["channels"][n].get("panel") != "cheap"]
    check(not bad,
          "every must_read channel is read_order 1 and panel cheap (cheap ⊂ standard, so the "
          "★ exists in BOTH rooms; no test pins WHICH channel - Igor rotates it)",
          ", ".join(bad))
    d = tempfile.mkdtemp(prefix="orch-r74-")
    try:
        out = os.path.join(d, "h")
        os.makedirs(out)
        mk = "R74-H-MARK"
        # Two tier-1 files; the ★ one is alphabetically LAST, so alphabetical order is the
        # control that fails on broken code.
        specs = {"AAA.md": ("plain smart answer.\n" + mk, "chan_a", 1, False),
                 "ZZZ.md": ("the mandatory voice.\n" + mk, "chan_z", 1, True),
                 "MMM.md": ("mid answer.\n" + mk, "chan_m", 2, False)}
        results = {}
        for fn, (body, cn, ro, must) in specs.items():
            with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
                f.write(body)
            results[cn] = {"answer_file": fn, "read_order": ro, "must_read": must,
                           "seconds": 1.0}
        with contextlib.redirect_stdout(io.StringIO()):
            h = o.write_handoff(out, results, marker=mk, answer_cap=20000)
        check(h.get("read_order_files") == ["ZZZ.md", "AAA.md", "MMM.md"],
              "the ★ row sorts FIRST within its tier, ahead of the alphabet",
              repr(h.get("read_order_files")))
        text = open(os.path.join(out, "HANDOFF.md"), encoding="utf-8").read()
        check("| 1★ |" in text and "mandatory minimum" in text and "Строку со ★" in text,
              "★ is printed in the read column, and the mandatory-minimum rule appears in the "
              "protocol AND in the Russian resume prompt")

        # ---- (d) ask-mode cap + payload order ---------------------------------------------
        captured = {}

        def _capture_gate(parts, *, strict_pii=False, warn_pii=False):
            captured["brief"] = dict(parts).get("brief", "")
            return 0

        real_gate, real_argv = o.pii_gate, sys.argv[:]
        o.pii_gate = _capture_gate
        try:
            sys.argv = ["orchestrate.py", "--ask", "R74 ask question", "--dry-run",
                        "--out", os.path.join(d, "a1")]
            with contextlib.redirect_stdout(io.StringIO()):
                rc1 = o.main()
            ask_plain = captured.get("brief", "")
            sys.argv = ["orchestrate.py", "--ask", "R74 ask question", "--dry-run",
                        "--out", os.path.join(d, "a2"), "--answer-cap", "7777"]
            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = o.main()
            ask_capped = captured.get("brief", "")
            att = os.path.join(d, "att.md")
            with open(att, "w", encoding="utf-8") as f:
                f.write("R74-ATT attached document body.")
            bp = os.path.join(d, "brief.md")
            with open(bp, "w", encoding="utf-8") as f:
                f.write("R74-BRIEF: no vendor is called by this test.")
            sys.argv = ["orchestrate.py", "--brief", bp, "--attach", att,
                        "--marker", "R74-DONE-MARK", "--dry-run",
                        "--out", os.path.join(d, "a3")]
            with contextlib.redirect_stdout(io.StringIO()):
                rc3 = o.main()
            rev = captured.get("brief", "")
        finally:
            o.pii_gate, sys.argv = real_gate, real_argv
        check(rc1 == 0 and rc2 == 0 and rc3 == 0, "all three --dry-run runs return 0",
              "rc=%r/%r/%r" % (rc1, rc2, rc3))
        check("Length discipline" not in ask_plain
              and "no length requirement" in ask_plain,
              "--ask alone: cap OFF and the no-length promise present (they used to "
              "contradict in one payload)")
        check("7777" in ask_capped and "no length requirement" not in ask_capped,
              "--ask --answer-cap 7777: the explicit cap wins and the contradicting "
              "sentence is dropped")
        i_att = rev.find("R74-ATT")
        i_cap = rev.find("Length discipline")
        i_mk = rev.rfind("R74-DONE-MARK")
        check(0 <= i_att < i_cap < i_mk,
              "review payload order is [attachments][cap][marker] - the cap rides the tail, "
              "not a megabyte before it", repr((i_att, i_cap, i_mk)))

        # ---- (e) citecheck hardening ------------------------------------------------------
        m6 = cc.URL_RE.findall("see https://[2001:db8::1]/x/y and more")
        check(m6 and m6[0].startswith("https://[2001:db8::1]"),
              "URL_RE keeps a bracketed IPv6 URL whole", repr(m6))
        check(cc.normalise("https://[2001:db8::1oops")[1] == "\x00unparseable",
              "normalise survives a malformed IPv6 bracket instead of raising")
        v, why = cc.probe_url("http://2130706433/")
        # Windows getaddrinfo REFUSES the decimal form outright (gaierror, measured R74) while
        # glibc resolves it to 127.0.0.1 - both roads end refused, so assert the refusal, not
        # which door said no.
        check(v == "SKIPPED" and ("non-public" in why or "DNS failed" in why),
              "a decimal-encoded loopback IP is refused offline (resolved-private on glibc, "
              "unresolvable on Windows - fail-closed either way)", repr((v, why)))
        real_gai = cc.socket.getaddrinfo
        try:
            # **kw: R75's _resolve_public passes type=SOCK_STREAM; a two-positional stub
            # would die with TypeError and report the wrong failure.
            cc.socket.getaddrinfo = lambda h, p, **kw: [(2, 1, 6, "", ("127.0.0.1", 0))]
            v2, why2 = cc.probe_url("http://r74-fake-public-host.example/")
            check(v2 == "SKIPPED" and "non-public" in why2,
                  "a hostname RESOLVING to loopback is refused before any connection",
                  repr((v2, why2)))
        finally:
            cc.socket.getaddrinfo = real_gai

        class _FakeResp:
            def __init__(self, payload):
                self._p = payload
            def read(self):
                return json.dumps(self._p).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        real_open = cc.urllib.request.urlopen
        try:
            cc.urllib.request.urlopen = (
                lambda *a, **k: _FakeResp({"title": "Public Charge Ground of Inadmissibility",
                                           "citation": "87 FR 55472",
                                           "publication_date": "2022-09-09"}))
            v3 = cc.resolve_federal_register(
                "https://www.federalregister.gov/documents/2022/09/09/2022-18867/inadmissibility")
            check(v3 and v3[0] == "TITLE MATCHES",
                  "a single-word FR slug can now MATCH (the old floor demanded 2 hits of a "
                  "slug that can supply at most 1)", repr(v3))
            cc.urllib.request.urlopen = (
                lambda *a, **k: _FakeResp({"title": "Area Navigation Route T-232 Fairbanks",
                                           "citation": "x", "publication_date": "2022-01-01"}))
            v4 = cc.resolve_federal_register(
                "https://www.federalregister.gov/documents/2022/09/09/2022-19286/inadmissibility")
            check(v4 and v4[0] == "WRONG DOCUMENT",
                  "the control that can fail: an unrelated title is still WRONG DOCUMENT",
                  repr(v4))
        finally:
            cc.urllib.request.urlopen = real_open

        # ---- (f) XAI key pattern ----------------------------------------------------------
        sec, _pii = o.scan_payload("XAI_API_KEY=xai-" + "Ab1" * 10, "t")
        check(any("XAI_KEY" in s for s in sec), "an xai- prefixed key is caught", repr(sec))
        sec2, _ = o.scan_payload("the xai-tools folder and xai-shaped prose", "t")
        check(not any("XAI_KEY" in s for s in sec2),
              "short xai- words in prose stay silent - the negative control")

        # ---- (g) update_check -------------------------------------------------------------
        home = os.path.join(d, "home")
        os.makedirs(os.path.join(home, ".claude"))
        real_exp = uc.os.path.expanduser
        try:
            uc.os.path.expanduser = lambda p: home if p == "~" else real_exp(p)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = uc.cmd_install_hook(None)
            sp = os.path.join(home, ".claude", "settings.json")
            data = json.load(open(sp, encoding="utf-8"))
            hook = data["hooks"]["SessionStart"][0]["hooks"][0]
            check(rc == 0 and "args" not in hook and "--hook" in hook.get("command", "")
                  and '"' in hook.get("command", ""),
                  "the installed hook is ONE command string (quoted path, --hook), never "
                  "command+args - the old shape ran a bare `python` and hung", repr(hook))
            with open(sp, "w", encoding="utf-8") as f:
                f.write("{broken json")
            with contextlib.redirect_stdout(io.StringIO()):
                rc2b = uc.cmd_install_hook(None)
            still = open(sp, encoding="utf-8").read()
            check(rc2b == 1 and still == "{broken json",
                  "malformed settings.json REFUSES the install and the file is untouched - "
                  "the old path wiped the user's whole config")
            legacy = {"hooks": {"SessionStart": [{
                "matcher": "startup",
                "hooks": [
                    {"type": "command", "command": "python",
                     "args": [os.path.abspath(uc.__file__), "--check"], "timeout": 5},
                    {"type": "command", "command": "echo other-tool"},
                ]}]}}
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            with contextlib.redirect_stdout(io.StringIO()):
                rc3b = uc.cmd_uninstall_hook(None)
            data3 = json.load(open(sp, encoding="utf-8"))
            left = data3["hooks"]["SessionStart"][0]["hooks"]
            check(rc3b == 0 and len(left) == 1 and left[0]["command"] == "echo other-tool",
                  "uninstall removes the legacy args-shape hook and KEEPS the co-tenant - "
                  "the old counter returned before writing", repr(left))
        finally:
            uc.os.path.expanduser = real_exp

        saved = {}
        real = (uc.is_check_disabled, uc.read_local_version, uc.read_stamp,
                uc.fetch_latest_tag, uc.check_agy_stale, uc.write_stamp)
        try:
            uc.is_check_disabled = lambda: False
            uc.read_local_version = lambda: "9.9.9"
            fresh_stamp = {"last_check_utc": uc._iso_now(), "consecutive_failures": 0,
                           "installed_version": "9.9.8", "pending_message": "OLD NAG"}
            uc.read_stamp = lambda: dict(fresh_stamp)
            uc.fetch_latest_tag = lambda s: ("9.9.9", s)
            uc.check_agy_stale = lambda: False
            uc.write_stamp = lambda s: saved.update(s)
            action, _p = uc.do_check()
            check(action in ("up-to-date", "agy-only")
                  and saved.get("pending_message") is None,
                  "after an upgrade a fresh stamp does NOT serve the stale nag: the check "
                  "re-runs and clears pending_message", repr((action, saved.get(
                      "pending_message"))))
            fresh_stamp["installed_version"] = "9.9.9"
            action2, _p2 = uc.do_check()
            check(action2 == "cached",
                  "the control that can fail: same version -> the fresh stamp IS served")
        finally:
            (uc.is_check_disabled, uc.read_local_version, uc.read_stamp,
             uc.fetch_latest_tag, uc.check_agy_stale, uc.write_stamp) = real
        check("per_page=100" in uc.GITHUB_TAGS_URL,
              "the tags query asks for 100 (this repo already exceeds 30 tags)")

        # ---- (h) the family words resolve in BOTH worlds ----------------------------------
        for w in ("openai", "gpt", "chatgpt", "опенай"):
            check(bool(r.canon_channel_safe(reg, w)),
                  "the word %r resolves to at least one channel (source: the group; kit: "
                  "package.py folds a dropped group's words onto the survivor)" % w)

        # ---- (i) a zero spend ceiling refuses dispatch ------------------------------------
        real_key = o._env_key
        try:
            o._env_key = lambda name: "r74-test-key-not-a-real-one"
            with contextlib.redirect_stdout(io.StringIO()):
                res = o.call_oai_reviewer("brief", "R74-X", os.path.join(d, "x.md"),
                                          model="test-model", provider="openrouter",
                                          spend_guard={"max_usd_per_review": 0})
            check(res.get("ok") is False and "SPEND NOTHING" in (res.get("error") or ""),
                  "max_usd_per_review=0 refuses DISPATCH - the in-loop breaker could only "
                  "arm after the first billed round", repr(res.get("error"))[:100])
        finally:
            o._env_key = real_key
    finally:
        shutil.rmtree(d, ignore_errors=True)


def suite_r75_backlog():
    """R75. The R73 deferred backlog, closed: pin-IP fetch, vetted attach copy,
    mid-stream SSE retry at zero delivered tokens, echocheck --levels ordering,
    workdir pid isolation + sweep.
    """
    section("R75. pin-IP fetch · vetted attach copy · SSE retry · echocheck order")
    import contextlib
    import http.server
    import io
    import threading
    import time as _time
    import citecheck as cc
    import echocheck as e
    import orchestrate as o

    d = tempfile.mkdtemp(prefix="r75-selftest-")
    try:
        # ---- (a) DNS rebinding: the connect goes to the address the CHECK vetted ----------
        # A TTL-0 attacker answers public to the first resolution and loopback to the second.
        # The pin means there IS no second resolution to poison.
        gai_calls = {"n": 0}
        connected = []

        class _FakeResp:
            status, reason = 200, "OK"
            msg = {"Content-Type": "text/plain"}

            def read(self, n=-1):
                return b"pinned body"

        class _FakeConn:
            def request(self, *a, **k):
                pass

            def getresponse(self):
                return _FakeResp()

            def close(self):
                pass

        real_gai, real_conn = cc.socket.getaddrinfo, cc._pinned_conn
        try:
            def _rebinding_gai(h, p, **kw):
                gai_calls["n"] += 1
                ip = "93.184.216.34" if gai_calls["n"] == 1 else "127.0.0.1"
                return [(2, 1, 6, "", (ip, p or 0))]
            cc.socket.getaddrinfo = _rebinding_gai
            cc._pinned_conn = (lambda scheme, host, ip, port, timeout:
                               connected.append(ip) or _FakeConn())
            st, _h, body, pin = cc._pinned_request("http://rebind.example/x")
            check(st == 200 and body == b"pinned body" and gai_calls["n"] == 1,
                  "R75: one exchange, ONE resolution - no second lookup exists to race",
                  repr((st, gai_calls["n"])))
            check(connected == ["93.184.216.34"] and pin == "93.184.216.34",
                  "R75: the socket connected to the FIRST, vetted address - the rebinding "
                  "second answer had nothing left to rebind", repr(connected))

            # Control that can fail: private resolution refuses BEFORE the socket seam.
            connected.clear()
            cc.socket.getaddrinfo = (lambda h, p, **kw:
                                     [(2, 1, 6, "", ("10.0.0.5", p or 0))])
            try:
                cc._pinned_request("http://internal.example/")
                check(False, "R75 control: a private-resolving host must raise")
            except ValueError as ve:
                check("non-public" in str(ve) and not connected,
                      "R75 control: private resolution is refused with the socket seam "
                      "untouched", repr((str(ve)[:60], connected)))

            # A MIXED answer (one public, one private) refuses too - the attacker must not
            # be able to smuggle a private target in a multi-A response.
            cc.socket.getaddrinfo = (lambda h, p, **kw:
                                     [(2, 1, 6, "", ("93.184.216.34", 0)),
                                      (2, 1, 6, "", ("192.168.1.7", 0))])
            try:
                cc._pinned_request("http://mixed.example/")
                check(False, "R75: a mixed public+private answer must raise")
            except ValueError as ve:
                check("non-public" in str(ve),
                      "R75: one private address in a multi-A answer refuses the whole host")

            # probe_url re-pins EVERY redirect hop: hop 1 -> 302 to a second host, which
            # resolves private -> SKIPPED «after redirect», with the connect count at 1.
            connected.clear()
            hop = {"n": 0}

            class _RedirResp(_FakeResp):
                status = 302
                msg = {"Location": "http://second.example/land"}

            class _RedirConn(_FakeConn):
                def getresponse(self):
                    return _RedirResp()

            def _hop_gai(h, p, **kw):
                hop["n"] += 1
                ip = "93.184.216.34" if h == "rebind.example" else "127.0.0.1"
                return [(2, 1, 6, "", (ip, p or 0))]
            cc.socket.getaddrinfo = _hop_gai
            cc._pinned_conn = (lambda scheme, host, ip, port, timeout:
                               connected.append(ip) or _RedirConn())
            v, why = cc.probe_url("http://rebind.example/start")
            check(v == "SKIPPED" and "after redirect" in why and connected == ["93.184.216.34"],
                  "R75: a public host 302-ing to a private-resolving one is refused at the "
                  "hop, having connected only to the vetted first address",
                  repr((v, why[:60], connected)))

            # _safe_fetch_url rides the same pinned path end to end.
            connected.clear()
            gai_calls["n"] = 0
            cc.socket.getaddrinfo = _rebinding_gai
            cc._pinned_conn = (lambda scheme, host, ip, port, timeout:
                               connected.append(ip) or _FakeConn())
            page = o._safe_fetch_url("http://rebind.example/page")
            check("pinned body" in page and connected == ["93.184.216.34"],
                  "R75: orchestrate's fetch_url tool fetches over the pinned connection",
                  repr((page[:40], connected)))
        finally:
            cc.socket.getaddrinfo, cc._pinned_conn = real_gai, real_conn

        # ---- (b) the vetted folder copy ----------------------------------------------------
        aroot = os.path.join(d, "attach")
        os.makedirs(os.path.join(aroot, "docs (old)"))
        with open(os.path.join(aroot, "notes.md"), "w", encoding="utf-8") as f:
            f.write("clean text for review\n")
        with open(os.path.join(aroot, "docs (old)", "blob.bin"), "wb") as f:
            f.write(b"\x00" * 64)
        parts, skipped = o._scan_dir_texts([aroot])
        pairs, writes = o._vet_snapshot([aroot], parts, os.path.join(d, "vet"))
        o._write_vetted_snapshot(pairs, writes)
        snap = pairs[0][0]
        check(os.path.isfile(os.path.join(snap, "notes.md")),
              "R75: the scanned file exists in the vetted copy")
        check(not os.path.exists(os.path.join(snap, "docs (old)", "blob.bin")),
              "R75: the SKIPPED file does not exist in the copy - «vetted» and «reachable» "
              "are the same set by construction")
        with open(os.path.join(snap, "notes.md"), encoding="utf-8", newline="") as f:
            check(f.read() == parts[0][1],
                  "R75: the copy holds EXACTLY the text the gate scanned, byte for byte")
        refs = o._attach_refs([], pairs, ["docs (old)%sblob.bin (binary)" % os.sep])
        check(snap in refs and aroot not in refs,
              "R75: refs hand out the vetted copy; the ORIGINAL folder path appears "
              "nowhere in the payload")
        check("NOT INCLUDED" in refs and "blob.bin (binary)" in refs,
              "R75: the skip manifest names what the copy does not hold, by relative path")
        refs_str = o._attach_refs([], [aroot], None)
        check(("- FOLDER: %s" % aroot) in refs_str,
              "R75 control: a plain-string folder entry still renders as itself")

        # End to end: a secret-shaped SKIPPED name still refuses the round (belt AND braces
        # - the copy would exclude it, but the operator error stays loud), and the refusal
        # fires through main() with the R75 reordering in place.
        er = os.path.join(d, "attach-bad")
        os.makedirs(er)
        with open(os.path.join(er, "prod.env"), "w", encoding="utf-8") as f:
            f.write("x" * 2_100_001)          # oversized -> skipped -> name gate fires
        bp = os.path.join(d, "brief.md")
        with open(bp, "w", encoding="utf-8") as f:
            f.write("R75 synthetic brief. No vendor is called.")
        real_gate, real_argv = o.pii_gate, sys.argv
        try:
            o.pii_gate = lambda parts, **kw: None
            sys.argv = ["orchestrate.py", "--brief", bp, "--attach-dir", er,
                        "--marker", "R75-DONE", "--dry-run", "--out", os.path.join(d, "o1")]
            with contextlib.redirect_stdout(io.StringIO()):
                rc_bad = o.main()
            sys.argv = ["orchestrate.py", "--brief", bp, "--attach-dir", aroot,
                        "--marker", "R75-DONE", "--dry-run", "--out", os.path.join(d, "o2")]
            with contextlib.redirect_stdout(io.StringIO()):
                rc_ok = o.main()
        finally:
            o.pii_gate, sys.argv = real_gate, real_argv
        check(rc_bad == 3, "R75: a key-shaped skipped name still refuses the round (rc 3)",
              "rc=%r" % rc_bad)
        check(rc_ok == 0, "R75 control: a clean folder passes the same path (rc 0)",
              "rc=%r" % rc_ok)
        check(not os.path.exists(os.path.join(d, "o2", "attach-vetted")),
              "R75: --dry-run materialises NO vetted copy - a dry run writes no payload "
              "artifacts")

        # ---- (c) mid-stream SSE error at zero delivered tokens is retried ------------------
        sse_hits = []

        class _SSESeq(http.server.BaseHTTPRequestHandler):
            scripts = []

            def do_POST(self):
                n = len(sse_hits)
                sse_hits.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = self.scripts[min(n, len(self.scripts) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        _err429 = (b'data: {"error": {"code": 429, "message": "mid-stream limit"}}\n\n'
                   b'data: [DONE]\n\n')
        _err400 = (b'data: {"error": {"code": 400, "message": "bad request"}}\n\n'
                   b'data: [DONE]\n\n')
        _good = (b'data: {"choices": [{"delta": {"content": "Fine.\\nR75-SSE-MARK"}, '
                 b'"finish_reason": "stop"}], "model": "selftest/echo"}\n\n'
                 b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 4}}\n\n'
                 b'data: [DONE]\n\n')
        _err_with_text = (b'data: {"choices": [{"delta": {"content": "partial"}}]}\n\n'
                          b'data: {"error": {"code": 429, "message": "late limit"}}\n\n'
                          b'data: [DONE]\n\n')

        srv = http.server.HTTPServer(("127.0.0.1", 0), _SSESeq)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
        sleeps = []
        real_sleep, real_envkey = o.time.sleep, o._env_key
        o.OAI_PROVIDERS["_r75sse"] = {
            "key_env": "SELFTEST_NOT_A_KEY", "url": url, "depth": "reasoning",
            "search": "plugin", "usage_request": None, "label": "r75 sse loopback",
            "streaming": True,
        }
        try:
            o.time.sleep = lambda s: sleeps.append(s)
            o._env_key = lambda name: "selftest-dummy"

            def _run(tag):
                return o.call_oai_reviewer(
                    BRIEF, "R75-SSE-MARK", os.path.join(d, tag + ".md"),
                    model="selftest/echo", name="_r75sse", provider="_r75sse",
                    fetch_tool={"enabled": False}, max_tokens=200, timeout=30)
            _SSESeq.scripts = [_err429, _good]
            with contextlib.redirect_stdout(io.StringIO()):
                res = _run("sse-a")
            check(res.get("ok") is True and len(sse_hits) == 2 and sleeps == [2],
                  "R75: an SSE 429 event with ZERO delivered tokens is retried once and "
                  "the seat survives",
                  "ok=%r hits=%d sleeps=%r err=%s" % (res.get("ok"), len(sse_hits),
                                                      sleeps, str(res.get("error"))[:80]))
            check((res.get("retries") or 0) >= 1,
                  "R75: the retry is visible in telemetry, not silent")

            sse_hits.clear()
            sleeps.clear()
            _SSESeq.scripts = [_err_with_text, _good]
            with contextlib.redirect_stdout(io.StringIO()):
                _run("sse-b")
            check(len(sse_hits) == 1 and not sleeps,
                  "R75 control: the same error AFTER a content delta is NOT retried - "
                  "delivered tokens mean a re-send could double-bill generation",
                  "hits=%d sleeps=%r" % (len(sse_hits), sleeps))

            sse_hits.clear()
            sleeps.clear()
            _SSESeq.scripts = [_err400, _good]
            with contextlib.redirect_stdout(io.StringIO()):
                _run("sse-c")
            check(len(sse_hits) == 1 and not sleeps,
                  "R75 control: a NON-transient error event (400) is not retried even at "
                  "zero tokens", "hits=%d" % len(sse_hits))
        finally:
            o.time.sleep = real_sleep
            o._env_key = real_envkey
            o.OAI_PROVIDERS.pop("_r75sse", None)
            srv.shutdown()
            srv.server_close()

        # ---- (d) echocheck: --levels order comes from the ladder, not the typing -----------
        # The candidate is DERIVED, not pinned (R74: tests must state the world) - any
        # channel knob_for() would give the supported_efforts ladder to, in either world.
        import routing as _rt
        reg = _rt.load_registry()
        cand = next((c for c, ch in reg["channels"].items()
                     if ch.get("kind") in ("openrouter", "oai")
                     and isinstance(ch.get("reasoning"), dict)
                     and len(ch.get("supported_efforts") or []) >= 2), None)
        check(cand is not None,
              "R75: at least one openrouter/oai channel declares a 2+ rung "
              "supported_efforts ladder")
        if cand:
            plan75 = _rt.resolve(reg, only=[cand])
            check((plan75.get(cand) or {}).get("supported_efforts")
                  == reg["channels"][cand]["supported_efforts"],
                  "R75: the PLAN carries supported_efforts - the third registry field "
                  "found dead at _decorate's allow-list in three rounds (read_order R73, "
                  "fallback_model R74, the R43 ladder now)")
            lad = reg["channels"][cand]["supported_efforts"]     # highest first (R43)
            hi, lo = lad[0], lad[-1]
            real_eargv = sys.argv
            try:
                sys.argv = ["echocheck.py", "--only", cand,
                            "--levels", "%s,%s" % (hi, lo), "--dry-run"]
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = e.main()
                out = buf.getvalue()
            finally:
                sys.argv = real_eargv
            check(rc == 0 and "re-ordered to low-first" in out,
                  "R75: --levels typed high-first is re-ordered by the channel's own "
                  "ladder instead of minting a false INVERTED verdict", out[-200:])
            check(("%r vs %r" % (lo, hi)) in out,
                  "R75: the dry-run plan shows the LOW arm first after re-ordering",
                  out[-200:])

        # run_arm sends the naked probe: --answer-cap 0 is on the child's command line.
        rec = {}
        real_subrun = e.subprocess.run

        def _fake_run(cmd, **kw):
            rec["cmd"] = list(cmd)
            raise e.subprocess.TimeoutExpired(cmd, 1)
        try:
            e.subprocess.run = _fake_run
            res_arm = e.run_arm("x", "max", {"channels": {}}, d, bp, bp, 5)
        finally:
            e.subprocess.run = real_subrun
        cmdl = rec.get("cmd") or []
        check("--answer-cap" in cmdl and cmdl[cmdl.index("--answer-cap") + 1] == "0",
              "R75: echocheck probes run with --answer-cap 0 - the review-mode default "
              "would contaminate the output-token fallback meter", repr(cmdl[-6:]))
        check(res_arm.get("error", "").startswith("timed out"),
              "R75 control: the fake subprocess was actually consulted", repr(res_arm))

        # ---- (e) workdir: pid isolation between processes, sweep of stale mirrors ----------
        wd = os.path.join(d, "кириллица-ws")
        with contextlib.redirect_stdout(io.StringIO()):
            m = o._ascii_safe_workdir(wd, "t", "r75test")
        check(m.endswith("-p%d" % os.getpid()),
              "R75: the mirror name carries THIS pid - two orchestrate processes on one "
              "workdir cannot share a mirror", m)
        root = os.path.dirname(m)
        stale = os.path.join(root, "stale-x")
        fresh = os.path.join(root, "fresh-x")
        os.makedirs(stale, exist_ok=True)
        os.makedirs(fresh, exist_ok=True)
        old = _time.time() - 4 * 86400
        os.utime(stale, (old, old))
        with contextlib.redirect_stdout(io.StringIO()):
            o._ascii_safe_workdir(wd, "t", "r75test")
        check(not os.path.exists(stale),
              "R75: a mirror sibling older than 3 days is swept on the next use")
        check(os.path.exists(fresh),
              "R75 control: a fresh sibling survives the sweep")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def suite_r78_agents_md():
    """R78. AGENTS.md + the CLAUDE.md bridge ship, and commands state their cwd.

    A four-CLI-reviewer panel on the built tree agreed unanimously on one defect: the first
    runnable commands in AGENTS.md assumed the repository root as cwd while every script lives
    in the skill subtree - a cold agent's very first step was a FileNotFoundError. Separately,
    Claude Code reads CLAUDE.md, not AGENTS.md (its memory docs, opened live 2026-08-31,
    recommend a one-line @AGENTS.md import as the bridge; backticked @paths are documented as
    literal text, i.e. a silent no-op). These pins hold the fixed state in the SOURCE layout;
    an installed tree has neither file and skips.
    """
    section("R78. AGENTS.md + CLAUDE.md bridge (source kit/)")
    kit = HERE / "kit"
    agents = kit / "AGENTS.md"
    if not agents.exists():
        check(True, "R78: no source kit/AGENTS.md here - an installed tree, nothing to pin "
              "(the files under test exist only in the source layout)")
        return
    at = agents.read_text(encoding="utf-8")
    check("cd plugins/model-orchestration/skills/model-orchestration" in at,
          "R78: AGENTS.md states the working directory its commands run from - the one "
          "defect all four panel reviewers found independently (bare `python routing.py` "
          "from the repo root is a FileNotFoundError)")
    check("SKILL_DIR" in at and ".../" not in at,
          "R78: the map table resolves paths via SKILL_DIR - no `.../` ellipsis path an "
          "agent's file tools would take literally")
    bridge = kit / "claude-md"
    check(bridge.exists(), "R78: the CLAUDE.md bridge source file kit/claude-md exists")
    if bridge.exists():
        bt = bridge.read_text(encoding="utf-8")
        check("@AGENTS.md" in [ln.strip() for ln in bt.splitlines()],
              "R78: the bridge holds the one-line @AGENTS.md import on its own line")
        check("`@AGENTS.md`" not in bt,
              "R78: the import is not wrapped in backticks - backticked, it is literal "
              "text and the bridge silently imports nothing")
    pkg = HERE / "package.py"
    if pkg.exists():
        ps = pkg.read_text(encoding="utf-8")
        check('"claude-md": "CLAUDE.md"' in ps,
              "R78: KIT_RENAME maps claude-md -> CLAUDE.md (stored under a neutral name so "
              "the author's own sessions do not discover a live nested CLAUDE.md, same "
              "reason as gitignore)")
        check(not (kit / "CLAUDE.md").exists(),
              "R78 control: no live CLAUDE.md sits in the source kit/ directory itself")


def main():
    global _quiet
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    _quiet = a.quiet

    # 🔴🔴 THE SELF-TEST MUST NOT READ THE USER'S OWN SETTINGS, and this was found the hard way:
    # run inside a freshly upgraded install whose owner had enabled one channel, FIVE routing
    # checks went red against completely correct code. They derive their expected channel sets
    # from `channels.json` on disk, while `load_registry` now merges the overlay on top - so the
    # suite silently tested "the shipped registry plus whatever this person configured", and every
    # kit user with a settings file would have seen a red self-test.
    #
    # Third instance of one class in a single round: an expectation that quietly depends on which
    # WORLD it is evaluated in (the two tier checks pinned to a value a human is meant to change;
    # the distribution check that only held in one of the two trees; this). The fix is the same
    # every time - state the world, do not inherit it. Pointing the variable at a path that cannot
    # exist is deliberate: unsetting it would fall back to the real `~/.claude` file.
    sys.path.insert(0, HERE)
    import routing as _r
    os.environ[_r.OVERLAY_ENV] = os.path.join(
        tempfile.gettempdir(), "orch_selftest_no_overlay_on_purpose.json")

    for suite in (suite_degradation, suite_routing, suite_redaction,
                  suite_prose_matches_behaviour, suite_contract,
                  suite_citations, suite_dispatch, suite_tiers_and_grounding,
                  suite_settings_and_upgrade, suite_echocheck, suite_dev_tooling,
                  suite_agy_plan_class, suite_spend_guard, suite_panels,
                  suite_max_depth_and_explicit_only, suite_refs_and_meters,
                  suite_r47_causes, suite_dedup_scripts, suite_r48_visibility,
                  suite_r49_record_integrity, suite_r55_child_env_and_first_error,
                  suite_r56_agy_concurrency_and_permissions,
                  suite_r57_agy_capability_model,
                  suite_r58_update_check,
                  suite_r59_grokbuild_cyrillic_and_recitation,
                  suite_r60_shipped_docs_and_kit_exclusion,
                  suite_r70_transport_retry_and_timeout,
                  suite_r71_bom_payload_and_gate_signature,
                  suite_r72_reading_protocol,
                  suite_r74_panel_fixes,
                  suite_r75_backlog,
                  suite_r78_agents_md):
        try:
            suite()
        except Exception as exc:                       # a broken suite is itself a failure
            check(False, f"{suite.__name__} raised", repr(exc)[:120])

    failed = [r for r in _results if not r[0]]
    print("\n" + "=" * 78)
    print("%d/%d checks passed" % (len(_results) - len(failed), len(_results)))
    if failed:
        print("\nFAILURES:")
        for _, name, detail in failed:
            print("  - %s %s" % (name, detail))
        print("\nHand this output, plus reviews/diagnostics.json from a real run, to an AI "
              "assistant and ask it to diagnose and fix the cause.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
