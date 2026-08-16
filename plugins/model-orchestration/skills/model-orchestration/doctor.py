#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
doctor.py - answer "is this machine able to run a review round?" in about two seconds.

    python doctor.py            human-readable
    python doctor.py --json     machine-readable, for scripts and installers

WHY THIS EXISTS
---------------
SKILL.md used to assert versions: "Codex CLI v0.144.6", "Antigravity CLI v1.1.7". Both were
wrong within five days (0.146.0 and 1.1.9), and a stale version in prose does not look stale -
it looks like documentation. Anything that can change between two runs belongs in a probe, not
in a sentence. So this file replaces every "verified value" table the docs used to carry.

It is also the first thing a new machine runs. Every check says what is broken AND what to do
about it, because an installer that reports "FAIL" without a next step just moves the problem.

Standard library only. Never prints a secret: for the API key it reports presence and length.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))

# Documented in code.claude.com/docs/en/skills and verified 2026-07-31: an auto-compaction
# re-attaches the most recent invocation of each skill keeping the FIRST 5,000 TOKENS of each,
# inside a 25,000-token budget shared across skills and filled most-recent-first. A SKILL.md over
# the per-skill limit is silently truncated in the re-attached copy - the file on disk is fine,
# which is exactly why nobody notices. Same page: description+when_to_use is cut at 1,536
# characters in the skill listing, and "keep SKILL.md under 500 lines".
TOKEN_BUDGET = 5000
DESC_LIMIT = 1536
LINE_GUIDE = 500
BYTES_PER_TOKEN = 4.0     # the ratio this project has been measuring with; an estimate, not a spec


class Report(object):
    def __init__(self):
        self.rows = []
        self.worst = 0

    def add(self, level, name, detail, fix=None):
        # 0 ok, 1 warn, 2 fail
        self.rows.append({"level": level, "check": name, "detail": detail, "fix": fix})
        self.worst = max(self.worst, level)

    def ok(self, n, d):
        self.add(0, n, d)

    def warn(self, n, d, fix=None):
        self.add(1, n, d, fix)

    def fail(self, n, d, fix=None):
        self.add(2, n, d, fix)


def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return (p.stdout or "").strip() or (p.stderr or "").strip()
    except Exception as e:
        return "ERROR: %r" % (e,)


