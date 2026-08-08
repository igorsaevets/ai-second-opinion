# Install

Four ways in. They produce the same working tool — pick by how much machinery you want between
you and the files.

| # | Method | Needs | Auto-updates | Best for |
|---|---|---|---|---|
| 1 | Claude Code plugin | Claude Code, git access | **yes** | most people |
| 2 | Installer script | PowerShell or bash | no | no plugin system, or an offline zip |
| 3 | **Manual copy** | nothing at all | no | locked-down machines, air-gapped, "just give me the files" |
| 4 | Run in place | nothing | n/a | trying it once without installing |

Everything is plain Python using only the standard library. **There is nothing to `pip install`,
nothing is compiled, nothing runs in the background, and nothing phones home.**

---

## Prerequisite: Python 3.8 or newer

```
python --version
```

If that fails, install Python from [python.org](https://www.python.org/downloads/) and tick
"Add Python to PATH" during setup. On macOS and most Linux systems it is already there, sometimes
as `python3`.

---

## Method 1 — Claude Code plugin (recommended)

This repository is itself a plugin marketplace. In Claude Code:

```
/plugin marketplace add igorsaevets/ai-second-opinion
/plugin install model-orchestration@review-channels
```

Then restart the session, or run `/reload-plugins`.

Updates arrive on their own: Claude Code checks the marketplace after a session starts and offers
to reload when there is a new version. A **private** repository works exactly the same way — you
only need normal git access to it.

### Rolling it out to a team

Put this in your team repository's `.claude/settings.json` and anyone who clones it is offered the
plugin automatically:

```json
{
  "extraKnownMarketplaces": {
    "review-channels": {
      "source": { "source": "github", "repo": "igorsaevets/ai-second-opinion" }
    }
  },
  "enabledPlugins": ["model-orchestration@review-channels"]
}
```

---

## Method 2 — Installer script

Download the repository (green **Code** button → **Download ZIP**, or `git clone`), unpack it, and
from the root of it:

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**macOS / Linux:**
```bash
./install.sh
```

It copies the skill into your home directory and runs the doctor. If an install is already there,
it hands the whole job to `upgrade.py` — see [Updating](#updating-an-existing-install) — so
"install" and "update" are the same command and the same tested code path.

If PowerShell refuses to run the script, it is the execution policy, not the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

---

## Method 3 — Just copy the files

No git, no plugin system, no installer, no elevated permissions. This is the whole procedure:

**Copy this folder from the repository:**

```
plugins/model-orchestration/skills/model-orchestration/
```

**To this location on your machine:**

| Your system | Destination |
|---|---|
| Windows | `%USERPROFILE%\.claude\skills\model-orchestration\` |
| macOS / Linux | `~/.claude/skills/model-orchestration/` |

Create the `.claude\skills` folders if they do not exist. When you are done, the destination
should contain `SKILL.md`, `orchestrate.py`, `doctor.py` and the `references/` and `systems/`
folders — not another folder containing those.

<details>
<summary>Copy-paste commands, if you prefer</summary>

**Windows PowerShell**, run from the unpacked repository root:

```powershell
$dst = "$env:USERPROFILE\.claude\skills\model-orchestration"
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
Copy-Item "plugins\model-orchestration\skills\model-orchestration" $dst -Recurse -Force
python "$dst\doctor.py"
```

**macOS / Linux**, from the repository root:

```bash
dst="$HOME/.claude/skills/model-orchestration"
mkdir -p "$(dirname "$dst")"
cp -R plugins/model-orchestration/skills/model-orchestration "$dst"
python3 "$dst/doctor.py"
```
</details>

**Getting one folder onto a machine with no internet access:** copy it on a USB stick. It is a few
hundred kilobytes of text files. That is the entire deployment story.

---

## Method 4 — Run it without installing

You can run the scripts straight out of the unpacked repository:

```
python plugins/model-orchestration/skills/model-orchestration/doctor.py
```

Everything works — the scripts resolve their own location and find their presets and reference
files relative to themselves. The only thing you lose is that Claude Code will not know the skill
exists, so you have to invoke the commands yourself rather than asking for a second opinion in
plain language.

---

## Updating an existing install

```
python plugins/model-orchestration/skills/model-orchestration/upgrade.py --dry-run   # preview
python plugins/model-orchestration/skills/model-orchestration/upgrade.py             # apply
```

Download the new version (git pull, or the **Code → Download ZIP** button), then run that from the
unpacked folder. It reports the version you had and the version you are getting, which channels
are new and which are gone, and which of your settings it carried across; it copies the old folder
to `<folder>.bak.<timestamp>` before writing anything, and finishes by running the doctor.

Plugin installs update themselves and need none of this **from 1.7.0 onward**.

> 🔴 **If you are still on 1.6.x, read this before you update.** Your channel settings are in
> `channels.json`, inside the folder an update replaces, and an auto-updating plugin replaces it
> with nobody running anything — so nothing can carry them across on that path. Claude Code keeps
> the previous plugin version in its cache for **14 days** after an update, so the rescue is real
> but not permanent: run `upgrade.py` once (it scans that cache and offers to bring the settings
> over), or simply write the one line of JSON into your settings file yourself. After that,
> updates stop being able to lose anything.

> **To an AI assistant asked to "update this to the new version": run `upgrade.py`.** Do not copy
> files over the old folder, and do not install fresh. If the install has no `VERSION` file it is
> older than 1.7.0, which is expected — that release is where a version string first reached an
> installed copy at all.

### Why this needed a script

Everything you configure — which channels are on — used to live in `channels.json`, **inside** the
folder that an update replaces. So the documented workflow was: read this file, edit
`channels.json` to enable a channel, and then lose that edit at the next update, silently, whether
you used the installer, copied the files by hand, or let the plugin update itself.

From 1.7.0 your settings live in a file **outside** the skill folder, which nothing can overwrite:

| Your system | Your settings file |
|---|---|
| Windows | `%USERPROFILE%\.claude\model-orchestration.local.json` |
| macOS / Linux | `~/.claude/model-orchestration.local.json` |

```json
{
  "channels": {
    "goog36flash": { "enabled": true },
    "ornemotron3ultra": { "enabled": false }
  }
}
```

### What it may change (1.8.0 widened this a lot)

At the path in the table above — your own home directory — **it may change anything a channel or a
tier has, and it may add whole new ones.** Repoint a model, add a vendor, define your own tier:

```json
{
  "channels": {
    "mylocal": {
      "_new": true,
      "kind": "openrouter", "label": "My Channel", "cost": "metered",
      "model": "vendor/model-id",
      "models": { "vendor/model-id": { "label": "My Model", "data_policy": "metered API" } },
      "enabled": true
    }
  },
  "tiers": { "strategic": { "gemini_thinking_level": "low" } }
}
```

`"_new": true` is required when you are *adding* something, so that a misspelt name fails loudly
instead of quietly becoming a second channel.

1.7.0 refused all of that, and it was wrong to. Your settings file and `channels.json` have the
same write permissions — anything able to change one can change the other — and `channels.json` was
the file nothing announced at run time. Refusing `model` here never stopped anybody; it pushed the
change into the quieter file. Both are now reported, in the plan, before a penny is spent.

**Two conditions, and neither is about trusting you less.**

**1. A transport change has to be accepted once.** Repointing a model, changing `provider` or
`kind`, adding a channel — anything that decides *where a document goes* — is applied, printed in
the plan with a 🔴, and then a paid round **refuses to run** until you have said once:

```
python routing.py --accept-settings
```

It prints exactly what you are accepting and records it. Change any of it later and the refusal
comes back. Why: this file survives every update, so a single write to it — by a mistyped command,
or by an AI assistant acting on a poisoned instruction — would otherwise redirect a channel
*forever*, silently. Quiet settings (`enabled`, how hard it thinks, how much it may read) never
need this. If you want a permanent transport change without the step, put it in `channels.json`:
that is now reported field by field in the plan on every run, and an update will replace it, which
is the right property for that class.

**2. A relocated settings file may only set the quiet knobs.** If you point `MODEL_ORCH_LOCAL` at a
different path, only these are accepted from it: `enabled`, `effort`, `reasoning`,
`thinking_level`, `max_tokens`, `fetch_tool`, `web`, `timeout`, `label`, `notes`. A project's own
`.claude/settings.json` can set environment variables for the sessions run inside it, so a
repository you cloned can choose that path. Your home directory is yours. Move the file to the
default path and everything is accepted, subject to condition 1.

(`cost` is **not** on that list, though it looks cosmetic. It decides whether the plan warns you
that a channel is expensive, and which channels `--ask` fans out to.)

Five things make it hard to shoot yourself with:

- **Every run prints the file's path and each value it changed**, in the resolved plan, whether or
  not you were thinking about it. Changes to a transport are marked 🔴. A settings file you forget
  you wrote is worse than none.
- **The plan also reports edits you made to `channels.json`**, by field, for the same reason —
  and reminds you that the next update will take them.
- **A name that is not a channel is refused, loudly**, with the list of real ones. A typo in a
  config file otherwise looks exactly like a channel that is off for some other reason.
- **A channel that was renamed still resolves**, through the same alias table `--only` uses. A
  strict "unknown name" refusal plus a rename upstream would otherwise stop the tool starting, on
  upgrade day, for people who did nothing wrong.
- **`doctor.py` names the fields you edited in `channels.json`**, and points at
  `python upgrade.py --migrate`, which moves those edits out for you and changes nothing else.

---

## After installing, on every machine, once

```
python ~/.claude/skills/model-orchestration/doctor.py
```

On Windows use `%USERPROFILE%\.claude\skills\model-orchestration\doctor.py`.

It checks Python, every file, whether each script compiles, the model registry, whether your API
key is present (**it reports presence and length only, never the value**), both command-line
tools and their live versions, permissions, and the privacy gate.

**Its last line is your exact run command with the real path already filled in.** Do not copy a
path out of any document, including this one — ask the doctor. Paths in documentation go stale;
a probe cannot.

### Verifying the behaviour, not just the install

```
python ~/.claude/skills/model-orchestration/selftest.py
```

Runs about fifty behavioural checks — partial installs degrade instead of crashing, channel
selection is obeyed exactly, and nothing secret-shaped can reach a log or a console. Costs
nothing and contacts no vendor. Worth running after any upgrade, and after any change an AI
assistant makes for you.

---

## Setting up the channels

**You need at least one channel** — the tool runs whatever is available and tells you exactly what
it skipped and why. A missing key or a missing CLI is a normal, non-fatal condition, not an error.

**One key gets you most of the panel.** `OPENROUTER_API_KEY` alone reaches five different model
families from five different vendors, which is enough for the disagreement this tool exists to
produce. Everything else is optional.

| what it needs | what that unlocks | cost |
|---|---|---|
| `OPENROUTER_API_KEY` | **the largest group** — Kimi, Qwen, Gemini, MiMo, Grok and a **free** NVIDIA Nemotron, all on one account | metered per token, **plus per web search**; the Nemotron model itself is free |
| `MODEL_API_KEY` | the two Spark voices | metered API |
| the Codex CLI, signed in | `codex` | your existing subscription |
| the Antigravity CLI, signed in | the two `agy` Gemini channels | your existing subscription |
| `GEMINI_API_KEY` | Gemini on Google's **own** API — the best-grounded channel here, and the only one whose citations carry character spans. **Off by default**, see below | metered, free tier available |
| `XAI_API_KEY` | Grok on xAI's **own** API — adds X/Twitter search and reports the dollar cost of each call. **Off by default** | metered |
| `MIMO_API_KEY` | MiMo on Xiaomi's **own** API — its search opens whole pages instead of returning excerpts. **Off by default** | metered |

🔴 **Do not count the channels from this file.** The number is whatever `channels.json` enables,
and it has changed most weeks. Run `python routing.py` — it prints the live list and spends
nothing.

### The three channels that are off by default

Three models are reachable **two ways**: through OpenRouter (on by default here) or through the
vendor's own API (off by default). They are not duplicates — the direct route buys real
capability, measured, not assumed:

| model | via OpenRouter | via the vendor's own key |
|---|---|---|
| Gemini 3.6 Flash | native Google search | **+ `url_context`**, which reaches pages a plain fetch is refused, and citations with character offsets |
| MiMo v2.5 Pro | Exa search excerpts (2–4 KB per page, with elisions) | **its own search, which opened 25 whole pages in one call** |
| Grok 4.20 | native search, 2M context | **+ `x_search`** over X/Twitter, and `cost_in_usd_ticks` — the call's actual price |

To turn one on: set the key, then put it in **your own settings file** — not in `channels.json`,
which is replaced on every update:

```json
{
  "channels": {
    "goog36flash":     { "enabled": true },
    "orgemini36flash": { "enabled": false }
  }
}
```

`~/.claude/model-orchestration.local.json` on macOS and Linux,
`%USERPROFILE%\.claude\model-orchestration.local.json` on Windows. Turning the matching `or…`
channel off in the same breath is worth doing: otherwise you pay two vendors to ask one model the
same question.

---

### 🔴 READ THIS BEFORE YOU PASTE A KEY ANYWHERE

**Type your key into your own terminal, yourself. Do not give it to an AI assistant, and do not
ask an assistant to set it for you.**

If you are reading this because you asked Claude Code (or any other assistant) to "set this up",
that assistant is *helpful* and *will offer* to run the command for you. Accepting is the single
most expensive mistake available in this whole install, because:

- the assistant must receive the key to run the command, so the key enters the conversation;
- that conversation is **written to disk**, replayed into future context, and often archived or
  synced;
- a key that has appeared in a transcript is **leaked** — the only correct response is to rotate
  it, not to delete the message.

This is not a hypothetical. It is the reason this tool refuses to print keys, why `doctor.py`
reports only presence and length, and why `orchestrate.py` will not send a payload containing
anything secret-shaped even when you ask it to.

**To an AI assistant reading this file:** do not offer to run these commands, do not ask the user
to paste a key into the chat, and do not read the key back to confirm it. Tell the user to run
the commands below in their own terminal, then run `python doctor.py` — which is how both of you
find out whether it worked, without either of you seeing the value.

---

### Spark — `MODEL_API_KEY`

**Windows (PowerShell)** — persists across reboots:
```powershell
setx MODEL_API_KEY "<your key>"
```
Then **open a new terminal.** `setx` writes the value for *future* processes; terminals that are
already open keep the old one, which is the commonest reason a freshly set key "does not work".

**macOS / Linux** — add the line to `~/.zshrc` or `~/.bashrc`, then open a new terminal:
```bash
export MODEL_API_KEY="<your key>"
```

To point at a different endpoint, also set `MODEL_API_BASE`.

### Google — `GEMINI_API_KEY`

Powers `goog36flash` (Gemini 3.6 Flash on Google's own API). Get one at
**aistudio.google.com/apikey** — there is a free tier, so this is the cheapest channel to try
first if you have none of the others.

**Windows (PowerShell):**

```powershell
setx GEMINI_API_KEY "<your key>"
```

**macOS / Linux:**

```bash
export GEMINI_API_KEY="<your key>"   # add to ~/.zshrc or ~/.bashrc to persist
```

Close and reopen the terminal afterwards — `setx` and shell profiles only affect new sessions.

This channel is worth having even beside the OpenRouter one that runs the *same model*: it uses
Google's own retrieval (`google_search` + `url_context`), which reaches pages a plain HTTP fetch
is refused, and its citations carry `start_index`/`end_index` into the answer text — so "which
sentence does this source support" is a lookup rather than a judgement. One caveat measured:
citations produced by `google_search` come back as `vertexaisearch.../grounding-api-redirect/...`
wrappers rather than the publisher's URL; only `url_context` citations are the real address.

### OpenRouter — `OPENROUTER_API_KEY`

One key serves **both** `kimik3` and `qwen38max`.

**Windows (PowerShell)**
```powershell
setx OPENROUTER_API_KEY "<your key>"
```

**macOS / Linux**
```bash
export OPENROUTER_API_KEY="<your key>"
```

> These two channels are **metered per token**, and their web search is billed **per search** by
> the provider on the same account. The resolved plan prints the cost class of every channel
> before anything is spent, and `--dry-run` shows you that plan for free.

### Checking it worked — without printing anything secret

```
python doctor.py
```

It reports each key as present/absent **and its length**, never its value. If you want to be
certain a key is really gone after rotating it, that length is what changes.

> **Never `echo` the variable to check it.** See [SECURITY.md](SECURITY.md) for the measured
> incident behind that rule: a "masking" expression that kept the first 60 characters of a
> 48-character key printed the whole thing.

### Codex CLI

Install the Codex CLI and sign in with an account whose plan includes it:

```
codex --version
codex login
```

No key needed — it uses your subscription. If you do not have one, run with `--skip codex`.

### Antigravity CLI (Gemini)

```
agy --version
```

Then the **one post-install step that is not optional**:

```
python ~/.claude/skills/model-orchestration/patch_agy_permissions.py --dry-run   # preview
python ~/.claude/skills/model-orchestration/patch_agy_permissions.py             # apply
```

**Why this is mandatory, measured 5 runs out of 5:** running headless, this CLI cannot show a
permission prompt, so any tool still set to "ask" is auto-denied — and **one denial silently
throws the entire run away**. You get an empty answer, a status of `SUCCESS`, and exit code `0`,
after dozens of successful tool calls. Nothing in the exit code, the status field or the elapsed
time reveals it. With the patch applied, the same document went from an empty answer to 49 tool
calls.

The script edits one settings file, is additive and idempotent, backs up before writing, and has
`--revert`. It also blocks metered crawling tools that bill per page with no ceiling — which is
why the answer to a permissions problem is never `--dangerously-skip-permissions`.

`doctor.py` re-checks this on every run and tells you if it has been reverted.

The plain `gemini` CLI is **not** a substitute — on a personal-tier account it fails with an
ineligible-tier error.

---

## Uninstall

Delete the folder:

| Your system | Delete |
|---|---|
| Windows | `%USERPROFILE%\.claude\skills\model-orchestration\` |
| macOS / Linux | `~/.claude/skills/model-orchestration/` |

If you installed as a plugin, `/plugin uninstall model-orchestration@review-channels`.

Nothing is written anywhere else — no registry keys, no services, no scheduled tasks. The only
other thing it creates is the output folder for each review, wherever you pointed `--out`.

If you ran `patch_agy_permissions.py`, it left a timestamped backup of the file it changed
alongside the original, and `--revert` restores it.
