---
name: model-orchestration
description: >
  Run a question past every external reviewer voice at once — Muse Spark, the Codex CLI, Gemini
  (both through the Antigravity subscription and through the OpenRouter API), Kimi and Qwen — in
  parallel, at maximum depth, with verification that each one actually did the work. `--ask` is
  the cheap one-shot form. Use this EVERY TIME the user asks for a
  "second opinion", «второе мнение», a review or verification of a document, plan or analysis by
  an external model, or names Spark / Codex / Gemini / Antigravity / Kimi / Qwen. Run doctor.py once per machine to check prerequisites;
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

Auto-compaction re-attaches each skill's most recent invocation after the summary, **keeping the
first 5,000 tokens**, inside a 25,000-token budget shared across skills and filled
most-recent-first — so an older skill can be dropped entirely. This file is kept **under** that
budget and never clipped; detail lives in `references/`, read on demand (§0.3). Everything that
changes a decision is in this block.

- **A channel that "ran" is not a review that happened.** Exit code, `status`, and even the end
  marker can all be satisfied by a failure. agy returns `status:"SUCCESS"` + exit 0 on an empty
  answer, and `status:"ERROR"` on a complete one (`references/channels.md`). Codex returned a 162-byte **refusal**
  ending in the end marker and was reported OK. Gate on content, never on status.
- 🔴 **A vendor can deliver the answer with characters MISSING and every other check still passes.**
  35 `(` absent from a 26 KB review: `INA § 208(a)(2)(D)` arrived as `208(a)2)(D)`, marker present,
  `ok` true. One vendor's *streamed* transport during its citation post-processing; that channel is
  unstreamed now. Every channel is bracket-checked, as a `note` not a warning. On
  `TEXT INTEGRITY:`, copy no quotation or section number from that answer without opening the
  source. Code spans are excluded: a review *about* damage quotes it.
- 🔴 **A flag can be accepted and grant nothing, and one tool can have two spellings.** Grok Build:
  `--allow web_fetch` inert, `--allow WebFetch` works, `--tools` wants the first. Assert the
  effect, never the exit code.
- **A refusal on a legal/immigration brief is a FRAMING bug, not a subject ban.** Pass
  `--system legal-research` and write the brief as source-verification for attorney review, not as
  filing strategy: codex then answers 6/6. Recipe: **`references/legal-briefs.md`**, read it FIRST.
- **agy dies silently if a tool is denied** — one auto-denied MCP call discards the whole run.
  Fixed by `python patch_agy_permissions.py` (applied). Check it first if agy returns empty.
- **agy re-runs itself once if it cites sources and opened none** — announced before it spends
  (0/3 grounded → 8/8). If the retry also grounds nothing the *first* answer is returned, both
  marked unverified. Never edit this into a loop.
- **Citations are not evidence — the most reliable failure there is.** Read the `CITATIONS:` line.
  For **Codex, which reports no tool telemetry**, "was it opened" is unanswerable, so ask the only
  remaining question: `citecheck.py --answer reviews\CODEX.md --resolve-urls`, which needs no event
  log. `DEAD` = the page is not there; `BLOCKED`/`UNKNOWN` = the check failed and prove nothing.
  Details and the FR-number resolver: `references/verification.md`.
- **Every model answers in English**, enforced by the default preset: Russian costs ~2× the
  tokens (§0.2).
- **Choosing models is config, not code.** `--route`, `--skip`, `--set`; free `--dry-run` (§0.1).
- **Codex is expensive and slow (~6-25 min).** Never send it a lookup: `--ask "…"` is one command.
- 🔴 **CONTEXT IS ALMOST FREE; SEARCHING IS NOT.** Round-26 legal brief: **2,026,852** input
  tokens ≈ $0.20 with no long-context premium, against **128 searches** at $2.50/1,000 = **$0.32,
  60 % of that channel's bill.** Caching is automatic, cached input **50× cheaper**. Sending more
  material is the cheap lever; asking for more searching is the expensive one.

## 0.3 Reference files — read on demand

`SKILL.md` stays under the 5,000-token budget an auto-compaction re-attaches, so it is never
truncated. The detail lives beside it, read on demand — which costs nothing until it is needed,
and a file read after a compaction comes back complete instead of clipped.

