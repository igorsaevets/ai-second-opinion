---
name: model-orchestration
description: >
  Run a question past the three external reviewer models — Muse Spark over HTTPS, the Codex CLI,
  and the Antigravity CLI (agy / Gemini) — in parallel, at maximum depth, with verification that
  each one actually did the work. Use this EVERY TIME the user asks for a "second opinion",
  «второе мнение», a review or verification of a document, plan or analysis by an external model,
  or names Spark / Codex / Gemini / Antigravity. Run doctor.py once per machine to check prerequisites;
  §0 is then a single command. Also contains the exact wire parameters, the CLI flag traps,
  the streaming and retry rules, and the checks that catch a model which silently did nothing.
user_invocable: true
---

# Model orchestration

**Before the first run on a new machine: `python <SKILL_DIR>/doctor.py`.** It probes every
prerequisite, prints the live version of each CLI, and its last line is the exact command to run
**with the real path already filled in** — so nothing in this file has to hardcode one.

`<SKILL_DIR>` below means the directory this SKILL.md sits in:

| install method | `<SKILL_DIR>` |
|---|---|
| Claude Code plugin | `${CLAUDE_PLUGIN_ROOT}/skills/model-orchestration` |
| `install.ps1` / `install.sh` | `~/.claude/skills/model-orchestration` |

Everything here is empirical: each rule exists because it failed at least once, on a real run.
Nothing was inherited from a vendor's documentation without being reproduced.

---

## 0.0 Hard rules — these decide outcomes, read them before §0

Auto-compaction re-attaches the most recent invocation of each skill after the summary, **keeping
the first 5,000 tokens of each**, with a 25,000-token budget shared across all re-attached skills
and filled most-recent-first — so an older skill can be dropped entirely. This file is therefore
kept **under** that budget (~4.3K tokens, ~300 lines) and is never clipped; the detail lives in
`references/` and is read on demand (§0.3). Everything that changes a decision is in this block.

- **A channel that "ran" is not a review that happened.** Exit code, `status`, and even the end
  marker can all be satisfied by a failure. agy returns `status:"SUCCESS"` + exit 0 on an empty
  answer, and `status:"ERROR"` on a complete one (`references/channels.md`). Codex returned a 162-byte **refusal**
  ending in the end marker and was reported OK. Gate on content, never on status.
- **A refusal on a legal/immigration brief is a FRAMING bug, not a subject ban.** Pass
  `--system legal-research` and write the brief as source-verification for attorney review, not
  as filing strategy. Codex then answers 6/6 with perfect citations. Full recipe: **`references/legal-briefs.md`** — read it BEFORE writing the brief.
- **agy dies silently if a tool is denied.** One auto-denied MCP call discards the whole run.
  Fixed once by `python patch_agy_permissions.py` (already applied 2026-07-31). If agy starts
  returning empty answers again, that is the first thing to check (`references/channels.md`).
- **agy re-runs itself once, automatically, if it cites sources and opened none** — and it costs a
  second agy call, announced before it spends. Measured: 0/3 grounded → **8/8**, tool calls 14 → 72,
  and dead citations 3 → 0. If the second attempt also grounds nothing the *first* answer is
  returned with both marked unverified. Never edit this into a loop.
- **Citations are not evidence — the most reliable failure there is.** Read the `CITATIONS:` line;
  the harness prints it for **agy and Spark**, which report the pages they opened. For **Codex,
  which reports no telemetry**, "was it opened" is unanswerable, so ask the only remaining
  question — `citecheck.py --answer reviews\CODEX.md --resolve-urls`, which needs no event log.
  Measured on one brief 2026-07-31: **agy 3 dead URLs of 11 having opened zero pages**, Spark 0/22,
  Codex 1/32 — and that 404 was a GitHub API probe for a tag that does not exist, i.e. the answer.
  `DEAD` means the page is not there; `BLOCKED`/`UNKNOWN` mean the check failed and prove nothing.
  Details and the FR-number resolver: `references/verification.md`.
- **Every model answers in English**, enforced by the default system preset — the report is
  machine-read, and Russian costs ~2× the tokens (§0.2 below).
- **Choosing models is config, not code.** `--route "не используй 5.6 Sol, вместо нее 5.5"`,
  `--skip`, `--set`; check free with `--dry-run` (§0.1 below).
- **Codex is expensive and slow (~6-25 min).** Never send it a lookup — that is Spark's job.

