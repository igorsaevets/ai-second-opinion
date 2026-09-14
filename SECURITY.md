# Security

## What this tool sends where

It sends the document you point it at, plus a system-prompt preset, to the vendors behind whichever
channels you have enabled — the plan names them before anything is sent. Nothing else, with one
stated exception: a version check against `api.github.com` at most once a week (the tag list; the
release notes when a newer tag exists), carrying no version string and nothing about you beyond
what any HTTP request carries. `python update_check.py --show-what-would-be-sent` prints it
verbatim; `MODEL_ORCH_UPDATE_CHECK=0` switches it off. No telemetry, no analytics, no background
process.

`update_check.py --apply` — only when you run it — downloads the release archive from github.com,
pinned to the commit the GitHub API named for the tag, and refuses it unless it holds one top-level
folder, the skill subtree, every required file, a `VERSION` equal to the tag, and no member that
escapes the folder or is a symlink. No signature is verified: the trust is TLS plus the commit pin,
the same as `git clone`. A plugin install is updated through Claude Code's own `plugin update`.

**Once a payload is sent it cannot be recalled.** It is at every vendor the round reached, under
their retention policies, not yours. Everything below exists because of that one sentence.

## What it refuses to send

**Credentials — no override exists.** Anything shaped like a private key block, a vendor API key,
a labelled `token=` / `password=` / `api_key=` assignment, or a bearer token is refused outright.
There is deliberately no flag to force it. If it is a false positive — a placeholder, a documented
example — rename the variable or redact the value in the document.

**Personal identifiers — detected, listed by kind and line, and sent by default.** National ID
numbers, case and receipt numbers, SSNs, email addresses, phone numbers, labelled dates of
birth and passport numbers are found and reported before send but the payload IS sent unless
you pass `--strict-pii`, which refuses the round instead. The default is warn-and-send; the
opt-in is refuse. Credentials, above, are always refused; PII is not.

The recommended handling is to tokenize in the **sent copy only** — never edit your source of
record — and tell the model the placeholders are expected. A reviewer never needs real identifiers
to review reasoning.

**The gate reports kind and line number, never the value.** Printing the matched value would leak
it into the terminal transcript, which is the same mistake one step earlier.

Both the document and the system-prompt file are scanned; a hand-written preset carries a key just
as easily as a brief does. The scan runs under `--dry-run`, so checking is free.

## What it refuses to print

Everything written to the console, `run.log` or `diagnostics.json` passes through a substitution
that replaces secret- and PII-shaped text with a `[REDACTED:KIND]` marker.

This is a substitution, never a truncation, and the distinction is not academic:

> A "masking" expression that kept the first 60 characters of a 48-character key kept **all of
> it**. The output looked masked, because the other half of the same command masked correctly.
> That is how a live API key reached a transcript.

A substitution cannot fail that way — either the pattern matched and the text is gone, or it did
not match and nothing claimed otherwise.

Scrubbing happens in the single logging choke point, not at each call site. That was not the
original design: it was found while testing the crash handler, where an exception whose *message*
contained a key printed it to the console in full, because only the diagnostics *file* was being
scrubbed.

## Never print a key to check it

```
❌  echo $env:MODEL_API_KEY
❌  printenv | grep API
❌  Get-ChildItem Env: | ForEach-Object { $_.Value }
❌  any "masking" transform you wrote yourself

✅  python doctor.py          # reports presence and length, never the value
```

A terminal transcript is written to disk, replayed into AI context, and archived. It is an
exfiltration surface, not a scratchpad. **If a key does appear in one: rotate it, do not scrub the
file.** Scrubbing races whatever is still appending to it, and only rotation makes the leaked
bytes worthless.

## Do not share one API key

A single key is metered against whoever owns it. Shared across a team: one person's card silently
pays for everyone, nobody can be attributed, and revoking it cuts off the whole team at once.

Issue one key per person. If that is not on offer, run the other channels — the harness runs any
subset on purpose.

## Permissions the tool changes

`patch_agy_permissions.py` is the only script that writes outside its own directory, and only to
one file: the Antigravity CLI's settings. It is additive and idempotent, backs up before writing,
supports `--dry-run` and `--revert`, and `doctor.py` re-checks the result on every run.

It also **denies metered crawling tools** that bill per page with no ceiling, and scheduled
monitors that bill with nobody watching. That is why, for the Antigravity CLI, the answer to a
permissions problem is never `--dangerously-skip-permissions` — that flag unlocks those too.

## The Claude Code CLI channel runs with permission prompts bypassed

`cclopus46` launches `claude -p --permission-mode bypassPermissions` — the same mode as
`--dangerously-skip-permissions` — on purpose, every time. A headless run has nobody to answer a
prompt, and in the CLI's default mode the reviewer's web fetches and shell commands were denied
while its JSON still reported success: a review that quietly did less (measured 2026-09-11).

What that mode means on the machine that runs it: every built-in tool — shell, file edits anywhere,
web — and **every MCP server configured in your Claude Code, with its credentials**, run without a
prompt, on a brief that is untrusted input by this document's own rule. Claude Code's own
documentation recommends the mode for isolated containers and VMs only. Three things still hold in
it: your `permissions.deny` rules (a bare tool name removes the tool, a scoped rule such as
`Bash(git push *)` denies the matching call — both measured), your hooks, and the CLI's short list
of actions no mode auto-approves.