| file | read it when |
|---|---|
| `references/legal-briefs.md` | **before** writing any legal / immigration / regulatory brief. A refusal there is a framing bug, and rewriting after the fact costs a whole expensive round |
| `references/channels.md` | wire parameters and CLI traps per channel; a channel misbehaves, returns empty, or you are changing flags |
| `references/briefs.md` | building any brief: what goes in it, and the live-web-search demand |
| `references/verification.md` | judging whether a review actually happened; reviewer signatures; citation spot-checks |
| `references/when-it-breaks.md` | **anything failed** — symptom → cause → fix, and the status fields that lie |
| `references/systems.md` | the `--system` presets in full: what each one says, and why the legal one omits a clause |
| `KIT-README.md` + `package.py` | giving this to somebody else's machine. `package.py --out <dir>` regenerates the distributable (plugin + installers) from this directory, so there is never a second copy to drift |

Paths are relative to this skill's own directory,
`<SKILL_DIR>\`.


## 0. Just run it


From **any** chat, **any** project, **any** working directory. Use the absolute path — the script
does not live in your project folder:

```powershell
python "<SKILL_DIR>\orchestrate.py" `
  --brief "$env:TEMP\brief.md" `
  --marker REVIEW-DONE-01 `
  --out "$env:TEMP\reviews"
```

That runs **every enabled channel in parallel**, writes one `<CHANNEL>.md` per channel into
`--out`, renders `REPORT.md`, and prints a verification block.

🔴 **Do not count the channels from this file, and do not list them.** The number is whatever
`channels.json` enables; every prose copy has been wrong within days — including the `--only` row
below, which listed seven channels three lines under this warning. `python routing.py` prints the
live set and spends nothing. Output filenames are the registry key, upper-cased.

🔴 **Some channels are deliberately off HERE and on in the published kit, and vice versa**, decided
by the registry's `distribution` field: this machine has direct vendor keys (Google, Xiaomi, xAI)
where the kit's install story is one OpenRouter key to the same models. `--dry-run` shows which is
which. A channel that is off is one `enabled: true` away, not a missing feature.

| flag | what it does |
|---|---|
| `--brief` | file with the question. Required unless you pass `--ask`. Its last line should instruct the model to end with your marker |
| `--ask` | **one-shot question instead of a round.** `--ask "text"` or `--ask @file`. Defaults to `spark12cont` (cheapest); `--ask-channel <name>` picks another. Prints the ANSWER to stdout, skips the citation audit. ~20 s |
| `--tier` | **one tier, `max`, and it is the default.** `strategic`/`deep` are aliases kept so old commands run. Depth is never a choice — see §2 |
| `--panel` | `cheap` (default) · `standard`. WHO is in the room; never changes depth. See §2 |
| `--marker` | literal string the reply must end with. If it is absent the output is incomplete |
| `--out` | output directory. Default `./reviews` |
| `--system` | preset name or path (§0.2). The harness **appends the no-non-existence rule** to whatever you pass — end the file with a newline or that sentence collides with your last word |
| `--only` | restrict channels. Channel names, aliases and **group** words all work; `python routing.py` prints every accepted spelling. Groups are the useful form — a vendor family (`gemini`, `grok`, `mimo`, `spark`) or a billing path (`agy`, `openrouter`, `direct`). Omit to run every enabled one. 🔴 One channel per `--only` argument: `--only a b c`, never `--only "a b c"` |
| `--skip` | the inverse of `--only` |
| `--set` | pin a model without editing anything: `--set codex=gpt-5.4` |
| `--route` | **paste what the operator typed, verbatim** — see §0.1 |
| `--dry-run` | full preflight — plan, brief, preset, keys, binaries, gates — then exit, spending nothing |
| `--strict-pii` | refuse to send when the payload holds personal identifiers. **Off by default since 2026-08-07** (the operator: «правила ослабляй, кроме паролей и api ключей») — identifiers now produce a loud itemised warning and go. `--allow-pii` still parses and is a no-op. **Secrets can never be sent, at any setting** |

### 0.1 Choosing channels and models without editing code

Weekly limits run out on one model at a time, so the channel/model choice changes per request,
in prose: *«не использовать 5.6 Sol, а использовать вместо нее 5.5»*, *«не использовать для
этого промта Spark»*. Paste that string into `--route`. It is parsed deterministically — Russian
and English, negation, substitution (including *«вместо нее»* anaphora) and *«только»* — against
the alias table in **`channels.json`**, which is the single place any model name lives.

```powershell
--route "не использовать 5.6 Sol, а использовать вместо нее 5.5. И не использовать для этого промта Spark"
```

