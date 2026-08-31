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
import http.client
import ipaddress
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit


def _resolve_line(url, enabled):
    if not enabled:
        return
    r = resolve_federal_register(url)
    if r:
        print("             -> %-16s %s" % r)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Bracketed-IPv6 alternative first, then the general form - kept in step with orchestrate.py's
# _URL_RE (R74; goog36flash, R73: orchestrate gained the bracket form and this copy did not, so
# a standalone run truncated IPv6 URLs at the first `]`).
URL_RE = re.compile(r"https?://\[[0-9A-Fa-f:.]+\][^\s)\]>\"'`|]*"
                    r"|https?://[^\s)\]>\"'`|]+")


def normalise(u):
    """Compare on host+path only: tracking params and trailing slashes are not differences.

    Never raises (R74): urlsplit throws ValueError on malformed IPv6 brackets, and the URLs
    this runs on are model-emitted text - one hostile-shaped citation must not kill the audit
    of all the others. A URL that cannot be split is returned as an unmatchable degenerate key.
    """
    try:
        s = urlsplit(u.rstrip(".,;:"))
    except ValueError:
        return (u.lower(), "\x00unparseable")
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
    # R74 (orgemini37flash, R73): the required-hits floor must never exceed what the slug can
    # supply. `max(2, ...)` demanded 2 hits of a single-word slug (`.../inadmissibility`),
    # which can produce at most 1 - every such real citation was branded WRONG DOCUMENT. And
    # a slug with NO comparable words cannot be judged at all; saying either verdict would lie.
    if not slug_words:
        return ("UNRESOLVED", "%s = %r - slug has no comparable words" % (doc, title))
    need = min(len(slug_words), max(2, len(slug_words) // 2))
    hit = len(slug_words & title_words)
    verdict = "TITLE MATCHES" if hit >= need else "WRONG DOCUMENT"
    return (verdict, "%s = %r (%s, %s)" % (doc, title, d.get("citation"),
                                           d.get("publication_date")))


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_PRIVATE = re.compile(r"^(?:localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|\[?::1\]?$)")


def _resolve_public(host, port=None):
    """getaddrinfo ONCE; refuse unless EVERY answer is public. (addresses, None) or (None, reason).

    R74 closed the «never resolved at all» half of this fence (a string regex let
    `localtest.me` and decimal IPs through). R75 closes the other half: the addresses
    returned here are what _pinned_request actually CONNECTS to, so there is no second
    resolution for a TTL-0 record to rebind. An address getaddrinfo returns but ipaddress
    cannot parse is a refusal, not a skip - an unparseable address cannot be vetted, and
    for a pin «could not check» must never mean «allowed».
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        return None, "DNS failed (%s)" % (getattr(e, "strerror", None) or e)
    addrs = []
    for info in infos:
        raw = str(info[4][0]).split("%")[0]     # strip IPv6 zone index
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return None, "unparseable address %r" % raw
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return None, "resolves to non-public address %s" % ip
        addrs.append(raw)
    if not addrs:
        return None, "DNS returned no addresses"
    return addrs, None


def _host_resolves_public(host):
    """Refusal reason if the host resolves to anything non-public, else None.

    Kept as the name the tests and older call sites know; the resolution itself moved into
    _resolve_public so that the checker and the pin draw from ONE implementation.
    """
    return _resolve_public(host)[1]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS to a PRE-RESOLVED address; SNI and the certificate check stay on the HOSTNAME.

    http.client is handed the vetted IP, so it has nothing left to resolve; the server
    still sees the right SNI and the certificate is validated against the original host,
    not the IP - a pin that broke TLS validation would trade one hole for another.
    """

    def __init__(self, ip, port, server_hostname, timeout):
        http.client.HTTPSConnection.__init__(
            self, ("[%s]" % ip) if ":" in ip else ip, port,
            timeout=timeout, context=ssl.create_default_context())
        self._server_hostname = server_hostname

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock,
                                              server_hostname=self._server_hostname)


def _pinned_conn(scheme, host, ip, port, timeout):
    """The socket seam, one function so tests can intercept it without touching sockets."""
    if scheme == "https":
        return _PinnedHTTPSConnection(ip, port, host, timeout)
    return http.client.HTTPConnection(("[%s]" % ip) if ":" in ip else ip, port,
                                      timeout=timeout)


def _pinned_request(url, *, method="GET", timeout=20, headers=None, body_cap=1_048_576):
    """One HTTP(S) exchange over a connection PINNED to a vetted address. Follows nothing.

    THE POINT (R75; codex + goog36flash + orgemini37flash, R73, independently): resolving a
    hostname to check it and then letting urllib open the URL is a time-of-check/time-of-use
    race - a TTL-0 DNS record can answer public to the check and 127.0.0.1 or
    169.254.169.254 to urllib's own second resolution at connect time. Here the socket
    connects to the exact address that passed the check, so a rebinding answer has nothing
    left to rebind. Redirects are NOT followed: every caller loops itself and re-enters this
    function, which re-checks and re-pins each hop. Ambient proxy variables are deliberately
    not honoured - an SSRF fence that hands the URL to a proxy delegates the one decision it
    exists to make.

    Returns (status, header_message, body_bytes, pinned_ip); the body is read to at most
    body_cap+1 bytes so a caller can detect overflow. Raises ValueError for refusals
    (scheme, credentials-in-URL, DNS, non-public address) and urllib.error.HTTPError for
    any status >= 400, so existing except-clauses keep their meaning.
    """
    s = urlsplit(url)
    if s.scheme not in ("http", "https"):
        raise ValueError("scheme %r is not allowed; only http and https" % s.scheme)
    host = s.hostname
    if not host:
        raise ValueError("no host in URL")
    if s.username or s.password:
        raise ValueError("credentials in a URL are refused")
    port = s.port or (443 if s.scheme == "https" else 80)
    addrs, bad = _resolve_public(host, port)
    if bad:
        raise ValueError(bad)
    pin = addrs[0]
    conn = _pinned_conn(s.scheme, host, pin, port, timeout)
    default_port = 443 if s.scheme == "https" else 80
    hdrs = {"Host": host if port == default_port else "%s:%d" % (host, port),
            "User-Agent": UA, "Connection": "close"}
    for k, v in (headers or {}).items():
        hdrs[k] = v
    path = (s.path or "/") + (("?" + s.query) if s.query else "")
    try:
        # An explicit Host header suppresses http.client's own (which would name the IP).
        conn.request(method, path, headers=hdrs)
        resp = conn.getresponse()
        body = b"" if method == "HEAD" else resp.read(body_cap + 1)
        status, msg, reason = resp.status, resp.msg, resp.reason
    finally:
        conn.close()
    if status >= 400:
        raise urllib.error.HTTPError(url, status, reason, msg, None)
    return status, msg, body, pin


def probe_url(u, timeout=20):
    """
    Does this URL exist? Returns (verdict, detail).

    Verdicts are deliberately asymmetric, because the failure modes are asymmetric:

        LIVE     200 and the final URL is the cited one
        MOVED    200 after a redirect - the page is real, the citation is stale
        DEAD     404/410 - the page does not exist. This is the one that means fabrication.
        BLOCKED  401/403/429 - a bot wall. Says NOTHING about whether the page exists.
        UNKNOWN  anything else, including a redirect chain longer than this client follows

    BLOCKED and UNKNOWN must never be reported as fabrication. Inferring "it is fake" from "I
    could not check" is precisely the move this harness forbids the models to make, and it would
    be worse coming from the harness, which is the thing that is supposed to be trustworthy.

    Only public http(s) hosts are probed. A cited URL is attacker-controlled text as far as this
    process is concerned - a model can emit http://127.0.0.1:8080/admin - so loopback and RFC1918
    targets are refused rather than fetched. R74: refused at EVERY hop, with DNS resolution -
    the old check matched a regex against the first hostname string only (codex + agy37flash +
    goog37flash, R73, independently). R75: the connection itself is PINNED to the vetted
    address via _pinned_request, closing the check-then-reconnect rebinding race the R74 fix
    documented as still open. Redirects are followed manually, each hop re-checked and re-pinned.
    """
    try:
        cur = u
        for _hop in range(6):
            s = urlsplit(cur)
            if s.scheme not in ("http", "https"):
                return "SKIPPED", "not http(s)" + ("" if cur == u else " after redirect")
            host = (s.hostname or "").lower()
            if not host or _PRIVATE.match(host):
                return "SKIPPED", "non-public host" + ("" if cur == u else " after redirect")
            try:
                st, hdrs, _body, _ip = _pinned_request(cur, method="GET",
                                                       timeout=timeout, body_cap=0)
            except ValueError as refuse:
                return "SKIPPED", str(refuse) + ("" if cur == u else " (after redirect)")
            if st in (301, 302, 303, 307, 308):
                loc = hdrs.get("Location")
                if not loc:
                    return "UNKNOWN", "HTTP %d with no Location header" % st
                cur = urljoin(cur, loc)        # a relative Location is legal
                continue
            if cur.split("?")[0].rstrip("/") == u.split("?")[0].rstrip("/"):
                return "LIVE", ""
            return "MOVED", "-> " + cur
        return "UNKNOWN", "more than 5 redirects"
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "DEAD", "HTTP %d" % e.code
        if e.code in (401, 403, 429):
            return "BLOCKED", "HTTP %d - existence not established" % e.code
        return "UNKNOWN", "HTTP %d" % e.code
    except Exception as e:
        return "UNKNOWN", type(e).__name__


WRAPPER_MARK = "grounding-api-redirect"


def resolve_wrapper(u, timeout=15):
    """
    Turn one vertexaisearch grounding-api-redirect wrapper into the publisher URL it points at.

    Returns the URL, or None if this is not a wrapper or Google did not answer.

    WHY THIS IS SEPARATE FROM probe_url, AND WHY IT STOPS AT THE FIRST HOP. Two different
    questions ride on one wrapper - "which page is this?" and "does that page exist?" - and they
    have different failure modes. Measured 2026-08-08: probing a wrapper end to end recovered
    en.wikipedia.org fine and TIMED OUT on a uefa.com wrapper, losing BOTH answers, when the
    first hop had already carried the URL. Google's redirector answers a 302 in milliseconds; the
    publisher behind it may be slow, walled, or down, and none of that should cost us the
    identity of the source. So: resolve here, probe separately, and let the slow half fail alone.

    A wrapper is otherwise unreadable - an opaque token - so this is the difference between a
    citation a human can check and a citation they cannot. The channel that emits them was
    written off as unauditable for two rounds on the strength of a sentence that was true about
    the OTHER question.

    R75: rides _pinned_request like every other model-influenced URL here - the wrapper host
    is Google's today, but the string arrives in model output and earns no exemption.
    """
    if not u or WRAPPER_MARK not in u:
        return None
    try:
        st, hdrs, _body, _ip = _pinned_request(u, timeout=timeout, body_cap=0)
    except Exception:
        return None
    if st in (301, 302, 303, 307, 308):
        loc = hdrs.get("Location")
        return urljoin(u, loc) if loc else None
    return None


def resolve_wrappers(urls, timeout=15):
    """
    resolve_wrapper over a list, returning {wrapper: publisher}. Only successes are keyed.

    Paced with a FRESH random interval per request, never a fixed one: this is a loop against a
    single Google host, and a metronome is a fingerprint where human traffic has variance. The
    range is small because this host just handed us these URLs and the whole point is a cheap
    first hop - the machine-wide ceiling is 11 s and nothing here needs to approach it.
    """
    out = {}
    for i, u in enumerate(urls):
        if i:
            time.sleep(random.uniform(0.3, 1.2))
        pub = resolve_wrapper(u, timeout=timeout)
        if pub:
            out[u] = pub
    return out


def resolve_all(urls, workers=10):
    """Probe in parallel; ordering preserved. Falls back to serial if threads are unavailable."""
    try:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(probe_url, urls))
    except Exception:
        return [probe_url(u) for u in urls]


