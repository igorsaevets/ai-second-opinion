#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_json_dup_keys.py - fail if any JSON file has duplicate object keys at any level.

Why this exists: `json.loads` silently collapses duplicate keys, taking the LAST one. So a file
that looks like it has both {"foo": 1, "foo": 2} parses as {"foo": 2} and no parser will tell you
about the first foo. Round 33 (2026-08-08): my Edit added SUPERSEDED-prefixed notes at the top of
a JSON block while the old copies still existed below; the old ones won on parse. Codex found it
live; nothing before it did. Standard `check-json` from pre-commit-hooks does NOT catch this
(issue #554 open since 2019).

Uses `object_pairs_hook` so the check fires on every object, not just the top level.

Usage:
    python check_json_dup_keys.py FILE [FILE ...]

Exit codes:
    0  every file is clean
    1  at least one file has a duplicate key (details printed to stderr)
    2  a file could not be read or parsed as JSON at all (a separate signal from a dup key)

Runs on Windows and POSIX. No third-party dependencies.
"""
import json
import sys
from pathlib import Path


class DuplicateKeyError(ValueError):
    """Raised by the object_pairs_hook when it sees a repeated key inside one object."""


def _no_dup_pairs(pairs):
    """object_pairs_hook: build a dict but raise on any repeated key at THIS object's level."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise DuplicateKeyError(k)
        seen[k] = v
    return seen


def check_file(path):
    """Return None if clean, or a human-readable error string."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return ("unreadable", "cannot read %s: %r" % (path, e))
    try:
        json.loads(text, object_pairs_hook=_no_dup_pairs)
    except DuplicateKeyError as e:
        return ("dup", "duplicate key %r" % (e.args[0],))
    except json.JSONDecodeError as e:
        return ("badjson", "invalid JSON at line %d col %d: %s" % (e.lineno, e.colno, e.msg))
    return None


def main(argv):
    if len(argv) < 2:
        print("usage: check_json_dup_keys.py FILE [FILE ...]", file=sys.stderr)
        return 2
    dup_or_bad = False
    unreadable = False
    for path in argv[1:]:
        result = check_file(path)
        if result is None:
            continue
        kind, msg = result
        print("%s: %s" % (path, msg), file=sys.stderr)
        if kind == "unreadable":
            unreadable = True
        else:
            dup_or_bad = True
    if dup_or_bad:
        return 1
    if unreadable:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