The resolved plan is **always printed before anything is spent**: the panel and what the other
panel would add or drop, then one `[RUN ]`/`[skip]` line per channel with its model, effort,
role, data policy, web access, spend ceiling and tier effect, then the running set and its
**vendor tally**. Read it; it is the one screen that exists to be read before money moves.

Rules that matter: an unparseable route is a **hard stop**, never a guess — a router that
silently picks an expensive model defeats its own purpose. A refused model with no stated
replacement falls back to the next model in registry order and says so on a `NOTE:` line. To add
a model or a channel, edit `channels.json`; nothing in `orchestrate.py` needs to change.
Check it costs nothing: `--dry-run`.

### 0.2 System presets — one line each, detail in `references/systems.md`

`--system <preset|path>` frames the reviewer. Two presets: **`base-depth`** (the default — the
maximum-depth amplifier, applied when `--system` is omitted) and **`legal-research`** (any legal
/ immigration / regulatory brief — read `references/legal-briefs.md` **before** writing it). All
presets force English output.

⚠️ **They are not interchangeable.** `base-depth` asks for "unofficial, grey routes alongside the
official one"; `legal-research` deliberately omits that clause, because in a regulated domain it
reads as *suggest a way around the rule* — which is what gets a brief refused. Do not merge them.

**Timing**: most channels land in 1–4 min; **Codex is the long pole at 8–35 min** and sets the
round's wall-clock — which is also why `--panel cheap` finishes far sooner. Run a full round in
the background; sanity check first with `--ask`, ~20 s.

---

## 1. What is on this machine — ask, do not assume


**Never pin a version here.** Both CLIs moved inside one week; a document asserting a version
reads as current long after it stops being true. Ask instead:

```powershell
python "<SKILL_DIR>\doctor.py"
```

Live versions, both CLI paths, key presence **without printing it**, the agy permission patch, the
installed version, your settings file, and a compile check. `--json` for machine form. What is
stable and worth knowing:

| thing | value |
|---|---|
| `MODEL_API_KEY` | the **only** environment variable that must be set. Everything else has a working default |
| HTTPS endpoint / model | `https://api.meta.ai/v1`; the model comes from `channels.json` and the **registry wins over `MODEL_NAME`** — one process-wide variable cannot address one of two channels on one endpoint. Docs: `dev.meta.ai/docs` (public; the login wall is the console, not the docs) |
| Codex CLI | resolved via `CODEX_BIN` → PATH → known install dirs |
| Antigravity CLI | `agy`, **not on PATH** on Windows; resolved via `AGY_BIN` → PATH → `%LOCALAPPDATA%\agy\bin\agy.exe` |
| agy models | one model per channel, base slug plus `--effort`. **Not** `gemini-3.1-pro-high` — a suffixed slug plus a disagreeing `--effort` is exit 1 in 3 s (`references/channels.md` §6.3). 🔴 This row said "two channels" while there were three: ask `routing.py`, never this table |
| Grok Build CLI | `grok`, **not on PATH**; `GROK_BIN` → PATH → `%USERPROFILE%\.grok\bin`. Subscription session, no key. Reads `CLAUDE.md` from its cwd upward → neutral cwd. 🔴 **One denied tool discards the whole turn**, and `--tools` does not bound the MCP gateway. Flags in `channels.json` |
| harness | `orchestrate.py`, standard library only, no `pip install`, Python 3.8+ |

**Secrets.** Never `Read`, `cat`, `echo` or `Write-Output` the key. The script reads it from the
environment; `doctor.py` reports presence and length only. Printing the value is a hard failure —
and `orchestrate.py` refuses to SEND one, with no override at any setting. Identifiers warn and go
(`--strict-pii` to block); both report kind and line, never the value. `--dry-run` runs the gate free.

---

## 2. ONE axis — WHO is in the room. Depth is always maximum.

**`--panel`** picks the reviewers. That is the only choice. Since 2026-08-15 every channel runs
at the ceiling its own vendor accepts, in every mode: **only the number of models differs.**

| `--panel` | who runs |
|---|---|
| **`cheap`** (default since 2026-08-16) | free, subscription and low-rate channels — all **except** spark11, codex, kimik3, qwen38max, terra-pro. *«запусти дешевые»* |
| **`standard`** | every enabled channel. *«запусти все»*, *«стандартная панель»* |

🔴 **Neither a panel nor a GROUP enables anything.** `--only openrouter`, *«только грок»* run only
the members already on. **Naming a channel is the one way to start one that ships off** —
`--only terra`. 🔴 **What `cheap` costs is vendor diversity, not depth**: it drops OpenAI,
Moonshot, Alibaba and Meta-Standard, and nearly half its seats are Google. The plan prints the
vendor tally, warns at half the room, and names what `standard` would add — six Geminis agreeing
is one opinion repeated, not corroboration.