## 0.3 Reference files — read on demand

`SKILL.md` is deliberately kept under the 5,000-token budget that an auto-compaction re-attaches,
so it is never truncated. The detail lives beside it and is read with the Read tool only when
needed — that costs nothing until it is, and a file read after a compaction returns the material
complete instead of clipped.

| file | read it when |
|---|---|
| `references/legal-briefs.md` | **before** writing any legal / immigration / regulatory brief. A refusal there is a framing bug, and rewriting after the fact costs a whole expensive round |
| `references/channels.md` | wire parameters and CLI traps per channel; a channel misbehaves, returns empty, or you are changing flags |
| `references/briefs.md` | building any brief: what goes in it, and the live-web-search demand |
| `references/verification.md` | judging whether a review actually happened; reviewer signatures; citation spot-checks |
| `KIT-README.md` + `package.py` | giving this to somebody else's machine. `package.py --out <dir>` regenerates the distributable (plugin + installers) from this directory, so there is never a second copy to drift |

Paths are relative to this skill's own directory,
`<SKILL_DIR>\`.


## 0. Just run it


From **any** chat, **any** project, **any** working directory. Use the absolute path — the script
does not live in your project folder:

```powershell
python "<SKILL_DIR>\orchestrate.py" `
  --brief "$env:TEMP\brief.md" `
  --tier strategic `
  --marker REVIEW-DONE-01 `
  --out "$env:TEMP\reviews"
```

That runs **all three channels in parallel** and writes `HTTP.md`, `CODEX.md`, `AGY.md` into
`--out`, plus a verification block on the console.

| flag | what it does |
|---|---|
| `--brief` | file with the question. **Required.** Its last line should instruct the model to end with your marker |
| `--tier` | depth: `quick` · `standard` · `strategic` · `deep`. Default `strategic`. See §2 |
| `--marker` | literal string the reply must end with. If it is absent the output is incomplete |
| `--out` | output directory. Default `./reviews` |
| `--system` | preset name or path (§0.2). The harness **appends the no-non-existence rule** to whatever you pass — end the file with a newline or that sentence collides with your last word |
| `--only` | restrict channels. Any alias in `channels.json` works: `spark`/`http`, `codex`, `agy`/`gemini`. Omit to run all three |
| `--skip` | the inverse of `--only` |
| `--set` | pin a model without editing anything: `--set codex=gpt-5.4` |
| `--route` | **paste what the operator typed, verbatim** — see §0.1 |
| `--dry-run` | full preflight — plan, brief, preset, key, binaries, agy permissions, PII gate — then exit, spending nothing |
| `--allow-pii` | send personal identifiers anyway. **Secrets can never be sent**, with or without it |

### 0.1 Choosing channels and models without editing code

Weekly limits run out on one model at a time, so the channel/model choice changes per request,
in prose: *«не использовать 5.6 Sol, а использовать вместо нее 5.5»*, *«не использовать для
этого промта Spark»*. Paste that string into `--route`. It is parsed deterministically — Russian
and English, negation, substitution (including *«вместо нее»* anaphora) and *«только»* — against
the alias table in **`channels.json`**, which is the single place any model name lives.

```powershell
--route "не использовать 5.6 Sol, а использовать вместо нее 5.5. И не использовать для этого промта Spark"
```

The resolved plan is **always printed before anything is spent**, with a reason line per channel:

```
  [skip] spark  Spark (Messages API)      model=muse-spark-1.1
           - route: excluded by name
  [RUN ] codex  Codex CLI                 model=gpt-5.5  effort=xhigh
           - route: gpt-5.6-sol -> gpt-5.5
           - cost: EXPENSIVE channel
