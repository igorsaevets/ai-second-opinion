#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
update_check.py — the kit's own update cycle: notice a new release, say so, apply it.

Four jobs, one file, standard library only:

  --status  One VERDICT (OK / FAIL) for "will this install keep itself current?": install kind,
            version vs the newest release, automatic updates on/off and what blocks them, the
            session-start hook. Each FAIL line names the command that fixes it (R136).
  --auto-update on|off   Automatic updates. A plugin install: Claude Code's own auto-update for
            this marketplace (`autoUpdate` on its settings entry - what /plugin's toggle writes).
            A script / manual install: the session-start hook applies a release by itself.
            `--apply` and upgrade.py switch it on unless you switched it off before (R136).
  --check   Ask GitHub once a day whether a newer release exists (stamped; ETag; 3 s timeout;
            silent on any network error). doctor.py runs it at the end of every doctor run and
            orchestrate.py at the end of every real (non --dry-run) round.
  --hook    SessionStart hook mode. Prints JSON for Claude Code: a one-time note after the
            installed VERSION changed (the local delta), plus the same weekly check as above,
            so the assistant AND the user are told at session start that a release is waiting
            and which one command applies it. The plugin ships this hook; --install-hook adds
            it for a script / manual install.
  --apply   Self-update with ONE command. Resolves the newest release tag through the GitHub
            API, downloads the archive pinned to that tag's commit, verifies it (one top-level
            folder, the skill subtree present, VERSION inside == the tag, every required file,
            no path escapes the folder), then hands the copy to the NEW release's upgrade.py:
            backup to <folder>.bak.<timestamp>, your settings carried into the overlay file,
            doctor at the end. A Claude Code plugin install is updated through Claude Code
            itself (`claude plugin marketplace update` + `claude plugin update`); a git checkout
            and the development tree are refused with the right command printed instead.
  --snooze / --show-what-would-be-sent / --install-hook / --uninstall-hook   housekeeping.

Why the kit carries its own cycle (R86, measured 2026-09-11 on claude 2.1.268 against the real
GitHub marketplace, in an isolated CLAUDE_CONFIG_DIR):
* Claude Code auto-updates plugins ONLY for marketplaces with auto-update enabled, and
  «Third-party and local development marketplaces have auto-update disabled by default»
  (code.claude.com/docs/en/discover-plugins). A stale plugin stayed stale across four sessions
  and `-p --maintenance` until a manual `claude plugin update`. The CLI has no "newer
  available" signal either (`claude plugin list --available` printed `available: []`).
* Script and manual installs never had a network check except doctor.py by hand, and the
  `--refresh-background` mode that used to live here was documented as «spawned by
  orchestrate.py preflight» and had zero callers. Gone; the check is in-process now.
