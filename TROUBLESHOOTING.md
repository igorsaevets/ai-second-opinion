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

**Both files are stripped of secrets and personal-data shapes by construction, not by you remembering,
and name your home folder as `~` (since 1.113.9).**
Anything shaped like an API key, token, private key, email address, national ID, SSN, phone
number or date of birth is replaced with a `[REDACTED:KIND]` marker before the file is written —
and the same filter runs on the console output, so a crash cannot print a credential either.

That is what makes the intended workflow safe:

> **Paste `diagnostics.json` into a chat with an AI assistant and ask it to diagnose and fix the
> cause.** Or attach it to a bug report. You do not need to read it first.

---

## Faults you are most likely to hit

### Windows: Codex runs for the whole timeout and returns nothing

Run `python doctor.py` and read the **`codex sandbox`** line. If it says it cannot spawn a
`pwsh.exe` under `C:\Program Files\WindowsApps\...`, this is your fault and it is not subtle:

**PowerShell 7 installed from the Microsoft Store cannot be launched inside Codex's sandbox.** A
WindowsApps package is ACL-locked to its own package identity, so the lowered token Codex uses gets
`CreateProcessAsUserW failed: 5` (access denied) on every command the model tries to run. Codex
still answers `--version` normally, still accepts the brief, still reasons — it just cannot execute
anything, so it flails until the timeout.

`winget install Microsoft.PowerShell` will **not** fix it: the default installer for that package id
is the same msix.

Fix, no admin required and nothing machine-wide changes:

```powershell
# download PowerShell-<version>-win-x64.zip from github.com/PowerShell/PowerShell/releases
Expand-Archive PowerShell-7.6.4-win-x64.zip -DestinationPath $HOME\pwsh7
python doctor.py        # the `codex sandbox` line should now say "spawns ...\pwsh7\pwsh.EXE"
```

The Store copy keeps working and stays what `pwsh` means for every other program; the harness puts
`~\pwsh7` first **on the Codex child process only**. Set `CODEX_SHELL_DIR` to override the location.
The MSI installer (which lands in `C:\Program Files\PowerShell\7`) works equally well if you have
admin.

Measured 2026-08-05, and worth knowing because none of the obvious signals point at it: the run
burned 50 minutes, the model's own analysis was finished after 8, and the failure was reported as
"timed out".

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
a length or time limit on a large document. Re-run that channel alone, or split the brief.
(There is no lower `--tier` to reach for: there is exactly one, and it is every channel's own
ceiling — on several vendors a lower depth would have changed nothing but the clock anyway.)

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
3. **A command-line channel had a tool denied mid-answer.** Same failure as (2), different
   vendor, and it is the single most confusing fault here because the run looks deliberate: a
   few hundred bytes of *planning* narration — "I will verify the vendor claims first, then…" —
   and then it stops. On the Grok Build channel the give-away is `stopReason: cancelled`.
   **One denied tool discards the entire turn**, so the answer you get is whatever the model had
   said before it reached for the tool. If you have edited the flags for that channel, put them
   back; the shipped ones are chosen for exactly this.
4. **The Claude Code CLI channel stopped at its turn ceiling.** `diagnostics.json` shows
   `terminal_reason: max_turns` and the warning names the field: raise `max_turns` on
   `cclopus46` in `channels.json`. A denied tool on this channel does not discard the turn — the
   model carries on, and the denial is listed by tool name in the channel's notes.

### "A command-line reviewer is not installed"

The run says `binary not found` and names a path. Three possibilities, in order of likelihood:

1. **You do not have that subscription.** Normal. Run with `--skip <channel>` — the tool is
   designed to run any subset, and every other channel is unaffected.
2. **It is installed somewhere the tool did not look.** Set the matching variable — `CODEX_BIN`,
   `AGY_BIN`, `HERMES_BIN` or `GROK_BIN` — to the full path of the executable.
3. **It is on `PATH` for your shell but not for this process.** Restart the shell, or use (2).

`python doctor.py` prints every command-line channel it knows about, whether the binary was
found, where, and what version answered. A channel missing from that list is a bug in the tool,
not in your setup — please report it.

### "It refuses to send my document"

If it found something shaped like a key, token or password: **there is no override.** A credential
sent to external vendors cannot be recalled. Remove or redact it in the document.

If it found personal identifiers: replace them with placeholders in the *sent copy only* — never
edit your source of record — and tell the model the placeholders are expected. A reviewer never
needs real identifiers to review reasoning. To make this a hard stop again, pass `--strict-pii`
deliberately.

Either way the tool reports **kind and line number, never the value**, because printing it would
leak it into the transcript, which is the same mistake one step earlier.

### "Nothing is happening"

The channels legitimately take very different times — roughly one minute, a few minutes, and up to
half an hour, in that order. They run in parallel, so the round takes as long as the slowest one
you enabled. Each one stops at its own `timeout`; since 1.114.0 that bounds an API read too, and a
CLI agent is stopped with every process it started.