```

Rules that matter: an unparseable route is a **hard stop**, never a guess — a router that
silently picks an expensive model defeats its own purpose. A refused model with no stated
replacement falls back to the next model in registry order and says so on a `NOTE:` line. To add
a model or a channel, edit `channels.json`; nothing in `orchestrate.py` needs to change.
Check it costs nothing: `--dry-run`.

### 0.2 System presets — depth, language and domain framing

`--system` takes a **preset name**, a path, or nothing. Resolution tries the literal path, then
the skill's own `systems/` directory, so a bare name works from any project directory:

| preset | when |
|---|---|
| `base-depth` | **the default**, applied when `--system` is omitted |
| `legal-research` | any legal / immigration / regulatory brief — read `references/legal-briefs.md` first |

`base-depth.md` is the amplifier that used to be pasted into briefs by hand: maximum depth, first
intuition may be wrong, enumerate alternatives, check for contradictions, name the
unofficial-but-lawful route beside the official one, no length cap, escalate fetch tools, never
reconstruct a citation from memory, and say so when nothing could open a page.

**All presets force English output.** The report is consumed by the orchestrating model, not read
directly by a human, and Cyrillic costs roughly twice the tokens for the same content. A Russian
brief still gets an English answer; quoted sources stay verbatim in their original language with
a translation beside them.

⚠️ **The presets are not interchangeable, and the difference is deliberate.** `base-depth` asks
for "unofficial, grey routes alongside the official one". `legal-research` deliberately omits
that clause: in a regulated domain it reads as *suggest a way around the rule*, which is exactly
what gets the brief refused and what makes the output useless to an attorney. Do not merge them,
and do not add the grey-routes line to the legal preset "for consistency".

`--dry-run` validates the preset name and the brief path before anything is spent; a mistyped
preset fails loudly and lists what exists.

**Timing.** `agy` ~1 min · Spark 2–5 min · **Codex 25–35 min**. Run the full set in the background
and do other work; do not sit and wait on Codex. If you only need a sanity check, `--only agy` is
the fastest useful answer on this machine.

---

## 1. What is on this machine — ask, do not assume


**Never pin a version in this file.** Both CLIs moved under it inside one week (`codex` 0.144.6 →
0.146.0, `agy` 1.1.7 → 1.1.9), and a document that asserts a version reads as current long after
it stops being true. Ask instead — it takes two seconds and it is never stale:

```powershell
python "<SKILL_DIR>\doctor.py"
```

It prints each prerequisite with its live version, resolves both CLI paths the same way
`orchestrate.py` does, checks that `MODEL_API_KEY` exists **without printing it**, verifies the
agy permission patch, and compiles the harness. `--json` for a machine-readable form.

What is stable and worth knowing:

| thing | value |
|---|---|
| `MODEL_API_KEY` | the **only** environment variable that must be set. Everything else has a working default |
| HTTPS endpoint / model | `https://api.meta.ai/v1` + `muse-spark-1.1`, defaults inside `orchestrate.py`; override with `MODEL_API_BASE` / `MODEL_NAME` |
| Codex CLI | resolved via `CODEX_BIN` → PATH → known install dirs |
| Antigravity CLI | `agy`, **not on PATH** on Windows; resolved via `AGY_BIN` → PATH → `%LOCALAPPDATA%\agy\bin\agy.exe` |
| agy model | base slug **`gemini-3.1-pro`** plus `--effort`. **Not** `gemini-3.1-pro-high` — a suffixed slug plus a disagreeing `--effort` is exit 1 in 3 seconds, and `gemini-3.1-pro` has no `medium` at all (`references/channels.md` §6.3) |
| harness | `orchestrate.py`, standard library only, no `pip install`, Python 3.8+ |

> ⚠️ An earlier version of this file listed five environment variables to configure. Only
> `MODEL_API_KEY` was ever set; the rest fell back to defaults, which is why it worked. Do not
> "restore" a config table describing state that never existed.

**Secrets.** Never `Read`, `cat`, `echo` or `Write-Output` the key. The script reads it from the
environment itself, and `doctor.py` reports presence and length only. Printing the value is a hard
failure.

**Nothing personal leaves without passing the gate.** `orchestrate.py` refuses to send a payload
containing a key or token (no override exists), and blocks one containing A-numbers, receipt
numbers, SSNs, emails, phones or a labelled date of birth unless you pass `--allow-pii`. It reports
kind and line number and never the value — printing it here would leak it into the transcript,
which is the same mistake one step earlier. `--dry-run` runs the gate, so checking is free.

---

## 2. Depth tiers


| tier | `thinking` sent | expected reasoning | output-token floor |
|---|---|---|---|
| `quick` | `{"type":"adaptive"}` | 2–8k | none |
| `standard` | `{"type":"adaptive","budget_tokens":30000}` | 15–30k | ≥5,000 |
| **`strategic`** — turn-level, multi-question, "what did we miss" | `{"type":"enabled","budget_tokens":60000}` | 40–60k | **≥15,000** |
| **`deep`** — architecture pivot, high-stakes audit | `{"type":"enabled","budget_tokens":100000}` | 60–100k | **≥25,000** |

