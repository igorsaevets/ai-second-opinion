#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
update_check.py — notice when a newer version of ai-second-opinion has been released.

Two modes, one file:

  --check          Full check, used by doctor.py. Talks to GitHub. Prints a formatted notice
                   if an update is available. Runs when the user explicitly asks (doctor.py).

  --hook           LOCAL ONLY, used by the plugin's SessionStart hook. Compares the CURRENT
                   VERSION file to what was stamped last time. Fires exactly once after the
                   marketplace auto-updates the plugin. NO NETWORK. Preserves INSTALL.md's
                   "nothing phones home" promise on the plugin path.

Why the split — measured by the R58 second-opinion panel, 9 channels:
* GitHub issue anthropics/claude-code#16538 (verified 2026-08-20): plugin SessionStart hooks
  DO NOT surface `hookSpecificOutput.additionalContext` to Claude. The workaround the issue
  documents is to add the same hook to ~/.claude/settings.json — that path is available here
  via --install-hook.
* Since Claude Code's marketplace already fetches new plugin files on its own schedule, a
  Method-1 install has a NEW `VERSION` file the moment auto-update runs. Comparing the current
  file to a stamp is strictly better than a second GitHub poll: no privacy trade-off, no rate
  limit, no network stall on air-gapped machines, no dependency on #16538 being fixed.
* GitHub's /releases/latest returns whichever release object was last CREATED, which is not
  the same as "highest tag" — this repo's own Releases object list stopped at v1.27.0 while
  tags climbed to v1.33.1 (measured live 2026-08-20). The full check uses /tags and picks the
  max version-tuple, so it does not depend on the release-creation habit being clean.
* Prior art followed: oh-my-zsh's `mode reminder|auto|disabled` + `frequency` (SPARK12CONT);
  npm `update-notifier`'s deferred-and-emit-after pattern (MIMO25PRO); pip's atomic stamp write
  and fail-open on corrupt state (GROKBUILD).

Uses only the standard library. Never fails loud on a network error.

    python update_check.py                       # respects the stamp, prints if there is news
    python update_check.py --force               # ignore the stamp and re-check now
    python update_check.py --hook                # SessionStart mode (local delta only)
    python update_check.py --refresh-background  # spawned by orchestrate.py, writes stamp only
    python update_check.py --snooze              # decline nag for --snooze-days days
    python update_check.py --snooze-days N       # decline for N days (default 7)
    python update_check.py --show-what-would-be-sent   # audit the outbound request
    python update_check.py --install-hook        # add SessionStart entry to ~/.claude/settings.json
    python update_check.py --uninstall-hook      # remove it
