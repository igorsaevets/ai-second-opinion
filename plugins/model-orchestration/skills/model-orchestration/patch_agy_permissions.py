#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patch_agy_permissions.py - make headless `agy` runs survivable.

    python patch_agy_permissions.py --dry-run     # show the diff, change nothing
    python patch_agy_permissions.py               # apply
    python patch_agy_permissions.py --keep-shell  # apply, but leave the shell rule alone
    python patch_agy_permissions.py --revert      # restore the newest backup
    python patch_agy_permissions.py --check       # exit 1 if the config is stale (for doctor/CI)

WHY THIS EXISTS
---------------
In headless (`-p`) mode agy cannot show a permission prompt. A tool that is in NEITHER list is
therefore auto-denied - and that auto-denial DISCARDS THE ENTIRE RUN:

    Print mode: soft-denying tool confirmation "X" at step N     ->  response = "",
                                                                     status = SUCCESS, exit 0

Nothing in the exit code or the status field distinguishes that from a good run. Measured losses:
29 tool calls thrown away on 2026-07-31; 56 s / 8 searches / 3 898 output tokens on 2026-08-19;
48 s / 2 840 tokens the same day.

THE ASYMMETRY EVERYTHING HERE IS BUILT ON (R56, agy 1.1.16, three arms)

    allowed            the tool runs
    explicitly DENIED  an ordinary tool error - "Permission denied for X. Matches user-configured
                       deny rule" - AND THE MODEL CARRIES ON AND FINISHES
    neither (UNLISTED) soft-deny, status CANCELED, the whole turn is discarded

**Silence is the dangerous state, not refusal.** A deny costs nothing; an omission costs a round.
So the goal of this file is not "grant as little as possible" - it is **leave nothing unlisted**.

WHAT R57 MEASURED, AND WHY THE SHAPE CHANGED
--------------------------------------------
The permission language has exactly six rule kinds. They are not documented anywhere: `agy --help`
covers flags only, and the vendor's reference page is slash-commands-only. They were read out of
the store the product itself writes, `~/.gemini/config/config.json` ->
`userSettings.globalPermissionGrants`:

    command(...)   mcp(server/tool)   read_file(path)   write_file(path)
    read_url(domain)                  execute_url(domain)

There is NO bare-tool-name rule: `run_command` and `RunCommand` in either list match nothing
(arms J, K, N). So the model is CAPABILITY-based, and "every tool" is a closed set of six - not a
treadmill of names.