def _load_orchestrate():
    """Import the harness by path so we can reuse its own binary resolution and its PII gate."""
    spec = importlib.util.spec_from_file_location("_orch", os.path.join(HERE, "orchestrate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_python(r):
    v = sys.version_info
    if v < (3, 8):
        r.fail("python", "%d.%d.%d" % v[:3], "the harness needs Python 3.8+; install a newer one")
    else:
        r.ok("python", "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable))


def check_files(r):
    need = ["orchestrate.py", "routing.py", "channels.json", "SKILL.md"]
    optional = ["citecheck.py", "patch_agy_permissions.py"]
    missing = [f for f in need if not os.path.isfile(os.path.join(HERE, f))]
    if missing:
        r.fail("files", "missing: " + ", ".join(missing),
               "re-install the skill; these are not optional")
        return False
    gone = [f for f in optional if not os.path.isfile(os.path.join(HERE, f))]
    for sub in ("systems", "references"):
        d = os.path.join(HERE, sub)
        if not os.path.isdir(d) or not os.listdir(d):
            gone.append(sub + "/")
    if gone:
        r.warn("files", "present, but missing: " + ", ".join(gone),
               "the harness runs without these; verification and presets will be degraded")
    else:
        r.ok("files", "all present in " + HERE)
    return True


def check_compile(r):
    bad = []
    for f in ("orchestrate.py", "routing.py"):
        p = os.path.join(HERE, f)
        if not os.path.isfile(p):
            continue
        out = _run([sys.executable, "-m", "py_compile", p])
        if out:
            bad.append("%s: %s" % (f, out.splitlines()[-1][:120]))
    if bad:
        r.fail("compile", "; ".join(bad), "the harness will not start; fix the syntax error")
    else:
        r.ok("compile", "orchestrate.py and routing.py compile")


def check_registry(r):
    try:
        sys.path.insert(0, HERE)
        import routing
        reg = routing.load_registry()
    except Exception as e:
        r.fail("registry", repr(e),
               "channels.json is unreadable or has a colliding alias - every --route would be a "
               "coin flip, which is why loading refuses rather than guessing")
        return
    chans = ", ".join("%s=%s" % (c, v.get("model")) for c, v in reg["channels"].items())
    r.ok("registry", "%d channels: %s" % (len(reg["channels"]), chans))

    # Your settings, and whether they are somewhere an update can destroy.
    ov = reg.get("_overlay") or {}
    if ov.get("present"):
        n = len(ov.get("applied", []))
        detail = "; ".join("%s.%s=%r" % (c, f, v) for c, f, _b, v in ov.get("applied", [])) or \
                 "present, changes nothing"
        if ov.get("added"):
            detail += "; adds channel(s): " + ", ".join(ov["added"])
        r.ok("your settings", "%s  (%d override(s): %s)" % (ov["path"], n, detail))
        if ov.get("trust") == routing.OVERLAY_TRUST_REDIRECTED:
            r.warn("settings provenance",
                   "that path came from %s, not from your home directory" % routing.OVERLAY_ENV,
                   "a project's own .claude/settings.json can set an environment variable, so a "
                   "repository you cloned could be choosing this file. Transport fields are "
                   "refused from it. If you set the variable yourself, that is fine; if you did "
                   "not, look at where it came from. Home path: %s" % routing.overlay_home_path())
    else:
        r.ok("your settings", "none yet - %s does not exist. Put channel changes THERE, not in "
                              "channels.json: nothing that updates this skill can reach it"
                              % ov.get("path", "the local settings file"))


def check_registry_pristine(r):
    """
    Has the shipped registry been edited in place, and WHICH fields? That edit dies at the update.

    The reference copy ships with a built kit and is deliberately absent from the author's working
    copy, which is edited every session - a check that always fails on the maintainer's machine is
    one the maintainer teaches themselves to ignore, and then it is not a check.

    1.7.0 shipped a sha256 here and could answer only yes/no. Naming the fields is what lets
    `upgrade.py` carry ALL of them instead of guessing `enabled`, and what lets a user see whether
    the thing they are about to lose matters.
    """
    sys.path.insert(0, HERE)
    import routing
    drift = routing.registry_drift()
    if drift is None:
        return          # source tree: no reference copy was shipped, and none should be
    if drift.get("error"):
        r.warn("registry edits", "could not be checked (%s)" % drift["error"], "harmless; skip it")
        return
    if drift["pristine"]:
        r.ok("registry edits", "channels.json is exactly what this version shipped")
        return
    shown = "; ".join("%s.%s=%r" % (c, f, a) for c, f, _b, a in drift["changed"][:6])
    if len(drift["changed"]) > 6:
        shown += "; +%d more" % (len(drift["changed"]) - 6)
    r.warn("registry edits",
           "channels.json has %d hand-edited field(s): %s" % (len(drift["changed"]), shown),
           "those changes live inside the skill folder, and the next update replaces the whole "
           "folder - so they will be lost without a word. Move them out with:  python "
           "%s --migrate" % os.path.join(HERE, "upgrade.py"))


def check_key(r):
    key = os.environ.get("MODEL_API_KEY")
    where = "process env"
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                key = winreg.QueryValueEx(reg, "MODEL_API_KEY")[0]
                where = "HKCU\\Environment"
        except OSError:
            pass
    if key:
        # Length only. The value must never reach a console, a log, or a model's context.
        r.ok("spark key", "MODEL_API_KEY present in %s, length %d" % (where, len(key)))
    else:
        r.warn("spark key", "MODEL_API_KEY not set",
               "the Spark/HTTPS channel cannot run. Set it, or always pass --skip spark. "
               "This key is per-person and metered - do not borrow somebody else's.")


def check_cli(r, mod, name, resolver, version_args):
    b = resolver()
    if not (os.path.isfile(b) or shutil.which(b)):
        r.warn("%s binary" % name, "not found (looked for %r)" % b,
               "install it, or set %s_BIN, or always exclude the channel with --skip %s"
               % (name.upper(), name))
        return None
    ver = _run([b] + version_args).splitlines()[0][:80] if True else ""
    r.ok("%s binary" % name, "%s -> %s" % (b, ver or "(version not reported)"))
    return b


def check_codex_sandbox(r, mod, binary):
    """Can codex's sandbox SPAWN A SHELL - the thing it will actually try to do?

    🔴🔴 THE OLD PREFLIGHT ASKED THE BINARY FOR ITS VERSION AND CALLED THAT "ok". On 2026-08-05
    codex answered `--version` perfectly and its shell tool was 100 % dead: every command the model
    issued came back `CreateProcessAsUserW failed: 5`, because PowerShell 7 was installed from the
    Microsoft Store and a WindowsApps package cannot be launched under a lowered token. The run
    burned ~50 minutes and produced nothing, and the doctor would have said READY throughout.

    🔴 The prose preflight in the skill was `codex sandbox cmd /c echo SANDBOX_OK` - and it PASSES
    on a machine in exactly this state, because `cmd.exe` spawns fine and the real run uses `pwsh`.
    A probe that does not travel the path the real call travels measures nothing. So this tests the
    shell codex will actually reach for, through the same PATH the harness hands it.
    """
    if os.name != "nt":
        r.ok("codex sandbox", "not Windows - the WindowsApps spawn failure cannot apply")
        return
    shell_dir = mod.sandbox_shell_dir()
    env = mod._codex_env() or os.environ
    exe = shutil.which("pwsh", path=env.get("PATH")) or shutil.which("pwsh")
    if not exe:
        r.warn("codex sandbox", "no pwsh found at all",
               "codex falls back to another shell; if reviews come back empty, install "
               "PowerShell 7 outside WindowsApps and re-run this check")
        return
    # 🔴 THE SENTINEL IS ASSEMBLED BY THE SHELL, NEVER WRITTEN IN THE COMMAND. The first version of
    # this check ran `-Command "echo SANDBOX_OK"` and looked for SANDBOX_OK in the output - and it
    # PASSED on the broken Store shell, because the sandbox's failure message quotes the command
    # line back at you, and the command line contains the sentinel. The probe was reading its own
    # input as the program's answer. Caught by a negative control, which is the only thing that
    # could have caught it: every positive run agreed with it.
    say = "[string]::Join('_','SANDBOX','OK')"
    try:
        p = subprocess.run([binary, "sandbox", exe, "-NoProfile", "-Command", say],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, env=env)
        out = (p.stdout or "") + (p.stderr or "")
    except Exception as e:                                                # noqa: BLE001
        r.warn("codex sandbox", "probe failed: %r" % (e,), "run the command by hand to see why")
        return
    if p.returncode == 0 and "SANDBOX_OK" in out:
        r.ok("codex sandbox", "spawns %s%s"
             % (exe, "" if not shell_dir else "  (via %s, ahead of any Store build)" % shell_dir))
        return
    store = "windowsapps" in exe.lower() or "WindowsApps" in out
    r.warn("codex sandbox",
           "cannot spawn %s - %s" % (exe, out.strip().splitlines()[0][:150] if out.strip() else
                                     "no output"),
           "PowerShell 7 from the Microsoft Store cannot be launched under codex's sandbox "
           "(CreateProcessAsUserW error 5). `winget install Microsoft.PowerShell` will NOT fix it "
           "- its default installer for that id is the same msix. Install the MSI, or unzip "
           "PowerShell-*-win-x64.zip into ~/pwsh7 (no admin, nothing machine-wide changes), then "
           "re-run. Codex's shell tool is dead until then and reviews will burn the full timeout."
           if store else
           "codex's shell tool cannot run commands; reviews will burn the full timeout")


def check_agy_permissions(r, mod):
    problem = mod.agy_permission_preflight()
    if problem:
        r.warn("agy permissions", problem.split(" - ")[0][:160],
               "run: python patch_agy_permissions.py    (backs up, idempotent, --revert exists). "
               "Until then a single auto-denied tool discards the whole run and returns an empty "
               "answer with status SUCCESS and exit code 0.")
    else:
        r.ok("agy permissions", "allow-rules present and firecrawl_crawl denied")


def check_pii_gate(r, mod):
    """
    A safety net nobody tests is a safety net nobody has. THREE of these patterns were wrong on
    first write, and every one was found by running the gate, never by reading it:
      - a leading \\b that can never match before '(' -> phone numbers sailed through;
      - a leading \\b that can never match after '_'  -> .env lines sailed through;
      - `bearer` as a labelled-assignment alternative, which demands ':' AFTER the label, while a
        real header is `Authorization: Bearer <token>` and puts the delimiter before it.

    That last one mattered most and was found last, because coverage here was lopsided: the PII
    class - the one a human can wave through with --allow-pii - had six detectors under test,
    while the SECRET class, which has no override at all, had exactly one. The probe below now
    covers every pattern in both sets, derived from the tables themselves so a newly added
    pattern fails this check until it is given a probe line.

    The secret-shaped literals are deliberately non-functional example values (the AWS one is
    AWS's own published EXAMPLE key). Nothing real is embedded in this file.
    """
    probe = ("Applicant A-123456789 receipt MSC2190123456 ssn 123-45-6789\n"
             "mail a@b.co phone (415) 555-0142 date of birth: 1988-04-12\n"
             "passport number: X1234567\n"
             "FIRECRAWL_API_KEY=fc-1234567890abcdefghij\n"
             "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6\n"
             "-----BEGIN RSA PRIVATE KEY-----\n"
             "anthropic sk-ant-" + "a" * 40 + "\n"
             "openai sk-" + "b" * 40 + "\n"
             "aws AKIAIOSFODNN7EXAMPLE\n"
             "github ghp_" + "c" * 36 + "\n"
             "slack xoxb-1234567890-abcdefghij\n"
             "google AIza" + "d" * 35 + "\n")
    secrets, pii = mod.scan_payload(probe, "selftest")
    kinds = {h.split(" at ")[0] for h in secrets + pii}
    expect = {k for k, _ in mod.SECRET_PATTERNS} | {k for k, _ in mod.PII_PATTERNS}
    missed = expect - kinds
    # Controls: an ordinary legal citation, plus the prose shapes that sit closest to a secret or
    # an identifier without being one. A gate that cries wolf here trains you to wave the real
    # alarm through. The last two lines are verbatim from this project's own documentation and
    # both used to FAIL: the DOB and passport patterns accepted any character after the label, so
    # describing the gate tripped the gate. Keep them - they are the regression test for that.
    clean = ("Rule 2026-14539, 91 FR 45324, effective 2026-09-18. See 8 CFR 212.22(a)(3).\n"
             "Use Bearer authentication rather than a query parameter.\n"
             "The API key is read from the environment and is never printed.\n"
             "blocks one containing SSNs, emails, phones or a labelled date of birth unless you\n"
             "pass --allow-pii, and the same applies to a passport number mentioned in prose.\n")
    fp = sum(len(x) for x in mod.scan_payload(clean, "selftest"))
    if missed:
        r.fail("secret/pii gate", "did not detect: " + ", ".join(sorted(missed)),
               "a pattern regressed. That class would be sent to three vendors unblocked.")
    elif fp:
        r.warn("secret/pii gate", "false positive on ordinary prose or a legal citation",
               "the gate will block clean briefs and train you to pass --allow-pii by reflex")
    else:
        r.ok("secret/pii gate",
             "%d detectors live (%d secret, %d pii), 0 false positives on the controls"
             % (len(kinds), len(mod.SECRET_PATTERNS), len(mod.PII_PATTERNS)))


def check_skill_size(r):
    """
    The check that keeps this skill inside the re-attach budget. This project got the number
    wrong once by quoting it from memory instead of the docs, so the constants carry their
    source at the top of this file.
    """
    p = os.path.join(HERE, "SKILL.md")
    if not os.path.isfile(p):
        return
    text = open(p, encoding="utf-8").read()
    lines = len(text.splitlines())
    est = int(len(text) / BYTES_PER_TOKEN)
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    desc = ""
    if m:
        d = re.search(r"description:\s*>?-?\s*\n?(.*?)(?=\n[a-z_]+:\s|\Z)", m.group(1), re.S)
        if d:
            desc = " ".join(d.group(1).split())
    bits = ["~%d tokens (budget %d)" % (est, TOKEN_BUDGET), "%d lines" % lines,
            "description %d chars (cap %d)" % (len(desc), DESC_LIMIT)]
    if est > TOKEN_BUDGET or len(desc) > DESC_LIMIT:
        # 🔴 THIS WAS A `fail`, AND IT MADE A CORRECT FRESH INSTALL PRINT "NOT READY". Measured
        # 2026-08-08 by installing the 1.7.0 build into an empty home: everything worked, every
        # channel could run, and the last line told the user the tool was broken - over a
        # maintainer's problem they cannot fix and that stops nothing from running. That is the
        # same defect this file was written to catch elsewhere: a status line that contradicts
        # the state it just measured. It stays loud, and it stays a MAINTAINER'S check - selftest
        # fails on it, because selftest is the tool the maintainer and CI run.
        r.warn("skill size", "; ".join(bits),
               "over budget: after an auto-compaction the re-attached copy is CLIPPED at the "
               "limit and the tail silently disappears while the file on disk still looks whole. "
               "Nothing here stops working - the assistant just sees less of the manual. Move a "
               "section into references/ and leave a pointer; a pointer announces itself, a "
               "clipped body does not.")
    elif est > TOKEN_BUDGET * 0.9 or lines > LINE_GUIDE:
        r.warn("skill size", "; ".join(bits),
               "close to the cliff. The next edit may cross it; move a section to references/.")
    else:
        r.ok("skill size", "; ".join(bits))


def check_effort_ladders_live(r):
    """Compare each channel's declared `supported_efforts` against the VENDOR's live catalogue.

    🔴🔴 THIS EXISTS BECAUSE THE SELF-TEST'S LADDER CHECK IS A TAUTOLOGY, and reviewers of round
    43 said so independently. That check asserts `reasoning.effort == supported_efforts[0]` — and
    BOTH values live in `channels.json`, so it proves the file agrees with itself and can never
    notice a vendor adding a rung above the one we copied. That is exactly the failure the whole
    max-depth change exists to prevent, reproduced inside the instrument built to prevent it —
    the same shape as the decorative `channels.spark.model` and the telemetry keyed on dead
    names. The self-test must run offline, so the live half belongs HERE.

    Off unless `--online`: doctor is what a person runs when something is already wrong, and it
    must not need a network or a key to answer the other twenty questions.
    """
    import urllib.request
    key = os.environ.get("OPENROUTER_API_KEY")
    try:
        with open(os.path.join(HERE, "channels.json"), encoding="utf-8") as fh:
            chans = json.load(fh)["channels"]
    except Exception as e:                                  # noqa: BLE001
        r.warn("effort ladders", "registry unreadable (%r)" % e, "fix channels.json first")
        return
    want = {n: c for n, c in chans.items()
            if c.get("kind") == "openrouter" and c.get("supported_efforts")}
    if not want:
        r.ok("effort ladders", "no channel declares supported_efforts - nothing to compare")
        return
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                     headers={"Authorization": "Bearer " + (key or "")})
        with urllib.request.urlopen(req, timeout=60) as resp:
            live = {m["id"]: m for m in json.loads(resp.read().decode("utf-8"))["data"]}
    except Exception as e:                                  # noqa: BLE001
        r.warn("effort ladders", "catalogue not reachable (%r)" % e,
               "harmless offline; re-run with a network to compare the declared ladders")
        return
    drift = []
    for name, c in want.items():
        m = live.get(c["model"])
        if not m:
            drift.append("%s: %s is no longer in the catalogue" % (name, c["model"]))
            continue
        vendor = ((m.get("reasoning") or {}).get("supported_efforts")) or []
        if vendor and list(vendor) != list(c["supported_efforts"]):
            drift.append("%s: vendor says %s, we declare %s"
                         % (name, vendor, c["supported_efforts"]))
        elif vendor and (c.get("reasoning") or {}).get("effort") != vendor[0]:
            drift.append("%s: vendor's top rung is %r, we send %r"
                         % (name, vendor[0], (c.get("reasoning") or {}).get("effort")))
    if drift:
        r.warn("effort ladders", "; ".join(drift),
               "the vendor moved. Update supported_efforts AND reasoning.effort in channels.json "
               "- the self-test only checks those two agree with EACH OTHER, never with the "
               "vendor, so it stays green while the depth is a rung short.")
    else:
        r.ok("effort ladders", "%d channel(s) match the vendor catalogue exactly" % len(want))


def main():
    ap = argparse.ArgumentParser(description="Check that a review round can actually run here.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--online", action="store_true",
                    help="also compare each channel's declared effort ladder against the "
                         "vendor's LIVE catalogue. Needs a network and OPENROUTER_API_KEY.")
    a = ap.parse_args()

    r = Report()
    check_python(r)
    have = check_files(r)
    check_compile(r)
    check_registry(r)
    check_registry_pristine(r)
    check_skill_size(r)
    check_key(r)
    if a.online:
        check_effort_ladders_live(r)

    mod = None
    if have:
        try:
            mod = _load_orchestrate()
        except Exception as e:
            r.fail("harness import", repr(e), "orchestrate.py could not be imported")
    if mod:
        codex_b = check_cli(r, mod, "codex", mod.codex_bin, ["--version"])
        check_cli(r, mod, "agy", mod.agy_bin, ["--version"])
        # A version string is not a capability. See check_codex_sandbox.
        if codex_b:
            check_codex_sandbox(r, mod, codex_b)
        check_agy_permissions(r, mod)
        check_pii_gate(r, mod)

    if a.json:
        print(json.dumps({"worst": r.worst, "checks": r.rows}, ensure_ascii=False, indent=1))
        return min(r.worst, 1)

    tag = {0: "[ ok ]", 1: "[warn]", 2: "[FAIL]"}
    print("model-orchestration doctor")
    print("  skill dir: %s" % HERE)
    # 🔴 UNTIL 1.7.0 NOTHING HERE CARRIED A VERSION. The only version string that shipped was in
    # plugin.json, which sits outside the folder the installer and the manual instructions copy -
    # so "am I on the latest?" was unanswerable on any non-plugin install, and every upgrade began
    # by guessing. Read from a file rather than a constant so a half-copied tree cannot claim to
    # be a release it is not.
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as vf:
            print("  version  : %s" % (vf.read().strip() or "empty VERSION file"))
    except OSError:
        print("  version  : not stamped (a working copy, or an install older than 1.7.0)")
    print("-" * 78)
    for row in r.rows:
        print("%s %-16s %s" % (tag[row["level"]], row["check"], row["detail"]))
        if row["fix"]:
            for line in _wrap(row["fix"], 68):
                print("       -> " + line)
    print("-" * 78)
    dead = [row["check"].split()[0] for row in r.rows
            if row["level"] and row["check"].split()[0] in ("codex", "agy", "spark")]
    # COUNTED FROM THE REGISTRY, never written as a word. This line said "All three channels can
    # run" while seven were configured - a status line asserting a number that another file owns,
    # which is the same defect this doctor exists to catch elsewhere. Degrades to "every channel"
    # rather than guessing if the registry cannot be read, because a wrong number is worse than none.
    try:
        import routing
        n = len([c for c, ch in routing.load_registry()["channels"].items()
                 if ch.get("enabled", True)])
        howmany = "All %d configured channels" % n
    except Exception:
        howmany = "Every configured channel"
    if r.worst == 0:
        print("READY. %s can run." % howmany)
    elif r.worst == 1 and dead:
        print("USABLE WITH GAPS. These channels cannot run: %s - exclude them with --skip, or "
              "fix the arrows above." % ", ".join(sorted(set(dead))))
    elif r.worst == 1:
        print("READY, with housekeeping warnings above. %s can run." % howmany)
    else:
        print("NOT READY. Fix the [FAIL] lines above first.")
    print('  python "%s" --brief BRIEF.md --marker DONE-01 --out reviews --dry-run'
          % os.path.join(HERE, "orchestrate.py"))
    # Exit code semantics, corrected 2026-07-31. This used to be min(worst, 1), which printed
    # "READY" and then exited 1 - a status line contradicting its own exit code, which is the
    # thing this project keeps saying is worse than no status line at all. It also made a partial
    # install a failure, contradicting the design: running a subset of channels is a SUPPORTED
    # state, not a fault, and a doctor that fails on it teaches people to ignore the doctor.
    #
    #   0  runnable - everything present, or only housekeeping warnings, or some channels absent
    #   1  NOT runnable - a [FAIL] line: a missing file, a script that will not compile, a
    #      registry that will not load. Something no --skip can work around.
    return 0 if r.worst < 2 else 1


def _wrap(s, width):
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    sys.exit(main())