"""

import argparse
import datetime
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))

# Registry. Overrideable by env var so a fork rebuilds without editing this file.
GITHUB_OWNER = os.environ.get("MODEL_ORCH_UPDATE_OWNER", "igorsaevets")
GITHUB_REPO = os.environ.get("MODEL_ORCH_UPDATE_REPO", "ai-second-opinion")
# /tags rather than /releases/latest: measured 2026-08-20, this repo's Releases stop at
# v1.27.0 while tags continue to v1.33.1. Tags is authoritative.
# per_page=100 (R74; agy36flash, R73): this repo already carries more than 30 tags, and the
# GitHub /tags ordering is not semver - relying on the newest landing in an unsorted first 30
# is a drift trap. 100 is the API maximum; real pagination is not worth a stdlib page-walker
# for a repo that gains ~1 tag a day.
GITHUB_TAGS_URL = "https://api.github.com/repos/%s/%s/tags?per_page=100" % (
    GITHUB_OWNER, GITHUB_REPO)

# Stamp OUTSIDE the plugin tree — an upgrade must not lose the snooze.
STAMP_PATH = os.path.join(os.path.expanduser("~"), ".claude",
                          "model-orchestration.update-check.json")

# Env vars we honour.
DISABLE_ENV = "MODEL_ORCH_UPDATE_CHECK"           # set to 0/no/off/false
NO_UPDATE_NOTIFIER_ENV = "NO_UPDATE_NOTIFIER"     # ecosystem convention (npm, gh, others)
CI_ENV = "CI"                                     # every CI system sets this to a truthy value
INTERVAL_HOURS_ENV = "MODEL_ORCH_UPDATE_CHECK_INTERVAL_HOURS"

# Weekly, matched to the release cadence (~1/week). Daily is 7× more phones-home for no
# information (panel: GROK420, MIMO25PRO).
DEFAULT_INTERVAL_HOURS = 168
DEFAULT_SNOOZE_DAYS = 7
NETWORK_TIMEOUT_SECONDS = 3.0

# Exponential backoff on network failure: 1h, 2h, 4h, 8h, 16h, capped at INTERVAL. On a truly
# offline machine every session pays the timeout budget with no benefit (panel: SPARK12CONT).
BACKOFF_HOURS = [1, 2, 4, 8, 16]

BANNER_HEAD = "[ai-second-opinion]"


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso_now():
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_truthy(s):
    return (s or "").strip().lower() in ("1", "true", "yes", "on")


def _is_falsy(s):
    return (s or "").strip().lower() in ("0", "false", "no", "off")


def is_check_disabled():
    if _is_falsy(os.environ.get(DISABLE_ENV)):
        return True
    if os.environ.get(NO_UPDATE_NOTIFIER_ENV):
        return True
    if _is_truthy(os.environ.get(CI_ENV)):
        return True
    return False


def interval_seconds():
    v = os.environ.get(INTERVAL_HOURS_ENV)
    try:
        h = max(1, int(v))
        return h * 3600
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS * 3600


def read_local_version():
    """Installed version, from the VERSION file next to this script, or None."""
    p = os.path.join(HERE, "VERSION")
    try:
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        return v or None
    except OSError:
        return None


def read_local_changelog_section(latest_version):
    """First ~15 lines of the CHANGELOG section for `latest_version`, from the local plugin
    tree if the marketplace shipped it. Returns "" if not found. Best-effort — no throws."""
    # CHANGELOG.md lives at the repo root, but the marketplace only ships the plugin subtree.
    # `package.py` was extended in R58 to copy CHANGELOG.md into the skill folder so the hook
    # can show what changed after an auto-update.
    p = os.path.join(HERE, "CHANGELOG.md")
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    v = (latest_version or "").lstrip("vV").strip()
    if not v:
        return ""
    # Section header format used by this project: "## 1.34.0 — YYYY-MM-DD"
    lines = text.splitlines()
    out, capturing = [], False
    for ln in lines:
        if ln.startswith("## "):
            if capturing:
                break
            head = ln[3:].strip()
            if head.startswith(v + " ") or head == v:
                capturing = True
                out.append(ln)
                continue
        elif capturing:
            out.append(ln)
    if not out:
        return ""
    # Cap: 15 non-blank lines is enough to say what changed and stay well under Claude Code's
    # 10 KB systemMessage limit (panel: SPARK12CONT quoted the cap).
    take, kept = [], 0
    for ln in out[1:]:  # skip the "## X.Y.Z" header itself
        take.append(ln)
        if ln.strip():
            kept += 1
        if kept >= 15:
            break
    return "\n".join(take).rstrip()


def read_stamp():
    try:
        with open(STAMP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_stamp(stamp):
    """Atomic write via os.replace(). Same-dir tempfile is required for atomicity on Windows.
    Panel (all channels): concurrent session starts race on this file."""
    try:
        d = os.path.dirname(STAMP_PATH)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".update-check.", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(stamp, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, STAMP_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass  # never fail on inability to write


def _backoff_seconds(consecutive_failures):
    """Exponential backoff so an air-gapped machine does not eat 3s every session."""
    idx = max(0, min(consecutive_failures - 1, len(BACKOFF_HOURS) - 1))
    return BACKOFF_HOURS[idx] * 3600


def stamp_is_fresh(stamp, now=None):
    now = now or _now_utc()
    last = _parse_iso((stamp or {}).get("last_check_utc"))
    if not last:
        return False
    # Clock skew: last_check in the future ⇒ system clock jumped back; treat as stale, do not
    # wait for it. (Panel: SPARK12CONT.)
    if last > now:
        return False
    fail_count = int((stamp or {}).get("consecutive_failures") or 0)
    window = _backoff_seconds(fail_count) if fail_count else interval_seconds()
    return (now - last).total_seconds() < window


def snooze_active(stamp, now=None):
    now = now or _now_utc()
    until = _parse_iso((stamp or {}).get("snoozed_until_utc"))
    if not until:
        return False
    if until < now - datetime.timedelta(days=365):
        # A snooze that expired a year ago is a lost stamp; ignore.
        return False
    return now < until


def _ver_tuple(s):
    """(1, 33, 1) from '1.33.1' or 'v1.33.1'; None for non-plain-release strings."""
    s = (s or "").lstrip("vV").strip()
    parts = s.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(remote, local):
    """True if remote > local (numeric tuple compare, so 1.10.0 > 1.9.0). Equal ⇒ NOT newer."""
    rt, lt = _ver_tuple(remote), _ver_tuple(local)
    if rt is None or lt is None:
        return False
    return rt > lt


def _user_agent():
    """No installed version in the UA. With a small user base, version+IP+time is a
    fingerprint. Panel: 4 of 6 said drop. Adoption telemetry can be added opt-in later."""
    return "ai-second-opinion-update-check"


def pick_latest_tag(tags):
    """From /tags JSON, return the tag name with the highest version tuple, or None."""
    best = None
    for t in tags or []:
        name = (t or {}).get("name") or ""
        vt = _ver_tuple(name)
        if vt is None:
            continue
        if best is None or vt > best[0]:
            best = (vt, name)
    return best[1] if best else None


def fetch_latest_tag(stamp, timeout=NETWORK_TIMEOUT_SECONDS):
    """Return (tag_name, updated_stamp) or (None, stamp) on any failure. Uses If-None-Match
    to save rate-limit budget when there is no change."""
    headers = {"User-Agent": _user_agent(), "Accept": "application/vnd.github+json"}
    etag = (stamp or {}).get("tags_etag")
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(GITHUB_TAGS_URL, headers=headers)
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            tags = json.loads(body.decode("utf-8"))
            latest = pick_latest_tag(tags)
            new_etag = resp.headers.get("ETag")
            if new_etag:
                stamp["tags_etag"] = new_etag
            if latest:
                stamp["tags_latest"] = latest
            return (latest, stamp)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return ((stamp or {}).get("tags_latest"), stamp)
        return (None, stamp)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return (None, stamp)


def check_agy_stale():
    """True iff agy is installed AND patch_agy_permissions.py --check exits non-zero."""
    p = os.path.join(HERE, "patch_agy_permissions.py")
    if not os.path.isfile(p):
        return False
    try:
        rc = subprocess.call([sys.executable, p, "--check"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=5)
        return rc != 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _is_git_install():
    """A Method-4 install runs from an unpacked git tree; detect and return the repo root."""
    p = HERE
    for _ in range(6):
        if os.path.isdir(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return None


def _remediation_lines():
    lines = []
    git_root = _is_git_install()
    if git_root:
        lines.append("     Git clone: cd \"%s\" && git pull" % git_root)
    lines.append("     Plugin:    /plugin update model-orchestration@review-channels")
    lines.append("     Installer: re-run install.ps1 / install.sh from the fresh download")
    lines.append("     Manual:    pull the repo and copy the folder over, then run doctor.py")
    return lines


def format_full_message(local, latest, agy_stale, changelog_excerpt=""):
    published = _iso_now()[:10]
    parts = [
        "%s update available: %s -> %s (as of %s)" % (BANNER_HEAD, local, latest, published),
    ]
    if changelog_excerpt:
        parts.append("")
        parts.append(changelog_excerpt)
        parts.append("")
    parts.append("To update:")
    parts.extend(_remediation_lines())
    parts.append("Skip for %d days: python \"%s\" --snooze" %
                 (DEFAULT_SNOOZE_DAYS, os.path.abspath(__file__)))
    parts.append("Disable checks entirely: set %s=0 (or NO_UPDATE_NOTIFIER=1)" % DISABLE_ENV)
    msg = "\n".join(parts)
    if agy_stale:
        msg += ("\n\n%s post-install action pending: python patch_agy_permissions.py"
                % BANNER_HEAD)
        msg += ("\n(Applies the agy channel's permission rules. "
                "Skip only if you never use agy.)")
    return msg


def format_local_delta_message(local, previous_installed):
    """The hook message. Short, factual, points at doctor for detail."""
    parts = [
        "%s auto-updated: %s -> %s" % (BANNER_HEAD, previous_installed, local),
    ]
    changelog = read_local_changelog_section(local)
    if changelog:
        parts.append("")
        parts.append(changelog)
        parts.append("")
    parts.append("Run doctor.py for the full check:")
    parts.append("  python \"%s\"" % os.path.join(HERE, "doctor.py"))
    return "\n".join(parts)


def do_check(force=False):
    """Full check. Returns (action, payload).
    Actions: disabled, no-version, fresh, no-net, up-to-date, snoozed, agy-only, update."""
    if is_check_disabled():
        return ("disabled", None)
    local = read_local_version()
    if not local:
        return ("no-version", None)
    stamp = read_stamp()
    # A fresh stamp is only trustworthy for the version it was written against (R74;
    # goog37flash, R73): after an upgrade the cached pending_message still told the user to
    # update to the version they were already running, for up to the whole freshness window.
    # A missing installed_version (old stamps) also falls through to one real check.
    if (not force and stamp_is_fresh(stamp)
            and stamp.get("installed_version") == local):
        if stamp.get("pending_message"):
            return ("cached", stamp)
        return ("fresh", stamp)
    latest, stamp = fetch_latest_tag(stamp)
    if latest is None:
        stamp["consecutive_failures"] = int((stamp or {}).get("consecutive_failures") or 0) + 1
        stamp["last_error"] = "network"
        stamp["last_check_utc"] = _iso_now()
        write_stamp(stamp)
        return ("no-net", stamp)
    agy_stale = check_agy_stale()
    stamp["last_check_utc"] = _iso_now()
    stamp["consecutive_failures"] = 0
    stamp["last_error"] = None
    stamp["installed_version"] = local
    stamp["latest_seen"] = latest
    if not is_newer(latest, local):
        stamp["pending_message"] = None
        write_stamp(stamp)
        if agy_stale:
            return ("agy-only", {"local": local, "agy_stale": True})
        return ("up-to-date", {"local": local, "latest": latest})
    if snooze_active(stamp):
        stamp["pending_message"] = None
        write_stamp(stamp)
        return ("snoozed", {"local": local, "latest": latest,
                            "until": stamp.get("snoozed_until_utc")})
    changelog = read_local_changelog_section(latest)  # empty if we're behind the local copy
    msg = format_full_message(local, latest, agy_stale, changelog)
    stamp["pending_message"] = msg
    write_stamp(stamp)
    return ("update", {"local": local, "latest": latest, "message": msg,
                       "agy_stale": agy_stale})


def do_local_delta():
    """LOCAL-ONLY. Compares HERE/VERSION to stamp['installed_version']. NO network.
    Fires exactly once after the marketplace auto-updates the plugin. Returns (action, msg)."""
    if is_check_disabled():
        return ("disabled", None)
    local = read_local_version()
    if not local:
        return ("no-version", None)
    stamp = read_stamp()
    prev = stamp.get("installed_version") or local
    if not is_newer(local, prev):
        # First-ever run: seed the stamp so we know the baseline. Never fires a notice on the
        # first run: we do not know if the user just installed or has been on this version.
        if not stamp.get("installed_version"):
            stamp["installed_version"] = local
            write_stamp(stamp)
        return ("no-change", None)
    # Auto-updated since last session: tell the user, and update the stamp so we do not repeat.
    msg = format_local_delta_message(local, prev)
    stamp["installed_version"] = local
    stamp["last_local_notice_utc"] = _iso_now()
    stamp["pending_message"] = None  # supersedes any older pending nag
    write_stamp(stamp)
    return ("local-update", msg)


# ------------------------------------------------------------------- commands

def cmd_check(args):
    action, payload = do_check(force=args.force)
    if action == "disabled":
        if args.verbose:
            print("%s update check disabled" % BANNER_HEAD)
    elif action == "no-version":
        if args.verbose:
            print("%s no VERSION file next to %s" % (BANNER_HEAD, __file__))
    elif action == "no-net":
        if args.verbose:
            print("%s network check failed; retrying with backoff" % BANNER_HEAD)
    elif action == "fresh":
        pass
    elif action == "up-to-date":
        if args.verbose:
            print("%s up to date (%s)" % (BANNER_HEAD, payload["local"]))
    elif action == "snoozed":
        if args.verbose:
            print("%s snoozed until %s" % (BANNER_HEAD, payload.get("until", "?")))
    elif action == "agy-only":
        print("%s post-install action pending: python patch_agy_permissions.py"
              % BANNER_HEAD)
    elif action in ("update", "cached"):
        pm = (payload or {}).get("pending_message") or (payload or {}).get("message")
        if pm:
            print(pm)
    return 0


def cmd_hook(args):
    """SessionStart hook mode. Local-only: no network. Emits both `systemMessage` (10 KB cap,
    user-visible per hooks docs) and `additionalContext` (model-visible, but broken by
    anthropics/claude-code#16538 for plugin hooks — kept as belt-and-braces for the day it is
    fixed)."""
    action, msg = do_local_delta()
    if not msg:
        return 0
    capped = msg[:9000]  # keep well under the 10 KB cap
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": capped,
        },
        "systemMessage": capped,
    }))
    return 0


def cmd_refresh_background(args):
    """Spawned by orchestrate.py preflight. Refreshes the stamp without printing."""
    do_check(force=True)
    return 0


def cmd_snooze(args):
    stamp = read_stamp()
    days = args.snooze_days or DEFAULT_SNOOZE_DAYS
    until = _now_utc() + datetime.timedelta(days=days)
    stamp["snoozed_until_utc"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp["pending_message"] = None
    write_stamp(stamp)
    print("%s snoozed for %d days (until %s)" %
          (BANNER_HEAD, days, stamp["snoozed_until_utc"]))
    return 0


def cmd_show(args):
    """Show what would be sent on a full --check. --show-what-would-be-sent."""
    print("Full check (doctor.py / --check) sends:")
    print("  URL:     GET %s" % GITHUB_TAGS_URL)
    print("  Headers:")
    print("    User-Agent: %s" % _user_agent())
    print("    Accept:     application/vnd.github+json")
    stamp = read_stamp()
    if stamp.get("tags_etag"):
        print("    If-None-Match: %s   (saves rate limit — 304 no body)"
              % stamp.get("tags_etag"))
    print()
    print("  What GitHub sees: your IP, the URL and headers above, and the time.")
    print("  The installed version is deliberately NOT in the User-Agent.")
    print()
    print("Hook mode (--hook, used by the plugin's SessionStart hook) is LOCAL-ONLY.")
    print("It does not talk to any network at all; it reads two files on disk.")
    print()
    print("Disable everything: set %s=0 (or NO_UPDATE_NOTIFIER=1, or CI=1)."
          % DISABLE_ENV)
    return 0


def cmd_install_hook(args):
    """Add SessionStart entry to ~/.claude/settings.json — the workaround the #16538 issue
    itself documents for plugin hooks not surfacing additionalContext. This gives Method-2/3
    users a proactive check even though they never installed the plugin."""
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    except OSError:
        settings = {}                     # no file yet - a fresh install starts one
    except ValueError as exc:
        # 🔴 R74 (goog36flash, R73): this used to fall through to `settings = {}` and WRITE
        # that back - one malformed byte in settings.json and installing a hook silently
        # replaced the user's entire Claude Code configuration with just the hook. A parse
        # failure on an EXISTING file is the user's config being unreadable, not absent.
        print("%s REFUSING: %s exists but is not valid JSON (%s). Fix the file first - "
              "installing would have overwritten it wholesale."
              % (BANNER_HEAD, settings_path, exc))
        return 1
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    my_path = os.path.abspath(__file__)
    # 🔴 One COMMAND STRING, not command+args (R74; orgemini37flash, R73): Claude Code's hook
    # schema has no `args` field, so the old shape ran a bare `python` that sat waiting on
    # stdin until the timeout killed it - every session start, for every user who installed
    # this. `--hook` rather than `--check`: SessionStart wants the local-only JSON emitter;
    # the network check already runs from orchestrate's own preflight.
    my_cmd = 'python "%s" --hook' % my_path
    entry = {
        "matcher": "startup",
        "hooks": [{"type": "command", "command": my_cmd, "timeout": 5}],
    }

    def _is_mine(h):
        if not isinstance(h, dict):
            return False
        if isinstance(h.get("args"), list):        # the legacy broken shape
            return my_path in " ".join(str(x) for x in h["args"])
        return my_path in str(h.get("command") or "")

    migrated = False
    for e in session_start:
        hl = e.get("hooks") or []
        inner = [h for h in hl if not (_is_mine(h) and h.get("command") != my_cmd)]
        if len(inner) != len(hl):
            e["hooks"] = inner
            migrated = True
    session_start[:] = [e for e in session_start if e.get("hooks")]
    already = any(any((h or {}).get("command") == my_cmd for h in (e.get("hooks") or []))
                  for e in session_start)
    if already and not migrated:
        print("%s SessionStart hook already installed in %s"
              % (BANNER_HEAD, settings_path))
        return 0
    if not already:
        session_start.append(entry)
    if migrated:
        print("%s migrating the old command+args hook shape (it ran a bare `python` and "
              "hung until its timeout)" % BANNER_HEAD)
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, settings_path)
    print("%s SessionStart hook installed in %s" % (BANNER_HEAD, settings_path))
    print("  Remove: python \"%s\" --uninstall-hook" % os.path.abspath(__file__))
    return 0


def cmd_uninstall_hook(args):
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        print("%s no settings.json at %s — nothing to remove"
              % (BANNER_HEAD, settings_path))
        return 0
    hooks = settings.get("hooks") or {}
    session_start = hooks.get("SessionStart") or []
    my_path = os.path.abspath(__file__)

    def _is_mine(h):
        if not isinstance(h, dict):
            return False
        if isinstance(h.get("args"), list):        # the legacy command+args shape
            return my_path in " ".join(str(x) for x in h["args"])
        return my_path in str(h.get("command") or "")

    # 🔴 Count removed HOOKS, not only emptied entries (R74; orgemini37flash, R73): when our
    # hook shared a SessionStart entry with another tool's, the old counter stayed 0, the
    # early return fired, and the filtered settings were never written - an uninstall that
    # reported failure while silently doing nothing.
    keep, removed = [], 0
    for e in session_start:
        hl = e.get("hooks") or []
        inner = [h for h in hl if not _is_mine(h)]
        removed += len(hl) - len(inner)
        if inner:
            e["hooks"] = inner
            keep.append(e)
    if not removed:
        print("%s no matching SessionStart hook found in %s"
              % (BANNER_HEAD, settings_path))
        return 0
    if keep:
        hooks["SessionStart"] = keep
    else:
        hooks.pop("SessionStart", None)
    if not hooks:
        settings.pop("hooks", None)
    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, settings_path)
    print("%s removed %d SessionStart hook(s) from %s"
          % (BANNER_HEAD, removed, settings_path))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="full check, respecting the stamp (default)")
    ap.add_argument("--hook", action="store_true",
                    help="LOCAL-only mode for the plugin SessionStart hook — no network")
    ap.add_argument("--force", action="store_true",
                    help="ignore the stamp and check now")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print all outcomes, not just news")
    ap.add_argument("--refresh-background", action="store_true",
                    help="spawned by orchestrate.py, refreshes stamp only")
    ap.add_argument("--snooze", action="store_true",
                    help="decline nag for --snooze-days days")
    ap.add_argument("--snooze-days", type=int, default=None,
                    help="days to snooze (default: %d)" % DEFAULT_SNOOZE_DAYS)
    ap.add_argument("--show-what-would-be-sent", action="store_true", dest="show",
                    help="audit the outbound request without making it")
    ap.add_argument("--install-hook", action="store_true",
                    help="add a SessionStart entry to ~/.claude/settings.json (works around #16538)")
    ap.add_argument("--uninstall-hook", action="store_true",
                    help="remove the SessionStart entry")
    a = ap.parse_args()
    if a.hook:
        return cmd_hook(a)
    if a.refresh_background:
        return cmd_refresh_background(a)
    if a.snooze:
        return cmd_snooze(a)
    if a.show:
        return cmd_show(a)
    if a.install_hook:
        return cmd_install_hook(a)
    if a.uninstall_hook:
        return cmd_uninstall_hook(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
