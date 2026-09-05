# AI Second Opinion

[![selftest](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml/badge.svg)](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](INSTALL.md)

**One AI agreeing with you proves nothing. Three of them arguing is worth reading.**

Send the same document to a **panel of independent AI models** — GPT, Claude, Gemini, Grok,
DeepSeek, Qwen, Kimi, Muse Spark, NVIDIA Nemotron and more — at once. Get back what each one
found, where they contradict each other, and a mechanical check that catches **AI
hallucinations**: fabricated citations, invented sources, and quiet refusals.

Install as a [Claude Code plugin](#install), run standalone from any terminal, or hand the repo
to your AI coding assistant (Claude Code, Cursor, Windsurf — anything with shell access).
Pure Python, zero dependencies, MIT license.

[Русская версия](README.ru.md) · [How it works, in technical detail](TECHNICAL.md) ·
[Install](INSTALL.md) · [When something breaks](TROUBLESHOOTING.md) ·
[For AI agents](AGENTS.md)

---

## The problem this solves

You ask an AI to review your strategy memo. It tells you the memo is strong, adds three
supportive points, and cites four sources.

That answer is nearly worthless, for three reasons most people never check:

1. **It is built to agree with you.** You wrote the memo, you asked the question, and the model
   optimises for a helpful-feeling reply. Ask the same model to attack the memo and it will find
   problems it just told you did not exist.
2. **One model has one set of blind spots.** Whatever it was weak at yesterday, it is weak at
   today, and nothing in its answer tells you which parts those are.
3. **The sources may not exist.** Models generate citations that *look* right — real domain,
   plausible path, correct-sounding document number — for pages that were never opened and
   sometimes never existed. In one measured run here, a model produced 11 source links; **3 of
   them were dead**, and its conclusions were still correct. That combination is the dangerous
   one, because it survives a casual read.

## What this does instead

- **A panel of independent models, same document, at the same time.** They do not see each
  other's answers, so agreement means something and disagreement means more.
- **It shows you the disagreement.** That is the actual product. Two models calling a claim fine
  and one calling it fatal is the most useful thing you will read all week.
- **It checks the receipts.** Every source link each model cites is opened and reported as
  live / moved / dead. Where the channel supports it, the tool also checks whether the model
  *actually opened* the page it cited, or just listed it.
- **It catches a model that quietly refused.** A model that declines a task still formats its
  reply correctly, so it passes every naive "did it finish?" check. This catches that.
- **Nothing with a password or key ever leaves your machine.** Blocked outright, no override.
  Personal data — ID numbers, SSNs, emails, phone numbers, dates of birth — is **found, itemised
  and reported, and then SENT**; `--strict-pii` turns that into a hard stop. **Names and street
  addresses are not detected at all**, at any setting. `PRIVACY.md` has the reasoning and the
  measurement behind both.

## Who this is for

| You are | You use it to |
|---|---|
| **Founder / CEO** | Pressure-test a strategy memo, a board deck, an investor update or a pricing decision before anyone external sees it. Three models, three sets of objections, before your board finds them. |
| **Product manager** | Review a spec or PRD for holes, check competitive claims you are about to publish, stress-test a launch plan's assumptions. |
| **C-level / operations** | Verify claims in a vendor proposal or a consultant's report. Check that a regulation you are relying on is still current and says what someone told you it says. |
| **Legal / compliance** | Verify that every citation in a research memo resolves to a real document that actually says what the memo claims. This is source-verification work, done properly and at speed. See the note below. |
| **AI / ML engineer** | Compare model behaviour on the same prompt across vendors. See which models ground their answers in real sources and which ones fabricate citations. Evaluate before you ship. |
| **Anyone writing something that matters** | Get the objections in private, before they arrive in public. |

### A note for legal teams

This is a **research verification** tool, not an advice tool, and the distinction is built into
the software rather than written on it. It ships with a mode (`--system legal-research`) that
frames the work as what it is: checking sources for a document a licensed professional will
review. Nothing in the output is legal advice, and the models are explicitly instructed not to
opine on any named individual's situation or decide what anyone should file.

That framing is also what makes it *work*. Asked to "review this filing strategy", the models
refuse on policy. Asked to "verify these six claims against their cited sources", the same models
answer all six with correct citations. The reframing is accurate, not a workaround — checking a
document number against the register genuinely is research.

## What one run looks like

You write the question in a plain text file, then run one command. A few minutes later:

```
[spark11]     OK  155s  model=Muse Spark 1.1 [muse-spark-1.1]
[spark13cont] OK  279s  model=Muse Spark 1.3 Contributor
[codex]       OK  407s  model=GPT-5.4    32 sources cited, 1 dead (deliberate check - correct)
[agy31pro]    OK   44s  model=Gemini 3.1 Pro    11 cited, only 2 actually opened  <- PROBLEM
[agy38flash]  OK   25s  model=Gemini 3.8 Flash
[kimik3]      OK  185s  model=Kimi K3     4 cited, 1 opened
[qwen38max]   OK  814s  model=Qwen3.8 Max 7 cited, 5 opened
6/7 channels returned a verified review.
```

Every line names the **model**, not just the channel, because two of these channels are two
checkpoints of one family and one of them deliberately rotates between models when a weekly
limit runs out. "Codex answered" is not a fact you can act on; "Codex answered on GPT-5.4" is.

Plus one file per model containing the actual review, and a diagnostics file if anything went
wrong.

## The one habit worth stealing

**Put a claim you know is false into every document you send for review.**

It costs nothing. A model that "confirms" your planted falsehood has just told you exactly what
all its other confirmations are worth. In the three-channel rounds this test was built on, all
three models caught both planted claims — which is the only reason to believe the things they
*did* confirm. (Scope stated on purpose: that measurement is from a three-channel round and has
not been repeated across the full panel. A number is worth only the run it came from.)

## What it costs, honestly

Several accounts, none of which this tool provides — but **one of them gets you most of the way**:

| What you need | What it unlocks | Rough cost |
|---|---|---|
| **The opencode CLI** (`npm install -g opencode-ai`) | `ocspark13free` — the **free** Muse Spark 1.3 voice, and the **default `--ask` channel** | **Free** — no key, no account |
| **`OPENROUTER_API_KEY`** | The biggest group in one account: Kimi, Qwen, Gemini, MiMo, Grok, GLM, DeepSeek, **a Muse Spark voice** and **a free NVIDIA Nemotron** — one signup | Metered per token, **plus per web search**. The Nemotron model itself is free |
| **`MODEL_API_KEY`** | The two Spark voices | Metered per use |
| **A paid OpenAI plan with Codex** | `codex` | Subscription, weekly limit |
| **An eligible Google account** | The Gemini channels via `agy` (Antigravity CLI) | Subscription, with limits |
| **Claude Code CLI** (`claude`) | Claude Opus — off by default | Subscription |
| **Grok CLI** (`grokbuild`) | Grok 4.5 with live web search | Free during beta |
| *Optional:* `GEMINI_API_KEY`, `XAI_API_KEY`, `MIMO_API_KEY` | The same Gemini, Grok and MiMo models through the **vendors' own** APIs, which buys real extra capability — see INSTALL.md. Off by default | Metered, free tiers vary |

**You do not need them all, and you should not start with them all.** Missing a key or a CLI is a
normal condition, not an error — the tool runs whatever is available and tells you plainly what it
skipped. Start with one.

🔴 **Do not count the channels from this file.** The number is whatever `channels.json` enables and
it has changed most weeks. `python routing.py` prints the live list and spends nothing. (Every
prose copy of that list in this repository has been wrong within days of being written — including,
at one point, two different numbers four lines apart in this very file.)

**Four channels are cheap because of their data terms, not despite them.** `ocspark13free`,
`spark13cont` and `orspark13cont` run the same Muse Spark 1.3 *Contributor* tier — through
opencode, directly and through OpenRouter respectively — and `ornemotron3ultra` runs a *free*
tier; on all four, the vendor may use prompts and completions for training. That is the trade
being made, it is stated in [PRIVACY.md](PRIVACY.md) with each vendor named, and the tool prints
each channel's data policy in the plan **before** it spends anything. If a brief should not be
trained on, drop those channels for that run:
`--skip ocspark13free spark13cont orspark13cont ornemotron3ultra`.

Nothing else in this file will tell you when to avoid a channel, and that is deliberate. An
earlier version carried a loud warning here and in the registry, and it was obeyed twice in ways
that were worse than the risk: once a run silently substituted a different model — destroying the
comparison the panel exists to produce — and once it dropped a reviewer nobody had decided to
drop. The facts belong in front of the person spending the money; the choice does not belong to a
sentence in a config file.

**Do not share one API key across a team.** It bills to whoever owns it, nobody can be
attributed, and revoking it cuts everyone off at once. One key per person — which is the whole
reason this is a repository you clone rather than a service you log into.

## Install

### The fastest way: hand this repository to your AI assistant

Paste this into Claude Code (or any coding assistant with shell access), replacing the URL:

```
Set up this tool for me: https://github.com/igorsaevets/ai-second-opinion
Read its INSTALL.md and follow it. I will set my own API keys myself —
do not ask me to paste a key into this chat, and do not run the key
commands for me. When you are done, run doctor.py and show me the output.
```

Those last two sentences are not politeness, they are the security model. An assistant that sets
the key for you must first *receive* the key, and the conversation is written to disk, replayed
into later context, and often archived. **A key that has appeared in a chat transcript is leaked
and must be rotated, not deleted.** The tool is built around this: it never prints a key,
`doctor.py` reports only presence and length, and `orchestrate.py` refuses to send a payload
containing anything secret-shaped even if you ask it to.

`INSTALL.md` contains an explicit instruction block addressed to the assistant itself, so a
competent one will decline to touch your keys without being told twice.

### Or do it yourself

Three ways, in order of how much you want to think about it. Full detail in
**[INSTALL.md](INSTALL.md)**.

**1 — Plugin (easiest, auto-updates).** In Claude Code:

```
/plugin marketplace add igorsaevets/ai-second-opinion
/plugin install model-orchestration@review-channels
```

**2 — Installer script.** Download the repo, then:

```powershell
.\install.ps1        # Windows
./install.sh         # macOS / Linux
```

**3 — Just copy the files.** No git, no plugin system, no installer. Copy one folder:

```
plugins/model-orchestration/skills/model-orchestration/
```

into

```
Windows:        %USERPROFILE%\.claude\skills\model-orchestration\
macOS / Linux:  ~/.claude/skills/model-orchestration/
```

That is the entire installation. It is plain Python with no dependencies to install — nothing is
compiled, nothing is downloaded, nothing runs in the background.

### Already have it? Updating is one command

```
python <the folder you downloaded>/plugins/model-orchestration/skills/model-orchestration/upgrade.py
```

Add `--dry-run` to see exactly what it would do first. It backs up the old folder, carries your
settings across, prints what changed between the two versions, and runs the checks. It is also
what `install.ps1` / `install.sh` call when an install already exists, so "install again" and
"update" are the same safe operation.

> **If you are an AI assistant that has been given this repository and asked to update an existing
> install: run `upgrade.py`. Do not copy files over the old folder by hand, and do not reinstall
> from scratch.** Before 1.7.0 there was no way to do this correctly — no installed copy carried a
> version number, and the user's own settings lived in a file that every update path overwrote. If
> the install you are updating has no `VERSION` file, it predates the fix and `upgrade.py` will
> migrate it. Run `python doctor.py` afterwards and report its version line.

**Your own settings live outside the skill folder**, in
`~/.claude/model-orchestration.local.json`, precisely so that no update can touch them:

```json
{ "channels": { "goog36flash": { "enabled": true } } }
```

Every run prints that file's path and each value it changed, so it can never quietly explain a
channel that is not doing what you expect.

From 1.8.0 that file may change **anything** — repoint a model, add a whole channel or your own
tier (`"_new": true` marks an addition, so a misspelt name fails loudly instead of quietly becoming
a second channel). 1.7.0 refused those, and it was wrong to: your settings file and the shipped
registry have the same write permissions, so refusing a field in one only pushed the change into
the other — and the other was the file nothing announced before a run. Both are reported now, with
transport changes marked. The one restriction left is about *provenance*, not about you: if you
move the file with `MODEL_ORCH_LOCAL`, only the "how hard does it work" knobs are accepted from it,
because a repository you cloned can set an environment variable and cannot set your home directory.

**Then, once per machine:**

```
python ~/.claude/skills/model-orchestration/doctor.py
```

It checks everything, reports what is missing in plain language, and prints your exact run
command with the real path already filled in.

## When something goes wrong

Every run writes two files next to its results:

- **`run.log`** — everything that happened, written as it happens, so it survives a crash.
- **`diagnostics.json`** — a structured report: what is installed, what each model did, what
  failed, and for each problem a plain-language cause and a suggested fix.

**Both are automatically stripped of keys, tokens and personal data**, so you can paste them
straight into a chat with an AI assistant and ask it to fix the problem, or attach them to a bug
report, without checking them first.

That is the intended workflow when you are stuck: hand `diagnostics.json` to your AI assistant and
ask it to diagnose the cause. See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**.

## Adding a model

Every model this tool knows about lives in a single `channels.json` registry: adding a channel is
an edit to that file, not new code, and your own additions go in the local settings file described
above, where no update can touch them. What changed recently is in [CHANGELOG.md](CHANGELOG.md);
the live channel list is `python routing.py`.

(An earlier version of this section was a roadmap. Its main items shipped — OpenRouter is now the
biggest group in the table above, and every run reports its own cost — while the section went on
promising them as future work for weeks. A list of plans in a README rots faster than anything
else in it, so this is now one line: the registry is the roadmap.)

## What this is not

- **Not ChatGPT, not Perplexity, not a single-model tool.** Those give you one answer from one
  model — fast and useful, but with one set of blind spots you cannot see. This gives you
  several answers from models that do not see each other, plus a mechanical audit of their work.
  The disagreement between independent reviewers is the product.
- **Not a fact database.** It reads the live web through the models' own search tools. It can be
  wrong, which is exactly why it shows you several answers instead of one.
- **Not a replacement for an expert.** It is very good at finding what an expert should look at.
- **Not "deep research" mode.** Those are separate, separately-billed products at several vendors
  and are not reachable from a normal subscription. This runs every model at its maximum depth
  with a source-discipline instruction — the same shape, but with multiple independent answers
  and an audit of every citation, which no deep-research product gives you.
- **Not a benchmark or eval harness.** Benchmarks measure models against known answers. This puts
  models to work on *your* question, where no answer key exists — and lets their disagreement
  tell you what a benchmark never could.
- **Not automatic.** You still read the disagreement and decide. The tool's job is to make sure
  you are deciding with the objections in front of you.

## Frequently asked questions

**Why not just ask ChatGPT / Claude / Gemini directly?**<br>
A single model has a single set of blind spots, and nothing in its answer tells you which parts
are weak. A panel of independent models — each blind to the others' answers — surfaces
disagreements that one model alone will never show you. Two models saying a claim is fine and
one calling it fatal is the most useful signal you will find.

**How is this different from Perplexity or AI search?**<br>
Perplexity gives you one synthesised answer with sources. This gives you several independent
answers, shows you where they contradict each other, and then mechanically checks whether the
sources each model cited actually exist and were actually opened. The disagreement and the audit
are what you are paying for.

**Can I use this from Cursor / Windsurf / another coding tool?**<br>
Yes. It is plain Python with no dependencies. Any tool with shell access can run it. There is
also a one-command Claude Code plugin install — see [Install](#install).

**Does this work with OpenRouter models?**<br>
Yes. OpenRouter is the biggest single-account group: one API key unlocks Kimi, Qwen, Gemini,
MiMo, Grok, GLM, DeepSeek, Muse Spark and NVIDIA Nemotron. Start with that and add direct
vendor access later for the models that benefit from it.

**Is this expensive?**<br>
One channel is free (Muse Spark 1.3 via opencode). The default cheap panel runs on subscriptions
and free/metered accounts, not premium APIs. A full run typically costs under $2 — and the tool
prints the exact cost when it finishes. See [what it costs](#what-it-costs-honestly).

## Found a bug? Want a feature? Want to work together?

| What you want to do | Where it goes |
|---|---|
| **Report a bug** | [Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) and attach `diagnostics.json` — it is scrubbed by construction, so you can attach it without reading it first |
| **Ask for a feature, or suggest an improvement** | [Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) |
| **Ask a question, or show what you built with it** | [Discussions](https://github.com/igorsaevets/ai-second-opinion/discussions) |
| **Report a security problem** | [Report a vulnerability privately](https://github.com/igorsaevets/ai-second-opinion/security/advisories/new) — please do *not* open a public issue first. See [SECURITY.md](SECURITY.md) |
| **Collaboration, consulting, or anything commercial** | [LinkedIn](https://www.linkedin.com/in/igorsaevets/) · [Facebook](https://facebook.com/igorsaevets) · [GitHub](https://github.com/igorsaevets) |

Maintained by **the operator Saevets** ([@igorsaevets](https://github.com/igorsaevets)), Los Angeles.

There is deliberately **no contact email in this repository.** A public address in a public repo is
harvested within days, and the address on the commits here is GitHub's no-reply relay, which has no
mail exchanger at all — mail sent to it is not delivered anywhere, quietly. A channel that silently
swallows a bug report is worse than no channel, so the links above are the real ones.

## Licence

MIT — see [LICENSE](LICENSE). Use it commercially, fork it, ship it inside your own product.

---

*Every rule enforced by this tool exists because something failed on a real run. The measurements
quoted above are from actual runs, not estimates. Details and dates are in
[TECHNICAL.md](TECHNICAL.md).*