So the channel ships **off**. Turning it on — `--only cclopus46` for a round, or `"enabled": true`
in `channels.json` — is the decision, and the plan prints the line before anything runs. To narrow
it, add deny rules to `~/.claude/settings.json`; they apply in every mode. The channel also removes
`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the child's environment: with either set the
CLI bills that key instead of the claude.ai login, and this channel is subscription-only.

## Bypass opt-in for the other CLI channels (R88, v1.63.0)

Since v1.63.0 the four other CLI channels — `codex`, `grokbuild`, `agy31pro`, `agy36flash`,
`agy38flash` — carry a `bypass_permissions` field in `channels.json` that ships **false**. When you
set it to `true`, or pass `--bypass-permissions <name>` / `--all-bypass` on the command line, the
channel launches with its vendor's own bypass flag:

| Channel | Kind | Flag added under bypass | Flag replaced |
|---|---|---|---|
| `codex` | codex-cli 0.154.0 | `--dangerously-bypass-approvals-and-sandbox` | `--sandbox read-only` |
| `grokbuild` | grok 1.0.30 | `--permission-mode bypassPermissions` | `--permission-mode dontAsk` |
| `agy31pro` / `36flash` / `38flash` | agy 1.2.2 | `--dangerously-skip-permissions` | `--sandbox` |
| `cclopus46` | claude 2.1.270 | `--permission-mode bypassPermissions` (always on since v1.61.0) | — |
| `ocspark13free` | opencode | (no field on purpose — `opencode run` is already YOLO by default) | — |

`opencode` is intentionally excluded: its `run` subcommand runs with all permissions bypassed by
default, so the concept has no per-channel switch to toggle. The dispatcher passes `bypass=` only
to the channels that carry the field.

**What bypass means on the machine that runs the call.** Every shell command, every file edit
anywhere, every web fetch runs without a prompt. On `agy` and `cclopus46`, every MCP server in the
user's config runs without a prompt too, with its credentials. On `grokbuild`, `read_file` is
**not** bounded by `--cwd` (measured — it served files out of `~/.grok/skills/` with a neutral cwd
in force), so bypass hands the model this machine's readable files. This is the operator's
decision to trust the reviewer with the mechanical guard OFF, not a permission the tool grants
itself.

**🔴 The `command(*)` shell fence disappears on agy under bypass** (R57, measured in
`patch_agy_permissions.py:71-74`): `--dangerously-skip-permissions` makes agy ignore the
operator's own deny rules for shell commands. `patch_agy_permissions.py` still governs non-bypass
runs and the interactive TUI, but under bypass its allow/deny list is a suggestion — the flag
overrides it. This is why the two agy channels arm a **workdir tripwire** in addition to the
safety-directive prompt: a snapshot of the workdir file list is taken before the call, compared
after, and any file that vanished or was truncated without a matching `BACKUP: <path>` line in the
model's answer is reported as a loud warning in the run's `notes`. The tripwire is a **signal**
after the fact, not a fence.

**The safety-directive prompt.** Every channel run with bypass gets a two-rule safety block
prepended to its brief:

- **Rule 1 — self-configure before you act.** Declare in the first 2–4 sentences which files or
  paths the model intends to read, write or modify, and which it will not touch. Never modify or
  delete anything under `.claude/`, `.git/`, any `.env`, `~/.ssh/`, `~/.gnupg/`, `~/.codex/`,
  `~/.gemini/`, or anything holding credentials.
- **Rule 2 — deletions are a three-step ritual.** Prefer NOT to delete. If a deletion is
  necessary, all three lines must appear in the reply BEFORE the destructive call actually runs:
  `BACKUP: <src> -> <dst>` (into a timestamped folder under the system TEMP), a `REASONING:`
  block of 2–4 sentences, and finally `DELETED: <path>`. Overwriting or truncating a file counts
  as deleting the pre-existing bytes.

The directive rides in the brief on purpose: measured on every CLI channel here, an instruction
in the brief outweighs one in the persona or system slot (R40, 2/2 vs 0/1). It is **steering, not
enforcement** — a model that ignores the directive gets past every mechanical check except the
agy tripwire.

**Reading order before you turn any of this on.** Run `--dry-run --bypass-permissions <name>`
first — the plan prints the line `[<name>] PERMISSIONS BYPASSED (source: ...)` before anything
runs, and for agy it also prints `+ workdir tripwire ARMED`. If that line is not in the plan, the
flag did not reach the call. Never turn on bypass for a channel reviewing a brief you did not
write yourself: a hostile document can steer a bypassed reviewer's write tools, and both the
safety prompt and the tripwire are documented failures under that threat model — read them and
decide.

## Treat model output as untrusted input

A cited URL is model-generated text. `citecheck.py` refuses to fetch non-public hosts (localhost,
private address ranges) for exactly that reason — and since 1.45.0 every such fetch, including
the model-facing page-fetch tool, connects to the exact address the check vetted (per redirect
hop, TLS validated against the hostname), so a TTL-0 DNS rebinding answer has nothing to rebind.

More generally: models produce real-looking source references for pages that were never opened and
sometimes never existed — measured here at 3 dead URLs out of 11, from a channel that had opened
zero pages, while its conclusions were correct. Verify before you repeat anything a model cited.

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/igorsaevets/ai-second-opinion/issues) for anything already public. For
something that should not be public yet, use
**[Report a vulnerability](https://github.com/igorsaevets/ai-second-opinion/security/advisories/new)**, which
opens a private advisory visible only to the maintainer.

There is no security email. The address on this repository's commits is GitHub's no-reply relay,
which has no mail exchanger — mail to it is not delivered anywhere, and it fails silently. A
reporting channel that quietly discards a vulnerability report is worse than an absent one, so the
private advisory form is the channel.

Please include `diagnostics.json` where relevant — it is scrubbed of keys and personal data by
construction, so it is safe to attach.

## Scope note

This tool has no server, no account system, no database and no network listener. Its attack
surface is: the documents you feed it, the model output it parses, and the local files it writes.
Findings in those areas are in scope.
