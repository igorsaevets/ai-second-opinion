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

# Free and read-only. These are what a research reviewer actually needs; every one of them
# would otherwise be auto-denied in headless mode and take the whole run down with it.
ALLOW = [
    "read_url(*)",
    "mcp(jina-mcp-server/read_url)",
    "mcp(jina-mcp-server/parallel_read_url)",
    "mcp(jina-mcp-server/search_web)",
    "mcp(jina-mcp-server/parallel_search_web)",
    "mcp(jina-mcp-server/search_arxiv)",
    "mcp(jina-mcp-server/extract_pdf)",
    "mcp(crawl4ai/crawl_url)",
    "mcp(crawl4ai/crawl_clean)",
    "mcp(crawl4ai/crawl_many)",
    "mcp(crawl4ai/extract_links)",
    "mcp(scrapling/get)",
    "mcp(scrapling/fetch)",
    "mcp(scrapling/stealthy_fetch)",
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