**Bare `adaptive` silently under-allocates on questions that look small.** Same brief, same model,
same day: `adaptive` → **9,955** output tokens and **0** searches; `{"type":"enabled",
"budget_tokens":100000}` → **20,132** and **34**. Demand depth with a floor; do not hope for it.

**The floor is waived when your brief caps the length.** "Under 250 words" makes a short reply
correct — the floor only means "under-allocated" when the brief did not ask for brevity.

Streaming is automatic above a 32,000 budget and is not optional — why, in `references/channels.md`.

---

## 3. Verify the answer is real — the whole point


A call that ran is not a review that happened. The harness prints all of this; read it.

**Hard failures — the answer is unusable, never report it as a review:**
1. `stop_reason` is anything other than `end_turn` → truncated, the tail of the analysis is gone.
2. The marker is not present → output is incomplete.
3. Empty body despite HTTP 200.

**Soft signals — the answer exists, judge it yourself:**
4. **Tool-invocation count is 0.** `tools:` is *permission*, not instruction. A model handed web
   search may never call it and then answer dated questions from training data. The harness reads
   `usage.server_tool_use.web_search_requests` and falls back to counting `server_tool_use` blocks.
   **Zero means every dated fact in that answer is unverified.**
5. Output tokens below the tier floor, *with an uncapped brief* → the model under-allocated. Raise
   the tier, or split the question into more sub-questions to force more reasoning.

Never let a soft signal mark a run failed. A check that cries wolf on a good answer trains you to
ignore the alarm that matters — that mistake was made in this harness and fixed.

---

## 9. When it breaks


| Symptom | Cause | Fix |
|---|---|---|
| `gaierror` / `getaddrinfo failed` | transient DNS | the harness retries 3× with backoff. If it persists, check the network, not the code |
| `FILTERED` / "content management policy" on the probe | cumulative content filter | neutralise sensitive-looking phrasing **in the sent copy only**, never on disk; strip large appendices; re-probe. Do NOT retry unchanged |
| HTTP 401 | key rotated or expired | run `doctor.py` (§1). Never print the key |
| `SECRETS IN THE PAYLOAD` / `PERSONAL IDENTIFIERS` | the pre-send gate fired | fix the brief at the reported line. Secrets have no override; PII needs `--allow-pii` |
| `the route and the flags contradict each other` | `--only`/`--set` re-enabled a channel the route excluded | decide which you meant and pass one, not both |
| HTTP 400 mentioning `thinking` | wrong thinking form for the host | the harness flips the form and retries once automatically |
| Codex output empty, exit 0 | still thinking, or buffered through a formatter | check the marker on the last line; check `Get-Process codex` and rollout growth |
| `agy` returns `jetski ... permission` | it tried to read the brief file | shorten the brief so it goes inline via `-p` |
| tool_calls = 0 | model never searched | treat all dated facts as unverified; re-run at a higher tier or split the question |
| `python` not found from another directory | you used a relative path | always use the absolute path in §0 |

---

## 10. Report back — always


Per channel: model, tier, elapsed seconds, input/output tokens, **tool-invocation count**, stop
reason, marker present, output size. Then separately: what was **accepted**, what was **rejected
with proof**, and where the channels **disagreed with each other**.

The disagreement is the product. One reviewer is not a second opinion.

Three rounds, three different winners. 07-26: `agy` at 55 s found the item Codex and Spark missed.
07-27: **Codex** found it, Spark contributed least despite 34 searches. 07-31: **Spark** graded the
one planted-wrong claim correctly while agy passed it and cited three URLs that 404. The lesson is
not "the cheap one wins" but the stronger claim: **which channel wins is not predictable from cost,
or from the last round** — the whole argument for running all three.

Both rounds share one shape worth naming: the highest-value finding was **not an answer to a
question that was asked**. It arrived under "what are we missing". Always include that question.

---

## 11. Relationship to the other skill

`second-opinion-consult` holds the **policy** — all three channels always, the cost ladder for
lookups, the sub-agent rules (never Fable 5, default `sonnet`, always pass `model`). **This skill
holds the mechanics, and it is the only home for them**: the copy that used to live over there
went stale in four places without looking stale. Read policy there, run calls from here. This file
alone is enough to run a round.
