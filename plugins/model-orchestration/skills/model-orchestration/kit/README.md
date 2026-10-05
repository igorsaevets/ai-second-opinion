# AI Second Opinion

[![selftest](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml/badge.svg)](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](INSTALL.md)

**Every AI model knows its own things. Ask several at once and you get not only a check, but advice no single model would have given you — including on what you did not ask.**

A panel pays off twice:
1. **Different knowledge.** Each model was trained on its own data and runs in its own harness: the vendor's CLI agent or an API, its own web search, its own way of reading pages. So when you ask "how should I design this" or "review this code", each one answers with its own recommendations, and together they cover more than any one of them.
2. **An independent check.** The models never see each other's answers. When they agree, that means something; when they disagree, it means more. One AI agreeing with you proves nothing.

Every review answer ends with an **UNASKED** section. That is where the model puts a wrong assumption, a risk, a better alternative or the thing worth checking next — only what it would defend as significant, and if there is nothing, it says so. The most valuable finding often comes from there.

Send the same document **to several independent AI models at once**: GPT, Gemini, Grok, Kimi, Qwen, DeepSeek, GLM, MiMo, Muse Spark, NVIDIA Nemotron, and Claude if you switch it on. You get back:
- what each model found and what it recommends;
- its UNASKED section — what matters that you did not ask about;
- where the models contradict each other;
- a mechanical check that catches **AI hallucinations**: fabricated citations, invented sources and quiet refusals.