* GitHub's /releases/latest returns whichever release object was last CREATED, not the highest
  tag (this repo's Releases once stopped at v1.27.0 while tags reached v1.33.1). /tags is
  authoritative; the release object is read only for its notes, by tag name.

Privacy, stated plainly: one GET to api.github.com per week per machine (tags list), one more
for the release notes when a newer tag exists, and the archive download only when YOU run
--apply. The User-Agent carries no version. Kill switches: MODEL_ORCH_UPDATE_CHECK=0,
NO_UPDATE_NOTIFIER=1, CI=1. `--show-what-would-be-sent` prints the exact requests.

Prior art followed: pip's weekly self-check with an atomic stamp and fail-open on corrupt
state; npm update-notifier's "notice now, apply on request"; gh's NO_UPDATE_NOTIFIER; uv/deno
style "download the release archive, verify, swap" for the apply step.

    python update_check.py                       # respects the stamp, prints if there is news
    python update_check.py --force               # ignore the stamp and re-check now
    python update_check.py --hook                # SessionStart mode (JSON for Claude Code)
    python update_check.py --apply               # update THIS install to the newest release
    python update_check.py --apply --dry-run     # download + verify + upgrade.py --dry-run
    python update_check.py --apply --tag v1.62.0 # a specific release (older needs --force)
    python update_check.py --snooze              # decline the notice for --snooze-days days
    python update_check.py --show-what-would-be-sent
    python update_check.py --install-hook        # SessionStart entry in ~/.claude/settings.json
    python update_check.py --uninstall-hook
"""

import argparse
import contextlib
import datetime
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request
import zipfile

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
# The release OBJECT is read by TAG NAME, only for its notes (its body is the CHANGELOG
# section since the Releases backfill tool exists). Never used to decide what "latest" is.
GITHUB_RELEASE_URL = "https://api.github.com/repos/%s/%s/releases/tags/%%s" % (
    GITHUB_OWNER, GITHUB_REPO)
# Fallback for the notes: the CHANGELOG at the tag's COMMIT (a ref-pinned raw URL; `main`
# would be stale or ahead). ~250 KB, read only when the release object has no usable body.
GITHUB_RAW_CHANGELOG_URL = "https://raw.githubusercontent.com/%s/%s/%%s/CHANGELOG.md" % (
    GITHUB_OWNER, GITHUB_REPO)
# The source archive, pinned to the commit the tag points at (the tags API returns it). A tag
# can be moved; a commit cannot. github.com redirects this to codeload.github.com.
GITHUB_ARCHIVE_URL = "https://github.com/%s/%s/archive/%%s.zip" % (GITHUB_OWNER, GITHUB_REPO)

# Where the skill lives inside the repository archive; the only part --apply extracts.
SKILL_SUBTREE = "plugins/model-orchestration/skills/model-orchestration"
# A release that lacks any of these is not something --apply will install over a working copy.
REQUIRED_SHIPPED_FILES = ("VERSION", "SKILL.md", "orchestrate.py", "routing.py",
                          "channels.json", "doctor.py", "upgrade.py", "update_check.py")
# Downloads and the extracted subtree, OUTSIDE the skill folder (an update replaces that) and
# next to the other per-user files of this skill (`model-orchestration.local.json` etc.).
UPDATES_DIR = os.path.join(os.path.expanduser("~"), ".claude", "model-orchestration.updates")
# The archive is ~1 MB (measured 1 013 518 bytes for v1.61.0). 50 MB is a corruption guard,
# not a size expectation.
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_API_BYTES = 4 * 1024 * 1024

# Stamp OUTSIDE the plugin tree — an upgrade must not lose the snooze. The env override is
# for tests and for probing an install from another home without touching this one's stamp.
STAMP_PATH = os.environ.get("MODEL_ORCH_UPDATE_STAMP") or os.path.join(
    os.path.expanduser("~"), ".claude", "model-orchestration.update-check.json")

# Env vars we honour.
DISABLE_ENV = "MODEL_ORCH_UPDATE_CHECK"           # set to 0/no/off/false
NO_UPDATE_NOTIFIER_ENV = "NO_UPDATE_NOTIFIER"     # ecosystem convention (npm, gh, others)
CI_ENV = "CI"                                     # every CI system sets this to a truthy value
INTERVAL_HOURS_ENV = "MODEL_ORCH_UPDATE_CHECK_INTERVAL_HOURS"

# Daily. SUPERSEDED R136 (2026-10-03): «weekly, matched to the release cadence (~1/week)» was
# written when releases were weekly; by October 3-4 releases shipped per DAY, and a check made an
# hour before a release stayed silent for up to 7 days. One conditional GET per day is still
# nothing next to the release traffic it announces.
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_SNOOZE_DAYS = 7
NETWORK_TIMEOUT_SECONDS = 3.0
API_TIMEOUT_SECONDS = 10.0          # --apply is interactive; a slow answer beats a wrong "no net"
DOWNLOAD_TIMEOUT_SECONDS = 60.0     # per socket operation, not for the whole transfer
# The SessionStart hook's own ceiling (hooks.json and --install-hook). Python start + the
# weekly tags GET (3 s) + the notes GET (3 s) + the agy check must fit; every other week the
# hook returns in well under a second because the stamp says "not due".
HOOK_TIMEOUT_SECONDS = 15
# R136: the settings.json hook of a SCRIPT install may also APPLY an update (automatic updates on):
# download + verify + upgrade.py without doctor, measured in seconds - the ceiling is for a slow
# network, and AUTO_APPLY_BUDGET_SECONDS stops before any file is copied if the download ate it.
AUTO_HOOK_TIMEOUT_SECONDS = 120
AUTO_APPLY_BUDGET_SECONDS = 60
AUTO_DOWNLOAD_TIMEOUT_SECONDS = 20.0
AUTO_RETRY_HOURS = 24               # a failed automatic apply is retried once a day, not per session
AUTO_LOCK_STALE_SECONDS = 900       # two sessions starting together: one applies, one waits its turn
# A plugin install whose Claude Code auto-update is ON stays quiet about a waiting release (Claude
# Code installs it during the session) - unless the release has waited this long, which means the
# switch is on but the updates are not arriving.
NATIVE_GRACE_HOURS = 72
# Env vars that switch Claude Code's whole plugin auto-update pass off, and the one that overrides
# them (code.claude.com/docs/en/plugins/loading «When auto-update runs», read 2026-10-02).
CC_AUTOUPDATE_BLOCKERS = ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER",
                          "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
CC_AUTOUPDATE_FORCE = "FORCE_AUTOUPDATE_PLUGINS"

# Exponential backoff on network failure: 1h, 2h, 4h, 8h, 16h, capped at INTERVAL. On a truly
# offline machine every session pays the timeout budget with no benefit (panel: SPARK12CONT).
BACKOFF_HOURS = [1, 2, 4, 8, 16]

BANNER_HEAD = "[ai-second-opinion]"


class NetError(Exception):
    """Any failure of an outbound request. Callers decide whether it is silent."""


class VerifyError(Exception):
    """The downloaded archive is not something to install over a working copy."""


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


def _changelog_section(text, version, max_lines=15, max_chars=2500):
    """The CHANGELOG section for `version` out of `text`: the lines after its "## X.Y.Z" header
    up to the next header, capped at `max_lines` non-blank lines. "" if not found."""
    v = (version or "").lstrip("vV").strip()
    if not v or not text:
        return ""
    # Header shapes that exist in this file: "## 1.34.0 — YYYY-MM-DD" and "## [1.3.1] — ...".
    out, capturing = [], False
    for ln in text.splitlines():
        if ln.startswith("## "):
            if capturing:
                break
            head = ln[3:].strip().lstrip("[").replace("]", " ", 1)
            if head.startswith(v + " ") or head.strip() == v:
                capturing = True
                out.append(ln)
                continue
        elif capturing:
            out.append(ln)
    if not out:
        return ""
    # Cap: enough to say what changed and stay well under Claude Code's 10 KB systemMessage
    # limit (panel: SPARK12CONT quoted the cap).
    take, kept, size = [], 0, 0
    for ln in out[1:]:  # skip the "## X.Y.Z" header itself
        take.append(ln)
        size += len(ln) + 1
        if ln.strip():
            kept += 1
        if kept >= max_lines or size >= max_chars:
            break
    return "\n".join(take).strip("\n")


def read_local_changelog_section(latest_version):
    """CHANGELOG section for `latest_version` from the copy shipped INSIDE the skill folder
    (package.py puts CHANGELOG.md there). "" if not found. Best-effort — no throws."""
    p = os.path.join(HERE, "CHANGELOG.md")
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    return _changelog_section(text, latest_version)


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


# ------------------------------------------------------------------- network

def _http_get(url, headers=None, timeout=NETWORK_TIMEOUT_SECONDS, dest=None, max_bytes=None):
    """The ONE outbound choke point. Returns (status, lower-cased headers, body) — body is bytes,
    or the byte count written when `dest` is a path (the archive is streamed, never held in
    memory). A 304 comes back as (304, headers, b""). Everything else that goes wrong raises
    NetError; callers decide whether that is silent (the weekly check) or printed (--apply)."""
    h = {"User-Agent": _user_agent()}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            if dest is None:
                body = resp.read((max_bytes + 1) if max_bytes else -1)
                if max_bytes and len(body) > max_bytes:
                    raise NetError("response larger than %d bytes: %s" % (max_bytes, url))
                return status, hdrs, body
            total = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if max_bytes and total > max_bytes:
                        raise NetError("download larger than %d bytes: %s" % (max_bytes, url))
                    f.write(chunk)
            return status, hdrs, total
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, {k.lower(): v for k, v in (exc.headers or {}).items()}, b""
        raise NetError("HTTP %d for %s" % (exc.code, url))
    except NetError:
        raise
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise NetError("%s: %s" % (type(exc).__name__, exc))


def pick_latest_tag_entry(tags):
    """From /tags JSON, the entry with the highest version tuple as {"name", "sha"}, or None."""
    best = None
    for t in tags or []:
        name = (t or {}).get("name") or ""
        vt = _ver_tuple(name)
        if vt is None:
            continue
        if best is None or vt > best[0]:
            sha = ((t or {}).get("commit") or {}).get("sha") or ""
            best = (vt, {"name": name, "sha": sha})
    return best[1] if best else None


def pick_latest_tag(tags):
    """From /tags JSON, return the tag name with the highest version tuple, or None."""
    e = pick_latest_tag_entry(tags)
    return e["name"] if e else None


def _fetch_tags(stamp, timeout, use_etag=True):
    """The tags list as parsed JSON, or None (304 with a cached answer, or any failure).
    Records the ETag in the stamp. Returns (tags_or_None, status)."""
    headers = {"Accept": "application/vnd.github+json"}
    etag = (stamp or {}).get("tags_etag")
    if etag and use_etag:
        headers["If-None-Match"] = etag
    status, hdrs, body = _http_get(GITHUB_TAGS_URL, headers, timeout, max_bytes=MAX_API_BYTES)
    if status == 304:
        return None, 304
    tags = json.loads(body.decode("utf-8"))
    if hdrs.get("etag"):
        stamp["tags_etag"] = hdrs["etag"]
    return tags, status


def fetch_latest_tag(stamp, timeout=NETWORK_TIMEOUT_SECONDS):
    """Return (tag_name, updated_stamp) or (None, stamp) on any failure. Uses If-None-Match
    to save rate-limit budget when there is no change; the tag's commit SHA is kept in the
    stamp (`tags_latest_sha`) for the notes fetch and for --apply."""
    try:
        tags, status = _fetch_tags(stamp, timeout)
    except (NetError, ValueError):
        return (None, stamp)
    if status == 304:
        return ((stamp or {}).get("tags_latest"), stamp)
    entry = pick_latest_tag_entry(tags)
    if entry:
        stamp["tags_latest"] = entry["name"]
        stamp["tags_latest_sha"] = entry["sha"]
        return (entry["name"], stamp)
    return (None, stamp)


def _first_lines(text, max_lines=15, max_chars=2500):
    take, kept, size = [], 0, 0
    for ln in (text or "").splitlines():
        take.append(ln)
        size += len(ln) + 1
        if ln.strip():
            kept += 1
        if kept >= max_lines or size >= max_chars:
            break
    return "\n".join(take).rstrip()


def fetch_release_notes(tag, sha=None, timeout=NETWORK_TIMEOUT_SECONDS):
    """What changed in `tag`, as text for the notice. First the release object by tag name
    (small; its body is the CHANGELOG section), then the CHANGELOG at the tag's commit.
    "" when neither answers — the notice still names the version and the command."""
    if not tag:
        return ""
    v = tag.lstrip("vV").strip()
    try:
        _s, _h, body = _http_get(GITHUB_RELEASE_URL % tag,
                                 {"Accept": "application/vnd.github+json"}, timeout,
                                 max_bytes=MAX_API_BYTES)
        text = (json.loads(body.decode("utf-8")) or {}).get("body") or ""
        sec = _changelog_section(text, v) or _first_lines(text)
        if sec:
            return sec
    except (NetError, ValueError, AttributeError):
        pass
    try:
        _s, _h, body = _http_get(GITHUB_RAW_CHANGELOG_URL % (sha or tag), None, timeout,
                                 max_bytes=MAX_API_BYTES)
        return _changelog_section(body.decode("utf-8", "replace"), v)
    except (NetError, ValueError):
        return ""


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


# ------------------------------------------------------------------- where am I installed

def _is_git_install(start=None):
    """A Method-4 install runs from an unpacked git tree; detect and return the repo root."""
    p = start or HERE
    for _ in range(6):
        if os.path.isdir(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return None


def install_kind(here=None):
    """Which kind of install this file runs from — decides how --apply updates it.

      dev     the development tree (package.py is here): releases are BUILT from it, so
              overwriting it with a release would be circular. Refused.
      plugin  Claude Code's plugin cache: <config>/plugins/cache/<marketplace>/<plugin>/<ver>/...
              Claude Code owns that folder and its version bookkeeping, so the update goes
              through `claude plugin update`.
      git     an unpacked git checkout (Method 4): `git pull` is the update; refused.
      tree    a script or manual install (any other folder): download + upgrade.py.
    Returns (kind, info)."""
    here = os.path.abspath(here or HERE)
    if os.path.isfile(os.path.join(here, "package.py")):
        return ("dev", {"root": here})
    parts = os.path.normpath(here).split(os.sep)
    low = [p.lower() for p in parts]
    for i in range(len(low) - 4):
        if low[i] == "plugins" and low[i + 1] == "cache":
            config_dir = os.sep.join(parts[:i]) or os.sep
            return ("plugin", {"config_dir": config_dir, "marketplace": parts[i + 2],
                               "plugin": parts[i + 3], "version_dir": parts[i + 4],
                               "plugin_id": "%s@%s" % (parts[i + 3], parts[i + 2])})
    git_root = _is_git_install(here)
    if git_root:
        return ("git", {"root": git_root})
    return ("tree", {"root": here})


def apply_command():
    """The one command every notice names."""
    return 'python "%s" --apply' % os.path.abspath(__file__)


def _how_to_update_lines():
    kind, info = install_kind()
    lines = ["To update now (backs up the current folder first, keeps your settings):",
             "  " + apply_command()]
    if kind == "plugin":
        lines.append("  (Claude Code plugin install: that command runs `claude plugin update %s`; "
                     "restart the session afterwards.)" % info["plugin_id"])
    elif kind == "git":
        lines.append("  (git checkout: `git -C \"%s\" pull` instead - --apply does not overwrite "
                     "a checkout.)" % info["root"])
    return lines


def format_full_message(local, latest, agy_stale, changelog_excerpt=""):
    checked = _iso_now()[:10]
    parts = [
        "%s update available: %s -> %s (checked %s)" % (BANNER_HEAD, local,
                                                          latest.lstrip("vV"), checked),
    ]
    if changelog_excerpt:
        parts.append("")
        parts.append(changelog_excerpt)
        parts.append("")
    parts.extend(_how_to_update_lines())
    parts.append("Update automatically from now on: python \"%s\" --auto-update on   "
                 "(check: --status)" % os.path.abspath(__file__))
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
    """The after-an-update message. Short, factual, points at doctor for detail."""
    parts = [
        "%s updated: %s -> %s" % (BANNER_HEAD, previous_installed, local),
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
    """Weekly check. Returns (action, payload).
    Actions: disabled, no-version, fresh, cached, no-net, up-to-date, snoozed, agy-only, update."""
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
    # What changed: the release notes from GitHub (the local CHANGELOG cannot know a release
    # newer than itself). Cached in the stamp for the week so the per-session notice costs
    # nothing.
    # When this release was first seen: a plugin install with Claude Code's auto-update on stays
    # quiet about it until NATIVE_GRACE_HOURS have passed (R136).
    if stamp.get("latest_first_seen_for") != latest:
        stamp["latest_first_seen_for"], stamp["latest_first_seen_utc"] = latest, _iso_now()
    notes = stamp.get("latest_notes") if stamp.get("latest_notes_for") == latest else ""
    if not notes:
        notes = fetch_release_notes(latest, stamp.get("tags_latest_sha"))
        stamp["latest_notes"], stamp["latest_notes_for"] = notes, latest
    msg = format_full_message(local, latest, agy_stale, notes)
    stamp["pending_message"] = msg
    write_stamp(stamp)
    return ("update", {"local": local, "latest": latest, "message": msg,
                       "agy_stale": agy_stale})


def pending_notice():
    """For callers that just finished real work (orchestrate.py at the end of a round): run the
    stamped weekly check and return the notice text, or None. Never raises."""
    try:
        action, payload = do_check()
    except Exception:                                    # noqa: BLE001 - a notice never crashes a round
        return None
    if action in ("update", "cached"):
        return (payload or {}).get("message") or (payload or {}).get("pending_message")
    return None


def do_local_delta():
    """LOCAL-ONLY. Compares HERE/VERSION to stamp['installed_version']. NO network.
    Fires exactly once after the installed copy changed underneath us (Claude Code updated the
    plugin, or --apply / upgrade.py ran). Returns (action, msg)."""
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
    # Updated since last session: tell the user, and update the stamp so we do not repeat.
    msg = format_local_delta_message(local, prev)
    stamp["installed_version"] = local
    stamp["last_local_notice_utc"] = _iso_now()
    stamp["pending_message"] = None  # supersedes any older pending nag
    write_stamp(stamp)
    return ("local-update", msg)


# ------------------------------------------------------------------- --apply

def resolve_target(tag=None, timeout=API_TIMEOUT_SECONDS):
    """(tag_name, commit_sha, None) for the newest release — or for `tag` when given — else
    (None, None, reason). Always a live read, no ETag: --apply is the one place where a
    definite answer is worth a full response."""
    stamp = read_stamp()
    try:
        tags, _status = _fetch_tags(stamp, timeout, use_etag=False)
    except (NetError, ValueError) as exc:
        return (None, None, "could not read the release list: %s" % exc)
    if tag:
        want = tag if tag.startswith(("v", "V")) else "v" + tag
        for t in tags or []:
            name = (t or {}).get("name") or ""
            if name.lower() == want.lower():
                sha = ((t or {}).get("commit") or {}).get("sha") or ""
                return (name, sha, None)
        return (None, None, "no tag named %s in %s/%s" % (want, GITHUB_OWNER, GITHUB_REPO))
    entry = pick_latest_tag_entry(tags)
    if not entry:
        return (None, None, "the tag list has no release-shaped tag")
    stamp["tags_latest"], stamp["tags_latest_sha"] = entry["name"], entry["sha"]
    # R136 I-3: no `last_check_utc` here. Only do_check owns the daily clock: this read records
    # no pending notice, so refreshing the clock from it silenced the session start for a day.
    stamp["consecutive_failures"], stamp["last_error"] = 0, None
    write_stamp(stamp)
    return (entry["name"], entry["sha"], None)


def extract_skill_subtree(zip_path, dest, expect_version):
    """Verify the downloaded archive and extract ONLY the skill subtree into `dest`.
    Raises VerifyError on anything that is not a clean release archive. Returns file count.

    What "verify" means here, honestly: the download came over TLS from github.com, pinned to
    the commit the tags API named; the archive must hold exactly one top-level folder, the
    skill subtree, every file a release must ship, a VERSION equal to the tag — and no member
    may resolve outside `dest` (zip-slip) or be a symlink. There is no signature: a fork or a
    compromised GitHub account is beyond what this checks, the same trust as `git clone`."""
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad:
            raise VerifyError("corrupt member %s" % bad)
        names = [n for n in z.namelist() if n]
        tops = {n.split("/")[0] for n in names}
        if len(tops) != 1:
            raise VerifyError("expected one top-level folder, found %d" % len(tops))
        prefix = "%s/%s/" % (tops.pop(), SKILL_SUBTREE)
        members = [n for n in names if n.startswith(prefix) and not n.endswith("/")]
        if not members:
            raise VerifyError("no %s inside the archive" % SKILL_SUBTREE)
        dest_abs = os.path.abspath(dest)
        if os.path.isdir(dest_abs):
            shutil.rmtree(dest_abs)
        os.makedirs(dest_abs)
        for n in members:
            rel = n[len(prefix):]
            segs = rel.split("/")
            if any(s in ("", ".", "..") for s in segs) or rel.startswith("/") or ":" in segs[0]:
                raise VerifyError("member path is not a plain relative path: %s" % n)
            out = os.path.abspath(os.path.join(dest_abs, *segs))
            if os.path.commonpath([dest_abs, out]) != dest_abs:
                raise VerifyError("member escapes the folder: %s" % n)
            info = z.getinfo(n)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise VerifyError("symlink in archive: %s" % n)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(n) as fin, open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
    missing = [f for f in REQUIRED_SHIPPED_FILES if not os.path.isfile(os.path.join(dest, f))]
    if missing:
        raise VerifyError("required files missing from the release: %s" % ", ".join(missing))
    with open(os.path.join(dest, "VERSION"), encoding="utf-8") as f:
        got = f.read().strip()
    want = (expect_version or "").lstrip("vV").strip()
    if got != want:
        raise VerifyError("the archive's VERSION says %s, the tag says %s" % (got, want))
    return len(members)


def _cleanup(*paths):
    for p in paths:
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.unlink(p)
        except OSError:
            pass


def _quote_cmd(cmd):
    return " ".join('"%s"' % c if (" " in c or not c) else c for c in cmd)


def _apply_tree(target, sha, args, local, log=None, download_timeout=DOWNLOAD_TIMEOUT_SECONDS,
                deadline=None):
    """Script / manual install: download the release, verify it, hand it to the NEW release's
    upgrade.py (it knows the migrations the old one cannot), report.

    The automatic path (R136, `auto_apply`) passes `log` - upgrade.py's output goes there, never
    to the hook's stdout, which must hold nothing but the JSON - and a `deadline` checked BEFORE
    any file is copied, so a slow download ends with nothing changed instead of a hook killed
    half-way through the copy."""
    ver = target.lstrip("vV").strip()
    work = os.path.join(UPDATES_DIR, ver)
    zip_path = os.path.join(UPDATES_DIR, ver + ".zip")
    src = os.path.join(work, "src")
    try:
        os.makedirs(UPDATES_DIR, exist_ok=True)
    except OSError as exc:
        print("  cannot create %s: %s" % (UPDATES_DIR, exc))
        return 1
    url = GITHUB_ARCHIVE_URL % (sha or target)
    print("  downloading %s" % url)
    try:
        _s, _h, size = _http_get(url, None, download_timeout, dest=zip_path,
                                 max_bytes=MAX_ARCHIVE_BYTES)
    except NetError as exc:
        print("  download failed: %s" % exc)
        _cleanup(zip_path)
        return 1
    print("  %d bytes" % size)
    try:
        n = extract_skill_subtree(zip_path, src, ver)
    except (VerifyError, zipfile.BadZipFile, OSError) as exc:
        print("  REFUSING this archive: %s" % exc)
        print("  kept for inspection: %s" % zip_path)
        _cleanup(work)
        return 1
    print("  verified: %d files, VERSION %s, commit %s" % (n, ver, (sha or "?")[:12]))
    # The incoming release's upgrade.py by preference: the migration from the version you have
    # to the version you are getting is something only the newer script can know. A release
    # too old to carry one (before 1.7.0) cannot be installed by this path anyway - the
    # verification above requires it.
    new_upgrade = os.path.join(src, "upgrade.py")
    cmd = [sys.executable, new_upgrade, "--from", src, "--to", HERE]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.no_doctor:
        cmd.append("--no-doctor")
    if args.carry_all:
        cmd.append("--carry-all")
    if deadline is not None and time.monotonic() > deadline:
        print("  out of time before copying anything - nothing was changed; retried later")
        _cleanup(work, zip_path)
        return 1
    print("  running: %s\n" % _quote_cmd(cmd))
    try:
        sys.stdout.flush()
        if log is not None:
            rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
        else:
            rc = subprocess.call(cmd)
    except OSError as exc:
        print("  could not run upgrade.py: %s" % exc)
        rc = 1
    if rc != 0:
        print("\n%s upgrade.py exited %d - read its report above. The download is kept at %s"
              % (BANNER_HEAD, rc, work))
        return 1
    if args.dry_run:
        _cleanup(work, zip_path)
        print("\n%s --dry-run: nothing was changed. Re-run without --dry-run to apply."
              % BANNER_HEAD)
        return 0
    now_local = read_local_version() or ver
    stamp = read_stamp()
    stamp["installed_version"] = now_local
    stamp["pending_message"] = None
    stamp["latest_seen"] = target
    stamp["last_check_utc"] = _iso_now()
    stamp["consecutive_failures"], stamp["last_error"] = 0, None
    write_stamp(stamp)
    print("\n%s updated: %s -> %s" % (BANNER_HEAD, local or "unknown version", now_local))
    notes = read_local_changelog_section(now_local)
    if notes:
        print("")
        print(notes)
    _cleanup(work, zip_path)
    print("\n  New sessions use the new version. A Claude Code session that is already open "
          "keeps the copy it loaded; restart it to be sure.")
    return 0


def _apply_plugin(info, target, args):
    """Claude Code plugin install: the folder and the version bookkeeping belong to Claude
    Code, so the update goes through its CLI - refresh the marketplace clone, then update the
    plugin. Both commands measured on claude 2.1.268 (R86): the second prints «updated from X
    to Y ... Restart to apply changes.» and keeps the old version folder for 14 days."""
    claude = shutil.which("claude")
    exe = claude or "claude"
    cmds = [[exe, "plugin", "marketplace", "update", info["marketplace"]],
            [exe, "plugin", "update", info["plugin_id"]]]
    print("  Claude Code plugin install (%s, in %s). The update goes through Claude Code:"
          % (info["plugin_id"], info["config_dir"]))
    for c in cmds:
        print("    claude " + " ".join(c[1:]))
    if args.dry_run:
        print("  --dry-run: nothing run.")
        return 0
    if not claude:
        print("  `claude` is not on PATH from here. Run the two commands above in a terminal, "
              "or inside Claude Code: /plugin marketplace update %s  then  /plugin update %s"
              % (info["marketplace"], info["plugin_id"]))
        return 1
    updated = False
    for c in cmds:
        print("\n  $ claude " + " ".join(c[1:]))
        try:
            p = subprocess.run(c, capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("  failed to run: %s" % exc)
            return 1
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        for ln in out.splitlines():
            print("    " + ln)
        if p.returncode != 0:
            print("  exit %d - not updated." % p.returncode)
            return 1
        if "updated from" in out:
            updated = True
    if updated:
        print("\n%s the plugin was updated through Claude Code (target %s). Restart the session "
              "(or /reload-plugins) so the new version loads; its first session start reports "
              "what changed." % (BANNER_HEAD, target))
    else:
        print("\n%s Claude Code reports no plugin change (see its output above). If the "
              "marketplace catalog still lists an older version than %s, the tag was published "
              "before the catalog caught up - retry later." % (BANNER_HEAD, target))
    return 0


def cmd_apply(args):
    kind, info = install_kind()
    local = read_local_version()
    print("%s self-update" % BANNER_HEAD)
    print("  this copy : %s  (%s install)" % (HERE, kind))
    print("  version   : %s" % (local or "no VERSION file (older than 1.7.0, or a dev tree)"))
    if kind == "dev":
        print("  This is the development tree (package.py is here) - releases are BUILT from it. "
              "Update it with git and rebuild with package.py. Nothing done.")
        return 2
    if kind == "git":
        print("  This copy runs inside a git checkout. Update it with:\n"
              "    git -C \"%s\" pull\n"
              "  --apply does not overwrite a checkout. Nothing done." % info["root"])
        return 2
    target, sha, why = resolve_target(args.tag)
    if not target:
        print("  could not resolve the release to install: %s" % why)
        return 1
    print("  release   : %s (commit %s)" % (target, (sha or "?")[:12]))
    if local and not is_newer(target, local) and not args.tag:
        print("  up to date: %s is the newest release." % local)
        rc = 0
    elif local and not is_newer(target, local) and not args.force:
        print("  %s is not newer than the installed %s. Pass --force to install it anyway "
              "(upgrade.py will call it a downgrade and say so)." % (target, local))
        return 2
    elif kind == "plugin":
        rc = _apply_plugin(info, target, args)
    else:
        rc = _apply_tree(target, sha, args, local)
    # R136: the first --apply is where automatic updates get switched on - you asked for an
    # update, the notice said this command also turns them on, and a choice you made before
    # (on OR off) is never overridden. --no-auto-update skips it.
    if rc == 0 and not args.dry_run and not getattr(args, "no_auto_update", False):
        print("")
        enable_auto_update_if_undecided(kind, info, by="apply")
    return rc


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


def hook_message():
    """What the SessionStart hook says, or None. Two parts, either may be absent: the local
    delta (the installed copy changed since last session - fires once) and the weekly release
    check (network only when the stamp says it is due; otherwise the cached notice)."""
    parts = []
    _action, msg = do_local_delta()
    if msg:
        parts.append(msg)
    action2, payload = do_check()
    if action2 in ("update", "cached"):
        pm = (payload or {}).get("message") or (payload or {}).get("pending_message")
        pm = _with_auto_update(pm, payload)
        if pm and pm not in parts:
            parts.append(pm)
    if not parts:
        return None
    return "\n\n".join(parts)


def _hours_since(iso):
    t = _parse_iso(iso)
    if not t:
        return None
    return (_now_utc() - t).total_seconds() / 3600.0


def _with_auto_update(pm, payload):
    """R136: what the session start says about a waiting release once automatic updates exist.
      script / manual install, automatic updates on -> apply it NOW; say what happened
      plugin install, Claude Code's auto-update on  -> say nothing (Claude Code installs it during
                                                       this session) unless it has waited
                                                       NATIVE_GRACE_HOURS - then the full notice
      anything else                                 -> the notice as before
    """
    kind, info = install_kind()
    stamp = read_stamp()
    latest = (payload or {}).get("latest") or stamp.get("latest_seen")
    local = read_local_version()
    if not (latest and local and is_newer(latest, local)):
        return pm
    if kind == "tree" and kit_auto_update_state()["on"]:
        failed = (stamp.get("auto_apply_failed_for") == latest
                  and (_hours_since(stamp.get("auto_apply_last_attempt_utc")) or 1e9)
                  < AUTO_RETRY_HOURS)
        if not failed:
            ok, text = auto_apply(latest, local)
            if ok is not False:
                return text
            stamp = read_stamp()
        return ("%s the automatic update to %s failed: %s\n  log: %s\n\n%s"
                % (BANNER_HEAD, latest.lstrip("vV"), stamp.get("auto_apply_error") or "unknown",
                   os.path.join(UPDATES_DIR, "auto-update.log"), pm or ""))
    if kind == "plugin":
        st = native_auto_update_state(info)
        if st["on"] and not st["blocked_by"]:
            waited = _hours_since(stamp.get("latest_first_seen_utc")
                                  if stamp.get("latest_first_seen_for") == latest else None)
            if waited is None or waited < NATIVE_GRACE_HOURS:   # None: the next daily check dates it
                return None
            return ("%s automatic updates are on, but %s has been out for %d+ hours and has not "
                    "arrived. Check: python \"%s\" --status\n\n%s"
                    % (BANNER_HEAD, latest.lstrip("vV"), NATIVE_GRACE_HOURS,
                       os.path.abspath(__file__), pm or ""))
    return pm


def cmd_hook(args):
    """SessionStart hook mode. Emits both `systemMessage` (10 KB cap, user-visible per the
    hooks docs) and `hookSpecificOutput.additionalContext` (model-visible). Measured on claude
    2.1.268 in `-p`: both fields reach the session from a plugin hook; anthropics/claude-code
    #12151 (open) says interactive delivery of additionalContext has regressed at times, which
    is why both are sent."""
    try:
        msg = hook_message()
    except Exception:                                    # noqa: BLE001 - see below
        # A session-start hook never fails the session, and never exits non-zero: the plugin's
        # hooks.json falls back python3 -> python -> py on a non-zero exit, which must mean
        # "this interpreter is missing", not "run the whole check again" (R136).
        return 0
    if not msg:
        return 0
    capped = msg[:8500]  # keep well under the 10 KB cap, with room for the note below
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            # R136: additionalContext is read silently («doesn't appear as a chat message»,
            # hooks docs) - so it says what to DO with it, or the assistant has no reason to.
            "additionalContext": capped + "\n\n" + ASSISTANT_NOTE,
        },
        "systemMessage": capped,
    }))
    return 0


ASSISTANT_NOTE = ("(For the assistant: tell the user this in ONE line in your first reply - the "
                  "version and, if there is one, the command. Run a command only if they ask.)")


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
    """Show what would be sent. --show-what-would-be-sent."""
    print("Daily check (--check from doctor.py / the end of a real round, and --hook at "
          "session start) sends, at most once per %d hours:" % DEFAULT_INTERVAL_HOURS)
    print("  URL:     GET %s" % GITHUB_TAGS_URL)
    print("  Headers:")
    print("    User-Agent: %s" % _user_agent())
    print("    Accept:     application/vnd.github+json")
    stamp = read_stamp()
    if stamp.get("tags_etag"):
        print("    If-None-Match: %s   (saves rate limit — 304 no body)"
              % stamp.get("tags_etag"))
    print("  and, ONLY when a newer tag exists, once per release, for the notes:")
    print("  URL:     GET %s" % (GITHUB_RELEASE_URL % "<tag>"))
    print("  URL:     GET %s   (fallback)" % (GITHUB_RAW_CHANGELOG_URL % "<commit>"))
    print()
    print("--apply (only when you run it) additionally sends:")
    print("  URL:     GET %s   (the release list, no ETag)" % GITHUB_TAGS_URL)
    print("  URL:     GET %s   (the archive, pinned to the tag's commit)"
          % (GITHUB_ARCHIVE_URL % "<commit>"))
    print("  A Claude Code plugin install runs `claude plugin marketplace update` and "
          "`claude plugin update` instead - Claude Code's own git traffic.")
    print()
    print("  What GitHub sees: your IP, the URLs and headers above, and the time.")
    print("  The installed version is deliberately NOT in the User-Agent.")
    print()
    print("Disable everything: set %s=0 (or NO_UPDATE_NOTIFIER=1, or CI=1)."
          % DISABLE_ENV)
    return 0


def _settings_path():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(cfg, "settings.json")


def _hook_python():
    """The interpreter the settings.json hook runs: THIS one, by absolute path (R136). A bare
    `python` is not there on macOS / most Linux (`python3`) nor on Windows without «Add to PATH»
    (it is the Store stub then) - the hook failed to start there, silently. The base interpreter
    when this runs in a venv, so deleting the venv does not kill the hook."""
    for cand in (getattr(sys, "_base_executable", None), sys.executable):
        if cand and os.path.isfile(cand):
            return os.path.abspath(cand)
    return "python3" if os.name != "nt" else "python"


def cmd_install_hook(args, out=print):
    """Add our SessionStart entry to ~/.claude/settings.json. A plugin install already has the
    hook (hooks.json); this is for script / manual installs, so a session start on those paths
    also gets the daily release check, the one-command notice and - automatic updates on - the
    update itself. R136: exec form (`command` = this interpreter's absolute path, `args` = the
    script) - no shell, so no quoting, and the same entry works under Git Bash, PowerShell and sh."""
    settings_path = _settings_path()
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
        out("%s REFUSING: %s exists but is not valid JSON (%s). Fix the file first - "
            "installing would have overwritten it wholesale."
            % (BANNER_HEAD, settings_path, exc))
        return 1
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    my_path = os.path.abspath(__file__)
    # SUPERSEDED R136: R74 wrote ONE command string `python "<path>" --hook` - a bare `python`
    # that does not exist on macOS / most Linux. Exec form with the absolute interpreter now.
    py = _hook_python()
    my_args = [my_path, "--hook"]
    my_hook = {"type": "command", "command": py, "args": my_args,
               "timeout": AUTO_HOOK_TIMEOUT_SECONDS}
    entry = {"matcher": "startup", "hooks": [my_hook]}

    def _is_mine(h):
        if not isinstance(h, dict):
            return False
        if isinstance(h.get("args"), list):        # the legacy shape this installer once wrote
            return my_path in " ".join(str(x) for x in h["args"])
        return my_path in str(h.get("command") or "")

    def _is_current(h):
        return (isinstance(h, dict) and h.get("command") == py and h.get("args") == my_args
                and h.get("timeout") == AUTO_HOOK_TIMEOUT_SECONDS)

    already = any(any(_is_current(h) for h in (e.get("hooks") or [])) for e in session_start)
    migrated = False
    for e in session_start:
        hl = e.get("hooks") or []
        inner = [h for h in hl if not (_is_mine(h) and not _is_current(h))]
        if len(inner) != len(hl):
            e["hooks"] = inner
            migrated = True
    session_start[:] = [e for e in session_start if e.get("hooks")]
    if already and not migrated:
        out("%s SessionStart hook already installed in %s"
            % (BANNER_HEAD, settings_path))
        return 0
    if not already:
        session_start.append(entry)
    if migrated:
        out("%s replacing the older hook entry for this file (command shape, interpreter or "
            "timeout changed)" % BANNER_HEAD)
    _write_json_atomic(settings_path, settings)
    out("%s SessionStart hook installed in %s" % (BANNER_HEAD, settings_path))
    out("  It runs the daily release check at session start (%s) and tells you (and the "
        "assistant) what changed. Remove: python \"%s\" --uninstall-hook"
        % (py, os.path.abspath(__file__)))
    return 0


def find_my_hook():
    """(hook dict or None, settings path, error) - our SessionStart entry in settings.json."""
    sp = _settings_path()
    settings, err = _load_json_file(sp)
    my_path = os.path.normcase(os.path.abspath(__file__))
    for e in ((settings or {}).get("hooks") or {}).get("SessionStart") or []:
        for h in (e.get("hooks") or []) if isinstance(e, dict) else []:
            blob = " ".join([str(h.get("command") or "")] + [str(x) for x in h.get("args") or []]
                            ) if isinstance(h, dict) else ""
            if my_path in os.path.normcase(blob):
                return h, sp, err
    return None, sp, err


# ------------------------------------------------------------------- automatic updates (R136)

def _load_json_file(path):
    """(data, error). A missing file is ({}, None); unreadable JSON is (None, message) - the
    caller must not write over a file it could not read (R74)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except OSError:
        return {}, None
    except ValueError as exc:
        return None, str(exc)


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _env_set(name, settings_env=None):
    """Set to anything but empty/0/false/no/off, in this process OR in the settings file's `env`
    block (a terminal run of --status does not inherit what Claude Code exports to sessions)."""
    for src in (os.environ, settings_env or {}):
        v = src.get(name)
        if v is not None and str(v).strip() and not _is_falsy(str(v)):
            return True
    return False


