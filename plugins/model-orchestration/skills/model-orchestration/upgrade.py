#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
upgrade.py - update an existing install without destroying the settings the docs told you to make.

    python upgrade.py                 # from the downloaded repo: update the install in ~/.claude
    python upgrade.py --dry-run       # print exactly what would happen, change nothing
    python upgrade.py --to <dir>      # a non-default install location
    python upgrade.py --migrate       # only move in-tree edits into your local settings file

WHY THIS SCRIPT EXISTS
----------------------
INSTALL.md tells the reader to open `channels.json` and set `"enabled": true` on a channel. Then
every way of updating destroyed that edit, and none of them mentioned it:

  * `install.ps1` / `install.sh` MOVE the old folder aside and copy a fresh one;
  * "just copy the files" overwrites it;
  * the PLUGIN path - the one the documentation recommends, the one that updates itself with
    nobody running a script - replaces the whole cached checkout.

Verified on the 1.6.0 tree on 2026-08-08. On top of that, nothing in a non-plugin install carried
a version string at all (`plugin.json` sits OUTSIDE the copied folder), so "update me to the new
version" could not even be answered with "you are already on it".

WHAT IT DOES ABOUT IT
---------------------
Settings move OUT of the shipped tree, into `~/.claude/model-orchestration.local.json`, which no
upgrade method can reach. After that the naive methods are correct too - which is the point. This
script is the one-time migration plus a readable report of what changed between the two versions.

It never deletes: the previous folder is copied to `<dir>.bak.<timestamp>` before anything is
written, and the report ends with that path.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"__pycache__", ".git", "reviews", "runs"}
UNKNOWN = "unknown (older than 1.7.0 - no version was shipped before that)"

# The one field a reader is INSTRUCTED to change, and the only one that cannot break the registry
# by being carried across a release: it is a boolean, and an unknown channel name is refused by
# the loader with the full list. Everything else is reported and left behind unless --carry-all,
# because a `model` string carried into a release that renamed it turns every later command into a
# validation error - which punishes the person who followed the instructions.
AUTO_CARRY = ("enabled",)


def default_dst():
    return os.path.join(os.path.expanduser("~"), ".claude", "skills", "model-orchestration")


def overlay_file():
    """Kept in step with routing.overlay_path() by importing it when we can, not by copying it."""
    try:
        sys.path.insert(0, HERE)
        import routing
        return routing.overlay_path()
    except Exception:                                    # noqa: BLE001 - a partial tree must still upgrade
        return os.path.join(os.path.expanduser("~"), ".claude", "model-orchestration.local.json")


def baseline_file():
    """
    The pristine copy of the registry this install shipped, kept OUTSIDE the skill folder.

    🔴 IT WAS INSIDE IT FOR AN HOUR, AND A REVIEWER CAUGHT THE OBVIOUS. A baseline stored in the
    tree is destroyed by exactly the thing it exists to survive: "the entire directory is replaced
    by the package manager ... forcing the toolkit into degraded inferred mode on every automated
    plugin update". The same reasoning that moved the user's settings out had simply not been
    applied to the file written to protect them — the fix's own blind spot, in the fix.
    """
    return os.path.join(os.path.dirname(overlay_file()), "model-orchestration.shipped.json")


def plugin_cache_copies(plugin_name="model-orchestration", root=None):
    """
    Previously installed copies of this plugin still sitting in Claude Code's plugin cache.

    🔴 THE ONE UPDATE PATH `upgrade.py` CANNOT BE ON. codex, reviewing this release, refused the
    sentence "this makes every update method correct": it makes every FUTURE update correct, and
    the one hop INTO 1.7.0 still loses a 1.6.x user's edit on any path that never runs this
    script - which is exactly the recommended one, since the plugin auto-updates with nobody
    running anything. Correct, and it falsified a line already written in the changelog.

    There is a rescue window, and it is documented. code.claude.com/docs/en/plugins-reference,
    read 2026-08-08: "Claude Code copies marketplace plugins to the user's local plugin cache
    (`~/.claude/plugins/cache`)... Each installed version is a separate directory in the cache.
    When you update or uninstall a plugin, the previous version directory is marked as orphaned
    and removed automatically 14 days later." So for two weeks after an auto-update the old
    `channels.json` - with the user's edit in it - is still on disk.

    Layout confirmed on a real machine, not only in the docs:
    `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. Everything here is best-effort
    and silent when it finds nothing: a rescue that crashes is worse than one that misses.
    """
    root = root or os.environ.get("MODEL_ORCH_PLUGIN_CACHE") or \
        os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache")
    found = []
    try:
        markets = sorted(os.listdir(root))
    except OSError:
        return found
    for market in markets:
        pdir = os.path.join(root, market, plugin_name)
        if not os.path.isdir(pdir):
            continue
        try:
            versions = sorted(os.listdir(pdir))
        except OSError:
            continue
        for ver in versions:
            for rel in (os.path.join("skills", plugin_name, "channels.json"),
                        os.path.join("plugins", plugin_name, "skills", plugin_name,
                                     "channels.json")):
                p = os.path.join(pdir, ver, rel)
                if os.path.isfile(p):
                    found.append((market, ver, p))
                    break
    return found


