# model-orchestration — review kit

Send one brief to three independent external reviewer models **in parallel**, then verify that
each one actually did the work instead of trusting its exit code.

| channel | what it is | speed | cost |
|---|---|---|---|
| **Spark** | Muse Spark 1.1 over the Messages API | 2–5 min | metered per API key |
| **Codex** | OpenAI Codex CLI | **25–35 min** | your subscription's heaviest tier |
| **agy** | Antigravity CLI (Gemini 3.1 Pro) | ~1 min | your Google subscription |

The point is not redundancy, it is **disagreement**. Measured across two rounds on real work, the
ordering inverted: once the 55-second channel found the item both slower ones missed, once the
25-minute one did. Which channel wins is not predictable from cost or from the previous round —
that is the entire argument for running all three.

---

## 1. What you must supply yourself

Nothing in this kit provisions an account. Install it and you still have three empty channels
until each of these is true **for you personally**:

| you need | how to check | if you don't have it |
|---|---|---|
| **Python 3.8+** on PATH | `python --version` | install it; the harness is standard-library only, there is nothing to `pip install` |
| **Codex CLI** + a plan that includes Codex | `codex --version`, then `codex login` | the Codex channel is unavailable; run with `--skip codex` |
| **Antigravity CLI** (`agy`) + an eligible Google account | `agy --version` | run with `--skip agy` |
| **`MODEL_API_KEY`** for Spark | `doctor.py` reports presence and length | run with `--skip spark` |

Two of those deserve a straight answer rather than a table row.

**🔴 The API key is per person. Do not share one.** A single `MODEL_API_KEY` is metered against
whoever owns it: shared, one person's card silently pays for everyone's runs, nobody can be
attributed, and revoking it takes down the whole team at once. If your organisation wants Spark
for everybody, issue a key per person. If that is not on offer, run the two CLI channels and
`--skip spark` — the harness is designed to run any subset.

**🔴 The CLI channels bill against a *subscription*, not an API key, and subscriptions have weekly
limits.** That is not a footnote, it is the reason the routing layer exists: when one model's
limit runs out you re-point the round in prose (`--route "don't use 5.6, use 5.5 instead"`)
instead of editing code. **Do not answer an exhausted limit by opening a metered API path** —
that is usually several times more expensive than waiting or switching channel.

**The `gemini` CLI (`@google/gemini-cli`) is not a substitute for `agy`.** On a personal-tier
account it fails with `IneligibleTierError / UNSUPPORTED_CLIENT`.

---

## 2. Install

### Option A — as a Claude Code plugin (recommended)

This repository is a plugin marketplace. In Claude Code:

```
/plugin marketplace add <owner>/<this-repo>
/plugin install model-orchestration@review-channels
/reload-plugins
```

Updates arrive automatically: Claude Code checks after session start and prompts you to
`/reload-plugins`. A private repository works — you just need normal git access to it.

To distribute it to a whole team without each person typing the commands, put this in the team
repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "review-channels": { "source": { "source": "github", "repo": "<owner>/<this-repo>" } },
  },
  "enabledPlugins": ["model-orchestration@review-channels"]
}
```

Anyone who clones that repo and trusts the folder is prompted to install it.

### Option B — as a personal skill (no git, no plugin system)

```powershell
.\install.ps1        # Windows
```
```bash
./install.sh         # macOS / Linux
```

Copies the skill into `~/.claude/skills/model-orchestration/`, backs up any previous install, and
runs the doctor. Works from a zip; no network needed after download.

### Then, on every machine, once:

```
python <SKILL_DIR>/doctor.py
```

Its last line is your exact run command with the real path already filled in. Do not copy a path
out of any document — ask the doctor.

---

## 3. The one post-install step that is not optional

If you will use the **agy** channel:

```
python <SKILL_DIR>/patch_agy_permissions.py --dry-run   # see the diff first
python <SKILL_DIR>/patch_agy_permissions.py             # apply
```

**Why it matters, measured 5 runs out of 5:** in headless mode `agy` cannot display a permission
prompt, so any tool still set to "ask" is auto-denied — and **one denial throws the entire run
away**. What comes back is `response: ""` with `status: "SUCCESS"` and **exit code 0**, after
dozens of successful tool calls. Nothing about the exit code, the status field or the elapsed time
tells you it happened. With the patch applied, the same brief went from an empty answer to 49 tool
calls and 3× the reasoning tokens.

The script edits only `~/.gemini/antigravity-cli/settings.json`, is additive and idempotent, backs
up before writing, and has `--revert`. It also **denies the metered Firecrawl tools** (`crawl`
bills per page with no ceiling; `monitor_*` bills on a schedule with nobody watching), which is
why the answer to a permission problem is never `--dangerously-skip-permissions` — that flag
unlocks those too.

`doctor.py` checks this on every run and tells you if the file has been reverted or rewritten.

---

## 4. Running a review

```powershell
python <SKILL_DIR>/orchestrate.py `
  --brief brief.md --tier strategic --marker REVIEW-DONE-01 --out reviews --dry-run
```

