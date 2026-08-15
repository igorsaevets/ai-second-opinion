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
- **agy re-runs itself once, automatically, if it cites sources and opened none** — a second agy
  call, announced before it spends (measured: 0/3 grounded → 8/8). If the retry also grounds
  nothing the *first* answer is returned, both marked unverified. Never edit this into a loop.
- **Citations are not evidence — the most reliable failure there is.** Read the `CITATIONS:` line,
  printed for every channel that reports which pages it opened. For **Codex, which reports no tool
  telemetry**, "was it opened" is unanswerable, so ask the only remaining question:
  `citecheck.py --answer reviews\CODEX.md --resolve-urls`, which needs no event log. `DEAD` means
  the page is not there; `BLOCKED`/`UNKNOWN` mean the check failed and prove nothing — one real
  DEAD URL was a probe for a tag that does not exist, i.e. the answer.
  Details and the FR-number resolver: `references/verification.md`.
- **Every model answers in English**, enforced by the default system preset — the report is
  machine-read, and Russian costs ~2× the tokens (§0.2 below).
- **Choosing models is config, not code.** `--route "не используй 5.6 Sol, вместо нее 5.5"`,
  `--skip`, `--set`; check free with `--dry-run` (§0.1 below).
- **Codex is expensive and slow (~6-25 min).** Never send it a lookup — that is Spark's job, and
  since 2026-08-07 a lookup is one command: `--ask "…"`.
- 🔴 **CONTEXT IS ALMOST FREE; SEARCHING IS NOT.** Measured on the round-26 legal brief:
  `spark12cont` consumed **2,026,852** input tokens (≈ $0.20 at the Contributor tier, which has
  **no long-context premium**) and issued **128 web searches** at $2.50/1,000 — **$0.32, i.e. 60 %
  of that channel's bill was search.** Caching is automatic on this endpoint and cached input is
  **$0.002/M — 50× cheaper**; `cache_control` breakpoints are accepted, validated, and change
  nothing. So sending more material is the cheap lever and asking for more searching is the
  expensive one, which is the opposite of how both feel.

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
  --tier strategic `
  --marker REVIEW-DONE-01 `
  --out "$env:TEMP\reviews"