def read_version(tree):
    p = os.path.join(tree, "VERSION")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or UNKNOWN
    except OSError:
        return UNKNOWN


def _ver_tuple(s):
    """(1, 7, 0) from '1.7.0'; None for anything that is not a plain release number."""
    parts = (s or "").strip().split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def channel_map(reg):
    return (reg or {}).get("channels") or {}


def find_edits(installed, pristine, incoming):
    """
    What did the user change? Returns (edits, basis).

    Three-way when we have the pristine copy of what was installed (`channels.shipped.json`,
    written by this script and by the installer from 1.7.0 on): anything differing from it is
    unambiguously a human edit.

    Two-way otherwise - which is every install that exists today - and the difference matters
    enough to print. Comparing the installed file against the INCOMING one cannot tell a user's
    edit from a default the new release changed on purpose, so the report says "probable" and
    shows both values instead of pretending to know.
    """
    edits, only = [], None
    base = channel_map(pristine)
    basis = "three-way (exact: your file against the copy your version shipped)"
    if not base:
        # 🔴 IN INFERRED MODE, LOOK AT ONE FIELD ONLY. Comparing against the incoming file makes
        # every deliberate change in the new release look like an edit of the user's, and a report
        # of two hundred "not carried" lines is a report nobody reads - which is how a real entry
        # in it gets missed. `enabled` is the field INSTALL.md tells people to change, so it is
        # the only one worth guessing about; everything else waits for a real baseline.
        base, only = channel_map(incoming), set(AUTO_CARRY)
        basis = ("two-way, %s ONLY (INFERRED: your version shipped no pristine copy, so a default "
                 "this release changed on purpose is indistinguishable from an edit of yours)"
                 % "/".join(AUTO_CARRY))
    for cname, ch in channel_map(installed).items():
        ref = base.get(cname)
        if ref is None:
            edits.append((cname, None, None, None))       # whole channel absent from the baseline
            continue
        for field, value in ch.items():
            if field.startswith("_") or (only and field not in only):
                continue
            if ref.get(field) != value:
                edits.append((cname, field, ref.get(field), value))
    return edits, basis


def merge_overlay(path, carry, dry):
    """Write the carried settings into the user's local file, never dropping what is there."""
    existing = load_json(path) or {}
    if not isinstance(existing.get("channels"), dict):
        existing = {"channels": {}} if not existing else dict(existing, channels={})
    kept, conflicts = [], []
    for cname, field, _before, after in carry:
        cur = existing["channels"].setdefault(cname, {})
        if field in cur and cur[field] != after:
            conflicts.append((cname, field, cur[field], after))
            continue                                     # the local file is the newer intent
        cur[field] = after
        kept.append((cname, field, after))
    if not dry and kept:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing.setdefault("_comment", "Your settings for the model-orchestration skill. This "
                                        "file lives OUTSIDE the skill folder on purpose: no "
                                        "update can overwrite it. Shape: "
                                        "{\"channels\": {\"<name>\": {\"enabled\": true}}}")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return kept, conflicts