def report_url_resolution(cited_raw):
    """
    Print the existence check and return the number of DEAD citations.

    This is the ONLY mechanical citation check that works on the Codex channel, which reports no
    tool telemetry at all - there is no event log to compare against, so "was it opened" is
    unanswerable and "does it exist" is all that is left. Measured on the 2026-07-31 probe run:
    agy 3 dead of 11, Spark 0 of 22, Codex 1 of 32 - and that single Codex one was a GitHub API
    query for a release tag that does not exist, i.e. the 404 was the answer it wanted, not a
    fabrication. Counting is not judging; read the list.
    """
    if not cited_raw:
        return 0
    print("\nURL existence check (%d cited URL(s)):" % len(cited_raw))
    results = resolve_all(cited_raw)
    tally = {}
    dead = 0
    for u, (v, detail) in zip(cited_raw, results):
        tally[v] = tally.get(v, 0) + 1
        if v in ("DEAD", "UNKNOWN", "MOVED"):
            print("  %-8s %-72s %s" % (v, u[:72], detail[:60]))
        if v == "DEAD":
            dead += 1
    print("  " + "  ".join("%s=%d" % (k, tally[k]) for k in sorted(tally)))
    if dead:
        print("  %d cited URL(s) do not exist. A citation to a 404 was not read - it was "
              "constructed." % dead)
    return dead


