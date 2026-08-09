#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_json_dup_keys_hook.py - Claude Code PostToolUse wrapper for check_json_dup_keys.py.

WHAT IT IS
    A PostToolUse hook that fires after Edit/Write/MultiEdit and warns Claude (exit 2 + stderr)
    if the edited file is JSON with a duplicate key at any nesting level. The raw check lives
    in check_json_dup_keys.py in this same directory; this file is the thin wrapper that reads
    Claude Code's hook input from stdin, filters to .json files, and translates exit codes into
    the shape the Claude Code hook contract expects.

WHY IT IS A HOOK (not just a pre-commit check)
    pre-commit catches duplicate keys at `git commit`. Edits you never commit - or edit-fix-fix-
    then-commit - still hit R33's silent-overwrite class. This hook closes the window between an
    Edit and the next commit. It CANNOT undo the write (PostToolUse fires after the tool has
    already executed per the Claude Code hooks reference); its actual mode is detect-and-signal,
    not prevent.

WHY THE PLUGIN LOCATION (not machine-wide)
    Shipping the hook as `<plugin>/hooks/hooks.json` scopes it to sessions where THIS plugin is
    enabled - the R33 defense follows the tool, not the machine, and one buggy version of this
    file cannot block JSON edits in unrelated projects. See CHANGELOG 1.12.0 for the round-35
    panel's rejection of the machine-wide alternative.

FAIL-OPEN PRINCIPLE
    A safety-gate false positive teaches you to switch the guard off (round-33 rule, reinforced
    round-35). Every internal error path here exits 0 with no output rather than raising the
    hook error banner: script missing, path malformed, encoding trouble, unreadable file, all
    silent pass-through. The only failure signal we emit is "this file HAS a duplicate key" or
    "this file is not parseable JSON at all" - both real user-visible defects.

CLAUDE CODE HOOK CONTRACT (as of 2026-08-09, source: code.claude.com/docs/en/hooks)
    Input: JSON object on stdin with `tool_name`, `tool_input.file_path`, and other fields.
    Output:
        exit 0 : no decision, tool continues normally (default).
        exit 2 : "warning to Claude"; stderr is fed back to the model, tool result is not
                 blocked (PostToolUse has already run).
        other  : logged as a hook error; not what we want for detected dup keys.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The raw check lives beside this file. If it is absent for any reason (partial install,
# renamed, path juggling), we degrade to a no-op rather than emitting a hook error.
try:
    from check_json_dup_keys import check_file
except Exception:
    sys.exit(0)


def _read_stdin_json():
    """Read Claude Code hook input as bytes and decode UTF-8.

    Reading `sys.stdin` as TEXT is dead for non-ASCII: single-byte codecs never raise and
    characters silently change (measured 2026-08-01, curly quote as the trigger). Always
    read `stdin.buffer` and decode explicitly.
    """
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None


def main():
    data = _read_stdin_json()
    if not isinstance(data, dict):
        return 0

    # Only fire on file-write tools. The matcher in hooks.json already narrows this, but the
    # `if` filter is documented as best-effort and permission rules do not always normalize
    # backslash paths on Windows, so re-check here.
    if data.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0

    file_path = data.get("tool_input", {}).get("file_path", "") or ""
    if not isinstance(file_path, str) or not file_path:
        return 0

    # Extension filter: JSON only. .jsonl is line-delimited and its parser is not this one.
    if not file_path.lower().endswith(".json"):
        return 0

    # File may have been deleted or moved between the tool call and this hook firing.
    if not os.path.isfile(file_path):
        return 0

    try:
        result = check_file(file_path)
    except Exception:
        # Any internal error in the check itself is fail-open. A crash-storm on every JSON
        # edit in every session is exactly how you train yourself to remove the hook.
        return 0

    if result is None:
        return 0

    kind, msg = result
    if kind == "dup":
        # Real defect: warn Claude via stderr + exit 2 per the hook contract.
        print("check_json_dup_keys: %s in %s" % (msg, file_path), file=sys.stderr)
        print("Note: json.loads silently collapses to the LAST duplicate. Remove one copy.",
              file=sys.stderr)
        return 2
    if kind == "badjson":
        print("check_json_dup_keys: %s in %s" % (msg, file_path), file=sys.stderr)
        return 2
    # "unreadable" and anything else: fail-open.
    return 0


if __name__ == "__main__":
    sys.exit(main())
