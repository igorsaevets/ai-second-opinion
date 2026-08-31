"""Insert a Firecrawl `disabled_tools` deny-list into ~/.codex/config.toml.

SAFETY CONTRACT. That file holds third-party API keys. This script reads it into memory to
edit it and **never prints any of its content** -- not on success, not in an error path.
Everything it emits is a boolean, a count, or a string this file itself contains.

Idempotent: if `disabled_tools` is already present in the firecrawl section, it changes
nothing and says so. Aborts without writing if the section is missing, duplicated, or written
as an inline table, rather than guessing and corrupting the file.

Usage:  python patch_codex_firecrawl.py [--apply]
Without --apply it is a dry run.
"""

import os
import re
import shutil
import sys

# tomllib is 3.11+ stdlib while the repo's documented floor is 3.8 (R74; agy37flash +
# goog37flash, R73: the bare import was an instant ModuleNotFoundError on 3.8-3.10 with no
# hint). This is optional local tooling, so the honest failure is a sentence, not a traceback.
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib      # the PyPI backport, if the user happens to have it
    except ModuleNotFoundError:
        sys.exit("patch_codex_firecrawl.py needs Python 3.11+ (stdlib tomllib) or "
                 "`pip install tomli` on 3.8-3.10. Python running now: %s."
                 % sys.version.split()[0])

CONFIG = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")

DENY = [
    # search family: free equivalents exist (built-in web search, tavily, exa, jina)
    "firecrawl_search",
    "firecrawl_research_search_papers", "firecrawl_research_search_github",
    "firecrawl_research_related_papers", "firecrawl_research_read_paper",
    "firecrawl_research_inspect_paper",
    # unbounded per call
    "firecrawl_crawl",        # 1 credit PER PAGE, no ceiling
    "firecrawl_agent",        # default cap 2500 credits per job
    "firecrawl_extract",      # token-metered
    "firecrawl_parse",        # cost undocumented
    # recurring and autonomous: docs' own example is 2880 credits/month for one URL
    "firecrawl_monitor_create", "firecrawl_monitor_update", "firecrawl_monitor_run",
    # 2 credits per browser-minute; playwright and cloakbrowser are free
    "firecrawl_interact", "firecrawl_interact_stop",
]

BLOCK = (
    "# Firecrawl credit policy, added 2026-07-26. Costs verified on docs.firecrawl.dev.\n"
    "# Kept usable: firecrawl_scrape (1 credit) and firecrawl_map (1 credit flat, any number\n"
    "# of URLs), plus the read-only status and monitor_list/get/delete tools.\n"
    "# Delete this key to restore the full 26-tool surface.\n"
    "disabled_tools = [\n"
    + "".join('  "%s",\n' % t for t in DENY)
    + "]\n"
)

SECTION = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?firecrawl"?\s*\]\s*$')


def fail(msg):
    print("  ABORT: " + msg)
    sys.exit(1)


def main():
    apply_it = "--apply" in sys.argv
    if not os.path.exists(CONFIG):
        fail("config.toml not found at the expected path")

    with open(CONFIG, encoding="utf-8") as fh:
        lines = fh.readlines()

    # Parse first, so we never edit a file that was already broken.
    with open(CONFIG, "rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            fail("config.toml does not parse as TOML (line %s). Not touching it." %
                 getattr(exc, "lineno", "?"))

    servers = data.get("mcp_servers", {})
    if "firecrawl" not in servers:
        fail("no [mcp_servers.firecrawl] entry exists")
    existing = servers["firecrawl"].get("disabled_tools")
    if existing is not None:
        print("  already present: disabled_tools has %d entries. Nothing to do." % len(existing))
        missing = [t for t in DENY if t not in existing]
        if missing:
            print("  NOTE: %d of the intended names are absent from it: %s"
                  % (len(missing), ", ".join(missing)))
        return

    hits = [i for i, ln in enumerate(lines) if SECTION.match(ln)]
    if len(hits) != 1:
        fail("expected exactly one [mcp_servers.firecrawl] header line, found %d. "
             "It may be an inline table; edit by hand." % len(hits))
    at = hits[0] + 1

    print("  section header found at line %d" % (hits[0] + 1))
    print("  will insert %d denied tool names (%d lines)" % (len(DENY), BLOCK.count("\n")))
    if not apply_it:
        print("  DRY RUN. Re-run with --apply to write.")
        return

    backup = CONFIG + ".bak-2026-07-26"
    shutil.copy2(CONFIG, backup)
    print("  backup written: %s (%d bytes)" % (backup, os.path.getsize(backup)))

    new = lines[:at] + [BLOCK] + lines[at:]
    with open(CONFIG, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(new)

    # Re-validate. On any failure, roll straight back.
    with open(CONFIG, "rb") as fh:
        try:
            after = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            shutil.copy2(backup, CONFIG)
            fail("result did not parse as TOML. Restored from backup, file is unchanged.")

    got = after.get("mcp_servers", {}).get("firecrawl", {}).get("disabled_tools")
    if got != DENY:
        shutil.copy2(backup, CONFIG)
        fail("round-trip mismatch. Restored from backup, file is unchanged.")

    # Prove nothing else moved: same server set, same key count on the firecrawl entry.
    before_keys = set(servers["firecrawl"].keys())
    after_keys = set(after["mcp_servers"]["firecrawl"].keys()) - {"disabled_tools"}
    print("  OK. disabled_tools now has %d entries and round-trips exactly." % len(got))
    print("  server list unchanged : %s" % (set(servers) == set(after["mcp_servers"])))
    print("  firecrawl other keys unchanged : %s" % (before_keys == after_keys))
    print("  total mcp_servers : %d" % len(after["mcp_servers"]))


if __name__ == "__main__":
    main()
