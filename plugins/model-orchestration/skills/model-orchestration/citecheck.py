#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
citecheck.py - compare the URLs an answer CITES against the URLs the run actually OPENED.

    python citecheck.py RUN.ndjson [--answer RUN.answer.md]

WHY
---
Tool telemetry proves activity, not grounding. Measured 2026-07-31 on a real run: the model
made 23 successful search/fetch calls and cited

    federalregister.gov/documents/2022/09/09/2022-18867/public-charge-ground-of-inadmissibility

which is the correct rule (87 FR 55472). But the URL it actually opened was ...2022-19286...,
which is "Amendment of United States Area Navigation (RNAV) Route T-232; Fairbanks, AK" - an
FAA notice about an Alaskan air route. The right citation did not come from the page it read.
It came from memory, and the fetch log made it look researched.

A citation whose URL never appears in the run's own event log is not evidence. It may still be
correct - as here - but it was not verified, and it must not be reported as if it were.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def _resolve_line(url, enabled):
    if not enabled:
        return
    r = resolve_federal_register(url)
    if r:
        print("             -> %-16s %s" % r)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"https?://[^\s)\]>\"'`|]+")


def normalise(u):
    """Compare on host+path only: tracking params and trailing slashes are not differences."""
    s = urlsplit(u.rstrip(".,;:"))
    path = (s.path or "/").rstrip("/")
    return (s.netloc.lower().replace("www.", ""), path.lower())


def opened_urls(ndjson):
    """Every URL the run actually passed to a fetch tool, plus every search query it issued."""
    urls, queries = [], []
    with open(ndjson, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            body = ev.get(kind) if isinstance(ev.get(kind), dict) else {}
            ti = body.get("tool_info") or {}
            p = ti.get("parameters") or {}
            if ti.get("name") == "call_mcp_tool":
                p = p.get("Arguments") or p
            for key in ("url", "Url", "URL"):
                if p.get(key):
                    urls.append((str(p[key]), body.get("state") != "ERROR"))
            for key in ("query", "Query", "search_term"):
                if p.get(key):
                    queries.append(str(p[key]))
    return urls, queries


def answer_text(ndjson, explicit=None):
    if explicit and os.path.exists(explicit):
        return open(explicit, encoding="utf-8", errors="replace").read()
    with open(ndjson, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("{") and '"result"' in line:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "result":
                    return (ev.get("result") or {}).get("response") or ""
    return ""


FR_DOC_RE = re.compile(r"federalregister\.gov/documents/\d{4}/\d{2}/\d{2}/([0-9]{4}-[0-9]+)/([a-z0-9-]+)")


def resolve_federal_register(url):
    """
    "Opened" is not "the page said what the answer claims" - and that gap is not theoretical.
    Measured 2026-07-31, all four in one session, every one of them a real-looking FR citation
    under the correct article slug:

        2022-19286 -> "Amendment of ... (RNAV) Route T-232; Fairbanks, AK"
        2022-19422 -> "Proposed Collection; Comment Request"
        2026-15432 -> "Agency Information Collection Activities..."
        2022-18869 -> "Information Collection Request; Direct Loan Making"

    The last one was marked GROUNDED by the check above, because the model really did fetch it -
    federalregister.gov serves the document for the NUMBER and ignores the slug, so a wrong
    number with a right slug returns HTTP 200 and looks fetched. Only resolving the number and
    comparing it to the slug catches that. The FR public API needs no key.

    Returns (verdict, detail) or None if this is not an FR document URL.
    """
    m = FR_DOC_RE.search(url)
    if not m:
        return None
    doc, slug = m.group(1), m.group(2)
    api = ("https://www.federalregister.gov/api/v1/documents/%s.json"
           "?fields[]=title&fields[]=citation&fields[]=publication_date" % doc)
    try:
        with urllib.request.urlopen(api, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return ("NO SUCH DOCUMENT", "%s -> HTTP %d" % (doc, e.code))
    except Exception as e:
        return ("UNRESOLVED", "%s -> %r" % (doc, e))
    title = (d.get("title") or "")
    # The slug is generated from the title, so a real match shares most of its words.
    slug_words = {w for w in slug.split("-") if len(w) > 3}
    title_words = {w.strip(".,;:()").lower() for w in title.split() if len(w) > 3}
    hit = len(slug_words & title_words)
    verdict = "TITLE MATCHES" if hit >= max(2, len(slug_words) // 2) else "WRONG DOCUMENT"
    return (verdict, "%s = %r (%s, %s)" % (doc, title, d.get("citation"),
                                           d.get("publication_date")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ndjson")
    ap.add_argument("--answer")
    ap.add_argument("--resolve", action="store_true",
                    help="resolve federalregister.gov document numbers against the public API")
    a = ap.parse_args()

    text = answer_text(a.ndjson, a.answer)
    urls, queries = opened_urls(a.ndjson)
    opened_ok = {normalise(u) for u, ok in urls if ok}
    opened_any = {normalise(u) for u, _ in urls}
    cited = []
    seen = set()
    for m in URL_RE.finditer(text):
        n = normalise(m.group())
        if n not in seen:
            seen.add(n)
            cited.append((m.group(), n))

    print("opened %d URL(s) (%d successfully), issued %d search queries"
          % (len(opened_any), len(opened_ok), len(queries)))
    print("answer cites %d distinct URL(s)\n" % len(cited))

    grounded, unopened = [], []
    for raw, n in cited:
        if n in opened_ok:
            grounded.append(raw)
        else:
            unopened.append((raw, n in opened_any))

    for u in grounded:
        print("  GROUNDED   %s" % u)
        _resolve_line(u, a.resolve)
    for u, attempted in unopened:
        print("  UNVERIFIED %s%s" % (u, "   (attempted but the fetch errored)" if attempted else ""))
        _resolve_line(u, a.resolve)

    print("\n%d/%d cited URLs were actually opened in this run." % (len(grounded), len(cited)))
    if unopened:
        print("The UNVERIFIED ones came from the model's own knowledge, not from a page it read.")
        print("They may still be correct - check them by hand before repeating them anywhere.")
    # Opened-but-never-cited is the other half of the picture: pages read and then ignored,
    # which is where a contradicting source quietly disappears.
    cited_set = {n for _, n in cited}
    dropped = [u for u, ok in urls if ok and normalise(u) not in cited_set]
    if dropped:
        print("\n%d page(s) were opened successfully but appear nowhere in the answer:" % len(dropped))
        for u in dict.fromkeys(dropped):
            print("    " + u[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
