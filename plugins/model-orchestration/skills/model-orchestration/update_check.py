#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
update_check.py — the kit's own update cycle: notice a new release, say so, apply it.

Four jobs, one file, standard library only:

  --check   Ask GitHub once a week whether a newer release exists (stamped; ETag; 3 s timeout;
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
import datetime
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
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

# Weekly, matched to the release cadence (~1/week). Daily is 7× more phones-home for no
# information (panel: GROK420, MIMO25PRO).
DEFAULT_INTERVAL_HOURS = 168
DEFAULT_SNOOZE_DAYS = 7
NETWORK_TIMEOUT_SECONDS = 3.0
API_TIMEOUT_SECONDS = 10.0          # --apply is interactive; a slow answer beats a wrong "no net"
DOWNLOAD_TIMEOUT_SECONDS = 60.0     # per socket operation, not for the whole transfer
# The SessionStart hook's own ceiling (hooks.json and --install-hook). Python start + the
# weekly tags GET (3 s) + the notes GET (3 s) + the agy check must fit; every other week the
# hook returns in well under a second because the stamp says "not due".
HOOK_TIMEOUT_SECONDS = 15

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
    stamp["last_check_utc"] = _iso_now()
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


def _apply_tree(target, sha, args, local):
    """Script / manual install: download the release, verify it, hand it to the NEW release's
    upgrade.py (it knows the migrations the old one cannot), report."""
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
        _s, _h, size = _http_get(url, None, DOWNLOAD_TIMEOUT_SECONDS, dest=zip_path,
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
    print("  running: %s\n" % _quote_cmd(cmd))
    try:
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
    if local and not is_newer(target, local):
        if not args.tag:
            print("  up to date: %s is the newest release." % local)
            return 0
        if not args.force:
            print("  %s is not newer than the installed %s. Pass --force to install it anyway "
                  "(upgrade.py will call it a downgrade and say so)." % (target, local))
            return 2
    if kind == "plugin":
        return _apply_plugin(info, target, args)
    return _apply_tree(target, sha, args, local)


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
        if pm and pm not in parts:
            parts.append(pm)
    if not parts:
        return None
    return "\n\n".join(parts)


def cmd_hook(args):
    """SessionStart hook mode. Emits both `systemMessage` (10 KB cap, user-visible per the
    hooks docs) and `hookSpecificOutput.additionalContext` (model-visible). Measured on claude
    2.1.268 in `-p`: both fields reach the session from a plugin hook; anthropics/claude-code
    #12151 (open) says interactive delivery of additionalContext has regressed at times, which
    is why both are sent."""
    msg = hook_message()
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
    print("Weekly check (--check from doctor.py / the end of a real round, and --hook at "
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
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def cmd_install_hook(args):
    """Add our SessionStart entry to ~/.claude/settings.json. A plugin install already has the
    hook (hooks.json); this is for script / manual installs, so a session start on those paths
    also gets the weekly release check and the one-command notice."""
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
        print("%s REFUSING: %s exists but is not valid JSON (%s). Fix the file first - "
              "installing would have overwritten it wholesale."
              % (BANNER_HEAD, settings_path, exc))
        return 1
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    my_path = os.path.abspath(__file__)
    # One COMMAND STRING with the path quoted. (`command` + `args` is also a documented shape
    # — measured working for the plugin's hooks.json in R86 — but the string form is what every
    # settings.json example uses and what this installer has written since R74.)
    my_cmd = 'python "%s" --hook' % my_path
    my_hook = {"type": "command", "command": my_cmd, "timeout": HOOK_TIMEOUT_SECONDS}
    entry = {"matcher": "startup", "hooks": [my_hook]}

    def _is_mine(h):
        if not isinstance(h, dict):
            return False
        if isinstance(h.get("args"), list):        # the legacy shape this installer once wrote
            return my_path in " ".join(str(x) for x in h["args"])
        return my_path in str(h.get("command") or "")

    def _is_current(h):
        return (isinstance(h, dict) and h.get("command") == my_cmd
                and h.get("timeout") == HOOK_TIMEOUT_SECONDS and "args" not in h)

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
        print("%s SessionStart hook already installed in %s"
              % (BANNER_HEAD, settings_path))
        return 0
    if not already:
        session_start.append(entry)
    if migrated:
        print("%s replacing the older hook entry for this file (command shape or timeout "
              "changed)" % BANNER_HEAD)
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, settings_path)
    print("%s SessionStart hook installed in %s" % (BANNER_HEAD, settings_path))
    print("  It runs the weekly release check at session start and tells you (and the "
          "assistant) the one command that updates. Remove: python \"%s\" --uninstall-hook"
          % os.path.abspath(__file__))
    return 0


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
    a = ap.parse_args()
    if a.hook:
        return cmd_hook(a)
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
