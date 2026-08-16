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
    ALL = {k for k, v in _CHANS.items() if v.get("enabled", True)}
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
    OPT_IN = {"orgpt56terrapro": "rationed: ~$1.80/review, strategic questions only"}
    for c, why in sorted(OPT_IN.items()):
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
                return GROUPS[g] & ALL
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
    _off = sorted(EXISTS - ALL)
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
        # 🔴 OPT-IN CHANNELS, round 38. Naming a default-OFF channel in prose must SELECT it -
        # until 2026-08-14 the route's only-branch could only turn things off, so "только 5.6
        # terra" removed the other twelve and left the named one disabled: "running 0 channel(s):
        # NONE". The --only FLAG was always right, so the two selection paths disagreed and the
        # prose one silently did nothing. Derived from the registry, not hard-coded to a count.
        (["--route", "только 5.6 terra"], {"orgpt56terrapro"},
         "route: только 5.6 terra (names an OFF-by-default channel)"),
        (["--route", "только терра-про"], {"orgpt56terrapro"},
         "route: только терра-про (RU alias of an OFF-by-default channel)"),
        # ADD mode: default set PLUS the named channel. Igor's rule is that «используй все
        # модели» must NOT pull in the rationed channel while «и ещё 5.6 Terra Pro» must.
        (["--route", "используй все модели и ещё 5.6 Terra Pro"], ALL | {"orgpt56terrapro"},
         "route: ADD keeps the default set and adds the opt-in one"),
        (["--route", "добавь терра-про"], ALL | {"orgpt56terrapro"},
         "route: добавь <opt-in channel>"),
        # The negative half of the same rule: no additive marker => the default set, unchanged.
        (["--route", "не используй gemini"], without(*group_of("gemini")),
         "route: a plain negation still leaves the opt-in channel OFF"),
        (["--route", "кроме gemini"], without(*group_of("gemini")), "route: кроме gemini (GROUP)"),
        (["--route", "не используй spark"], without(*GROUPS["spark"]),
         "route: RU negation of a GROUP"),
        (["--route", "only codex"], {"codex"}, "route: EN only"),
        ([], ALL, "no flags: every enabled channel runs"),
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
    roots = [here / "kit"] + list(here.parents)
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
        # 🔴 THE STUBS MUST REPLACE SOMETHING THAT EXISTS. Found while renaming
        # call_openrouter_reviewer -> call_oai_reviewer on 2026-08-08: `o.old_name = stub(...)`
        # does not fail on a name the module no longer has, it CREATES it. The dispatcher then
        # calls the real function, this suite makes real paid calls against live vendors, and
        # every check still passes. A test whose isolation can evaporate silently is worse than
        # no test. Asserted BEFORE assignment would need a different structure; asserted here it
        # still fires on the next rename, which is what matters.
        "for _n in ('call_http_reviewer','call_codex','call_agy','call_oai_reviewer',\n"
        "           'call_xai_responses','call_gemini_direct','call_hermes'):\n"
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
        "en = sorted(c for c, ch in reg['channels'].items() if ch.get('enabled', True))\n"
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
        # 🔴 ONLY CHANNELS THAT ACTUALLY RUN. `web.enabled` is a property of the channel; "the
        # setting reached the call" is a property of a LAUNCH, and a channel that is disabled here
        # never makes one. Before the local/kit split every channel ran, so the two sets were the
        # same and the difference was invisible; on 2026-08-08 three kit-only channels turned this
        # into three red checks against working code. That is the same mistake as asserting group
        # membership against the enabled set, twenty lines up, and it is worth noticing that BOTH
        # were written by assuming "every channel in the registry runs here" - an assumption no
        # line of code stated and that stopped being true in one edit.
        webbed = [c for c, ch in json.loads(
            Path(HERE, "channels.json").read_text(encoding="utf-8"))["channels"].items()
            if not c.startswith("_") and ch.get("enabled", True)
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
        pinned = [c for c, ch in json.loads(
            Path(HERE, "channels.json").read_text(encoding="utf-8"))["channels"].items()
            if not c.startswith("_") and ch.get("enabled", True)
            and ch.get("provider_route")]
        for c in pinned:
            row = next((r for r in launched if r["name"] == c), None)
            check(bool(row and row.get("provider_route")),
                  "provider_route from the registry REACHED the %s call" % c,
                  "registry=%s got=%s" % (
                      json.loads(Path(HERE, "channels.json").read_text(encoding="utf-8"))
                      ["channels"][c].get("provider_route"),
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
        "    o.call_grokcli = ok\n"
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
    for name in ("spark12cont", "ornemotron3ultra"):
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
    free = o._free_extras("spark12cont")
    declared = [c for c, ch in pristine["channels"].items()
                if not c.startswith("_") and ch.get("enabled", True) and ch.get("cost") == "free"]
    check(sorted(free) == sorted(x for x in declared if x != "spark12cont"),
          "--ask's free channels come from the registry, not a list in the code",
          "derived=%s declared=%s" % (free, declared))
    check(o._free_extras("nosuchchannel__") is not None,
          "the free-channel lookup never raises - a lookup must not die over its extras")

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

    # Placement is load-bearing (measured 2/2 in the brief vs 0/1 in the persona alone), and
    # --mode default vs plan was the A/B that root-caused the class. Assert the SOURCE, so a
    # refactor that drops either one goes red here instead of resurfacing in a paid round.
    src = inspect.getsource(o._agy_once)
    check("AGY_ENV_CONSTRAINT" in src,
          "the env constraint is appended to the BRIEF in _agy_once")
    check('"--mode", "default"' in src, '--mode is "default" (the plan-mode A/B, 2026-08-14)')

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

    def run(responses, spend_guard=None, raise_at=None):
        n = [0]

        def fake_urlopen(req, timeout=None):
            i = n[0]
            n[0] += 1
            if raise_at is not None and i == raise_at:
                raise urllib.error.HTTPError(
                    "u", 403, "Forbidden", {},
                    io.BytesIO(b'{"error":{"message":"Key limit exceeded (monthly limit)."}}'))
            if "tools" not in _json.loads(req.data.decode()):
                return FakeResp(answer_round(0.50))    # tools removed = the answer is demanded
            return FakeResp(responses[min(i, len(responses) - 1)])

        real_open, real_fetch, real_log = (urllib.request.urlopen, o._safe_fetch_url, o.log)
        urllib.request.urlopen = fake_urlopen
        o._safe_fetch_url = lambda url: "PAGE TEXT " * 50
        o.log = lambda *a, **k: None
        try:
            return o.call_oai_reviewer("brief", "END-01", None, model="test/model",
                                       name="probe",
                                       fetch_tool={"enabled": True, "max_calls": 8},
                                       spend_guard=spend_guard), n[0]
        finally:
            urllib.request.urlopen, o._safe_fetch_url, o.log = real_open, real_fetch, real_log

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
    zero, _ = run([fetch_round(0.10) for _ in range(4)] + [answer_round(0.05)],
                  spend_guard={"max_usd_per_review": 0})
    check(zero.get("spend_stopped") is True,
          "max_usd_per_review: 0 ARMS the guard (it is not read as absent)",
          "usd=%s" % zero.get("usd"))
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
            ("EMPTY OUTPUT from stream - status=completed, output items=['reasoning', "
             "'web_search_call'], reasoning_tokens=10276 of 10279 output tokens. A high reasoning "
             "share with no message item means the turn ended inside the agentic loop",
             "reasoned past its budget and never answered")):
        c3, f3 = o.diagnose(text)
        check(c3 is not None and f3 is not None, "diagnosed: %s" % label,
              (c3 or "NO MATCH")[:60])
    c4, _ = o.diagnose("the model answered normally and cited three sources")
    check(c4 is None, "CONTROL: an ordinary success line is not diagnosed as a failure")

    # The registry is the one home for the policy; the code must not carry a second copy.
    reg = _json.load(open(os.path.join(HERE, "channels.json"), encoding="utf-8"))
    guarded = {c: ch for c, ch in reg["channels"].items()
               if not c.startswith("_") and (ch.get("spend_guard") or {}).get("requires_ack")}
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
    # 🔴 IGOR'S OWN SENTENCE, VERBATIM. It was a hard ROUTE ERROR until R43 because «включая» was
    # not an instruction word - so the one phrasing named in the instruction that authorises this
    # channel was the one phrasing that could not authorise it.
    check(set(expl) <= enabled(route="Запусти все, включая Terra pro"),
          "«Запусти все, включая Terra pro» starts it (Igor's own authorising sentence)")
    check(not (set(expl) & enabled(route="запусти все")),
          "«запусти все» does NOT start it - «все» is the standard panel, not everything")
    # 🔴🔴 THE INVERSE SENTENCE, ONE WORD APART, AND IT USED TO DO THE OPPOSITE OF WHAT IT SAYS.
    # Found by a reviewer in the round that introduced «включая»: the ADD word matched INSIDE
    # «не включая», so the user's «не» became decoration and the round ran the most expensive
    # channel in the registry. Every selection marker is now checked for a preceding negation.
    # The negated forms are asserted for EVERY mode word, not just the one that was broken.
    for neg in ("Запусти все, не включая Terra pro", "запусти все, кроме terra",
                "запусти все без terra", "не используй terra",
                "запусти все, не включая terra", "все, но не terra"):
        check(not (set(expl) & enabled(route=neg)),
              "a NEGATED mention does not start it: %r" % neg)
    # And naming by MODEL id must reach the channel through the FLAG path too, not only prose.
    for word in ("terra pro", "5.6 terra", "openai/gpt-5.6-terra-pro"):
        check(set(expl) & enabled(only=[word]),
              "--only %r resolves through a MODEL alias to the channel" % word)


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
        "agy31pro", "agy36flash", "agy37flash",
        "goog36flash", "goog37flash", "orgemini36flash", "orgemini37flash",
        "mimo25pro", "ormimo25pro", "ornemotron3ultra", "spark12cont",
    }
    # 🔴 THE DICTATED SET IS AN ANCHOR AND MUST NEVER SHRINK SILENTLY; growth is a SEPARATE,
    # NAMED list. Equating the two was right while the roster was frozen, and wrong the first
    # time a channel was added - it turned "did anyone quietly drop one of Igor's channels",
    # which is the question worth asking, into "has the roster changed at all", which goes red
    # on every legitimate addition and trains the next person to edit the expected value until
    # it matches. Splitting them keeps the alarm on the half that matters: a name leaving
    # DICTATED_CHEAP is still a hard failure, while an addition costs one deliberate line here
    # that has to say WHY the channel is cheap.
    ADDED_TO_CHEAP_SINCE = {
        # R44, 2026-08-16. Both belong by the criterion the panel names - cost - and both were
        # added at Igor's request in the same message that asked for the models.
        # NOTE grokbuild carries `enabled: false`: panel membership and enablement are different
        # questions, and it is filed here as cheap so that turning it on later needs no second
        # decision about which room it belongs in.
        "grokbuild":  "subscription CLI, free at the margin exactly like the agy seats",
        "orglm52":    "$0.308/M in, cheaper than ordeepseekv4pro which he did name as cheap",
    }
    actual_cheap = {c for c, v in CH.items() if v.get("panel") == "cheap"}
    check(not (DICTATED_CHEAP - actual_cheap),
          "every channel Igor named as cheap is STILL cheap",
          "dropped=%s" % sorted(DICTATED_CHEAP - actual_cheap))
    check(actual_cheap == DICTATED_CHEAP | set(ADDED_TO_CHEAP_SINCE),
          "the cheap panel is what Igor dictated plus the additions recorded here",
          "unrecorded=%s" % sorted(actual_cheap - DICTATED_CHEAP - set(ADDED_TO_CHEAP_SINCE)))
    check(all(ADDED_TO_CHEAP_SINCE.values()),
          "every later addition to the cheap panel states why it is cheap")
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
    check("--panel cheap would DROP" in dflt,
          "the DEFAULT plan offers the cheaper panel and names what it costs")
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
                  suite_max_depth_and_explicit_only):
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
