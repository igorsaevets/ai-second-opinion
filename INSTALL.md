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

It copies the skill into your home directory, **backs up any existing install first** (it will
never silently overwrite a copy you have edited), and then runs the doctor.

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

There are **seven** channels. **You need at least one** — the tool runs whatever is available and
tells you exactly what it skipped and why. A missing key or a missing CLI is a normal, non-fatal
condition, not an error.

| channel | what it needs | cost |
|---|---|---|
| `spark11`, `spark12cont` | `MODEL_API_KEY` | metered API |
| `kimik3`, `qwen38max` | `OPENROUTER_API_KEY` | metered API |
| `codex` | the Codex CLI, signed in | your existing subscription |
| `agy31pro`, `agy36flash` | the Antigravity CLI, signed in | your existing subscription |

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