`--dry-run` is a **complete** preflight — plan, brief, system preset, API key, both binaries, agy
permissions, and the personal-data gate — and spends nothing. Run it first, every time. Drop it to
launch for real.

Useful flags: `--only` / `--skip` (any channel alias works: `spark`/`http`, `codex`,
`agy`/`gemini`), `--set codex=gpt-5.4` to pin a model, `--route "<what the user actually typed>"`
for prose routing, `--system legal-research` for regulated-domain briefs.

Everything else — the depth tiers, the flag traps, how to tell a real review from a fluent one —
is in `SKILL.md` and the `references/` files beside it. Claude Code reads them on demand.

---

## 5. Rules that are enforced in code, not in prose

This kit assumes prose does not restrain anything, because that was measured: an instruction in
the model's own system prompt failed **5 times out of 5** to stop it calling a tool. Only
permission rules worked. So the things that must not happen are checks, not sentences.

- **A payload containing a key, token or private key is never sent.** There is no override flag.
- **A payload containing personal identifiers is blocked** — alien numbers, case/receipt numbers,
  SSNs, emails, phone numbers, a labelled date of birth. Tokenize them in the *sent copy* only
  (`APPLICANT_1`, `[CASE-NUMBER]`) and tell the model the placeholders are expected; a reviewer
  never needs real identifiers to review reasoning. If they genuinely belong, pass `--allow-pii`
  deliberately. The gate reports **kind and line number, never the value** — printing it would
  leak it into the transcript, which is the same mistake one step earlier.
  **Once a payload is sent it cannot be recalled.** It is at three separate vendors.
- **A refusal cannot pass as a review.** A model that declines still obeys the formatting
  instruction, so it appends your end marker and clears every mechanical check. Measured: a
  162-byte refusal was reported as a successful channel.
- **Citations are cross-checked against pages actually opened.** One channel fabricated four
  real-looking document numbers in a single session, each under the correct article slug, and one
  of them was even fetched successfully because the site resolves by number and ignores the slug.
  Grounding varied 1/6 → 4/4 → 5/5 across runs of the *same* brief, so this is checked every run,
  not once.

---

## 6. Cost discipline

1. **Spark first** for anything a competent researcher with a search engine would settle.
2. **agy** when that is inconclusive, for large-context reading, and as the fast sanity check.
3. **Codex last.** It is for judgement calls and line-level audits of a finished artifact.
   Sending it a lookup wastes 25 minutes and the most expensive quota you have.

That ladder governs ordinary work. It does **not** apply to a commissioned second opinion: there,
run all three on the identical packet, because the disagreement is the product.

---

## 7. Configuring it for your own models

Every model name lives in **`channels.json`** and nowhere else — adding, disabling or re-pointing
a channel is an edit there, never in `orchestrate.py`. Aliases in that file are what prose routing
matches against, and a duplicate alias is rejected at load time (where it is a typo) rather than
at spend time (where it is the wrong model).

`systems/` holds the system-prompt presets. Two ship: `base-depth` (the default) and
`legal-research`.

⚠️ **Do not merge the two presets.** `base-depth` asks for "unofficial, grey routes alongside the
official one", which is right for engineering and wrong for a regulated domain, where it reads as
*suggest a way around the rule* — the exact trigger that gets a legal brief refused. The omission
in `legal-research` is deliberate and is documented inside both files.

---

## 8. When something breaks

Run `doctor.py` first — it names the broken thing and what to do about it. Then `SKILL.md` §9,
which is a symptom → cause → fix table built entirely from failures that actually happened.

Two habits worth having from day one: **never pin a version in a document** (both CLIs moved under
this kit inside one week, and stale versions in prose do not look stale — they look like
documentation), and **never validate a channel on its exit code**. Both `status` and the exit code
have been observed lying in both directions on the same channel.
