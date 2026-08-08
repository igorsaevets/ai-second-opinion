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
    bad = []
    for c, v in sorted(_CHANS.items()):
        d = v.get("distribution", "both")
        on = v.get("enabled", True)
        if d not in ("both", "local", "kit"):
            bad.append("%s: unknown distribution %r" % (c, d))
        elif d == "local" and not on:
            bad.append("%s: distribution=local but enabled=false here" % c)
        elif d == "kit" and on:
            bad.append("%s: distribution=kit but enabled=true here (it would run locally)" % c)
    check(not bad, "every channel's `distribution` agrees with its local `enabled`",
          "; ".join(bad))

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
    def group_of(word):
        """Which channels does a human word expand to? Answered by the registry, never by memory."""
        for g, v in _groups_raw.items():
            if word == g or word in (v.get("aliases") or []):
                return GROUPS[g]
        raise AssertionError("no group answers to %r - this test names a word the registry lost"
                             % word)

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
        # 🔴 THE TIER MUST REACH THE CALL, NOT ONLY THE PRINTOUT. Compared arg-for-arg between a
        # strategic and a deep dispatch of the same registry: a knob that resolves and prints but
        # never reaches the function is the defect class this repository has now recorded seven
        # times (`channels.spark.model`, the four dispatch literals, the telemetry keyed on old
        # names, `tools` on goog36flash, the renamed flag that missed its own reporter, ...).
        deep = {r["name"]: r for r in data.get("deep") or []}
        for r in launched:
            d = deep.get(r["name"])
            if not d:
                continue
            if (r.get("fetch_tool") or {}).get("enabled"):
                check((d.get("fetch_tool") or {}).get("max_calls")
                      == (r["fetch_tool"].get("max_calls") or 8) * 2,
                      "deep's doubled fetch budget REACHES the %s call" % r["name"],
                      "%s -> %s" % (r["fetch_tool"].get("max_calls"),
                                    (d.get("fetch_tool") or {}).get("max_calls")))
            if (r.get("reasoning") or {}).get("max_tokens"):
                check((d.get("reasoning") or {}).get("max_tokens")
                      == r["reasoning"]["max_tokens"] * 2,
                      "deep's doubled reasoning ceiling REACHES the %s call" % r["name"],
                      "%s -> %s" % (r["reasoning"]["max_tokens"],
                                    (d.get("reasoning") or {}).get("max_tokens")))
            if r["kind"] == "gemini":
                check(r.get("thinking_level") == "medium"
                      and d.get("thinking_level") == "high",
                      "the tier's thinking_level REACHES the %s call" % r["name"],
                      "strategic=%s deep=%s" % (r.get("thinking_level"),
                                                d.get("thinking_level")))
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
            expected = {n for n in st if n not in ("agy31pro", "agy36flash", "ghost")}
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

    for c in live:
        ft_s = (strat[c].get("fetch_tool") or {})
        ft_d = (deep[c].get("fetch_tool") or {})
        if ft_s.get("enabled"):
            check((ft_d.get("max_calls") or 0) == (ft_s.get("max_calls") or 8) * 2,
                  "deep doubles the page-fetch budget on %s" % c,
                  "%s -> %s" % (ft_s.get("max_calls"), ft_d.get("max_calls")))
    g = [c for c in live if strat[c].get("kind") == "gemini"]
    for c in g:
        check(strat[c].get("thinking_level") == "medium"
              and deep[c].get("thinking_level") == "high",
              "the Gemini depth knob moves with the tier on %s" % c,
              "strategic=%s deep=%s" % (strat[c].get("thinking_level"),
                                        deep[c].get("thinking_level")))
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

    # --- the codex hint is wired, and reaches the prompt -----------------------------------
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


def main():
    global _quiet
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    _quiet = a.quiet

    for suite in (suite_degradation, suite_routing, suite_redaction, suite_contract,
                  suite_citations, suite_dispatch, suite_tiers_and_grounding):
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