def main():
    ap = argparse.ArgumentParser()
    # Optional: Codex ships no event log, so --answer alone with --resolve-urls is a valid run
    # and is the only check available for that channel.
    ap.add_argument("ndjson", nargs="?")
    ap.add_argument("--answer")
    ap.add_argument("--resolve", action="store_true",
                    help="resolve federalregister.gov document numbers against the public API")
    ap.add_argument("--resolve-urls", action="store_true",
                    help="fetch every cited URL and report which ones do not exist. Works "
                         "without an event log, so it covers channels with no telemetry")
    a = ap.parse_args()

    if not a.ndjson:
        if not a.answer:
            ap.error("give an event log, or --answer FILE (optionally with --resolve-urls)")
        text = open(a.answer, encoding="utf-8", errors="replace").read()
        cited_raw, seen = [], set()
        for m in URL_RE.finditer(text):
            n = normalise(m.group())
            if n not in seen:
                seen.add(n)
                cited_raw.append(m.group().rstrip(".,;:"))
        print("no event log given - grounding cannot be checked, only existence.")
        print("answer cites %d distinct URL(s)" % len(cited_raw))
        report_url_resolution(cited_raw if a.resolve_urls else [])
        if not a.resolve_urls:
            print("pass --resolve-urls to check whether those URLs exist.")
        return 0

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

    # Wrapper citations resolve to their publisher URL BEFORE grounding is judged (R74;
    # agy36flash, R73: orchestrate.py calls resolve_wrappers and this standalone entry point
    # never did, so a Google grounding-api-redirect citation was branded UNVERIFIED against
    # the very publisher page the event log shows was opened).
    wrapped = resolve_wrappers([raw for raw, _ in cited if WRAPPER_MARK in raw])
    grounded, unopened = [], []
    for raw, n in cited:
        pub = wrapped.get(raw)
        shown = raw + ((" -> " + pub) if pub else "")
        if n in opened_ok or (pub and normalise(pub) in opened_ok):
            grounded.append(shown)
        else:
            unopened.append((shown, n in opened_any))

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

    if a.resolve_urls:
        report_url_resolution([raw for raw, _ in cited])
    return 0


if __name__ == "__main__":
    sys.exit(main())
