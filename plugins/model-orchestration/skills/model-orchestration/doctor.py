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
    # Controls: an ordinary legal citation, plus the two prose shapes that sit closest to a secret
    # without being one. A gate that cries wolf here trains you to wave the real alarm through.
    clean = ("Rule 2026-14539, 91 FR 45324, effective 2026-09-18. See 8 CFR 212.22(a)(3).\n"
             "Use Bearer authentication rather than a query parameter.\n"
             "The API key is read from the environment and is never printed.\n")
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
        r.fail("skill size", "; ".join(bits),
               "over budget: after an auto-compaction the re-attached copy is CLIPPED at the "
               "limit and the tail silently disappears while the file on disk still looks whole. "
               "Move a section into references/ and leave a pointer - a pointer announces itself, "
               "a clipped body does not.")
    elif est > TOKEN_BUDGET * 0.9 or lines > LINE_GUIDE:
        r.warn("skill size", "; ".join(bits),
               "close to the cliff. The next edit may cross it; move a section to references/.")
    else:
        r.ok("skill size", "; ".join(bits))


def main():
    ap = argparse.ArgumentParser(description="Check that a review round can actually run here.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = Report()
    check_python(r)
    have = check_files(r)
    check_compile(r)
    check_registry(r)
    check_skill_size(r)
    check_key(r)

    mod = None
    if have:
        try:
            mod = _load_orchestrate()
        except Exception as e:
            r.fail("harness import", repr(e), "orchestrate.py could not be imported")
    if mod:
        check_cli(r, mod, "codex", mod.codex_bin, ["--version"])
        check_cli(r, mod, "agy", mod.agy_bin, ["--version"])
        check_agy_permissions(r, mod)
        check_pii_gate(r, mod)

    if a.json:
        print(json.dumps({"worst": r.worst, "checks": r.rows}, ensure_ascii=False, indent=1))
        return min(r.worst, 1)

    tag = {0: "[ ok ]", 1: "[warn]", 2: "[FAIL]"}
    print("model-orchestration doctor")
    print("  skill dir: %s" % HERE)
    print("-" * 78)
    for row in r.rows:
        print("%s %-16s %s" % (tag[row["level"]], row["check"], row["detail"]))
        if row["fix"]:
            for line in _wrap(row["fix"], 68):
                print("       -> " + line)
    print("-" * 78)
    dead = [row["check"].split()[0] for row in r.rows
            if row["level"] and row["check"].split()[0] in ("codex", "agy", "spark")]
    if r.worst == 0:
        print("READY. All three channels can run.")
    elif r.worst == 1 and dead:
        print("USABLE WITH GAPS. These channels cannot run: %s - exclude them with --skip, or "
              "fix the arrows above." % ", ".join(sorted(set(dead))))
    elif r.worst == 1:
        print("READY, with housekeeping warnings above. All three channels can run.")
    else:
        print("NOT READY. Fix the [FAIL] lines above first.")
    print('  python "%s" --brief BRIEF.md --marker DONE-01 --out reviews --dry-run'
          % os.path.join(HERE, "orchestrate.py"))
    return min(r.worst, 1)


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
