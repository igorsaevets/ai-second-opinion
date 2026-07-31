# When something breaks

## The short version

```
python ~/.claude/skills/model-orchestration/doctor.py
```

It names the broken thing and what to do about it. If that does not settle it, open
`diagnostics.json` in your review output folder — or better, hand it to an AI assistant and ask
it to diagnose and fix the cause.

---

## The two files every run leaves behind

Both land in the output folder you gave `--out` (default `./reviews`).

### `run.log`

Everything the run printed, written **line by line as it happens** rather than buffered at the
end. That is deliberate: if a run is killed, times out, or the machine dies, a buffered log is
empty at exactly the moment you need it.

### `diagnostics.json`

A structured account of the run. The fields worth knowing:

| Field | What it tells you |
|---|---|
| `problems` | **Start here.** One entry per fault, each with the raw detail, a plain-language `likely_cause`, and a `suggested_fix`. |
| `environment` | What is actually installed: Python version, OS, whether each CLI was found and its live version, whether the API key is present *(presence and length only — never the value)*. |
| `plan` | Which channels were selected, with which model, and why. |
| `preflight` | The free checks run before anything was spent. |
| `channels` | What each reviewer actually did — timings, tool calls, tokens, warnings. A channel with `ok: false` has often still produced usable text, saved beside this file. |
| `console` | The full console transcript. |
| `traceback` | Present only if the tool itself crashed. |

**Both files are stripped of secrets and personal data by construction, not by you remembering.**
Anything shaped like an API key, token, private key, email address, national ID, SSN, phone
number or date of birth is replaced with a `[REDACTED:KIND]` marker before the file is written —
and the same filter runs on the console output, so a crash cannot print a credential either.

That is what makes the intended workflow safe:

> **Paste `diagnostics.json` into a chat with an AI assistant and ask it to diagnose and fix the
> cause.** Or attach it to a bug report. You do not need to read it first.

---

## Faults you are most likely to hit

### "It says my API key is not set, but I just set it"

On Windows, `setx` writes the variable for *future* processes. Terminals that were already open
keep the old value. **Open a new terminal.**

Confirm with `doctor.py`, which reports presence and length. Do not echo the variable to check it
— see [SECURITY.md](SECURITY.md) for why that is a genuinely bad idea rather than a stylistic
preference.

### "One of the reviewers is missing / I only have one account"

**That is a supported configuration, not an error.** The tool runs whatever is available. To stop
it even trying:

```
--skip spark          # no API key
--skip codex          # no Codex CLI
--skip gemini         # no Antigravity CLI
```

Or state it positively: `--only spark`, `--only codex agy`.

If a CLI *is* installed but is not being found, point at it explicitly with the `CODEX_BIN` or
`AGY_BIN` environment variable.

### "It says END MARKER ABSENT"

The model stopped before finishing, or never emitted the agreed end-of-review marker.

The tool appends the marker instruction to your document automatically when your document does
not already contain it, so this should be rare. When it does happen it usually means the model hit
a length or time limit on a large document. Re-run that channel alone, or lower `--tier`.

**Why this check exists at all:** a truncated review looks exactly like a finished one until you
notice the argument stops mid-sentence. The marker proves the model reached the end of its own
turn.

### "A reviewer returned almost nothing"

Two different faults look identical:

1. **The model refused the task** on policy grounds. It still formats the reply correctly and
   still appends your end marker, so every naive check passes. The tool catches this and labels
   it — a 162-byte refusal was once reported as a successful channel, which is why the check
   exists. **This is almost always a framing problem, not a subject ban:** rewrite the request as
   *verification of sources* rather than as strategy or advice, and pass
   `--system legal-research` for regulated subjects. The same models that refuse the first
   framing answer the second one in full.
2. **The Antigravity channel hit a denied tool permission**, which discards the whole run and
   returns an empty answer with a `SUCCESS` status and exit code `0`. Fix:
   `python patch_agy_permissions.py`. See [INSTALL.md](INSTALL.md).

### "It refuses to send my document"

If it found something shaped like a key, token or password: **there is no override.** A credential
sent to three external vendors cannot be recalled. Remove or redact it in the document.

If it found personal identifiers: replace them with placeholders in the *sent copy only* — never
edit your source of record — and tell the model the placeholders are expected. A reviewer never
needs real identifiers to review reasoning. If they genuinely belong, pass `--allow-pii`
deliberately.

Either way the tool reports **kind and line number, never the value**, because printing it would
leak it into the transcript, which is the same mistake one step earlier.

### "Nothing is happening"

The channels legitimately take very different times — roughly one minute, a few minutes, and up to
half an hour, in that order. They run in parallel, so the round takes as long as the slowest one
you enabled.

Run with `--dry-run` first, always. It is a **complete** preflight — plan, document, preset, key,
both binaries, permissions and the privacy gate — and it spends nothing.

### "It worked yesterday"

Compare yesterday's `diagnostics.json` with today's. This is why the file is written on **every**
run, not only on failures. The `environment` block usually holds the answer: a CLI updated itself,
a subscription limit reset, a key was rotated.

---

## Verifying the tool itself

```
python ~/.claude/skills/model-orchestration/selftest.py
```

About fifty behavioural checks: partial installs degrade rather than crash, channel selection is
obeyed exactly, and nothing secret-shaped can reach a console, a log or a diagnostics file. Costs
nothing, contacts no vendor.

**Run this after any change an AI assistant makes for you.** It is the difference between "the
assistant said it fixed it" and "the fix is verified".

---

## Two habits worth having from day one

**Never trust an exit code on these channels.** Both the status field and the exit code have been
observed lying in both directions, on the same channel, in the same week: `SUCCESS` with an empty
answer, `ERROR` with a complete one, and exit `0` on a hard HTTP 400 from the vendor's own server.
Judge the content.

**Never pin a version number in a document.** Both command-line tools changed version inside a
single week. A document asserting a version does not look stale — it looks like documentation.
Ask `doctor.py` instead; it prints what is actually installed.

---

## Reporting a bug

Open an issue with `diagnostics.json` attached. It is already scrubbed of keys and personal data,
and it contains the environment, the plan and the failure — which is everything needed to
reproduce, and considerably more than a screenshot.