```

That runs **every enabled channel in parallel**, writes one `<CHANNEL>.md` per channel into
`--out`, renders `REPORT.md`, and prints a verification block.

🔴 **Do not count the channels from this file, and do not list them.** The number is whatever
`channels.json` enables; it went three → five → eight → fourteen inside four weeks, and every prose
copy of that list has been wrong within days — including the `--only` row of this very table, which
listed seven channels three lines under this warning until 2026-08-08. `python routing.py` prints
the live set and spends nothing. Output filenames are the registry key, upper-cased.

🔴 **Some channels are deliberately off HERE and on in the published kit, and vice versa.** The
registry's `distribution` field decides: this machine has direct vendor keys (Google, Xiaomi, xAI)
and the kit's install story is one OpenRouter key to the same models. `--dry-run` shows which are
which. A channel that is off is one `enabled: true` away, not a missing feature.

| flag | what it does |
|---|---|
| `--brief` | file with the question. Required unless you pass `--ask`. Its last line should instruct the model to end with your marker |
| `--ask` | **one-shot question instead of a round.** `--ask "text"` or `--ask @file`. Defaults to `spark12cont` (cheapest); `--ask-channel <name>` picks another. Prints the ANSWER to stdout, skips the citation audit. ~20 s |
| `--tier` | `strategic` (default) · `deep`. Two only. See §2 |
| `--marker` | literal string the reply must end with. If it is absent the output is incomplete |
| `--out` | output directory. Default `./reviews` |
| `--system` | preset name or path (§0.2). The harness **appends the no-non-existence rule** to whatever you pass — end the file with a newline or that sentence collides with your last word |
| `--only` | restrict channels. Channel names, aliases and **group** words all work; `python routing.py` prints every accepted spelling. Groups are the useful form — a vendor family (`gemini`, `grok`, `mimo`, `spark`) or a billing path (`agy`, `openrouter`, `direct`). Omit to run every enabled one. 🔴 One channel per `--only` argument: `--only a b c`, never `--only "a b c"` |
| `--skip` | the inverse of `--only` |
| `--set` | pin a model without editing anything: `--set codex=gpt-5.4` |
| `--route` | **paste what the operator typed, verbatim** — see §0.1 |
| `--dry-run` | full preflight — plan, brief, preset, key, binaries, agy permissions, PII gate — then exit, spending nothing |
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
| agy models | two channels, one model each: **`gemini-3.1-pro`** (agy31pro) and **`gemini-3.6-flash`** (agy36flash), base slug plus `--effort`. **Not** `gemini-3.1-pro-high` — a suffixed slug plus a disagreeing `--effort` is exit 1 in 3 seconds (`references/channels.md` §6.3) |
| harness | `orchestrate.py`, standard library only, no `pip install`, Python 3.8+ |

**Secrets.** Never `Read`, `cat`, `echo` or `Write-Output` the key. The script reads it from the
environment; `doctor.py` reports presence and length only. Printing the value is a hard failure —
and `orchestrate.py` refuses to SEND one, with no override at any setting. Identifiers warn and go
(`--strict-pii` to block); both report kind and line, never the value. `--dry-run` runs the gate free.

---

## 2. Two axes — WHO is in the room, and HOW DEEP each one goes

**`--panel`** picks the reviewers. **`--tier`** picks the depth. They compose freely, and
`--panel cheap --tier deep` (few voices, thinking hard) is a sensible, cheap way to ask a hard
question.

| `--panel` | who runs |
|---|---|
| **`standard`** (default) | every enabled channel — what ran before panels existed |
| **`cheap`** | the free, subscription and low-rate channels — everything **except** spark11, codex, kimik3, qwen38max and terra-pro. Ask the plan for the list; it prints both sets by name |

🔴 **A panel FILTERS DOWN and never enables anything** — unlike `--only`, which resurrects a
default-off channel on purpose. So `--panel cheap` cannot turn on a channel whose key you do not
have. 🔴 **What `cheap` really costs is vendor diversity, not depth**: it drops OpenAI, Moonshot,
Alibaba and Meta-Standard, and over half its remaining seats are Google. The plan prints the
vendor tally of whatever resolves and warns when one vendor holds half the room — six Geminis
agreeing is one opinion repeated, not corroboration. Route words work too: *«дешевая панель, без
grok»*.

| `--tier` | what it buys |
|---|---|
| **`strategic`** (default) | every vendor at the depth it supports; agy 25m, codex 50m; Spark budget 60k, floor ≥15,000 |
| **`deep`** | agy 40m, codex 75m; Spark budget 100k, floor ≥25,000; **reasoning cap and page-fetch budget DOUBLED** on every OpenRouter/MiMo channel |

`quick`/`standard` are gone **as tiers** (argparse error); `standard` is now a PANEL, a different
flag. **Read both from the plan, not this table** — it prints per channel what the tier resolved
to, including "nothing this tier can raise here". `deep` costs money: double the fetch budget is
double the context, and context is the bill.

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
3. Empty body despite HTTP 200.

**Soft signals — the answer exists, judge it yourself:**
4. **Tool-invocation count is 0.** `tools:` is *permission*, not instruction. A model handed web
   search may never call it and then answer dated questions from training data. The harness reads
   `usage.server_tool_use.web_search_requests` and falls back to counting `server_tool_use` blocks.
   **Zero means every dated fact in that answer is unverified.**
5. Output tokens below the tier floor, *with an uncapped brief* → the model under-allocated. Raise
   the tier, or split the question into more sub-questions to force more reasoning.

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