**Any AI agent can install it, not only Claude Code.** Any agent with terminal access will do — for example Codex CLI, Cursor, Gemini CLI, opencode, Kimi Code or Qwen Code: this is plain Python with no dependencies. Give the agent the repository link and ask it to install the tool ([a ready-made request is under «Install»](#install)). The repository carries [AGENTS.md](AGENTS.md), the instructions for AI agents on how to install the tool and run a review, so from then on the agent runs the panel itself. The sequence an agent runs — `install.ps1`, `doctor.py` and a free `--dry-run` — was checked on 2026-10-05 from a plain terminal, without Claude Code.

In Claude Code the plugin reacts to "second opinion" by itself. To make another agent do the same, add one line to its standing instructions: *"When I ask for a second opinion, run AI Second Opinion following `~/.claude/skills/model-orchestration/SKILL.md`"*.

Pure Python, zero dependencies, MIT license.

[Русская версия](README.ru.md) · [How it works, in technical detail](TECHNICAL.md) ·
[Install](INSTALL.md) · [When something breaks](TROUBLESHOOTING.md) ·
[For AI agents](AGENTS.md)

---

## Getting started: accounts and keys

To start you need **one OpenRouter key and a couple of free programs**. Everything else adds voices but is optional: the tool runs whatever you have and tells you plainly what it skipped.

| Step | What to do | What it gives you | What it costs (as of 2026-10-05) |
|---|---|---|---|
| **1. OpenRouter** — the main one | Sign up at [openrouter.ai](https://openrouter.ai), add credit and create a key (*Keys → Create Key*). Put it in the `OPENROUTER_API_KEY` variable in your own terminal, not in a chat with an AI ([how — INSTALL.md](INSTALL.md#openrouter--openrouter_api_key)) | The paid voices: MiMo in the cheap panel; Qwen, DeepSeek, GLM and Nemotron in the standard one, and Kimi when there is no NVIDIA key; a fallback route to Gemini and Muse Spark | Pay per token on the key; prices are in the tables below. OpenRouter takes a 5.5% fee when you buy credits. Free models: 50 requests a day, or 1000 a day once you have bought at least $10 of credits |
| **2. opencode CLI** | `npm install -g opencode-ai` | The free Muse Spark 1.3 voice (Meta). The free MiMo v2.6 Flash (Xiaomi) runs through it too | Free, no key and no account: that is how it works for us. OpenCode warns that its free models are available "for a limited time" |
| **2b. Kimi Code CLI + an NVIDIA key** | Install the [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) with one command ([which one — INSTALL.md](INSTALL.md#kimi-code-cli-free-kimi-k3-through-nvidia)), sign up for free at [build.nvidia.com](https://build.nvidia.com), get a key and put it in `NVIDIA_NIM_API_KEY` | Kimi K3 (Moonshot) in both panels. Without this step the cheap panel answers with five voices, and in the standard one Kimi answers through OpenRouter, for a fee | Free. NVIDIA calls it a trial service, limited to about 40 requests a minute |
| **3. Antigravity CLI (`agy`)** | Install it, sign in with a Google account and run `patch_agy_permissions.py` **once**. Without that step the answers come back empty ([INSTALL.md](INSTALL.md#antigravity-cli-gemini)) | Gemini 3.8 Flash, and Gemini 3.1 Pro by name | Free on a personal Google account, with weekly limits. Google AI Pro ($19.99/month) and Ultra ($99.99 or $199.99/month) raise them |
| **4. Grok Build CLI** | Install it and run `grok login` ([INSTALL.md](INSTALL.md#grok-build-cli)) | Grok 4.7 with live web search | An xAI account. xAI says Grok Build is now available to everyone; SuperGrok ($30/month) and SuperGrok Plus ($100/month) raise the limits. Whether the CLI works on a free account we have not checked |
| **5. Codex CLI** — for the standard panel | Install it and sign in with a ChatGPT account ([INSTALL.md](INSTALL.md#codex-cli)) | GPT-6.1 Sol | A ChatGPT plan: Plus $20/month, Pro $200/month. OpenAI says Codex is also in Free and Go ($8/month); whether GPT-6.1 Sol is available there we have not checked. Limits are counted in 5-hour and weekly windows |
| optional | Claude Code with a subscription ([INSTALL.md](INSTALL.md#claude-code-cli-claude-opus--off-by-default)) | Claude Opus 4.6. Off by default | Claude Pro $20/month, Max from $100/month. Claude Code is in every paid plan |
| optional | A Meta key, `MODEL_API_KEY` ([INSTALL.md](INSTALL.md#spark--model_api_key)) | A second route to Muse Spark if opencode is not installed | Per key: $0.10 / $0.20 per 1M tokens (Contributor tier) |

To check what was found: `python doctor.py`. It prints which keys and programs it found, and never prints a key itself: only present / absent and the length.

🔴 **Do not paste a key into a chat with an AI assistant.** Set it yourself in the terminal; why is explained under [«Install»](#install).

## How to ask

In Claude Code with the plugin (and in any agent you gave the line from the top of this page) just say it in plain words:

| You say | What runs (version 1.105.5) |
|---|---|
| **"second opinion"** (for example, "give me a second opinion on this file") | **The cheap panel**, the default: 6 models (5 without step 2b) |
| **"second opinion, CLI only"** | Only the CLI agents of the cheap panel: 5 models — Muse Spark, MiMo v2.6 Flash, Kimi K3, Gemini 3.8 Flash and Grok 4.7. Not one paid request on your OpenRouter key |
| "standard panel" or "all models" | 11 models: the cheap panel plus GPT-6.1 Sol, Qwen 3.8 Max, DeepSeek V4 Pro, GLM 5.3 and Nemotron. Kimi K3 answers here too, for free through NVIDIA, or through OpenRouter for a fee without step 2b |
| "CLI only, standard panel" | 6 models: the five CLI agents plus Codex (GPT-6.1 Sol) |
| "ask only codex", "only grok" | The one named channel |
| "only agy31pro" or "only agypro" | Gemini 3.1 Pro: it starts only when named |
| "don't use openrouter" | Everything except the OpenRouter channels |

From a terminal the same is done with flags: `--panel cheap` (the default), `--panel standard`, `--route "только CLI"`, `--only codex`, `--skip openrouter`. You can see the plan before anything is spent, for free: `python routing.py` or `--dry-run`.

## Panels as of 2026-10-05 (version 1.105.5)

How to read the tables:
- **Transport.** A *CLI agent* is the vendor's program on your computer (opencode, agy, grok, kimi, codex, claude). It runs on your subscription or for free and needs no OpenRouter key. *OpenRouter* is an API request on your key, paid per token.
- **Effort** is how hard the model thinks. The tool always sets the highest level the model accepts.
- **Price per 1M tokens** is from the OpenRouter catalogue on 2026-10-05, input / output.
- **Per run** is the median of our runs from 1 September to 5 October 2026; n is the number of runs. Yours will be higher or lower: it depends on the size of the document and the number of web searches. The tool prints the exact amount at the end of every run.

### The cheap panel — the default: 6 models

| Model | Channel | Transport | Effort | Payment | Price per 1M (in / out) | Per run |
|---|---|---|---|---|---|---|
| Muse Spark 1.3 (Meta) | `ocspark13free` | opencode CLI agent | xhigh — the free version's ceiling | free | — | $0 |
| MiMo v2.6 Flash (Xiaomi) | `ocmimo26flashfree` | opencode CLI agent | the default: the free version takes no levels | free | — | $0 |
| Kimi K3 (Moonshot) | `nvkimik3` | Kimi Code CLI agent, NVIDIA's free server | max | free, needs an NVIDIA key | — | $0 |
| Gemini 3.8 Flash (Google) | `agy38flash` | Antigravity CLI agent (`agy`) | not set: agy decides | Google account: free, with weekly limits | — | $0 beyond the plan |
| MiMo v2.6 Pro (Xiaomi) | `ormimopro` | OpenRouter | a reasoning budget of 85,000 tokens | per key | $0.435 / $0.87 | ≈ $0.06 (n=9) |
| Grok 4.7 (xAI) | `grokbuild` | Grok Build CLI agent | xhigh (the ceiling) | xAI account or plan | — | $0 beyond the plan |

If you lack the main transport, a fallback route to the same model takes over:
- **Muse Spark.** No opencode → `spark13cont` (Meta key) → `orspark13cont` (OpenRouter, $0.10 / $0.20, effort xhigh).
- **Gemini 3.8 Flash.** No `agy` → `orgemini38flash` (OpenRouter, $0.75 / $3.75, effort high — this model's ceiling).
- **Kimi K3.** The cheap panel has no fallback for it: without the Kimi Code CLI or an NVIDIA key (step 2b) the panel answers with five voices ([what you see then and how to switch the channel off](#if-you-do-not-want-kimi-or-mimo-flash)). NVIDIA's limit is per key: two panels started at the same time share it, and Kimi may not answer in one of them.

**In total:** about $0.06 a run on your OpenRouter key plus your subscriptions. Expect about 35 minutes: Kimi through NVIDIA thinks longest, its median is 34 minutes. Without Kimi, about 15 minutes: MiMo v2.6 Pro is next, its median is 14 minutes.

#### If you do not want Kimi or MiMo Flash

Without the Kimi Code CLI or an NVIDIA key, the run's plan says up front what is missing, and the `nvkimik3` channel ends at once with a pointer and sends nothing: the cheap panel answers with five voices. To stop it from starting, put this in `~/.claude/model-orchestration.local.json` (your own settings file; updates never touch it):

```json
{ "channels": { "nvkimik3": { "enabled": false } } }
```

Any other channel switches off the same way, for example `ocmimo26flashfree`. If the file already exists, add the channel to its `channels` section. In the standard panel the paid `kimik3` on OpenRouter (≈ $1.38 a run) answers in place of a switched-off `nvkimik3`. `python routing.py` shows who will run, spending nothing.


### The standard panel — 11 models: the cheap one + 5

| Model | Channel | Transport | Effort | Payment | Price per 1M (in / out) | Per run |
|---|---|---|---|---|---|---|
| GPT-6.1 Sol (OpenAI) | `codex` | Codex CLI agent | xhigh | ChatGPT plan | — | $0 beyond the plan |
| Kimi K3 (Moonshot) — only when `nvkimik3` cannot start: no Kimi Code CLI or NVIDIA key | `kimik3` | OpenRouter | max | per key | $0.67 / $14.00 | ≈ $1.38 (n=9) |
| Qwen 3.8 Max (Alibaba) | `qwen38max` | OpenRouter | xhigh (the ceiling) | per key | $2.00 / $6.00 | ≈ $1.02 (n=8), see below |
| DeepSeek V4 Pro | `ordeepseekv4pro` | OpenRouter | xhigh (the ceiling) | per key | $0.209 / $0.418 | ≈ $0.11 (n=8) |
| GLM 5.3 (Z.ai) | `orglm53` | OpenRouter | max | per key | $0.05 / $7.00 | ≈ $0.54 (n=8) |
| Nemotron 3 Ultra (NVIDIA) | `ornemotron3ultra` | OpenRouter | high (the ceiling) | free model | $0 / $0 | ≈ $0.06 (n=12): only the web searches are paid here |

⚠️ **Qwen 3.8 Max:** in September 0 of our 8 runs ended with an answer, although each cost about $1.02; the cause is not known yet. To avoid paying for it, add `--skip qwen38max`.

**In total:** about $1.8 a run on your OpenRouter key plus subscriptions; about $3.2 when Kimi answers through OpenRouter (no NVIDIA key). Expect up to 45 minutes: GLM 5.3 thinks longest, its median is 44 minutes. Every paid channel has a spending ceiling per run ($2–5); when it reaches it, the channel stops.

### By name only

| Model | Channel | Transport | Effort | Payment |
|---|---|---|---|---|
| Gemini 3.1 Pro (Google) | `agy31pro` | agy CLI agent | not set | as for agy above. Starts only when named: "only agy31pro" |
| Claude Opus 4.6 [1M] (Anthropic) | `cclopus46` | Claude Code CLI agent | max | a Claude plan (Pro $20/month). Off by default; when on, it runs with permission prompts bypassed ([SECURITY.md](SECURITY.md)) |

### Web search

On the OpenRouter channels the search is done by the Exa plugin: $0.007 a request (up to 10 results). It is billed apart from the tokens, so it is paid even on a free model. The CLI agents search by themselves, within their plan.

**`python routing.py` always prints the live channel list.** The tables above are a snapshot of 2026-10-05, and the registry changes most weeks.

*Where the prices come from (2026-10-05):*
- the OpenRouter catalogue `openrouter.ai/api/v1/models`, read at 09:15 UTC;
- free-model limits: `openrouter.ai/docs/api_reference/limits`;
- web search: `openrouter.ai/docs/features/web-search`;
- the fee: `openrouter.ai/pricing`;
- MiMo prices match Xiaomi's own price list: `mimo.mi.com/docs/en-US/price/pay-as-you-go`;
- NVIDIA: the model page `build.nvidia.com/moonshotai/kimi-k3`;
- plans: `help.openai.com/en/articles/11369540`, `openai.com/index/introducing-chatgpt-go`, `antigravity.google`, `gemini.google/subscriptions`, `x.ai/pricing`, `x.ai/news/grok-build-for-everyone`, `opencode.ai/docs/zen`, `claude.com/pricing`, `dev.meta.ai/docs/pricing-rate-limits`.

### The premium panel — a separate script, not a `--panel` value

True-batch and Flex lanes for the heaviest models at their discounted prices. Batch jobs work
nothing like the synchronous channels above, so this deliberately ships as its **own script**
rather than more channels: `premium/premium_panel.py`, with its own gates — a call-plan file on
disk before anything is paid, a secrets scan on every lane with **no override**, a per-lane
discount check (refuses when the discount is gone unless you say `--allow-nodiscount`), a PII
scan on the broker lane, and a pre-submit cost ceiling (a batch cannot be aborted mid-flight,
so the ceiling is enforced before submit or not at all).

```
python premium/premium_panel.py --mode dry --plan PLAN.md --brief BRIEF.md --rundir runs/premium
```

`--mode dry` builds everything and bills nothing. Then `smoke` (a tiny public-fact brief through
the same code path), `submit`, `poll`, `collect`. `--panel premium` on the main tool prints a
pointer here.

| lane | model | transport | key required | role |
|---|---|---|---|---|
| `solpro` | GPT-5.6 Sol Pro | OpenRouter batch (broker) | `OPENROUTER_API_KEY` | vote |
| `gpt55` | GPT-5.5 | OpenAI batch | `OPENAI_API_KEY` | vote |
| `gemini31` | Gemini 3.1 Pro | Google batch | `GEMINI_API_KEY` | vote |
| `flash` | Gemini 3.7 Flash | Google batch | `GEMINI_API_KEY` | **canary** — reported in its own section, never counted into convergence |
| `live54` | GPT-5.4 + web search | OpenAI Flex, synchronous | `OPENAI_API_KEY` | vote — the one live-web seat |

**With only an `OPENROUTER_API_KEY`, only the broker lane is reachable: run `--only solpro`.** A
missing key refuses loudly per lane, and `--mode collect` aggregates what exists — the report
shows the hole rather than papering over it.

Prices resolve from `premium/models_snapshot.json` (capture dates inside; a price past its
validity date refuses rather than inventing a number). Maturity, stated honestly: each lane's
transport was measured against its vendor individually; the assembled panel has not
yet run a paid round. Smoke first.

### Targeting specific channels

```
--only codex agy31pro grokbuild     # just the ones named
--skip ornemotron3ultra              # everyone except this one
```

The live channel list is always `python routing.py`; the tables on this page are a snapshot
dated in the «Panels» heading — channels and their count change most weeks.

## The problem this solves

You ask an AI to review your strategy memo. It tells you the memo is strong, adds three
supportive points, and cites four sources.

That answer is nearly worthless, for three reasons most people never check:

1. **It is built to agree with you.** You wrote the memo, you asked the question, and the model
   optimises for a helpful-feeling reply. Ask the same model to attack the memo and it will find
   problems it just told you did not exist.
2. **One model knows only what it knows.** Its ceiling is the data it was trained on and the tools of its harness. Whatever it was weak at yesterday, it is weak at today, and nothing in the answer hints which parts are unreliable or what it simply does not know. Another model, trained differently, will suggest what never occurred to the first.
3. **The sources may not exist.** Models generate citations that *look* right — real domain,
   plausible path, correct-sounding document number — for pages that were never opened and
   sometimes never existed. In one measured run here, a model produced 11 source links; **3 of
   them were dead**, and its conclusions were still correct. That combination is the dangerous
   one, because it survives a casual read.

## What this does instead

- **Different knowledge, different recommendations.** Models from different vendors were trained on different data and run in different harnesses. Ask "how should I build this" or "what is wrong with this code" and each proposes its own answer, so you get several options rather than one repeated three times. Every answer also carries an UNASKED section: what matters that you did not ask about.
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
  Personal data — ID numbers, SSNs, emails, phone numbers, dates of birth — is **found, counted
  and reported in one line, and then SENT**; `--warn-pii` lists each by kind and line, and
  `--strict-pii` turns it into a hard stop. **Names and street
  addresses are not detected at all**, at any setting. `PRIVACY.md` has the reasoning and the
  measurement behind both.

## Who this is for

| You are | You use it to |
|---|---|
| **Founder / CEO** | Pressure-test a strategy memo, a board deck, an investor update or a pricing decision before anyone external sees it. Several models, several sets of objections, before your board finds them. |
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

You write your question in a plain text file and run one command. Below is the real output of a run of this version on 2026-10-05, shortened: the cheap panel (all six models) checked five claims about OpenRouter and NVIDIA, one of which we made false on purpose.

```
[agy38flash] OK  275.2s  model=Gemini 3.8 Flash [gemini-3.8-flash]
[grokbuild] OK  251.0s  model=Grok 4.7 [grok-4.7]
[nvkimik3] OK  666.6s  model=Kimi K3 (via Kimi Code CLI + NVIDIA free endpoint) [nvidia/moonshotai/kimi-k3]
[ocmimo26flashfree] PROBLEM  0.8s  model=MiMo v2.6 Flash Free (via opencode) [opencode/mimo-v2.6-flash-free]
    FAIL: EXIT 1: {"type":"error", ... "error":{"type":"provider.quota", ...
[ocspark13free] PROBLEM  1.1s  model=Muse Spark 1.3 Contributor Free (via opencode) [opencode/muse-spark-1.3-contributor-free]
    FAIL: EXIT 1: {"type":"error", ... "error":{"type":"provider.quota", ...
[ormimopro] OK  185.0s  model=MiMo v2.6 Pro (OpenRouter) [xiaomi/mimo-v2.6-pro]
4/6 channels returned a verified review.
cost reported BY THE VENDORS for this round: $0.0697 across 1 channel(s) (ormimopro $0.0697)
Citation existence check (no vendor cost; fetches the cited pages directly):
  [agy38flash] 6 cited, 6 probed  LIVE=6
  [grokbuild] 12 cited, 12 probed  LIVE=10  MOVED=1  UNKNOWN=1
  [nvkimik3] 9 cited, 9 probed  LIVE=6  MOVED=3
```

Every line names the **model**, not just the channel: "the channel answered" is not a fact you can act on; "Kimi K3 answered through NVIDIA's free server" is. The two free opencode models hit the free service's quota this time (`provider.quota`), and the run said so plainly instead of passing an empty answer off as a review. The tool checks the links itself by opening every cited page: one that moved is marked MOVED, one that would not open is UNKNOWN.

Plus one file per model with its full report, a `HANDOFF.md` summary to read first, and a diagnostics file if anything went wrong.

## The one habit worth stealing

**Put a claim you know is false into every document you send for review.**

It costs nothing. A model that "confirms" your planted falsehood has just told you exactly what
all its other confirmations are worth. In the three-channel rounds this test was built on, all
three models caught both planted claims — which is the only reason to believe the things they
*did* confirm. (Scope stated on purpose: that measurement is from a three-channel round. Later,
larger panels kept refuting the planted claim — 4 of 4 in the 1.46.0 review and 12 of 12 readable
answers in the thirteen-channel round behind 1.44.0, per CHANGELOG.md — but a number is worth only
the run it came from.)

## What it costs, honestly

In short, from our runs of September–October 2026:
- **the cheap panel** — about $0.06 a run on your OpenRouter key plus subscriptions;
- **the standard one** — about $1.8, or about $3.2 when Kimi answers through OpenRouter (no NVIDIA key);
- **CLI only** — $0 on the key, subscriptions only.

The per-model breakdown is in the tables above. The accounts, none of which this tool provides, are listed under [«Getting started»](#getting-started-accounts-and-keys). *Optional:* the `GEMINI_API_KEY`, `XAI_API_KEY` and `MIMO_API_KEY` keys reach the same Gemini, Grok and MiMo models through the vendors' own APIs; those channels are off by default, details in [INSTALL.md](INSTALL.md#direct-vendor-alternatives-to-openrouter-off-by-default).

**You do not need them all, and you should not start with them all.** Missing a key or a CLI is a
normal condition, not an error — the tool runs whatever is available and tells you plainly what it
skipped. Start with one.

🔴 **Do not count the channels from this file.** The number is whatever `channels.json` enables and
it has changed most weeks. `python routing.py` prints the live list and spends nothing. (Every
prose copy of that list in this repository has been wrong within days of being written — including,
at one point, two different numbers four lines apart in this very file.)

**Some channels are cheap because of their data terms, not despite them.** As published,
`ocspark13free`, `spark13cont` and `orspark13cont` run the same Muse Spark 1.3 *Contributor* tier
— through opencode, directly and through OpenRouter respectively — and `ornemotron3ultra` runs a
*free* tier; on every one of them the vendor may use prompts and completions for training. That is
the trade being made, it is stated in [PRIVACY.md](PRIVACY.md) with each vendor named, and the
tool prints each channel's data policy in the plan **before** it spends anything — that line, not
this paragraph, is the current list. If a brief should not be trained on, drop those channels for
that run: `--skip ocspark13free spark13cont orspark13cont ornemotron3ultra`.

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

**1 — Plugin (easiest).** In Claude Code:

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
compiled, nothing runs in the background, and nothing is downloaded until you ask for an update.

### Already have it? Updating is one command

```
python ~/.claude/skills/model-orchestration/update_check.py --apply
```

Run it from the copy you have: it finds the newest release on GitHub, downloads and verifies it,
backs up the old folder, carries your settings across, prints what changed between the two versions
and runs the checks (`--dry-run` shows all of that without changing anything). A plugin install is
updated through Claude Code by the same command. You will not have to remember it, either: once a
week the kit checks for a release — at session start and after a real round — and prints that
command when there is one. The same machinery is what `install.ps1` / `install.sh` call when an
install already exists, so "install again" and "update" are the same safe operation.

> **If you are an AI assistant asked to update an existing install: run `update_check.py --apply`
> from the installed folder (or, given a downloaded copy, its `upgrade.py`). Do not copy files over
> the old folder by hand, and do not reinstall from scratch.** Before 1.7.0 there was no way to do this correctly — no installed copy carried a
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
No. The cheap panel costs about $0.06 a run on your OpenRouter key, plus subscriptions you most likely already have. Say "CLI only" and there is not one paid request on the key. The standard panel costs about $1.8 a run, or about $3.2 when Kimi answers through OpenRouter (no NVIDIA key). These are the medians of our runs in September–October 2026; the tool prints the exact amount of your run at its end. See [what it costs](#what-it-costs-honestly).

## Found a bug? Want a feature? Want to work together?

| What you want to do | Where it goes |
|---|---|
| **Report a bug** | [Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) and attach `diagnostics.json` — it is scrubbed by construction, so you can attach it without reading it first |
| **Ask for a feature, or suggest an improvement** | [Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) |
| **Ask a question, or show what you built with it** | [Discussions](https://github.com/igorsaevets/ai-second-opinion/discussions) |
| **Report a security problem** | [Report a vulnerability privately](https://github.com/igorsaevets/ai-second-opinion/security/advisories/new) — please do *not* open a public issue first. See [SECURITY.md](SECURITY.md) |
| **Collaboration, consulting, or anything commercial** | [LinkedIn](https://www.linkedin.com/in/igorsaevets/) · [Facebook](https://facebook.com/igorsaevets) · [GitHub](https://github.com/igorsaevets) |

Maintained by **Igor Saevets** ([@igorsaevets](https://github.com/igorsaevets)), Los Angeles.

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