**`--tier` parses and chooses nothing.** One tier, `max`; `strategic`/`deep` are aliases so old
commands work, and the plan says which word it honoured. `quick` is still an argparse error.
Resolved depth is printed per channel.

**The floor is waived when your brief caps the length.** "Under 250 words" makes a short reply
correct — the floor only means "under-allocated" when the brief did not ask for brevity.
Streaming is automatic above a 32,000 budget — why, in `references/channels.md`.

**Your own settings go in `~/.claude/model-orchestration.local.json`, never in `channels.json`** —
an update replaces the skill folder and cannot reach that file. `{"channels": {"<name>":
{"enabled": true}}, "tiers": {"strategic": {"gemini_thinking_level": "low"}}}`. There it may change
anything and add channels or tiers (`"_new": true`). Both files' changes print in the plan. 🔴 A
change to WHERE a document goes needs `python routing.py --accept-settings` once, or a paid round
refuses. Rules and errors: `references/when-it-breaks.md`. Updating: `python upgrade.py`.

🔴 **A depth knob you have only SENT is not a depth knob.** `python echocheck.py --only <channel>
--samples 3` judges by the reasoning counter that comes back, refusing CONFIRMED on overlap.

---

## 3. Verify the answer is real — the whole point


A call that ran is not a review that happened. The harness prints all of this; read it.

**Hard failures — the answer is unusable, never report it as a review:**
1. `stop_reason` is anything other than `end_turn` → truncated, the tail of the analysis is gone.
2. The marker is not present → output is incomplete.
3. Empty body despite HTTP 200. 🔴 **Read the warning before diagnosing it.** If it says
   `OUTPUT BUDGET EXHAUSTED BY REASONING`, the reasoning trace filled `max_tokens` and the answer
   never started — raise that channel's `max_tokens`, or split the brief. Measured: mimo25pro,
   766 s, 60 002 reasoning tokens against a 60 000 cap, zero bytes.

**Soft signals — the answer exists, judge it yourself:**
4. **Tool-invocation count is 0.** `tools:` is *permission*, not instruction. A model handed web
   search may never call it and then answer dated questions from training data. The harness reads
   `usage.server_tool_use.web_search_requests` and falls back to counting `server_tool_use` blocks.
   **Zero means every dated fact in that answer is unverified.**
5. Output tokens below the floor, *with an uncapped brief* → the model under-allocated. There is
   no deeper setting to raise it to; split the question into more sub-questions instead.

Never let a soft signal mark a run failed. A check that cries wolf on a good answer trains you to
ignore the alarm that matters — made twice in this harness, and fixed twice.

---

## 9. When it breaks

**The symptom → cause → fix table is `references/when-it-breaks.md`.** Read it before diagnosing
anything from source. Three that decide what you do next:

- **A status field is not evidence.** `agy` reports `SUCCESS` on an empty answer; `codex exec`
  exits 0 on a hard 400; an HTTP 200 can mean only that the request parsed. Judge by the end
  marker and the counters.
- **`FILTERED` on the Spark probe**: neutralise the phrasing **in the sent copy only**, never on
  disk, and strip appendices. Do not retry unchanged.
- **A channel is off and nothing explains why**: your own settings file, whose path and every
  changed field the plan prints at the top.

---

## 10. Report back — always


Per channel: model, tier, elapsed seconds, input/output tokens, **tool-invocation count**, stop
reason, marker present, output size. Then separately: what was **accepted**, what was **rejected
with proof**, and where the channels **disagreed with each other**.

The disagreement is the product. One reviewer is not a second opinion.

Four rounds, four different winners — `agy`, Codex, Spark, then qwen38max, which refuted a claim
the two expensive channels accepted. **Which channel wins is not predictable from cost, or from
the last round**: that is the whole argument for running all of them.

One shape recurs: the highest-value finding was **not an answer to a question that was asked**. It
arrived under "what are we missing". Always include that question.

---

## 11. Relationship to the other skill

`second-opinion-consult` holds the **policy** — all three channels always, the cost ladder for
lookups, the sub-agent rules (never Fable 5, default `sonnet`, always pass `model`). **This skill
holds the mechanics, and it is the only home for them**: the copy that used to live over there
went stale in four places without looking stale. Read policy there, run calls from here. This file
alone is enough to run a round.