def native_auto_update_state(info):
    """Claude Code's own auto-update for the marketplace this plugin came from, resolved as the
    docs say (code.claude.com/docs/en/plugins/loading, read 2026-10-02): `autoUpdate` on its
    `extraKnownMarketplaces` settings entry first, then on its `known_marketplaces.json` entry
    (what the /plugin toggle writes), else OFF for a third-party marketplace. Measured on 2.1.285
    (R136): `claude plugin marketplace add` itself writes the settings entry, without the flag."""
    cfg = info["config_dir"]
    mkt = info["marketplace"]
    sp = os.path.join(cfg, "settings.json")
    settings, err = _load_json_file(sp)
    entry = ((settings or {}).get("extraKnownMarketplaces") or {}).get(mkt) or {}
    on, source = None, "default (off for a third-party marketplace)"
    if isinstance(entry, dict) and isinstance(entry.get("autoUpdate"), bool):
        on, source = entry["autoUpdate"], sp
    else:
        kp = os.path.join(cfg, "plugins", "known_marketplaces.json")
        km, _e = _load_json_file(kp)
        kentry = (km or {}).get(mkt) or {}
        if isinstance(kentry, dict) and isinstance(kentry.get("autoUpdate"), bool):
            on, source = kentry["autoUpdate"], kp
    senv = (settings or {}).get("env") or {}
    blocked = [n for n in CC_AUTOUPDATE_BLOCKERS if _env_set(n, senv)]
    if blocked and _env_set(CC_AUTOUPDATE_FORCE, senv):
        blocked = []
    return {"on": bool(on), "explicit": on is not None, "source": source,
            "blocked_by": blocked, "settings_path": sp, "settings_error": err}