Run with `--dry-run` first, always. It is a **complete** preflight — plan, document, preset, key,
both binaries, permissions and the privacy gate — and it spends nothing.

### "A free opencode voice shows `[skip]` ... refused earlier today"

The free allowance that MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash share was used up
earlier this UTC day: a run on this machine got `provider.quota`. The allowance is counted per
network address, so runs from other machines on your network spend it too. Every run until 00:00
UTC would be refused in seconds, so the plan leaves them out and says how long is left. Nothing is
wrong with your install. Since 1.122.0 each of those voices goes on to the next transport your
machine has: GLM to NVIDIA's free copy with `NVIDIA_NIM_API_KEY`, else MiMo and GLM to their paid
OpenRouter copies where `OPENROUTER_API_KEY` is set (printed PAID in the plan; 1.121.0 left the
voices out); `--skip ormimo26flash orglm53flash ordeepseekv41flash` keeps such a day free. To try anyway, name the channel - its name, an
alias, its model id or a `--set` pin: `--only ocmimo26flashfree` or "only ocmimo26flashfree"; a
group word such as "only opencode" does not count. To forget the refusal, delete
`~/.claude/model-orchestration.quota-state.json`; the next run starts the voice again (and
writes the file again if it is refused).

A `QUOTA:` line at the end of a run says what happened to that record: «NOT remembered» (the file
could not be written, so the next run starts the group again), «could NOT be cleared … delete
<path>» (an answer came, but the old refusal stays until you delete the file), «… is kept» (another
run recorded its refusal after this run began, so it is newer than this run's answer and stands), or
«before 00:00 UTC - not remembered» (the allowance has reset since).

### "ANSWER [nvnemotron3ultra] ... NOT PRINTED" after `--ask`

Since 1.115.1 `--ask` does not print the answer of a channel that `channels.json` marks
`never_read`: every Nemotron channel, by the rule «run it, never read its answer». The channel ran;
the line gives the path of its answer file. Nothing failed.

### "NVIDIA_NIM_API_KEY not set (a free key: build.nvidia.com)"

The cheap panel has two voices on NVIDIA's free endpoint, DeepSeek V4.1 Flash (since 1.118.0; Step 5
Preview from 1.114.0 to 1.117.2) and Nemotron 3 Ultra; both need a free NVIDIA key. Without it the plan
says so («NVIDIA_NIM_API_KEY is not set. This channel will fail ...») and both voices end at once,
spending nothing. DeepSeek's GLM fallback needs the same key, so the plan prints it `OFF in this run`;
Nemotron's OpenRouter fallback still answers after the round. Set the key
([INSTALL.md](INSTALL.md#nvidia--nvidia_nim_api_key)) or leave the voice out: `--skip nvdeepseekv41flash`.

### "MiMo v2.6 Flash is PAID in the plan"

The opencode CLI is not found on this machine, so the free MiMo Flash cannot run and its paid OpenRouter
copy `ormimo26flash` carries the voice (since 1.114.0; $0.14 / $0.28 per 1M tokens, about $0.006 a
review). Install opencode to make it free again, or leave it out: `--skip ormimo26flash`. Where opencode is
installed the paid copy never starts unless you name it.

### "A Grok run says GROK SUBSCRIPTION LIMIT"

xAI refused the turn with HTTP 402: the usage your Grok subscription includes is used up, and
every Grok run on this machine stops the same way until it is back (on 2026-10-22 it was the
weekly limit; buying more usage on the account restored it at once). It is not the brief and not
this tool, and the other voices' answers stand. `LINGERED` on a Grok run is something else: Grok
finished its turn but its process did not exit, so the tool stopped it and kept the answer.

### "agy: model gemini-3.1-pro is not recognized as a known model"

agy checks the model against the list your Google account offers before it starts anything: exit 1 in seconds, nothing spent. `agy models` prints that list (agy fetches it from Google per account). On 2026-10-24 agy 1.4.7 offered our Google AI Pro account Gemini 4 Argon and Gemini 3.8 Flash and no Gemini 3.1 Pro, so `agy31pro` ships off since 1.115.0; the deep Gemini voice is `agy4argon` (Gemini 4 Argon, on the standard panel). The same error for `gemini-4-argon` means your account does not offer Argon: `--skip agy4argon`, or switch it off in your settings file.

Gemini 4 Argon has its own weekly limit on Google AI Pro; `agy -p /usage --output-format json` shows what is left without spending anything. When a limit is used up and agy's «Use AI Credits» setting is on, agy goes on with AI credits (`/credits`).

### "It worked yesterday"

Compare yesterday's `diagnostics.json` with today's. This is why the file is written on **every**
run, not only on failures. The `environment` block usually holds the answer: a CLI updated itself,
a subscription limit reset, a key was rotated.

---

### "A free voice ended with no fallback" (1.119.0 in-round rule)

If a free voice ended without an answer and nothing fell back for it, the in-round rule (new in 1.119.0) classified the failure as `silence` (clean exit, no error) or `past_window` (the primary ran past 25 minutes of its start). Both branches intentionally skip the retry and the fallback — the operator's rule verbatim «silence or past 25 min: no fallback». Grep `in-round:` in `run.log` to see which classifier branch fired; the full note is in the `free_inround` field of `diagnostics.json`. Paid and subscription voices are not affected — their after-round fallback still runs on the same inputs. If you want a free voice retried unconditionally, raise its primary timeout or run the fallback directly by name (`--only <fallback>`), the in-round classifier only acts on the first failure.

---

### "An opencode voice timed out — is it still working?"

Before 1.122.2, often yes: opencode 2.x runs a session in its shared background service, and the harness stopped only the `opencode run` process, so the session went on there with its tools for up to an hour. Since 1.122.2 the voice's notes in `diagnostics.json` say what happened at the timeout: «stopped with `opencode api session.interrupt`» (the shared service) or «its row was deleted (`opencode session delete`)» (a `--standalone` run). A 🔴 warning instead - «may still be working» or «the next opencode server start will resume it» - means that step failed: run the command it names. A command the agent had already started may still run to its own end.

The second effect comes from opencode itself: a session whose private server (`--standalone`) was stopped stays suspended, and the next opencode server that starts - a restart of its background service, a reboot, an update - resumes it, even days later (anomalyco/opencode #55076). Sessions left by earlier versions of this kit are listed by `opencode session list` run inside the folder the harness gives opencode (`orchestrate-neutral-cwd` in your temporary folder); one is removed, with its sub-agents, by

    opencode session delete <session id>

`opencode session export <session id> > backup.json` keeps a copy first; `opencode session import backup.json` brings it back.

Since 1.124.0 what such a voice printed before the stop counts as its answer only when it holds the brief's end marker. Without it the voice has no answer whatever its size (an agent's notes between tool calls used to pass once they reached 2000 bytes), its fallback runs, and the voice's file keeps the text. The plan prints the rule under the voice: «stopped at its 100m limit before its end marker = no answer, whatever its size (R186)».

### A relay voice (wokey.ai / asale.ai) ended without an answer (1.125.0)

- **«WOKEY_API_KEY not set» / «ASALE_API_KEY not set»** in the plan: that relay copy cannot run here; the voice takes its next route. Nothing is spent.
- **asale `503` («no supply», capacity)**: no seller was serving that model at that moment; the next route answers after the round. A third request on one asale account waits for one of its 2 slots.
- **«estimate past the ceiling» / HTTP 402 from the local meter**: the run reached its spending ceiling, estimated at LIST price (asale's real price floats below it). Raise `spend_guard.max_usd_per_review` for that channel in your settings file if you mean to.
- **The voice ran as a plain API call although opencode is installed**: the dry run's `[transport]` line names the reason (opencode older than 2.2.6, or `"agent_harness": null` in your settings).

### Qwen 3.8 Max runs through wokey / OpenRouter although Qwen Code is installed (1.125.0)

The harness uses the Qwen Code CLI only when your user settings file names `qwen3.8-max` (`model.name`, or an `id` under `modelProviders`, in `~/.qwen/settings.json` or `$QWEN_HOME/settings.json`); the plan prints why it skipped the CLI. A model declared only in Qwen Code's system-level settings is not read.

### Where do the reviewers' dialogs go?

The harness deletes nothing. Since 1.125.0, per CLI:

| CLI | Kept in its own history? | Why |
|---|---|---|
| Claude Code | no | `--no-session-persistence` |
| Muse Code | no, where `muse exec` has `--no-session-log` and reports usage in its stream (1.4.23 has both); yes on an older Muse Code | the token counts come from the run's own stream; an older one keeps its log, which `muse export` reads for them |
| opencode | yes, archived and titled «second-opinion:<channel>» | no per-run switch; archived sessions stay out of opencode's TUI and app lists, but `opencode session list`, run in the harness's folder `orchestrate-neutral-cwd` inside your temp folder, still prints them; `opencode session delete <id>` removes one (the database shrinks only after a VACUUM with opencode stopped) |
| Codex | yes | the harness reads the run's record for the weekly quota and for why a run died; `codex resume` lists these runs only with `--include-non-interactive` |
| Qwen Code | yes (`--chat-recording`) | the budget salvage resumes the session |
| Grok Build | yes | no per-run switch; the harness names the session and reads its events during the run |
| agy (Antigravity) | yes | no per-run switch. agy itself appears to keep only its newest 500 conversations ([issue #1417](https://github.com/google-antigravity/antigravity-cli/issues/1417)), so on a machine that runs many panels a run may push out your oldest agy conversation (not observed directly) |
| Kimi Code CLI, MiMo CLI (both off by default) | yes | no per-run switch |

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
