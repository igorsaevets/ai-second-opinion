---
name: model-orchestration
description: >
  Run a question past every external reviewer voice at once — Muse Spark, Codex, Gemini (via the
  Antigravity subscription and via OpenRouter), Grok, DeepSeek, GLM, Kimi, Qwen — in parallel, at
  maximum depth, with verification that each one actually did the work. `--ask` is the cheap
  one-shot form. Use this EVERY TIME the user asks for a "second opinion", «второе мнение», a
  review or verification of a document, plan or analysis by an external model, or names any of
  those channels. Everything is already installed here; §0 is a single command. Also holds the
  wire parameters, the CLI flag traps, the streaming and retry rules, and the checks that catch a
  model which silently did nothing.
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
  35 `(` absent from a 26 KB review — `208(a)(2)(D)` → `208(a)2)(D)`, marker present, `ok` true.
  Every channel is bracket-checked. On `TEXT INTEGRITY:`, copy no quotation or section number out
  of that answer without opening the source. Details: `references/when-it-breaks.md`.
- 🔴 **A flag can be accepted and grant nothing, and one tool can have two spellings.** Grok Build:
  `--allow web_fetch` inert, `--allow WebFetch` works. Assert the effect, never the exit code.
- **A refusal on a legal/immigration brief is a FRAMING bug, not a subject ban.** Pass
  `--system legal-research` and write the brief as source-verification for attorney review, not as
  filing strategy: codex then answers 6/6. Recipe: **`references/legal-briefs.md`**, read it FIRST.