def set_native_auto_update(info, on, only_if_undecided=False, out=print):
    """Write `autoUpdate` on this marketplace's entry in Claude Code's settings.json - the field
    the /plugin toggle writes when the settings file declares the marketplace (docs, as above)."""
    st = native_auto_update_state(info)
    sp, mkt = st["settings_path"], info["marketplace"]
    ui = "/plugin -> Marketplaces -> %s -> %s auto-update" % (mkt, "Enable" if on else "Disable")
    if st["settings_error"]:
        out("%s REFUSING: %s is not valid JSON (%s) - writing would replace it. Fix it, or in "
            "Claude Code: %s" % (BANNER_HEAD, sp, st["settings_error"], ui))
        return 1
    if only_if_undecided and st["explicit"]:
        out("%s automatic updates: %s - left as you set them (%s). Change: --auto-update %s"
            % (BANNER_HEAD, "ON" if st["on"] else "OFF", st["source"], "off" if st["on"] else "on"))
        return 0
    settings, _err = _load_json_file(sp)
    settings = settings or {}
    ekm = settings.setdefault("extraKnownMarketplaces", {})
    entry = ekm.get(mkt) if isinstance(ekm.get(mkt), dict) else {}
    if not entry.get("source"):
        km, _e = _load_json_file(os.path.join(info["config_dir"], "plugins",
                                              "known_marketplaces.json"))
        src = ((km or {}).get(mkt) or {}).get("source")
        if not src:
            out("%s marketplace %s is not in %s - switch it in Claude Code instead: %s"
                % (BANNER_HEAD, mkt, info["config_dir"], ui))
            return 1
        entry = dict(entry, source=src)          # copied verbatim: a different source re-clones
    if entry.get("autoUpdate") is bool(on):
        out("%s automatic updates: already %s (%s)" % (BANNER_HEAD, "ON" if on else "OFF", sp))
    else:
        entry["autoUpdate"] = bool(on)
        ekm[mkt] = entry
        _write_json_atomic(sp, settings)
        out("%s automatic updates: %s - Claude Code's auto-update for marketplace %s, written to "
            "%s (the same switch as %s)" % (BANNER_HEAD, "ON" if on else "OFF", mkt, sp, ui))
    if on:
        out("  Claude Code installs a new release during a session (after your first message, "
            "within ~10 min) and says «Plugin updated»; the next session runs it.")
        if st["blocked_by"]:
            out("  🔴 BUT %s is set, which switches that whole pass off. Add %s=1 to the \"env\" "
                "block of %s to let plugins update anyway." % (", ".join(st["blocked_by"]),
                                                              CC_AUTOUPDATE_FORCE, sp))
            return 1
    return 0


