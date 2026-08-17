# Security

## What this tool sends where

It sends the document you point it at, plus a system-prompt preset, to whichever of three vendors
you have enabled. Nothing else. No telemetry, no analytics, no background process, no phone-home.

**Once a payload is sent it cannot be recalled.** It is at up to three separate vendors, under
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
monitors that bill with nobody watching. That is why the answer to a permissions problem is never
`--dangerously-skip-permissions` — that flag unlocks those too.

## Treat model output as untrusted input

A cited URL is model-generated text. `citecheck.py` refuses to fetch non-public hosts (localhost,
private address ranges) for exactly that reason.

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