def copy_tree(src, dst):
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".bak")):
                continue
            shutil.copy2(os.path.join(root, name), os.path.join(target, name))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=HERE,
                    help="the new skill folder (default: the one this script is in)")
    ap.add_argument("--to", dest="dst", default=default_dst(),
                    help="the install to update (default: %s)" % default_dst())
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--migrate", action="store_true",
                    help="only move in-tree edits into your local settings file; do not copy files")
    ap.add_argument("--carry-all", action="store_true",
                    help="also carry fields other than %s. Read the report first: a stale model "
                         "name carried across a release makes every later command fail validation"
                         % "/".join(AUTO_CARRY))
    ap.add_argument("--no-doctor", action="store_true", help="skip the check run at the end")
    a = ap.parse_args()

    src, dst = os.path.abspath(a.src), os.path.abspath(a.dst)
    same = os.path.normcase(src) == os.path.normcase(dst)
    if same and not a.migrate:
        print("The source and the destination are the same folder:\n  %s\n\n"
              "This is the copy that is already installed, so there is nothing to copy FROM.\n"
              "Download the new version and run ITS upgrade.py, or pass --from <new folder>.\n"
              "If you only wanted to move your channels.json edits into your local settings "
              "file, run:  python upgrade.py --migrate" % src)
        return 2
    if not os.path.isfile(os.path.join(src, "channels.json")):
        print("not a skill folder: %s (no channels.json). Run this from the unpacked repository, "
              "at plugins/model-orchestration/skills/model-orchestration/upgrade.py" % src)
        return 2

    fresh = not os.path.isdir(dst)
    new_v, old_v = read_version(src), read_version(dst) if not fresh else "not installed"
    print("=" * 78)
    print("UPGRADE" if not fresh else "FRESH INSTALL")
    print("=" * 78)
    print("  from : %s   (version %s)" % (src, new_v))
    print("  to   : %s   (version %s)" % (dst, old_v))
    if a.dry_run:
        print("  --dry-run: nothing will be written")
    # 🔴 NOTHING STOPPED A DOWNGRADE. Named by mimo25pro: pointed at an older archive, by accident
    # or by an assistant that cloned the wrong tag, this happily copied older files over newer ones
    # and reported success. It is not refused - going back is a legitimate thing to want after a
    # bad release - but it is no longer SILENT, which is the actual defect.
    if _ver_tuple(new_v) and _ver_tuple(old_v) and _ver_tuple(new_v) < _ver_tuple(old_v):
        print("\n  🔴 THIS IS A DOWNGRADE: %s is OLDER than the %s you have. If that is what you "
              "meant, carry on; if not, you are probably pointed at the wrong folder or an old "
              "tag." % (new_v, old_v))
    if new_v == old_v and not fresh:
        print("\n  Note: same version in both places. Nothing will change except a fresh copy of "
              "the files.")

    carry, conflicts, edits, basis = [], [], [], ""
    if not fresh:
        installed = load_json(os.path.join(dst, "channels.json"))
        # ...and the in-tree location is still read, so an install made by an earlier 1.7.0 build
        # keeps its baseline instead of silently dropping to inferred mode.
        pristine = (load_json(baseline_file())
                    or load_json(os.path.join(dst, "channels.shipped.json")))
        incoming = load_json(os.path.join(src, "channels.json"))
        if installed is None:
            print("\n  ! %s\\channels.json is missing or unreadable - treating this as a fresh "
                  "install of the files, and leaving your local settings file alone." % dst)
        else:
            edits, basis = find_edits(installed, pristine, incoming)
            old_names, new_names = set(channel_map(installed)), set(channel_map(incoming))
            print("\nCHANNELS")
            print("  new in this version : %s" % (", ".join(sorted(new_names - old_names)) or "none"))
            print("  gone from it        : %s" % (", ".join(sorted(old_names - new_names)) or "none"))
            print("\nYOUR EDITS TO channels.json")
            print("  detection: %s" % basis)
            if not edits:
                print("  none found - your registry matches the baseline")
            for cname, field, before, after in edits:
                if field is None:
                    print("  %-18s a channel that is not in the baseline at all - left in your "
                          "local settings file untouched" % cname)
                    continue
                take = field in AUTO_CARRY or a.carry_all
                print("  %-18s %-14s %r -> %r   %s"
                      % (cname, field, before, after,
                         "CARRIED OVER" if take else "not carried (pass --carry-all to keep it)"))
                if take:
                    carry.append((cname, field, before, after))
            ovp = overlay_file()
            kept, conflicts = merge_overlay(ovp, carry, a.dry_run)
            print("\nYOUR SETTINGS NOW LIVE OUTSIDE THE SKILL FOLDER")
            print("  %s" % ovp)
            # "written" in a --dry-run report is a false statement about the filesystem, and this
            # project's whole discipline is that a printed claim has to match what happened.
            print("  %d setting(s) %s there. No update can overwrite that file - which is the "
                  "whole point of moving them."
                  % (len(kept), "WOULD BE written" if a.dry_run else "written"))
            for cname, field, before, after in conflicts:
                print("  ! %s.%s: your local file already says %r; leaving it, not overwriting "
                      "with %r from the skill folder" % (cname, field, before, after))

    # The plugin path never runs this script, so its rescue has to be offered rather than reached.
    cached = plugin_cache_copies()
    if cached:
        incoming = load_json(os.path.join(src, "channels.json")) or {}
        cache_carry = []
        for market, ver, path in cached:
            old = load_json(path)
            if not old:
                continue
            for cname, ch in channel_map(old).items():
                ref = channel_map(incoming).get(cname)
                if ref is not None and ch.get("enabled") != ref.get("enabled"):
                    cache_carry.append((cname, "enabled", ref.get("enabled"), ch.get("enabled"),
                                        "%s/%s" % (market, ver)))
        print("\nOLDER COPIES IN THE PLUGIN CACHE (%d)" % len(cached))
        for market, ver, _p in cached:
            print("  %s / %s" % (market, ver))
        if cache_carry:
            print("  These carry settings your current install does not have. The cache copy is "
                  "deleted 14 days after an update, so this is a limited window:")
            for cname, field, before, after, where in cache_carry:
                print("    %-18s %-9s %r -> %r   (from %s)" % (cname, field, before, after, where))
            kept2, _c2 = merge_overlay(overlay_file(),
                                       [(c, f, b, af) for c, f, b, af, _w in cache_carry],
                                       a.dry_run)
            print("  %d %s into %s"
                  % (len(kept2), "would be written" if a.dry_run else "written", overlay_file()))
        else:
            print("  Nothing in them differs from this release. Nothing to carry.")

    if a.migrate:
        print("\n--migrate: settings moved, no files copied. Nothing else changed.")
        return 0

    backup = None
    if not fresh and not a.dry_run:
        backup = "%s.bak.%s" % (dst, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(dst, backup, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    if not a.dry_run:
        # 🔴 A HALF-COPY IS THE ONE STATE WITH NO GOOD ENDING, so it does not end silently. A
        # read-only file, a full disk or a locked handle stops `copy2` partway and leaves a tree
        # that is neither version. Nothing is rolled back automatically - guessing wrong about
        # which half is good is worse - but the exact command that restores the backup is printed,
        # which is the thing the user cannot reconstruct (the folder name has a timestamp in it).
        try:
            copy_tree(src, dst)
            # The pristine copy of what was just installed, so the NEXT upgrade is a three-way
            # merge instead of a guess. Written OUTSIDE the tree - see baseline_file().
            os.makedirs(os.path.dirname(baseline_file()), exist_ok=True)
            shutil.copy2(os.path.join(src, "channels.json"), baseline_file())
        except OSError as exc:
            print("\n🔴 THE COPY FAILED PART-WAY: %s" % exc)
            print("   The folder is now a mix of both versions. Your settings file is untouched.")
            if backup:
                print("   PUT BACK WHAT YOU HAD:\n"
                      "     python \"%s\" --from \"%s\" --to \"%s\" --no-doctor"
                      % (os.path.join(backup, "upgrade.py"), backup, dst))
            return 1

    print("\nRESULT")
    print("  files    : %s" % ("would be copied (--dry-run)" if a.dry_run else "copied -> %s" % dst))
    print("  version  : %s" % read_version(src))
    print("  backup   : %s" % (backup or "none (nothing was replaced)"))
    if a.dry_run:
        print("\nNothing was written. Re-run without --dry-run to apply.")
        return 0

    # 🔴 THE BACKUP WAS TAKEN AND NEVER MENTIONED AGAIN. mimo25pro, reviewing this release: the
    # backup-then-copy pattern is the minimum viable safety net, and it leaves a user whose new
    # install does not start holding a folder whose timestamped name they now have to reconstruct.
    # So the checks are RUN AND READ, not run and printed, and a failure prints the one command
    # that undoes this. Not automatic: rolling back on a FAIL that is a missing API key would
    # undo a good upgrade over an unrelated problem.
    if not a.no_doctor:
        print("\nRunning the checks...\n")
        try:
            rc = subprocess.call([sys.executable, os.path.join(dst, "doctor.py")])
        except OSError as exc:
            print("could not run doctor.py (%s) - run it yourself: python %s"
                  % (exc, os.path.join(dst, "doctor.py")))
            rc = 0
        if rc and backup:
            print("\n🔴 The checks did not pass. Read them above - a missing key or CLI is normal "
                  "and disables one channel.\n   If this install is genuinely broken, THIS PUTS "
                  "BACK EXACTLY WHAT YOU HAD:\n"
                  "     python \"%s\" --from \"%s\" --to \"%s\" --no-doctor"
                  % (os.path.join(dst, "upgrade.py"), backup, dst))
            print("   Your settings file is not touched either way: %s" % overlay_file())
    return 0


if __name__ == "__main__":
    sys.exit(main())