def kit_auto_update_state():
    stamp = read_stamp()
    v = stamp.get("auto_update")
    return {"on": v is True, "explicit": isinstance(v, bool),
            "set_by": stamp.get("auto_update_set_by"), "set_utc": stamp.get("auto_update_set_utc")}


def set_kit_auto_update(on, only_if_undecided=False, by="user", out=print):
    """Script / manual install: the decision lives in the stamp (outside the tree, so an update
    cannot lose it); the session-start hook is what applies, so ON makes sure it is installed."""
    st = kit_auto_update_state()
    if only_if_undecided and st["explicit"]:
        out("%s automatic updates: %s - left as set (by %s, %s). Change: --auto-update %s"
            % (BANNER_HEAD, "ON" if st["on"] else "OFF", st["set_by"] or "?",
               (st["set_utc"] or "?")[:10], "off" if st["on"] else "on"))
        return cmd_install_hook(None, out=lambda *_a: None) if st["on"] else 0
    if on:
        rc = cmd_install_hook(None, out=out)
        if rc != 0:
            return rc
    stamp = read_stamp()
    stamp.update(auto_update=bool(on), auto_update_set_by=by, auto_update_set_utc=_iso_now())
    write_stamp(stamp)
    if read_stamp().get("auto_update") is not bool(on):
        out("%s could not record the choice in %s" % (BANNER_HEAD, STAMP_PATH))
        return 1
    out("%s automatic updates: %s (recorded in %s)" % (BANNER_HEAD, "ON" if on else "OFF",
                                                       STAMP_PATH))
    if on:
        out("  A session start that finds a newer release installs it (download, verify, "
            "backup, keep your settings) and says so. Off: python \"%s\" --auto-update off"
            % os.path.abspath(__file__))
    return 0


