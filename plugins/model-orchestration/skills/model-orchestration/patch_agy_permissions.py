#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patch_agy_permissions.py - make headless `agy` runs survivable, and close the Firecrawl hole.

WHY THIS EXISTS (measured 2026-07-31, agy 1.1.9)
------------------------------------------------
In headless (`-p`) mode agy cannot show a permission prompt, so any tool left at the default
"ask" is auto-denied - and a single auto-denial DISCARDS THE ENTIRE RUN. Observed: the model
made 29 successful tool calls (10 web searches, 6 page fetches), then reached for
`mcp(jina-mcp-server/read_url)`, was denied, and the CLI returned:

    response = ""      status = "SUCCESS"      exit code = 0

Nothing in the exit code or the status field distinguishes that from a good run. Every
orchestration round that used the agy channel for research was exposed to this.

Second, independent problem: agy has the FULL Firecrawl toolset registered
(`~/.gemini/config/mcp_config.json`), including `firecrawl_crawl` (1 credit PER PAGE,
unbounded), `firecrawl_agent` and `firecrawl_monitor_create` (recurring, autonomous spend).
orchestrate.py denies exactly those tools to Codex and has never denied them to agy.

WHAT IT DOES
------------
Adds to `~/.gemini/antigravity-cli/settings.json`:
  * allow-rules for the read-only, free web tools a reviewer legitimately needs;
  * deny-rules for every metered or unbounded Firecrawl tool (deny beats allow in agy's
    precedence order: deny > ask > allow).
Both lists are additive and idempotent - existing user rules are preserved, order is stable.
A timestamped backup is written next to the file before the first change.

    python patch_agy_permissions.py --dry-run     # show the diff, change nothing
    python patch_agy_permissions.py               # apply
    python patch_agy_permissions.py --revert      # restore the newest backup
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

# 🔴🔴 R56 2026-08-19 — THREE STATES, NOT TWO, AND THE HARMLESS-SOUNDING ONE IS THE FATAL ONE.
# Measured on agy 1.1.16 by running each case (runs/r56/permprobe/results.json):
#
#   allowed            the tool runs.
#   explicitly DENIED  the tool returns an ordinary error - «Permission denied for
#                      mcp(jina-mcp-server/search_web). Matches user-configured deny rule» -
#                      and THE MODEL CARRIES ON AND FINISHES. Arm 3 still answered.
#   neither (UNLISTED) `Print mode: soft-denying tool confirmation "CallMcpTool" at step N`,
#                      status CANCELED, THE WHOLE TURN IS DISCARDED.
#
# So a deny is cheap and silence is what costs a round. The old list below was an ENUMERATION of
# tool names against somebody else's server, which means the default state for anything the
# server gains later is the fatal one. That is not hypothetical and it is not new: the docstring
# above records 2026-07-31 losing a run to `jina-mcp-server/read_url`, fixed by adding the name -
# and on 2026-08-19 an agy31pro run died at 56 s, 3 898 output tokens and 8 searches thrown away,
# on `jina-mcp-server/search_web_deep`. Same server, same shape, nineteen days apart. A list that
# has to keep pace with an upstream server is a treadmill, and every lap costs a whole review.
#
# `mcp(<server>/*)` IS honoured (arm 2), and deny still beats it (arm 3). So: wildcard the servers
# whose whole toolset is free, local and read-only, and keep an enumeration exactly where a wrong
# guess costs money or leaks a credential.
#
# NOT wildcarded, deliberately:
#   firecrawl   metered per page, no ceiling on firecrawl_crawl. A new Firecrawl tool must cost a
#               cancelled run rather than an unbounded bill.
#   playwright  runs against a PERSISTENT profile holding live logins, and ships
#               browser_run_code_unsafe. A research reviewer has no business there.
ALLOW = [
    "read_url(*)",
    "mcp(jina-mcp-server/*)",   # free, read-only search/fetch; show_api_key denied below
    "mcp(crawl4ai/*)",          # local process, no network cost beyond the fetch itself
    "mcp(scrapling/*)",         # local process
    "mcp(cloakbrowser/*)",      # local browser; the scripting tools are denied below
    "mcp(firecrawl/firecrawl_scrape)",   # 1 credit, markdown only - the sanctioned last resort
    "mcp(firecrawl/firecrawl_map)",      # 1 credit flat for any number of URLs
]

# Metered, unbounded, or recurring. Costs verified on docs.firecrawl.dev 2026-07-26 and already
# enforced for the Codex channel in orchestrate.py; this brings agy to parity.
DENY = [
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

    # 🔴 R56: these become REACHABLE the moment their server is wildcarded above, so they have to
    # be named here in the same change. Denying them is not a cost - a denied tool returns an
    # error and the run continues (measured, see the ALLOW comment); it is the unlisted state
    # that kills a run. So the deny list is where a wildcard's blast radius gets cut back, and
    # every entry below is a tool a *research reviewer* has no reason to call.
    "mcp(jina-mcp-server/show_api_key)",     # prints the account's own API key into the answer
    "mcp(cloakbrowser/cloak_evaluate)",      # arbitrary JS in a real browser
    "mcp(cloakbrowser/cloak_set_cookies)",   # session material
    "mcp(cloakbrowser/cloak_get_cookies)",   # session material, outbound
    "mcp(cloakbrowser/cloak_network_intercept)",
    "mcp(cloakbrowser/cloak_network_continue)",
]


def load():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS, encoding="utf-8") as f:
        return json.load(f)


def newest_backup():
    d = os.path.dirname(SETTINGS)
    b = sorted(x for x in os.listdir(d) if x.startswith("settings.json.bak."))
    return os.path.join(d, b[-1]) if b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    if a.revert:
        b = newest_backup()
        if not b:
            print("no backup found next to " + SETTINGS)
            return 1
        shutil.copy2(b, SETTINGS)
        print("restored from " + b)
        return 0

    cfg = load()
    perms = cfg.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    deny = perms.setdefault("deny", [])

    added_a = [r for r in ALLOW if r not in allow]
    added_d = [r for r in DENY if r not in deny]

    print("settings: %s" % SETTINGS)
    print("  existing allow: %d rules, deny: %d rules" % (len(allow), len(deny)))
    print("  + %d allow rules, + %d deny rules" % (len(added_a), len(added_d)))
    for r in added_a:
        print("      allow  " + r)
    for r in added_d:
        print("      deny   " + r)
    if not added_a and not added_d:
        print("  nothing to do - already patched")
        return 0
    if a.dry_run:
        print("  --dry-run: nothing written")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = SETTINGS + ".bak." + stamp
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, bak)
        print("  backup -> " + bak)

    allow.extend(added_a)
    deny.extend(added_d)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS)   # atomic: a half-written settings.json locks the CLI out
    print("  written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