Measured on 1.1.16, each pair one variable, logs under runs/r57/:

    allow mcp(*)                        -> every server, INCLUDING ones added later   (T, ctrl U)
    deny  mcp(srv/*) + allow mcp(srv/t) -> DENY WINS. A server is all-or-nothing.     (S)
    allow command(echo)  on `echo X`    -> soft-denied. command() is EXACT-match, not (B)
                                           the prefix its own help string claims.
    allow command(<exact>)              -> runs                                       (D, F2)
    allow command(<exact>) + --sandbox  -> soft-denied. --sandbox cancels an ALLOW.    (E)
    deny  command(*)       + --sandbox  -> hard deny, RUN SURVIVED                     (M, H)
    allow command(*) + deny command(*del*) -> the canary FILE WAS DELETED.             (R1)
                                           `*` is an all-token, not a glob.

That last line is the answer to "allow everything except deleting files": **it cannot be written.**
`*` does not glob, so no deny pattern can carve deletion out of an allow-all. And there is nothing
else to deny instead - agy has NO file-deletion tool. Its 60 tool configs contain none; the
`DeleteFileOrDirectory` symbols in the binary belong to a gRPC WorkspaceService used by the IDE.
**Deletion is reachable only through the shell.** So the command capability has exactly two usable
states, and only one of them is compatible with "do not let it delete files":

    allow command(*)   (needs --sandbox dropped)  unrestricted shell, deletion included
    deny  command(*)                              no shell at all, run survives, deletion
                                                  impossible - and the R56 fatality is gone

`--dangerously-skip-permissions` is not a third option. It does auto-approve everything, and R57
measured that MCP denies STILL WIN under it (so the reason this file used to give - "it unlocks
firecrawl_crawl" - was wrong). But a `command(*)` deny is IGNORED under that flag, so it hands an
unattended reviewer an unrestricted shell with no way to bound it. Worse reason, same conclusion.
"""

import argparse
import json
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SETTINGS = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "settings.json")

# ---------------------------------------------------------------------------------------------
# ALLOW - one line, because `mcp(*)` is a real rule (measured: arm T, with a valid control).
#
# This replaces a 60-entry enumeration of somebody else's tool names. That enumeration was the
# defect, not the shortcut: the default state for anything the server gains later is the FATAL
# one, and it cost a run twice on the same server nineteen days apart (jina read_url on 07-31,
# jina search_web_deep on 08-19). `mcp(*)` covers tools added upstream AND servers added later -
# and a new SERVER is the same fatal state one level up, which no per-server wildcard reaches.
#
# The old entries are left in place by the merge below; they are now redundant, not wrong.
ALLOW = [
    "mcp(*)",
]

# ---------------------------------------------------------------------------------------------
# DENY - deny is where every bound now lives, and it costs nothing to be here.
#
# 🔴 A wildcard deny takes the WHOLE server: a more specific allow does NOT rescue one tool from
# it (arm S). So `mcp(firecrawl/*)` below really does mean no Firecrawl at all, on purpose.
DENY = [
    # THE SHELL. This is the fix for the class R55 and R56 both lost a round to: the model reaches
    # for a shell (usually as a FALLBACK after another tool breaks), nothing matches, and the turn
    # is discarded. Denying it converts that into an ordinary tool error the model recovers from -
    # measured with AND without --sandbox (arms M, H), the run finishing both times.
    #
    # It is also the only way to honour "do not let it delete files", because the shell is the only
    # route to deletion and `*` cannot be narrowed.
    #
    # 🔴 THE COST, STATED PLAINLY: settings.json is MACHINE-WIDE, so this also stops shell commands
    # in the interactive agy TUI. `--keep-shell` skips this one rule; `--revert` undoes everything.
    "command(*)",

    # 🔴 METERED FIRECRAWL - NAMED, NOT WHOLESALE, AND THE CORRECTION IS THE POINT.
    #
    # R57 first shipped `mcp(firecrawl/*)`, reasoning that under `mcp(*)` a tool the server gains
    # later would otherwise be auto-allowed and could bill. That is true, and it was still wrong,
    # because a wildcard deny takes the WHOLE server (a more specific allow does not rescue one
    # tool from it - measured, arm S) and the owner's policy is *scrape + map are allowed*, only
    # the rest is not. The over-reach was visible within the hour: in the verification panel
    # agy31pro reached for `firecrawl_scrape` and got
    # `Permission denied ... Matches user-configured deny rule` - a tool it was supposed to have.
    #
    # A safety rule that also removes a sanctioned capability is not "the strict version" of the
    # policy, it is a different policy. The list below is the policy as it actually stands:
    # everything metered, recurring or duplicative of a free tool is denied by name; `scrape`
    # (1 credit, the sanctioned last resort for a bot-protected page) and `map` (1 credit flat for
    # any number of URLs) are left reachable.
    #
    # The residual, stated rather than hidden: a Firecrawl tool added upstream is NOT on this list,
    # so `mcp(*)` will allow it. That is a real exposure and the price of not taking the server
    # wholesale. It is bounded by the fact that every unbounded spender Firecrawl ships today -
    # crawl, agent, the monitors - is named here.
    "mcp(firecrawl/firecrawl_crawl)",            # 1 credit PER PAGE, no ceiling
    "mcp(firecrawl/firecrawl_agent)",            # caps at 2500 credits per job
    "mcp(firecrawl/firecrawl_extract)",
    "mcp(firecrawl/firecrawl_parse)",
    "mcp(firecrawl/firecrawl_search)",           # free equivalents exist
    "mcp(firecrawl/firecrawl_interact)",         # 2 credits per browser-MINUTE
    "mcp(firecrawl/firecrawl_interact_stop)",
    "mcp(firecrawl/firecrawl_monitor_create)",   # recurring spend with nobody watching
    "mcp(firecrawl/firecrawl_monitor_update)",
    "mcp(firecrawl/firecrawl_monitor_run)",
    "mcp(firecrawl/firecrawl_research_search_papers)",
    "mcp(firecrawl/firecrawl_research_search_github)",
    "mcp(firecrawl/firecrawl_research_related_papers)",
    "mcp(firecrawl/firecrawl_research_read_paper)",
    "mcp(firecrawl/firecrawl_research_inspect_paper)",

    # Named, because their servers stay allowed: each is a tool a research reviewer has no reason
    # to call, and a denial is free.
    "mcp(jina-mcp-server/show_api_key)",     # prints the account's own API key into the answer
    "mcp(cloakbrowser/cloak_evaluate)",      # arbitrary JS in a real browser
    "mcp(cloakbrowser/cloak_set_cookies)",   # session material, inbound
    "mcp(cloakbrowser/cloak_get_cookies)",   # session material, outbound
    "mcp(cloakbrowser/cloak_network_intercept)",
    "mcp(cloakbrowser/cloak_network_continue)",
]

# The shell rule is separated so --keep-shell can drop exactly it, and so the checker below can
# report the two halves independently instead of as one opaque "stale".
SHELL_DENY = "command(*)"


def load(path=SETTINGS):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def missing(cfg, keep_shell=False):
    """What this script would add. The single source of truth for --check and for doctor.py."""
    perms = cfg.get("permissions") or {}
    allow = perms.get("allow") or []
    deny = perms.get("deny") or []
    want_deny = [d for d in DENY if not (keep_shell and d == SHELL_DENY)]
    return ([r for r in ALLOW if r not in allow],
            [r for r in want_deny if r not in deny])


def newest_backup():
    d = os.path.dirname(SETTINGS)
    if not os.path.isdir(d):
        return None
    b = sorted(x for x in os.listdir(d) if x.startswith("settings.json.bak."))
    return os.path.join(d, b[-1]) if b else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    ap.add_argument("--revert", action="store_true", help="restore the newest backup")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if rules are missing; prints them. Writes nothing.")
    ap.add_argument("--keep-shell", action="store_true",
                    help="do not add deny command(*) - keeps the interactive TUI's shell, and "
                         "keeps the headless failure it causes")
    a = ap.parse_args()

    if a.revert:
        b = newest_backup()
        if not b:
            print("no backup found next to " + SETTINGS)
            return 1
        shutil.copy2(b, SETTINGS)
        print("restored from " + b)
        return 0

    # 🔴 "No settings file" and "settings file missing the rules" are DIFFERENT states, and
    # conflating them is a false positive aimed at people who are not Igor. Most employees who
    # pull this kit have never installed agy; without this branch `--check` tells them their
    # config is STALE and hands them a command to fix software they do not have. A gate that
    # cries wolf on a clean machine is how the whole class gets ignored.
    if not os.path.exists(SETTINGS):
        if a.check:
            print("agy permissions: agy is not installed on this machine (no %s) - nothing to do"
                  % SETTINGS)
            return 0
        print("agy is not installed here: %s does not exist.\n"
              "Nothing was changed. Install Antigravity CLI first, then re-run this script."
              % SETTINGS)
        return 0

    cfg = load()
    add_a, add_d = missing(cfg, a.keep_shell)

    if a.check:
        if not add_a and not add_d:
            print("agy permissions: current")
            return 0
        print("agy permissions: STALE - %d allow, %d deny rule(s) missing from %s"
              % (len(add_a), len(add_d), SETTINGS))
        for r in add_a + add_d:
            print("    " + r)
        print("  fix: python %s" % os.path.abspath(__file__))
        return 1

    perms = cfg.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    deny = perms.setdefault("deny", [])

    print("settings: %s" % SETTINGS)
    print("  existing allow: %d rules, deny: %d rules" % (len(allow), len(deny)))
    print("  + %d allow rules, + %d deny rules" % (len(add_a), len(add_d)))
    for r in add_a:
        print("      allow  " + r)
    for r in add_d:
        print("      deny   " + r)
    if SHELL_DENY in add_d:
        print("  NOTE: `deny command(*)` also stops shell commands in the INTERACTIVE agy TUI, "
              "because this file is machine-wide. Re-run with --keep-shell to skip that one rule, "
              "or --revert to undo everything.")
    if not add_a and not add_d:
        print("  nothing to do - already current")
        return 0
    if a.dry_run:
        print("  --dry-run: nothing written")
        return 0

    bak = SETTINGS + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, bak)
        print("  backup -> " + bak)

    allow.extend(add_a)
    deny.extend(add_d)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS)   # atomic: a half-written settings.json locks the CLI out
    print("  written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