def enable_auto_update_if_undecided(kind, info, by, out=print):
    """--apply and upgrade.py: switch automatic updates on unless the user decided before."""
    if is_check_disabled():
        out("%s automatic updates: not switched on - update checks are disabled in this "
            "environment (%s=0 / %s / %s)" % (BANNER_HEAD, DISABLE_ENV, NO_UPDATE_NOTIFIER_ENV,
                                               CI_ENV))
        return 0
    if kind == "plugin":
        return set_native_auto_update(info, True, only_if_undecided=True, out=out)
    if kind == "tree":
        return set_kit_auto_update(True, only_if_undecided=True, by=by, out=out)
    return 0


def _acquire_lock(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if time.time() - os.path.getmtime(path) > AUTO_LOCK_STALE_SECONDS:
            os.unlink(path)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    except OSError:
        return False


def auto_apply(latest, local):
    """Script / manual install, automatic updates on, a newer release waiting: apply it now,
    from the session-start hook. Returns (True, message) / (False, None) / (None, message) when
    another session holds the lock. Never prints: everything goes to the log."""
    lock = os.path.join(UPDATES_DIR, "auto-apply.lock")
    if not _acquire_lock(lock):
        return (None, "%s an automatic update to %s is running in another session."
                % (BANNER_HEAD, latest.lstrip("vV")))
    log_path = os.path.join(UPDATES_DIR, "auto-update.log")
    rc = 1
    try:
        st = read_stamp()
        sha = st.get("tags_latest_sha") if (st.get("tags_latest") or "").lower() == \
            latest.lower() else None
        try:
            if os.path.getsize(log_path) > 256 * 1024:
                os.replace(log_path, log_path + ".old")
        except OSError:
            pass
        with open(log_path, "a", encoding="utf-8", errors="replace") as log:
            log.write("\n==== %s automatic update %s -> %s\n" % (_iso_now(), local, latest))
            args = types.SimpleNamespace(dry_run=False, no_doctor=True, carry_all=False,
                                         tag=None, force=False)
            try:
                with contextlib.redirect_stdout(log):
                    rc = _apply_tree(latest, sha, args, local, log=log,
                                     download_timeout=AUTO_DOWNLOAD_TIMEOUT_SECONDS,
                                     deadline=time.monotonic() + AUTO_APPLY_BUDGET_SECONDS)
            except Exception as exc:                     # noqa: BLE001 - logged, reported once
                log.write("  crashed: %r\n" % (exc,))
                rc = 1
    except OSError:
        rc = 1
    finally:
        _cleanup(lock)
    stamp = read_stamp()
    stamp["auto_apply_last_attempt_utc"] = _iso_now()
    if rc == 0:
        stamp["auto_apply_failed_for"] = stamp["auto_apply_error"] = None
        write_stamp(stamp)
        now_local = read_local_version() or latest.lstrip("vV")
        parts = ["%s updated automatically: %s -> %s" % (BANNER_HEAD, local, now_local)]
        notes = read_local_changelog_section(now_local)
        if notes:
            parts += ["", notes, ""]
        parts.append("Automatic updates are on. Off: python \"%s\" --auto-update off"
                     % os.path.abspath(__file__))
        return (True, "\n".join(parts))
    last = ""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            tail = [ln.strip() for ln in f.read().splitlines()[-40:] if ln.strip()]
        last = next((ln for ln in reversed(tail) if ln.startswith(("download failed", "REFUSING",
                    "out of time", "could not", "crashed", "cannot"))), tail[-1] if tail else "")
    except OSError:
        pass
    stamp["auto_apply_failed_for"], stamp["auto_apply_error"] = latest, last[:300] or "unknown"
    write_stamp(stamp)
    return (False, None)


def cmd_uninstall_hook(args):
    settings_path = _settings_path()
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


def cmd_auto_update(args):
    kind, info = install_kind()
    on = args.auto_update == "on"
    if kind == "plugin":
        return set_native_auto_update(info, on)
    if kind == "tree":
        return set_kit_auto_update(on, by="user")
    print("%s automatic updates do not apply to a %s install (%s) - %s"
          % (BANNER_HEAD, kind, HERE, "update it with git" if kind == "git"
             else "releases are built from this tree"))
    return 2


def _windows_hook_shell_ok():
    """The plugin's session-start hook is a SHELL-form command (R136: python3 || python || py),
    which Claude Code runs in Git Bash on Windows, or in PowerShell without Git Bash - where only
    PowerShell 7 knows `||`. Best-effort detection; a WARN, never a FAIL."""
    if os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return True
    git = shutil.which("git")
    if git and os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(git)), "bin",
                                           "bash.exe")):
        return True
    return bool(shutil.which("pwsh"))