- 🔴 **agy dies silently on an UNLISTED tool — a DENIED one is harmless.** A tool in neither list
  cancels the whole turn and reports it as an empty answer with `status: SUCCESS`, exit 0; an
  explicitly denied one is an ordinary error the model recovers from. **Silence is the dangerous
  state, not refusal.** Fixed by `python patch_agy_permissions.py`: one `mcp(*)` allow, denies
  the shell, Firecrawl's metered tools and cloakbrowser's session/JS tools; `playwright`
  deliberately stays reachable (owner's call 2026-08-20, selftest-asserted) — this line once
  promised a browser denial the list never contained (R73); the prose was the bug. Run it
  after any update: those rules live in `~/.gemini/`, not in this tree, so pulling a new
  version does not apply them. Check it first if agy returns empty.
- **agy re-runs itself once if it cites sources and opened none** — announced before it spends
  (0/3 grounded → 8/8). If the retry also grounds nothing the *first* answer is returned, both
  marked unverified. Never edit this into a loop.
- **Citations are not evidence — the most reliable failure there is.** Read the `CITATIONS:` line.
  For **Codex, which reports no tool telemetry**, "was it opened" is unanswerable, so ask the only
  remaining question: `citecheck.py --answer reviews\CODEX.md --resolve-urls`. `DEAD` = the page is
  not there; `BLOCKED`/`UNKNOWN` = the check failed and prove nothing. `references/verification.md`.
- **Every model answers in English**, enforced by the default preset: Russian costs ~2× (§0.2).
- **Choosing models is config, not code.** `--route`, `--skip`, `--set`; free `--dry-run` (§0.1).
- **Codex is expensive and slow (~6-25 min).** Never send it a lookup: `--ask "…"` is one command.
- 🔴 **CONTEXT IS ALMOST FREE; SEARCHING IS NOT.** Measured: **2 026 852** input tokens ≈ $0.20 with
  no long-context premium, against **128 searches** = **$0.32, 60% of that channel's bill.**
  Sending more material is the cheap lever; asking for more searching is the expensive one.

## 0.3 Reference files — read on demand

`SKILL.md` stays under the 5,000-token budget an auto-compaction re-attaches, so it is never
truncated. Detail lives beside it in `references/` (paths relative to this skill's directory).

| file | read it when |
|---|---|
| `legal-briefs.md` | **before** any legal / immigration / regulatory brief — a refusal there is a framing bug, and rewriting after one costs a whole round |
| `channels.md` | wire parameters and CLI traps per channel; a channel misbehaves, or you are changing flags |
| `briefs.md` | building any brief: what goes in it, and the live-web-search demand |
| `verification.md` | judging whether a review actually happened; signatures; citation spot-checks |
| `reading-the-answers.md` | **a round landed** — the manifest, the read cost, when to defer to a fresh context |
| `when-it-breaks.md` | **anything failed** — symptom → cause → fix, and the status fields that lie |
| `systems.md` | the `--system` presets in full, and why the legal one omits a clause |
| `../KIT-README.md` + `package.py` | giving this to another machine — `package.py --out <dir>` regenerates the kit; no second copy to drift |


## 0. Just run it


From **any** chat, project or working directory — use the absolute path:

```powershell
python "<SKILL_DIR>\orchestrate.py" `
  --brief "$env:TEMP\brief.md" `
  --marker REVIEW-DONE-01 `
  --out "$env:TEMP\reviews"
```

That runs **every enabled channel in parallel**, writes one `<CHANNEL>.md` per channel into
`--out`, renders `REPORT.md`, and prints a verification block.

🔴 **Do not count the channels from this file, and do not list them** — every prose copy has been
wrong within days. `python routing.py` prints the live set and spends nothing. Output filenames
are the registry key, upper-cased.

🔴 **Some channels are deliberately off HERE and on in the published kit, and vice versa**, per
`distribution`: this machine holds direct vendor keys; the kit's story is one OpenRouter key.
`--dry-run` shows which.

| flag | what it does |
|---|---|
| `--brief` | file with the question. Required unless `--ask`. Last line should ask for your marker |
| `--ask` | **one-shot question instead of a round.** `--ask "text"` or `--ask @file`. Default per `ask_default` in channels.json; `--ask-channel` overrides. Prints the ANSWER to stdout; no citation audit. ~20 s |
| `--tier` | **one tier, `max`, and it is the default.** `strategic`/`deep` are aliases kept so old commands run. Depth is never a choice — see §2 |
| `--panel` | `cheap` (default) · `standard`. WHO is in the room; never changes depth. See §2 |
| `--marker` | literal string the reply must end with; absent ⇒ incomplete |
| `--out` | output directory. Default `./reviews` |
| `--system` | preset name or path (§0.2). The no-non-existence rule is appended — end the file with a newline |
| `--attach` / `--attach-dir` | document / folder beside the brief. CLI channels: files by **absolute path**, folders as a **vetted copy** of scanned text (skips listed; original path never sent); API channels: files **inline**, folders named unreadable. 🔴 Refs trust the attachment: only material you authored |
| `--answer-cap` | ask every reviewer to keep the FINAL answer under N chars (default `20000` ≈ 5K tokens; `0` = off and the `--ask` default). Prompt-level at the payload TAIL, after attachments — depth and `max_tokens` untouched; `HANDOFF.md` names who exceeded it or declared `TRUNCATED-BY-LIMIT` |
| `--only` | restrict channels. Names, aliases and **group** words all work — vendor families and billing paths; `routing.py` prints every accepted spelling. 🔴 One channel per argument: `--only a b c`, never `--only "a b c"` |
| `--skip` | the inverse of `--only`; on a clash (`--only <group> --skip <member>`) **skip wins** |
| `--set` | pin a model: `--set codex=gpt-5.4` |
| `--route` | **paste what the operator typed, verbatim** — see §0.1 |
| `--dry-run` | full preflight (plan, payload, keys, gates), then exit — spends nothing |
| `--strict-pii` | identifier gate **OFF by default since R45** (summary line, then send); `--warn-pii` itemises, `--strict-pii` refuses, `--allow-pii` is a no-op. **Secrets can never be sent, at any setting** |

### 0.1 Choosing channels and models without editing code

Weekly limits run out on one model at a time, so the channel/model choice changes per request,
in prose: *«не использовать 5.6 Sol, а использовать вместо нее 5.5»*, *«не использовать для
этого промта Spark»*. Paste that string into `--route`. Parsed deterministically (RU/EN,
negation, substitution incl. anaphora, *«только»*) against the alias table in
**`channels.json`** — the single home of every model name.

```powershell
--route "не использовать 5.6 Sol, а использовать вместо нее 5.5. И не использовать для этого промта Spark"
```

The resolved plan is **always printed before anything is spent**: the panel and what the other
panel would add or drop, then one `[RUN ]`/`[skip]` line per channel with its model, effort,
role, data policy, web access, spend ceiling and tier effect, then the running set and its
**vendor tally**. Read it; it is the one screen that exists to be read before money moves.

Rules that matter: an unparseable route is a **hard stop**, never a guess. A refused model with
no stated replacement falls back to the next in registry order, on a `NOTE:` line. Adding a
model or channel is a `channels.json` edit only. Checking costs nothing: `--dry-run`.

### 0.2 System presets — one line each, detail in `references/systems.md`

`--system <preset|path>` frames the reviewer. Two presets: **`base-depth`** (the default — the
maximum-depth amplifier) and **`legal-research`** (any legal / immigration / regulatory brief —
read `references/legal-briefs.md` **before** writing it). All presets force English output.
⚠️ Not interchangeable and never merged — the legal one deliberately omits a clause; the why is
in `references/systems.md`.

**Timing**: most channels 1–4 min; **Codex 8–35 min** sets the wall-clock (`--panel cheap` is
far faster). Run rounds in the background; sanity-check with `--ask` (~20 s).

---

## 1. What is on this machine — ask, do not assume


**Never pin a version here** — both CLIs moved inside one week. Ask instead:

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
| agy models | one model per channel, base slug plus `--effort`. **Not** `gemini-3.1-pro-high` — a suffixed slug plus a disagreeing `--effort` is exit 1 in 3 s (`references/channels.md` §6.3) |
| Grok Build CLI | `grok`, **not on PATH**; `GROK_BIN` → PATH → `%USERPROFILE%\.grok\bin`. Subscription session, no key. Reads `CLAUDE.md` from its cwd upward → neutral cwd. 🔴 **One denied tool discards the whole turn**, and `--tools` does not bound the MCP gateway. Flags in `channels.json` |
| harness | `orchestrate.py`, standard library only, no `pip install`, Python 3.8+ (`patch_codex_firecrawl.py` alone needs 3.11+ and says so) |

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
| **`cheap`** (default since 2026-08-16) | free, subscription and low-rate channels — all **except** spark11, codex, kimik3, qwen38max and the rationed opt-in channel where the registry has one. *«запусти дешевые»* |
| **`standard`** | every enabled channel. *«запусти все»*, *«стандартная панель»* |

🔴 **Neither a panel nor a GROUP enables anything.** `--only openrouter`, *«только грок»* run only
the members already on. **Naming a channel is the one way to start one that ships off** —
`--only goog36flash`. 🔴 **What `cheap` costs is vendor diversity, not depth**: it drops OpenAI,
Moonshot, Alibaba, DeepSeek and Meta-Standard; the plan prints the vendor tally and what
`standard` adds — six Geminis agreeing is one opinion repeated.

**`--tier` parses and chooses nothing.** One tier, `max`; `strategic`/`deep` are aliases so old
commands work. `quick` is still an argparse error. Resolved depth is printed per channel.

**A below-floor note names its cause.** With the default `--answer-cap` a short FINAL answer is
what was asked for; "under-allocated" means something only with the cap off.
Streaming is automatic above a 32,000 budget — why, in `references/channels.md`.

**Your own settings go in `~/.claude/model-orchestration.local.json`, never in `channels.json`** —
an update replaces the skill folder and cannot reach that file. `{"channels": {"<name>":
{"enabled": true}}}`. There it may change anything and add channels or tiers (`"_new": true`).
Both files' changes print in the plan. 🔴 A
change to WHERE a document goes needs `python routing.py --accept-settings` once, or a paid round
refuses. Rules and errors: `references/when-it-breaks.md`. Updating: `python upgrade.py`.

🔴 **A depth knob you have only SENT is not a depth knob.** `python echocheck.py --only <channel>
--samples 3` judges by the reasoning counter that comes back, refusing CONFIRMED on overlap.

---

## 3. Verify the answer is real — the whole point


A call that ran is not a review that happened. The harness prints all of this.

**Hard failures — the answer is unusable, never report it as a review:**
1. `stop_reason` is anything other than `end_turn` → truncated, the tail of the analysis is gone.
2. The marker is not present → output is incomplete.
3. Empty body despite HTTP 200. 🔴 **Read the warning before diagnosing it** — it now names which
   of three things happened: the reasoning filled `max_tokens` (raise it or split the brief), the
   vendor ended its loop mid-turn, or the model spent its last turn calling tools.

**Soft signals — the answer exists, judge it yourself:**
4. **Tool-invocation count is 0.** `tools:` is *permission*, not instruction. A model handed web
   search may never call it and then answer dated questions from training data.
   **Zero means every dated fact in that answer is unverified.**
5. Output tokens below the floor, *with an uncapped brief* → the model under-allocated. There is
   no deeper setting; split the question into more sub-questions instead.

Never let a soft signal mark a run failed. A check that cries wolf on a good answer trains you to
ignore the alarm that matters — made twice here, fixed twice.

---

## 9. When it breaks

**The symptom → cause → fix table is `references/when-it-breaks.md`.** Read it before diagnosing
anything from source. Three that decide what you do next:

- **A status field is not evidence.** `agy` reports `SUCCESS` on an empty answer; `codex exec`
  exits 0 on a hard 400; an HTTP 200 can mean only that the request parsed. Judge by the end
  marker and the counters.
- **`FILTERED` on the Spark probe**: neutralise the phrasing **in the sent copy only**, never on
  disk, and strip appendices. Do not retry unchanged.
- **A channel is off and nothing explains why**: your own settings file, whose path and changed
  fields the plan prints at the top.
- 🔴 **The LAUNCH is refused with «Auto mode could not evaluate this action»**: the permission
  classifier returned no verdict — not a decision about your command. **Retry the identical
  command**, never a rewritten one. Measured; the rule lives in `~/.claude/CLAUDE.md`.

---

## 10. Report back — always


🔴 **Name every channel WITH its model and depth** — `codex (gpt-5.4 @ xhigh)`, never a bare name
(the operator, R46: «не просто Codex, а Codex5.4Xhigh»). Per channel: seconds, in/out tokens, **tool/fetch
count**, **grounding** (pages we fetched / vendor-stated / none) and **citations in the text** — a
web-capable channel that cited nothing is a fact to state, not a blank column: the review is
training-data-plus-brief, which R45's table hid. Then separately: **accepted**, **rejected with
proof**, and where the channels **disagreed with each other**.

The disagreement is the product; one reviewer is not a second opinion. **Which channel wins is not
predictable from cost or from the last round** — four rounds, four winners. And convergence is not
independence: R49 had three channels agree on a Cyrillic token ratio that one measurement refuted.

One shape recurs: the highest-value finding was **not an answer to a question that was asked**. It
arrived under "what are we missing". Always include that question.

🔴 **`HANDOFF.md` is the reading list — a `listdir`, never built by hand — priced in tokens and
sorted smart-first** (`read` 1 = frontier reasoners, 3 = flash-class and measured-weak; tiers in
`channels.json → read_order`). **Read the answers YOURSELF in that order — never via sub-agents;
open the 3s only if context room remains. ★ = the mandatory minimum** (the operator 2026-09-01): read
that row yourself whatever the pressure, even before a deferred round's telemetry report —
flag `must_read` in `channels.json`. With the default `--answer-cap` the full read is usually
direct-readable. Over ~40K tokens with a session already large: do NOT read them this turn —
report the telemetry, hand the operator the resume prompt, let a fresh context read after `/compact`.
Detail: `references/reading-the-answers.md`.

---

## 11. Relationship to the other skill

`second-opinion-consult` holds the **policy**; **this skill is the only home for the mechanics** —
the copy there went stale in four places without looking stale. This file alone runs a round.