def _working_interpreters():
    """Which of the names the plugin hook tries (python3, python, py -3) START a Python 3.8+ here.
    Found on PATH is not enough: on Windows `python3` / `python` may be the Microsoft Store stub,
    which exists, prints «Python was not found» and exits 9009 (measured R136)."""
    ok = []
    for name, pre in (("python3", []), ("python", []), ("py", ["-3"])):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            rc = subprocess.call([exe] + pre + ["-c", "import sys; sys.exit(sys.version_info "
                                                      "< (3, 8))"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            rc = 1
        if rc == 0:
            ok.append(" ".join([name] + pre))
    return ok


def cmd_status(args):
    """One VERDICT for the person who has to answer «is my kit updating itself?» (R136)."""
    rows, fails = [], [0]

    def row(level, label, text, fix=None):
        rows.append("  [%s] %-12s: %s" % ({"ok": " ok ", "fail": "FAIL", "warn": "warn"}[level],
                                           label, text))
        for f in ([fix] if isinstance(fix, str) else fix or []):
            rows.append("         fix: %s" % f)
        if level == "fail":
            fails[0] += 1

    me = os.path.abspath(__file__)
    kind, info = install_kind()
    local = read_local_version()
    row("ok", "install", "%s (%s), version %s" % (HERE, kind, local or "unknown"))
    if kind in ("dev", "git"):
        row("ok" if kind == "dev" else "warn", "updates",
            "development tree - releases are BUILT here, nothing to update" if kind == "dev"
            else "git checkout - update it with: git -C \"%s\" pull" % info["root"])
    if is_check_disabled():
        row("fail", "checks", "update checks are switched off in this environment",
            "remove %s=0 / %s / %s from it" % (DISABLE_ENV, NO_UPDATE_NOTIFIER_ENV, CI_ENV))
    target, _sha, why = resolve_target()
    behind = bool(target and local and is_newer(target, local))
    if target and not is_check_disabled():
        # R136 I-3: record what was found the way the session-start check does (latest_seen, a
        # pending notice), so «the automatic update brings it at the next session start» below
        # is true. Measured on v1.99.0: without this the next session start stayed silent.
        try:
            do_check(force=True)
        except Exception:                                # noqa: BLE001 - a status report never dies here
            pass
    if not target:
        row("warn", "newest", "could not ask GitHub: %s" % why)
    elif not behind:
        row("ok", "newest", "%s - this is the newest release" % target)
    auto_ok = False
    stamp = read_stamp()
    if kind == "plugin":
        st = native_auto_update_state(info)
        ui = "or in Claude Code: /plugin -> Marketplaces -> %s -> Enable auto-update" % info[
            "marketplace"]
        if st["settings_error"]:
            row("fail", "auto-update", "%s is not valid JSON: %s" % (st["settings_path"],
                                                                      st["settings_error"]))
        elif not st["on"]:
            row("fail", "auto-update", "OFF - Claude Code's auto-update for marketplace %s (%s)"
                % (info["marketplace"], st["source"]),
                ['python "%s" --auto-update on' % me, ui])
        elif st["blocked_by"]:
            row("fail", "auto-update", "switched on, but %s turns Claude Code's plugin "
                "auto-update off" % ", ".join(st["blocked_by"]),
                "add \"%s\": \"1\" to the \"env\" block of %s" % (CC_AUTOUPDATE_FORCE,
                                                                st["settings_path"]))
        else:
            auto_ok = True
            row("ok", "auto-update", "ON - Claude Code installs a release during a session; the "
                "next session runs it (%s)" % st["source"])
        root = os.path.dirname(os.path.dirname(HERE))
        hj, herr = _load_json_file(os.path.join(root, "hooks", "hooks.json"))
        has_ss = bool(((hj or {}).get("hooks") or {}).get("SessionStart"))
        interp = _working_interpreters()
        if not has_ss:
            row("fail", "hook", "the plugin ships no SessionStart hook here (%s)" % (herr or root),
                "reinstall: claude plugin update %s" % info["plugin_id"])
        elif not interp:
            row("fail", "hook", "no python3 / python / py on PATH for the session-start hook",
                "install Python 3 from python.org (Windows: tick «Add Python to PATH»)")
        elif os.name == "nt" and not _windows_hook_shell_ok():
            row("warn", "hook", "the session-start hook needs Git Bash or PowerShell 7 on "
                "Windows; neither was found")
        else:
            row("ok", "hook", "session-start notice: plugin hooks.json, interpreter %s"
                % interp[0])
    elif kind == "tree":
        st = kit_auto_update_state()
        hook, sp, herr = find_my_hook()
        if herr:
            row("fail", "hook", "%s is not valid JSON: %s" % (sp, herr))
        elif not hook:
            row("fail", "hook", "no session-start hook for this install in %s" % sp,
                'python "%s" --auto-update on' % me)
        elif "args" in hook and not os.path.isfile(str(hook.get("command"))):
            row("fail", "hook", "the hook's Python is gone: %s" % hook.get("command"),
                'python "%s" --install-hook' % me)
        elif "args" not in hook:
            row("fail", "hook", "old hook shape (bare `python`, no fallback) in %s" % sp,
                'python "%s" --install-hook' % me)
        else:
            row("ok", "hook", "session start runs %s" % hook.get("command"))
        if not st["on"]:
            row("fail", "auto-update", "OFF%s" % (" (switched off %s)" % (st["set_utc"] or "")[:10]
                                                 if st["explicit"] else ""),
                'python "%s" --auto-update on' % me)
        else:
            auto_ok = bool(hook) and "args" in (hook or {})
            row("ok", "auto-update", "ON - a session start that finds a newer release installs "
                "it (set by %s, %s)" % (st["set_by"] or "?", (st["set_utc"] or "?")[:10]))
        if stamp.get("auto_apply_failed_for"):
            row("warn", "last apply", "the automatic update to %s failed: %s (log: %s)"
                % (stamp["auto_apply_failed_for"], stamp.get("auto_apply_error") or "?",
                   os.path.join(UPDATES_DIR, "auto-update.log")))
    if behind:
        if auto_ok:
            row("warn", "newest", "%s is out, you have %s - the automatic update brings it %s"
                % (target, local, "during a session" if kind == "plugin"
                   else "at the next session start"))
        else:
            row("fail", "newest", "%s is out, you have %s" % (target, local), apply_command())
    last = stamp.get("last_check_utc")
    row("ok" if last else "warn", "last check", "%s%s" % (
        last or "never", " (last error: %s)" % stamp["last_error"] if stamp.get("last_error")
        else ""))
    print("%s update status" % BANNER_HEAD)
    print("\n".join(rows))
    if fails[0]:
        print("VERDICT: FAIL - %d problem(s) above, each with the command that fixes it"
              % fails[0])
        return 1
    print("VERDICT: OK - this install %s" % ("keeps itself current" if auto_ok
                                             else "needs no updates from here"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="weekly check, respecting the stamp (default)")
    ap.add_argument("--hook", action="store_true",
                    help="SessionStart hook mode: local delta + the weekly check, as JSON")
    ap.add_argument("--apply", action="store_true",
                    help="update THIS install to the newest release (or --tag) with a backup")
    ap.add_argument("--tag", default=None,
                    help="with --apply: a specific release tag, e.g. v1.62.0")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --apply: download, verify and report; change nothing")
    ap.add_argument("--no-doctor", action="store_true",
                    help="with --apply: skip the doctor run at the end")
    ap.add_argument("--carry-all", action="store_true",
                    help="with --apply: upgrade.py --carry-all (re-add channels this release "
                         "removed, from your own copy)")
    ap.add_argument("--force", action="store_true",
                    help="--check: ignore the stamp; --apply --tag: allow an older release")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print all outcomes, not just news")
    ap.add_argument("--snooze", action="store_true",
                    help="decline the notice for --snooze-days days")
    ap.add_argument("--snooze-days", type=int, default=None,
                    help="days to snooze (default: %d)" % DEFAULT_SNOOZE_DAYS)
    ap.add_argument("--show-what-would-be-sent", action="store_true", dest="show",
                    help="audit the outbound requests without making them")
    ap.add_argument("--install-hook", action="store_true",
                    help="add the SessionStart entry to ~/.claude/settings.json "
                         "(script / manual installs; the plugin ships it)")
    ap.add_argument("--uninstall-hook", action="store_true",
                    help="remove the SessionStart entry")
    ap.add_argument("--status", action="store_true",
                    help="one VERDICT: is this install current, and will it stay current?")
    ap.add_argument("--auto-update", choices=("on", "off"), default=None,
                    help="automatic updates for this install (plugin: Claude Code's marketplace "
                         "auto-update; script install: the session-start hook applies releases)")
    ap.add_argument("--if-undecided", action="store_true",
                    help="with --auto-update on: only when no choice was recorded before "
                         "(what upgrade.py uses)")
    ap.add_argument("--no-auto-update", action="store_true",
                    help="with --apply: do not switch automatic updates on")
    a = ap.parse_args()
    if a.hook:
        return cmd_hook(a)
    if a.status:
        return cmd_status(a)
    if a.auto_update:
        if a.if_undecided and a.auto_update == "on":
            kind, info = install_kind()
            return enable_auto_update_if_undecided(kind, info, by="upgrade")
        return cmd_auto_update(a)
    if a.apply:
        return cmd_apply(a)
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
