# AI Second Opinion

[![selftest](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml/badge.svg)](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](INSTALL.md)

**Every AI model knows different things. Ask several at once and you get not only a check, but advice no single model would have given you — including on what you did not ask.**

**One document goes to several AI models at once, and one report comes back.** Sending your document to the AI vendors behind the channels you turn on is the whole point; the tool itself has no server and collects nothing, and on its own it only checks GitHub for a new version, at most once a day ([what goes where](PRIVACY.md)).

## What a real run found

A run of version 1.113.0 on 2026-10-17, on the author's machine and accounts: seven CLI agents, not the published default panel (that one is below). They were asked where this tool should be listed and what would block a listing, with our own table of channels and their rules attached. From that run's REPORT.md, which stays on the author's machine:

| Model (how it ran) | Time | Links cited | Live | Dead |
|---|---|---|---|---|
| GPT-6.1 Sol (Codex CLI) | 16 min | 50 | 40 | 0 |
| Qwen 3.8 Max (Qwen Code CLI) † | 26 min | 14 | 7 | 0 |
| Grok 4.7 (Grok Build CLI) | 7 min | 21 | 17 | 0 |
| Gemini 3.8 Flash (Antigravity CLI) | 5 min | 29 | 21 | 0 |
| Muse Spark 1.3 (opencode, Go plan) † | 1 min | 22 | 16 | 2 |
| MiMo v2.6 Pro (mimo CLI) † | 8 min | 1 | 1 | 0 |
| MiMo v2.6 Flash (opencode) | 12 min | 0 | — | — |

† A route the published version leaves off. *Live*: the link opened when the tool checked it; *dead*: it returned 404; the rest had moved, blocked the check or could not be checked. A live link does not show that the model read it.

- **A rule our own table had missed.** It said we meet the entry rules of the awesome-claude-code list. Five of the seven (GPT-6.1 Sol, Qwen, Grok, Gemini, MiMo Pro) pointed to a rule further down [its CONTRIBUTING.md](https://github.com/hesreallyhim/awesome-claude-code/blob/main/CONTRIBUTING.md): «if your project requires any form of signup or payment, this is a blocker for reviewing it - I can only recommend such a resource on the basis of community adoption signals».
- **A disagreement, kept in view.** Build an MCP server now? MiMo Flash, the one voice that cited no links: «Build it — as a thin wrapper, started now…». The other six: not now.
- **What was cited is not what was read.** Gemini cited 29 links; the tool saw it open 2 of them. Muse Spark's two dead links are one real GitHub Docs page, cited twice with part of the path replaced by «...».

The seven worked in parallel: 74 minutes of model time took 26 min 31 s, 15 seconds more than the slowest voice. On your OpenRouter key the published default panel pays for at most two voices: Muse Spark when you have no Meta key ($0.002 in our first run, each web search extra) and, on a machine without opencode, MiMo v2.6 Flash ($0.0003 for our first two-line question); the rest runs on your subscriptions or free (details below), and `--dry-run` shows the plan before anything is spent. What the console shows during a run is in [What one run looks like](#what-one-run-looks-like).

A panel pays off twice:
1. **Different knowledge.** Each model was trained on its own data and runs in its own harness: the vendor's CLI agent or an API, its own web search, its own way of reading pages. So when you ask "how should I design this" or "review this code", each one answers with its own recommendations, and together they cover more than any one of them.
2. **An independent check.** The models never see each other's answers. When they agree, that means something; when they disagree, it means more. One AI agreeing with you proves nothing.

Every review answer ends with an **UNASKED** section. That is where the model puts a wrong assumption, a risk, a better alternative or the thing worth checking next — only what it would defend as significant, and if there is nothing, it says so. The most valuable finding often comes from there.

Send the same document **to several independent AI models at once**: GPT, Gemini, Grok, Kimi, Qwen, DeepSeek, GLM, MiMo, Muse Spark, NVIDIA Nemotron, and Claude if you switch it on. You get back:
- what each model found and what it recommends;
- its UNASKED section — what matters that you did not ask about;
- where the models contradict each other;
- a mechanical check for the **AI hallucinations** a script can catch: dead links, links the model cited without opening them, and quiet refusals.

**Any AI agent can install it, not only Claude Code.** Any agent with terminal access will do — for example Codex CLI, Cursor, Gemini CLI, opencode, Kimi Code or Qwen Code: this is plain Python with no dependencies. Give the agent the repository link and ask it to install the tool ([a ready-made request is under «Install»](#install)). The repository carries [AGENTS.md](AGENTS.md), the instructions for AI agents on how to install the tool and run a review, so from then on the agent runs the panel itself. The sequence an agent runs — `install.ps1`, `doctor.py` and a free `--dry-run` — was checked on 2026-10-05 from a plain terminal, without Claude Code.

In Claude Code the plugin reacts to "second opinion" by itself. For any other agent `install.ps1`/`install.sh` already installs the skill to every known location (`~/.claude`, `~/.agents`, `~/.codex`, `~/.config/opencode`, `~/.gemini`) so the same phrase works there too; if it doesn't, add one line to its standing instructions: *"When I ask for a second opinion, run AI Second Opinion following `~/.claude/skills/model-orchestration/SKILL.md` (fallback: `~/.agents/...`, `~/.config/opencode/...`, or absolute `python "$HOME/.claude/skills/model-orchestration/orchestrate.py" --ask "..."`)"*.

Pure Python, zero dependencies, MIT license.

[Русская версия](README.ru.md) · [How it works, in technical detail](TECHNICAL.md) ·
[Install](INSTALL.md) · [When something breaks](TROUBLESHOOTING.md) ·
[For AI agents](AGENTS.md)

---

## Getting started: accounts and keys

To start you need **one OpenRouter key and a couple of free programs**. Everything else adds voices but is optional: the tool runs whatever you have and tells you plainly what it skipped.

| Step | What to do | What it gives you | What it costs (as of 2026-10-05) |
|---|---|---|---|
| **1. OpenRouter** — the main one | Sign up at [openrouter.ai](https://openrouter.ai), add credit and create a key (*Keys → Create Key*). Put it in the `OPENROUTER_API_KEY` variable in your own terminal, not in a chat with an AI ([how — INSTALL.md](INSTALL.md#openrouter--openrouter_api_key)) | The paid voices: Grok 4.20 Reasoning only when you name it (since 1.123.0; in 1.122.0–1.122.2 on every cheap-panel run); the two Muse Spark Contributor voices (1.2 and 1.3) where opencode has no OpenCode Go plan (Spark 1.2 runs on a Meta key instead when you have one) and, where opencode is missing, MiMo v2.6 Flash in the cheap panel; Kimi K3, Qwen 3.8 Max (where the Qwen Code CLI is not set up) and GLM 5.3 in the standard one; DeepSeek V4 Pro, MiMo v2.6 Pro and Gemini 3.8 Flash (`orgemini38flash`) only when you name them; a fallback route to Nemotron | Pay per token on the key; prices are in the tables below. OpenRouter takes a 5.5% fee when you buy credits. Free models: 50 requests a day, or 1000 a day once you have bought at least $10 of credits |
| **2. opencode CLI** | `npm install -g @opencode/cli` (v2; had the old `opencode-ai`? `npm uninstall -g opencode-ai` first) | Two free voices since 1.121.0: MiMo v2.6 Flash (Xiaomi) and GLM 5.3 Flash (Z.ai), on one daily allowance; opencode's free DeepSeek V4.1 Flash shares it and starts only when named (see below). Nemotron left opencode in 1.116.1, and Step 5 Preview (on your NVIDIA key, step 2b) runs here only when you name it since 1.118.0. No free Muse Spark since 2026-10-17 (see below). With an [OpenCode Go](https://opencode.ai/docs/go) plan, since 1.120.0: both Muse Spark Contributor voices, 1.2 and 1.3, on the plan, after one setting in the OpenCode Console (below, «Spark on an OpenCode Go plan»); since 1.121.0 also MiMo v2.6 Flash, DeepSeek V4.1 Flash and GLM 5.3 Flash, on the plan first (below, «Go plan first, then free, then paid») | Free, no key and no account: that is how it works for us (checked on 2026-10-18). OpenCode warns that its free models are available "for a limited time", and they do come and go |
| **2b. NVIDIA key** — optional, since 1.114.0 | Sign in at [build.nvidia.com](https://build.nvidia.com) and create an API key. Put it in the `NVIDIA_NIM_API_KEY` variable in your own terminal ([how — INSTALL.md](INSTALL.md#nvidia--nvidia_nim_api_key)) | Two free voices on NVIDIA's own endpoint: DeepSeek V4.1 Flash (since 1.118.0, in place of Step 5 Preview) and Nemotron 3 Ultra; GLM 5.3 Flash on the same key answers when DeepSeek does not | Free at the point of use (NVIDIA's free endpoints allow about 40 requests a minute). Its queue is slow at times: 57–110 s before the first byte on 2026-10-23 |
| **3. Antigravity CLI (`agy`)** | Install it, sign in with a Google account and run `patch_agy_permissions.py` **once**. Without that step the answers come back empty ([INSTALL.md](INSTALL.md#antigravity-cli-gemini)) | Gemini 3.8 Flash; Gemini 4 Argon on the standard panel | Free on a personal Google account, with weekly limits. Google AI Pro ($19.99/month) and Ultra ($99.99 or $199.99/month) raise them |
| **4. Grok Build CLI** | Install it and run `grok login` ([INSTALL.md](INSTALL.md#grok-build-cli)) | Grok 4.6 with live web search | An xAI account. xAI says Grok Build is now available to everyone; SuperGrok ($30/month) and SuperGrok Plus ($100/month) raise the limits. Whether the CLI works on a free account we have not checked |
| **5. Codex CLI** — for the standard panel | Install it and sign in with a ChatGPT account ([INSTALL.md](INSTALL.md#codex-cli)) | GPT-6.1 Sol | A ChatGPT plan: Plus $20/month, Pro $200/month. OpenAI says Codex is also in Free and Go ($8/month); whether GPT-6.1 Sol is available there we have not checked. Limits are counted in 5-hour and weekly windows |
| optional | Claude Code with a subscription ([INSTALL.md](INSTALL.md#claude-code-cli-claude-opus--off-by-default)) | Claude Opus 4.6. Off by default | Claude Pro $20/month, Max from $100/month. Claude Code is in every paid plan |
| optional | A Meta key, `MODEL_API_KEY` ([INSTALL.md](INSTALL.md#spark--model_api_key)) | Muse Spark 1.2 Contributor straight from Meta: with this key, where opencode has no Go plan, the cheap panel's Spark 1.2 voice runs here instead of through OpenRouter (without the Go plan Spark 1.3 takes OpenRouter, key or no key) | Per key: $0.10 / $0.20 per 1M tokens (Contributor tier) |

**There is no free Kimi any more.** NVIDIA retired its free Kimi K3 on 2026-10-15: the endpoint answers HTTP 410 «end of life». Versions 1.106–1.111 asked for a step «2b» (the Kimi Code CLI and an `NVIDIA_NIM_API_KEY`); since 1.112.0 the Kimi Code CLI is unused, the `nvkimik3` channel ships switched off, and the cheap panel has no Kimi voice. The NVIDIA key is used again since 1.114.0: by DeepSeek V4.1 Flash (Step 5 Preview until 1.117.2) and Nemotron 3 Ultra (step 2b above). Kimi K3 answers in the standard panel through OpenRouter (`kimik3`, paid). «only kimik3» starts that channel alone; «only kimi» does too on the standard panel, while on the default cheap panel it stops and asks you to name `kimik3`: a family word never moves you from free to paid in silence.

**There is no free Muse Spark any more.** On 2026-10-17 opencode stopped serving its free Muse Spark 1.3 (`opencode/muse-spark-1.3-contributor-free`): the call answers «provider.no-route: Model unavailable» and the model is gone from `opencode models`. Since 1.113.3 its channel `ocspark13free` ships switched off, and the cheap panel's Spark voice is paid; since 1.113.8 it is Muse Spark 1.2 Contributor: `spark12cont` on a Meta key, otherwise `orspark12cont` on your OpenRouter key, $0.10 / $0.20 per 1M tokens either way (the price of the Spark 1.3 Contributor it replaces, which took $0.002 in our first run); the plan marks the OpenRouter route PAID. Since 1.120.0 Spark 1.3 Contributor is the panel's second Spark voice (`ocspark13go` on the Go plan, otherwise `orspark13cont`); `spark13cont`, the same model on a Meta key, still starts only when you name it. Instead it opencode gave three more free voices, run live on 2026-10-18: Nemotron 3 Ultra (retired in 1.116.1 — its model left opencode on 2026-10-29), GLM 5.3 Flash and DeepSeek V4.1 Flash; since 1.113.7 GLM and DeepSeek wait in reserve, because they share MiMo Flash's daily allowance (see the cheap panel below). Free models come and go without notice (NVIDIA's free Kimi K3 ended two days earlier); when one disappears, the run names it and says what to do, and `opencode models` is the live list.

To check what was found: `python doctor.py`. It prints which keys and programs it found, and never prints a key itself: only present / absent and the length.

🔴 **Do not paste a key into a chat with an AI assistant.** Set it yourself in the terminal; why is explained under [«Install»](#install).

## How to ask

In Claude Code with the plugin (and in any agent you gave the line from the top of this page) just say it in plain words:

| You say | What runs (version 1.125.0) |
|---|---|
| **"second opinion"** (for example, "give me a second opinion on this file") | **The cheap panel**, the default: 9 models since 1.125.0, when Hy4 joined it (in 1.122.0–1.122.2 Grok 4.20 Reasoning also ran there, paid on your OpenRouter key on every run; since 1.123.0 it runs only when you name it: below) |
| **"second opinion, CLI only"** | Only the CLI agents of the cheap panel: 8 models — MiMo v2.6 Flash and GLM 5.3 Flash (opencode: on the OpenCode Go plan, else free), DeepSeek V4.1 Flash, Muse Spark 1.2 and 1.3 Contributor and, since 1.125.0, Hy4 (opencode on the Go plan), Gemini 3.8 Flash and Grok 4.6 (the NVIDIA voices are API calls). Without the Go plan the two Spark voices and DeepSeek's Go copy still start here and end at once (the plan says so before the run); leave them out with `--skip ocspark12go ocspark13go ocdeepseekv41flashgo`. Since 1.121.0 a voice that ends without an answer can be answered by its fallback, and that may be an API call: DeepSeek's free NVIDIA copy, or a paid OpenRouter copy (printed PAID in the plan; below, «Go plan first, then free, then paid»); add `--no-fallback` for a run with no fallback at all |
| "standard panel" or "all models" | 14 models: the cheap panel plus GPT-6.1 Sol, Gemini 4 Argon, Kimi K3, Qwen 3.8 Max and GLM 5.3, each on the first route your machine has (the subscription CLI where you have it, else OpenRouter: the standard-panel table below). DeepSeek V4 Pro runs only when named since 1.125.0. No Muse Wonder: since 1.113.0 it starts only when named; no MiMo v2.6 Pro: since 1.113.8 it too starts only when named |
| "CLI only, standard panel" | 11 models: the eight CLI agents plus Codex (GPT-6.1 Sol), Gemini 4 Argon and, since 1.125.0, Qwen 3.8 Max through the Qwen Code CLI |
| "ask only codex", "only codex cli" | The one named channel ("codex cli" since 1.120.1: before, "cli" also added every CLI agent) |
| "only grok", «только грок» | Grok 4.6 through the Grok Build CLI alone (your xAI account or plan) — again since 1.123.0, as in 1.121.0; with the shipped settings the same as "only grokbuild" / «только грок билд». In 1.122.0–1.122.2 these words also ran Grok 4.20 Reasoning through OpenRouter (**PAID** on your key, ≈ $0.10 a review); since 1.123.0 that voice needs its own words (next row). One Grok model by its version — "only grok 4.6" / «только грок 4.6», 4.7 and 4.5 the same — runs Grok Build alone on that model (since 1.122.1; in 1.122.0 the English words started the paid Grok 4.20 too) |
| "only grok 4.20", «только грок 4.20» | Grok 4.20 Reasoning alone, through OpenRouter (`orgrok420`, **PAID**, ≈ $0.10 a review). Since 1.123.0 it runs only when named: these words, "add grok 4.20" / «добавь грок 4.20» (the cheap panel plus it) or `--only orgrok420`; no panel, "all models", "only grok" or "only openrouter" starts it. In 1.121.0 «только грок 4.20» started the direct xAI copy `grok420`, which needs `XAI_API_KEY` (without it the run ended at once), and "only grok 4.20" was refused |
| "only opencode", «только opencode» | The opencode voices that are on: in the kit MiMo v2.6 Flash and, since 1.121.0, GLM 5.3 Flash (on the OpenCode Go plan, else free), DeepSeek V4.1 Flash and the two Spark voices (on the Go plan; without it they end at once, as in "CLI only"); opencode's free DeepSeek V4.1 Flash only when named, and since 1.118.0 Step 5 Preview too. "without opencode" runs everything else. Before 1.113.5 the word named only the free Spark that is gone since 2026-10-17. On a day opencode's free allowance was refused, its free voices are left out (`[skip]`): a group word never counts as naming a channel (the cheap panel below) |
| "only ocglm53flashfree", "only ocmimo26flashfree" | That one free voice, also on a day its allowance was refused (otherwise the plan shows `[skip]` for it, see the cheap panel below) |
| "only mimo flash", «только мимо флеш»; "only glm 5.3 flash", «только глм 5.3 флеш»; "only deepseek flash", «только дипсик флеш» | The same voice as in the cheap panel, on the first transport your machine has (since 1.121.0 the OpenCode Go plan first; the cheap panel below). Before 1.121.0 "glm 5.3 flash" started opencode's free copy `ocglm53flashfree` on every machine, also one without opencode, and "deepseek flash" was refused. Since 1.120.1: before, «мимо флеш» started the paid mimo CLI copy, "mimo flash" was refused and "glm 5.3 flash" started the paid GLM 5.3. Since 1.122.0 "deepseek flash" also runs on a machine with opencode but neither the Go plan nor an NVIDIA key (the paid copy; 1.121.0 refused it there), and on a day opencode's free allowance was refused "mimo flash" and "glm 5.3 flash" go on to the next transport: GLM to NVIDIA's free copy with the key, else both to their paid copies (in 1.121.0 they ran nothing that day) |
| "only wonder", "muse wonder", «вондер»; "add wonder" | One Muse Wonder, on your Muse Code subscription where the Muse Code CLI is installed. Without it the run stops before paying and names the paid ones: "only wonder10" (Meta key) or "only orwonder" (OpenRouter) runs that one |
| "only kimi" | The run stops and says why: the cheap panel has no free Kimi since 1.112.0. On the standard panel ("standard panel, only kimi") the Kimi K3 voice runs through OpenRouter, paid. "only kimik3" runs the OpenRouter copy by its name |
| "only gemini 4 cli", «только gemini 4 аргон» | Gemini 4 Argon alone (it is on the standard panel, not the cheap one). "only gemini 4 argon" and "only gemini 4 cli" both run it through agy. "only agy31pro" still starts Gemini 3.1 Pro, off since 1.115.0 (below, «By name only») |
| "only glm 5.3", «только глм 5.3» | GLM 5.3 alone, through OpenRouter (`orglm53`, paid). "only glm 5.3 flash" is the cheap panel's GLM 5.3 Flash |
| "only deepseek v4 pro", "add deepseek v4 pro" | DeepSeek V4 Pro through OpenRouter (`ordeepseekv4pro`, paid), alone or with the cheap panel. Since 1.125.0 no panel starts it by itself |
| "only qwen", "only qwen 3.8 max" | The OpenRouter copy, `qwen38max` (paid): these words name that channel. The standard panel itself takes the Qwen Code CLI first; "only qwen38maxcli" runs the CLI copy alone |
| "add mimo 2.6 pro", "only mimo 2.6 pro", "only mimo pro", «добавь мимо 2.6 про», «только мимо про» | MiMo v2.6 Pro through OpenRouter (`ormimopro`, paid, ≈ $0.06 a run), with the cheap panel or alone. Since 1.119.3: before, "mimo 2.6 pro" started the retired Gemini 3.1 Pro (it answered to the bare word "pro") and no MiMo Pro. The bare words "pro" and "flash" name nothing. "mimo pro" and «мимо про» name it since 1.120.1 (before: the older MiMo v2.5 Pro and the free MiMo Flash) |
| "don't use openrouter" | Everything except the OpenRouter channels |

From a terminal the same is done with flags: `--panel cheap` (the default), `--panel standard`, `--route "only CLI"`, `--only codex`, `--skip openrouter`. You can see the plan before anything is spent, for free: `python routing.py` or `--dry-run`.

## Panels: line-up as of 1.125.0, prices as dated below

Each voice is one model. For every model the tables say **which harness runs it** and **what takes over when that route is missing or fails**. `python routing.py` prints the same for your own machine and spends nothing; the tables are a snapshot of version 1.125.0, and the line-up changes most weeks.

How to read the tables:
- **Harness** is the program the model works in. A *CLI agent* is the vendor's own agent on your computer — opencode, the Antigravity CLI (`agy`), Grok Build, Codex CLI, Qwen Code, Muse Code, Claude Code. It runs on your subscription or a free tier, searches the web and opens pages its own way, and needs no OpenRouter key. An *API call* is this tool sending the brief to a vendor's API on your key — OpenRouter, NVIDIA, Meta. There the model gets this tool's page-fetch tool (the tool opens the pages, so the report knows which ones were read) and, where a row says so, OpenRouter's web search, billed per search.
- **Fallback** is the next route to the same model, in order. A route takes the voice when the ones before it cannot run on your machine (no CLI, no plan, no key); the plan of the run names the route it picked. *After a failure* is the second kind: when a voice ran and ended without an answer, that route answers the same brief once, after the round; `--no-fallback` turns this kind off for a run. A step marked **paid** spends money on your key.
- **Effort** is how hard the model thinks. The tool sets the highest level each route accepts; a row says where a route takes none.
- **Prices** are per 1M tokens, input / output, on the date given; **a run** is the median of our runs (n = how many), and yours depends on the size of the document and the number of web searches. The tool prints the exact amount at the end of every run.

### Harnesses and APIs

| Harness | Maker | What it runs on | Its voices here |
|---|---|---|---|
| [opencode](https://opencode.ai/docs) | Anomaly | the [OpenCode Go](https://opencode.ai/docs/go) plan ($10 a month), or opencode's free models (OpenCode Zen) | Muse Spark 1.2 and 1.3 Contributor, MiMo v2.6 Flash, DeepSeek V4.1 Flash, GLM 5.3 Flash, Hy4 |
| [Antigravity CLI](https://antigravity.google) (`agy`) | Google | a Google account: free, with a weekly limit; Google AI Pro and Ultra raise it, and Gemini 4 Argon needs one of them | Gemini 3.8 Flash, Gemini 4 Argon |
| [Grok Build](https://docs.x.ai/build/overview) (`grok`) | xAI | an xAI account or plan | Grok 4.6 |
| [Codex CLI](https://github.com/openai/codex) (`codex`) | OpenAI | a ChatGPT plan | GPT-6.1 Sol |
| [Qwen Code](https://github.com/QwenLM/qwen-code) (`qwen`) | Alibaba (Qwen) | your Qwen Code sign-in: a plan or an API key | Qwen 3.8 Max |
| [Muse Code](https://dev.meta.ai) (`muse`) | Meta | a Muse Code plan | Muse Wonder (by name only) |
| [Claude Code](https://code.claude.com/docs) (`claude`) | Anthropic | a paid Claude plan (Pro or higher) | Claude Opus 4.6 (off by default) |
| [OpenRouter](https://openrouter.ai) API | OpenRouter | your key, paid per token (a 5.5% fee when you buy credits) | Kimi K3, GLM 5.3 and the paid fallback routes |
| [NVIDIA API](https://build.nvidia.com) | NVIDIA | your key, free for prototyping (about 40 requests a minute) | Nemotron 3 Ultra, DeepSeek V4.1 Flash, GLM 5.3 Flash |
| [Meta Model API](https://dev.meta.ai/docs/pricing-rate-limits) | Meta | your Meta key (`MODEL_API_KEY`), paid per token: Contributor $0.10 / $0.20 (Meta trains on it), Standard $1.25 / $4.25 | Muse Spark 1.2 Contributor (its second route); Muse Wonder by name |

The plan prices in this table were read on the vendors' own pages on 2026-11-20; the other plans' prices are under [«Getting started»](#getting-started-accounts-and-keys). How to set each one up: [«Getting started»](#getting-started-accounts-and-keys) and [INSTALL.md](INSTALL.md).

### The cheap panel — the default: 9 models

| Model | Harness | Fallback, in order | Effort | Payment |
|---|---|---|---|---|
| Muse Spark 1.2 Contributor (Meta) | opencode on the OpenCode Go plan (`ocspark12go`) | Meta Model API with a Meta key (`spark12cont`) → OpenRouter (`orspark12cont`, **paid**). After a failure on the Go plan: OpenRouter, once | xhigh (the ceiling) | Go plan; on a key $0.10 / $0.20 (≈ $0.002 a run, n=1) |
| Muse Spark 1.3 Contributor (Meta) | opencode on the Go plan (`ocspark13go`) | OpenRouter (`orspark13cont`, **paid**). After a failure on the Go plan: OpenRouter, once | xhigh (the ceiling) | Go plan; on OpenRouter $0.10 / $0.20 |
| MiMo v2.6 Flash (Xiaomi) | opencode on the Go plan (`ocmimo26flashgo`) | opencode's free copy (`ocmimo26flashfree`) → OpenRouter (`ormimo26flash`, **paid**; as a route only where opencode is missing). After a failure: the free copy, then OpenRouter, once each | opencode offers no levels for it; a reasoning budget of 85,000 tokens on OpenRouter | Go plan; free; on OpenRouter $0.14 / $0.28 (2026-11-15) |
| DeepSeek V4.1 Flash (DeepSeek) | opencode on the Go plan (`ocdeepseekv41flashgo`) | NVIDIA API, free, with an NVIDIA key (`nvdeepseekv41flash`) → OpenRouter (`ordeepseekv41flash`, **paid**). After a failure: NVIDIA, then OpenRouter, once each | max (accepted; its effect on depth not measured); xhigh on OpenRouter | Go plan; free; on OpenRouter $0.14 / $0.42 (2026-11-16), ≈ $0.01 a run (n=1) |
| GLM 5.3 Flash (Z.ai) | opencode on the Go plan (`ocglm53flashgo`) | opencode's free copy (`ocglm53flashfree`) → NVIDIA API, free (`nvglm53flash`) → OpenRouter (`orglm53flash`, **paid**). After a failure: the free copy, then OpenRouter, once each | max where the route takes a level (its effect not measured) | Go plan; free; free; on OpenRouter $0.0399 / $0.0784 (2026-11-15), $0.0053 a run (n=1) |
| Nemotron 3 Ultra (NVIDIA) | NVIDIA API, free, with an NVIDIA key (`nvnemotron3ultra`) | OpenRouter (`ornemotron3ultra`: a free model whose web searches are **paid**, ≈ $0.07 a run, n=6). After a failure on NVIDIA: OpenRouter, once | high on NVIDIA; the default on OpenRouter | free |
| Hy4 (Tencent) | opencode on the Go plan (`ochy4go`) | none: without the plan it ends at once and spends nothing | high | Go plan |
| Gemini 3.8 Flash (Google) | Antigravity CLI (`agy38flash`) | none: without `agy` it ends at once (its OpenRouter copy runs only when you name it: [By name only](#by-name-only)) | agy decides | Google account: free, with weekly limits |
| Grok 4.6 (xAI) | Grok Build CLI (`grokbuild`) | none: without Grok Build it ends at once | xhigh (the ceiling) | xAI account or plan |

What else to know about these voices:
- **Muse Spark** has had no free route since 2026-10-17. The Meta and OpenRouter routes run the *Contributor* tier, and if it fails they retry on the Standard Muse Spark of the same version at $1.25 / $4.25 (12.5× the input price, 21× the output); each OpenRouter route's spend guard stops a review at $1.00. Since 1.125.0 a Go voice that ends without an answer is answered once by the same model's paid OpenRouter copy (up to 1.124.0 the Go routes had no backup). `--skip ocspark12go ocspark13go` moves both voices to their next route (paid); `--skip spark` drops every Spark voice. `spark13cont` (Spark 1.3 on a Meta key) starts only when you name it.
- **Time limits.** MiMo v2.6 Flash and GLM 5.3 Flash wait up to 100 minutes on opencode, on the Go plan and on the free copy alike: through opencode they write slowly, and on the maintainer's machine 10 of 38 finished MiMo reviews on the Go plan took longer than 40 minutes. DeepSeek V4.1 Flash has 40 minutes on the Go plan and 61 on NVIDIA (the model reasons at length and NVIDIA's free endpoint streams slowly); the OpenRouter copies of these three have 20, Hy4 and the Spark Go voices 40. A cheap panel waits for its slowest voice, so with the fallbacks one voice can hold a round for 100 + 100 + 20 minutes. A shorter limit is one line in your settings file: `"timeout": "40m"` on `ocmimo26flashgo`. A voice through opencode stopped at its limit before its end marker has no answer, whatever it printed, so the next step runs; it also stops working on opencode's side (TROUBLESHOOTING.md, «An opencode voice timed out»).
- **Go plan first, then free, then paid.** MiMo v2.6 Flash, DeepSeek V4.1 Flash and GLM 5.3 Flash each take the first of these your machine has. If the Go voice ends without an answer, the free copy answers the same brief once after the round (MiMo and GLM: opencode's free copy; DeepSeek: NVIDIA's, with the key); if that ends without an answer too, the paid OpenRouter copy answers once more — never a third time. Where the free copy cannot run here (no NVIDIA key for DeepSeek, or opencode's free allowance refused today), the paid copy is the fallback directly, and the plan says so before anything is spent: `second link: if ocmimo26flashfree fails too (no answer), ormimo26flash (MiMo v2.6 Flash (OpenRouter), PAID per call - metered) runs the same brief once more, after it (up to 20m more)`.
- **A free voice's fallback runs inside the round**: within the first 25 minutes of its start, a known refusal (`provider.quota`, `provider.free-limit`) or a non-recoverable error starts the fallback at once, and a known transient error (429, 5xx) retries the voice once after a short wait. Silence (a clean exit with no answer) and a failure past 25 minutes end the voice with no retry and no fallback. A free API voice whose request gets no response headers within 300 s sends the same request once more first; paid channels never re-send. Paid and subscription voices keep the after-the-round fallback.
- **Gemini 3.8 Flash** has no paid copy in its seat: without `agy` the voice is lost, and the OpenRouter copy `orgemini38flash` runs only when you name it. **Grok 4.6** likewise has no other route in the kit.
- **Kimi K3** is not in the cheap panel: NVIDIA retired its free Kimi K3 on 2026-10-15 (its endpoint answers HTTP 410 «end of life»), and the paid OpenRouter copy is a standard-panel voice. On the cheap panel «only kimi» stops and says why.

**Three of opencode's free models share one daily allowance.** MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash behave as one daily allowance of requests per network address (measured on 2026-10-18/19; the vendor can change it): every request of every free voice spends it, and so does every run from any machine on your network. Where opencode has no Go plan the cheap panel starts two of them (MiMo and GLM); on a Go plan both run on the plan, and the free copies are only their first fallback. After `provider.quota` the refused call is not retried: the voice's next route answers instead, and the refusal is written to `~/.claude/model-orchestration.quota-state.json`. Until 00:00 UTC every run prints `[skip] <channel>` for the group with the time left and does not start it; each of the two voices then goes on to its next route (GLM to NVIDIA's free copy with an NVIDIA key, else MiMo and GLM to their paid OpenRouter copies, printed PAID). `--skip ormimo26flash orglm53flash ordeepseekv41flash` keeps such a day free. Name a channel by its own name (`--only ocmimo26flashfree`) to run it anyway; a group word such as "only opencode" does not count. opencode's free DeepSeek V4.1 Flash (`ocdeepseekv41flashfree`) starts only when named.

**In total:** on the subscriptions and free routes the cheap panel costs nothing on your OpenRouter key. It pays there only for a paid route: the two Spark voices where opencode has no Go plan (Spark 1.2 runs on your Meta key instead when you have one; $0.002 in our first run of one of them, each web search extra), MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash where their free routes cannot run (≈ $0.006 for a MiMo review of about 25,000 tokens in and 8,000 out; $0.0053 and ≈ $0.01 in our first GLM and DeepSeek calls), Nemotron's web searches on OpenRouter, and any paid copy that answers as a fallback. The run time of this line-up has not been measured yet.

#### If you do not want MiMo Flash (or any other channel)

To stop a channel from starting, put this in `~/.claude/model-orchestration.local.json` (your own settings file; updates never touch it):

```json
{ "channels": { "ocmimo26flashfree": { "enabled": false } } }
```

Any other channel switches off the same way. If the file already exists, add the channel to its `channels` section. `python routing.py` shows who will run, spending nothing.

**Spark on an OpenCode Go plan — one setting first.** Both Spark voices run on your [OpenCode Go](https://opencode.ai/docs/go) plan by themselves when opencode is signed in to it (`opencode models` lists `opencode-go/muse-spark-1.2-contributor` and `opencode-go/muse-spark-1.3-contributor`); MiMo v2.6 Flash, DeepSeek V4.1 Flash, GLM 5.3 Flash and Hy4 do the same where `opencode models` lists their `opencode-go/` ids. A Go workspace refuses both Spark models until its owner or an admin turns one switch on: in the OpenCode Console open **Settings › Privacy** (`https://opencode.ai/console/<workspace id>/settings/privacy`; shown to owners and admins of a workspace with an active Go subscription) and turn on **«Allow paid endpoints that train on request data»** (opencode.ai/docs/go, read 2026-11-13: off by default, needed by both Muse Spark Contributor models). Until then each call fails with «This Go model trains on request data. Allow paid endpoints that train on request data in your workspace's Privacy settings to use it.», and the run names the action in `REPORT.md`'s channel table and in `HANDOFF.md`, opposite the Spark that failed; since 1.125.0 the paid OpenRouter copy then answers once. To send both voices through OpenRouter (Spark 1.2 through your Meta key when you have one) instead, skip the Go copies for one run (`--skip ocspark12go ocspark13go`) or switch them off in your settings file:

```json
{ "channels": { "ocspark12go": { "enabled": false }, "ocspark13go": { "enabled": false } } }
```

**DeepSeek V4.1 Flash on the Go plan needs one more setting:** select **Global** under **Regions** on the same Privacy page (opencode.ai/docs/go, read 2026-11-09 and 2026-11-15: allowed regions are Europe and the United States by default). Until then its call fails with «This Go model requires Global regions. Select Global in your workspace's Privacy settings to use it.». The page names no region setting for MiMo v2.6 Flash or GLM 5.3 Flash.

On the plan, Spark costs $0.10 / $0.20 per 1M tokens out of the plan's limits (opencode.ai/docs/go, 2026-10-08); a live run on a 35,000-character brief used ≈ $0.01. If «Use balance» is on in the opencode console, the plan does not refuse past its limit; it bills your Zen balance instead.

**If an answer is «word salad».** A model sometimes returns its own control tokens mixed with fragments of words in several languages instead of text: NVIDIA's free Kimi K3 did so from 6 October until it was retired, and users of other programs reported the same. The run marks such an answer **CORRUPTED OUTPUT**: `REPORT.md` says «🔴 CORRUPTED», `HANDOFF.md` tells you not to open it, and it does not count as an answer.


### The standard panel — 14 models: the cheap one + 5

| Model | Harness | Fallback, in order | Effort | Payment |
|---|---|---|---|---|
| GPT-6.1 Sol (OpenAI) | Codex CLI (`codex`) | none: without Codex it ends at once | xhigh | ChatGPT plan, $0 beyond it |
| Gemini 4 Argon (Google) | Antigravity CLI (`agy4argon`) | none: without `agy` it ends at once | high (the ceiling: agy offers low, medium, high) | your Google subscription: its own weekly limit on Google AI Pro (one review used 22% of a week, n=1) |
| Qwen 3.8 Max (Alibaba) | Qwen Code CLI (`qwen38maxcli`, where its settings name `qwen3.8-max`) | OpenRouter (`qwen38max`, **paid**) where the CLI is not set up | xhigh (the ceiling) | your Qwen plan; on OpenRouter $2.00 / $6.00, ≈ $0.59 a run (n=3) |
| Kimi K3 (Moonshot) | OpenRouter (`kimik3`, **paid**) | none | max | $0.192 / $16.00 (2026-11-15); ≈ $1.38 a run (n=3, at an earlier price) |
| GLM 5.3 (Z.ai) | OpenRouter (`orglm53`, **paid**) | none | max | $0.084 / $3.60 (2026-11-15; $0.05 / $7.00 when measured); ≈ $0.19 a run (n=3) |

**Gemini 4 Argon** costs no money by itself: agy runs it on your Google subscription. On a Google AI Pro plan it has its own weekly limit, apart from the Gemini quota (agy's `/usage` lists it separately; agy says a higher Argon quota comes inside the larger Gemini quota on Ultra plans). One review of ours (a release brief with seven attachments: 17.5 minutes, 276 tool calls) used 22% of that week (n=1), so about four standard runs fill it. What agy does next is its own «Use AI Credits» setting (`agy`, then `/config`): when it is on, agy goes on and spends AI credits (`/credits` shows the balance); with it off we have not measured what happens. Where the account does not offer Argon, agy refuses at the start (exit 1 in seconds, nothing spent). `--skip agy4argon` leaves it out of one run, `{"channels": {"agy4argon": {"enabled": false}}}` in your settings file out of every run.

**Qwen 3.8 Max** runs through the Qwen Code CLI where its settings name `qwen3.8-max`, else through OpenRouter. OpenRouter serves the name `qwen/qwen3.8-max` as a dated snapshot (`qwen/qwen3.8-max-0902`); the report notes that and still counts the answer. All 3 of our OpenRouter runs since 1 September ended with a full answer (22–63 KB).

Muse Wonder left this panel in 1.113.0 and DeepSeek V4 Pro in 1.125.0: both start only when named (below).

**In total:** with OpenRouter alone, a little under ≈ $2.5 a run on your key plus subscriptions (the 1.124.0 figure, which included DeepSeek V4 Pro, ≈ $0.18); Kimi K3 through OpenRouter (≈ $1.38) is the largest part. GLM 5.3 and Qwen 3.8 Max take longest, medians 16 and 15 minutes on OpenRouter (GLM's longest run: 44 minutes). Most paid channels have a spending ceiling per run ($2-5); a channel that reaches it stops.

### By name only

| Model | Harness | Fallback | Effort | Payment |
|---|---|---|---|---|
| Grok 4.20 Reasoning (xAI) | OpenRouter with xAI's own web search (`orgrok420`) | none (the same model through xAI's own API, `grok420`, ships off) | a reasoning budget of 90,000 tokens | **paid**: $1.25 / $2.50, each web search $0.005 (2026-11-16); ≈ $0.10 a review (n=1); a ceiling of $2.00 a review |
| Muse Wonder 1.0 (Meta) | Muse Code CLI (`mcwonder10cont`) | none: without Muse Code the run stops before paying; the Meta Model API copy (`wonder10`) and the OpenRouter copy (`orwonder`) run only by their own names | xhigh on the plan (its ceiling); max on the API | your Muse Code plan; on a key $4.00 / $20.00 |
| DeepSeek V4 Pro (DeepSeek) | OpenRouter (`ordeepseekv4pro`) | none | xhigh (the ceiling) | **paid**: $0.90 / $1.50 (2026-11-15), ≈ $0.18 a run (n=3, at an earlier price) |
| GLM 5.3 FlashX Legal (Z.ai) | OpenRouter, web search on (`orglm53flashxlegal`) | none | max | **paid**, a ceiling of $2.00 a review. Joins a run started with `--system legal-research` (the framing every legal brief uses) by itself |
| Gemini 3.8 Flash (Google), API copy | OpenRouter (`orgemini38flash`) | none | high (this model's ceiling) | **paid**: $0.825 / $4.125 (2026-11-13) |
| MiMo v2.6 Pro (Xiaomi) — off | OpenRouter (`ormimopro`) | none | — | **paid**: $0.435 / $0.87, ≈ $0.06 a run (n=9). Off since 1.113.8: it was almost all of the cheap panel's cost and its slowest voice |
| Claude Opus 4.6 [1M] (Anthropic) — off | Claude Code CLI (`cclopus46`) | none | max | a Claude plan. Off by default; when on, it runs with permission prompts bypassed ([SECURITY.md](SECURITY.md)) |
| Gemini 3.1 Pro (Google) — off | Antigravity CLI (`agy31pro`) | none | — | agy 1.4.7 no longer offered `gemini-3.1-pro` on our Google AI Pro account on 2026-10-24 (`agy models` lists what yours has); where the model is gone agy refuses at the start |
| Step 5 Preview (StepFun) — off | opencode on NVIDIA's endpoint (`ocstep5previewnv`) | NVIDIA API (`nvstep5preview`, the route without opencode) | max | free, needs an NVIDIA key. Off since 1.118.0: it ran out of its 20 minutes in two of our rounds |

**Muse Wonder** is Meta's most capable model (preview) and the most expensive voice here per token: 40× the input and 100× the output price of Spark Contributor. No panel runs it; it starts only when you name it — "only wonder", "add wonder", "muse wonder", "wonder 1.0", «вондер», or a channel's own name. A broader word never does: not "standard panel", "all models", "only muse", "only openrouter", "CLI only". The model's word starts ONE Wonder, `mcwonder10cont`: the Contributor variant only Muse Code subscriptions offer (Meta may use that content for training), at `xhigh` (the plan's measured ceiling), counted against your plan and never against a key (setup: INSTALL.md, «Muse Code CLI»). Where the Muse Code CLI is not installed, the run stops before paying anything and names the paid Wonders it could start: `wonder10`, the Meta Model API on `MODEL_API_KEY` (Standard mode only: what you send is not used for training), with no per-run ceiling, because Meta runs the whole search loop inside one request (a two-line check cost $0.016), or `orwonder` on OpenRouter, which stops at $5 a run. To seat it in the standard panel again, on your subscription only, put `{"channels": {"mcwonder10cont": {"explicit_only": false}}}` in `~/.claude/model-orchestration.local.json`.

**Grok 4.20 Reasoning** (xAI) runs only when you name it: "only grok 4.20" / «только грок 4.20» runs it alone, "add grok 4.20" / «добавь грок 4.20» runs the cheap panel plus it, and so does `--only orgrok420`. A broader word never does: not "standard panel", "all models", "only grok", "only openrouter"; the plan of such a run prints «🔴 grok420voice was NOT started (orgrok420): it starts only when named - by the model's word (grok420voice / grok 4.20) or its own name». It runs through OpenRouter on your key (model `x-ai/grok-4.20`) and is **paid**: ≈ $0.10 a review in our first call (22.8 s; 66,862 tokens in, 3,343 out; $0.0981 as OpenRouter reported it). It searches the web through OpenRouter's web plugin, which uses xAI's own search ($0.005 a search). Without `OPENROUTER_API_KEY` a run that names it ends at once, naming the missing key and spending nothing. In 1.122.0–1.122.2 it ran in every cheap and standard panel; the maintainer switched it off by default as too expensive through OpenRouter. To have it in every panel again, put `{"channels": {"orgrok420": {"explicit_only": false}}}` in your settings file and run `python routing.py --accept-settings` once (the field changes where your briefs go, so a paid run refuses until you accept it). The direct xAI copy (`grok420`, with `XAI_API_KEY`) ships off; switched on in your settings file it is a cheap-panel voice (one voice with the OpenRouter copy, never both; it ends xAI's server-side search loop after 10 turns, `max_turns`), and when it cannot run (no `XAI_API_KEY`) the run names the missing key instead of moving to the paid OpenRouter copy.

### Web search

On the OpenRouter channels each web search is billed apart from the tokens, so it is paid even on a free model. A channel that names the Exa engine pays $0.007 a request (Exa includes up to 10 results; ours ask for 5); a channel that names none gets the model provider's own search where OpenRouter offers one (OpenAI, Anthropic, Google, Perplexity and SpaceXAI models, in OpenRouter's words), at that provider's price, and Exa otherwise (OpenRouter's web-search page, 2026-10-23). The OpenRouter copies of MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash and the NVIDIA voices do not search: they read pages with the page-fetch tool. The CLI agents search by themselves, within their plan.

**`python routing.py` always prints the live channel list.** The tables above are a snapshot: the line-up of 1.125.0, prices of 2026-10-05 unless a row gives its own date, and the registry changes most weeks.

*Where the prices come from (2026-10-05):*
- the OpenRouter catalogue `openrouter.ai/api/v1/models`, read at 09:15 UTC;
- free-model limits: `openrouter.ai/docs/api_reference/limits`;
- web search: `openrouter.ai/docs/features/web-search`;
- the fee: `openrouter.ai/pricing`;
- MiMo prices match Xiaomi's own price list: `mimo.mi.com/docs/en-US/price/pay-as-you-go`;
- NVIDIA's Kimi K3 (`build.nvidia.com/moonshotai/kimi-k3`) is gone: on 2026-10-15 from 08:00 UTC its endpoint answers HTTP 410 «end of life» (measured that day);
- plans: `help.openai.com/en/articles/11369540`, `openai.com/index/introducing-chatgpt-go`, `antigravity.google`, `gemini.google/subscriptions`, `x.ai/pricing`, `x.ai/news/grok-build-for-everyone`, `opencode.ai/docs/zen`, `claude.com/pricing`, `dev.meta.ai/docs/pricing-rate-limits`.
- checked again on 2026-11-20 for the harness table: `opencode.ai/docs/go`, `claude.com/pricing`, `dev.meta.ai/docs/pricing-rate-limits`, `openrouter.ai/pricing`, `build.nvidia.com`, `antigravity.google/blog/gemini-4-argon-in-google-antigravity`.

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
--only codex agy4argon grokbuild    # just the ones named
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
- **The harness never sends a password or key.** A brief or attachment that holds one is refused outright, no override. A CLI agent opens files on your disk by itself, so what it may read is set by its own rules ([SECURITY.md](SECURITY.md)).
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

You write your question in a plain text file and run one command. Below is the real output of a run on 2026-10-05, shortened, from before NVIDIA retired Kimi K3 and opencode its free Muse Spark (so the cheap panel then had six models, `nvkimik3` and `ocspark13free` among them): it checked five claims about OpenRouter and NVIDIA, one of which we made false on purpose.

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

`REPORT.md` has a **«Tokens per model»** table: how many characters and roughly how many tokens (≈, by the «bytes ÷ 4» rule) we sent each model and how much it wrote back, and beside them the vendor's own meter (`billed in`, `out tok`), or `unknown` where the vendor reports no such number. For a CLI agent the vendor's count is usually 10-50 times what we sent: the agent reads files and pages on its own and re-sends the whole conversation at every step. For the opencode channels the tool takes tokens and cost from opencode's own session store, because its output stream sometimes drops the per-step totals.

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
- **the cheap panel** — on your OpenRouter key only these are paid: the two Muse Spark voices where opencode has no Go plan (Spark 1.2 runs on a Meta key instead when you have one; $0.002 in our first run of one of them, each web search extra); MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash where their free route cannot run on your machine, and since 1.121.0 their paid copy when it answers as a fallback ($0.0003 to ≈ $0.01 in our first calls); Grok 4.20 Reasoning only when you name it (since 1.123.0; in 1.122.0–1.122.2 on every run, ≈ $0.10 a review); plus subscriptions;
- **the standard one** — about $2.5 with OpenRouter alone (Kimi K3 ≈ $1.38 of it), and DeepSeek V4 Pro now runs only when named;
- **CLI only** — $0 on the key, subscriptions only.

The per-model breakdown is in the tables above. The accounts, none of which this tool provides, are listed under [«Getting started»](#getting-started-accounts-and-keys). *Optional:* the `GEMINI_API_KEY`, `XAI_API_KEY` and `MIMO_API_KEY` keys reach Gemini, Grok and MiMo through the vendors' own APIs, not always in the version a panel runs (Grok 4.20 and MiMo v2.5 Pro there; `python routing.py` prints each channel's model); those channels are off by default, details in [INSTALL.md](INSTALL.md#direct-vendor-alternatives-to-openrouter-off-by-default).

**You do not need them all, and you should not start with them all.** Missing a key or a CLI is a
normal condition, not an error — the tool runs whatever is available and tells you plainly what it
skipped. Start with one.

🔴 **Do not count the channels from this file.** The number is whatever `channels.json` enables and
it has changed most weeks. `python routing.py` prints the live list and spends nothing. (Every
prose copy of that list in this repository has been wrong within days of being written — including,
at one point, two different numbers four lines apart in this very file.)

**Some channels are cheap because of their data terms, not despite them.** As published,
`ocspark12go`, `spark12cont` and `orspark12cont` run the same Muse Spark 1.2 *Contributor* tier — on the OpenCode Go plan,
directly and through OpenRouter respectively — `ocspark13go` and `orspark13cont` its 1.3 *Contributor* tier, and `ornemotron3ultra`, `ocmimo26flashfree` and `ocnemotron3ultrafree` run *free* tiers; on every one of them the vendor may use prompts and completions for training or to improve its model. That is
the trade being made, it is stated in [PRIVACY.md](PRIVACY.md) with each vendor named, and the
tool prints each channel's data policy in the plan **before** it spends anything — that line, not
this paragraph, is the current list. If a brief should not be trained on, drop those channels for
that run: `--skip ocspark12go ocspark13go spark12cont orspark12cont orspark13cont ornemotron3ultra ocmimo26flashfree ocnemotron3ultrafree`. (The free Spark through opencode,
`ocspark13free`, was on this list until opencode dropped it on 2026-10-17; it ships switched off.)

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
day the kit checks for a release — at session start and after a real round — and prints that
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

**Both are automatically stripped of keys, tokens and personal-data shapes, and your home folder
is written as `~`** (since 1.113.9), so you can paste them straight into a chat with an AI assistant and ask
it to fix the problem. Before you attach them to a public bug report, glance at the paths of the files
you passed (the brief, `--out`, attachments): they are kept, because a fix needs them.

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
sources each model cited actually exist and, where the channel reports it, were actually opened. The disagreement and the audit
are what you are paying for.

**Can I use this from Cursor / Windsurf / another coding tool?**<br>
Yes. It is plain Python with no dependencies. Any tool with shell access can run it. There is
also a one-command Claude Code plugin install — see [Install](#install).

**Does this work with OpenRouter models?**<br>
Yes. OpenRouter is the biggest single-account group: one API key unlocks Kimi, Qwen, Gemini,
MiMo, Grok, GLM, DeepSeek, Muse Spark and NVIDIA Nemotron. Start with that and add direct
vendor access later for the models that benefit from it.

**Is this expensive?**<br>
No. On the cheap panel only these are paid on your OpenRouter key: the two Muse Spark voices where opencode has no Go plan (Spark 1.2 runs on a Meta key instead when you have one; $0.002 in our first run of one of them, each web search extra), MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash where their free route cannot run on your machine, and since 1.121.0 their paid copy when it answers as a fallback ($0.0003 to ≈ $0.01 in our first calls; Grok 4.20 Reasoning, paid on every run in 1.122.0–1.122.2, only when you name it since 1.123.0), plus subscriptions you most likely already have. Say "CLI only" with `--no-fallback` and there is not one paid request on the key. The standard panel costs about $2.5 a run (in 1.122.0–1.122.2 ≈ $0.10 more, Grok 4.20 Reasoning): since 1.112.0 Kimi K3 always answers there through OpenRouter. The $2.5 is the median of our runs in September–October 2026, the $0.002 a single run; the tool prints the exact amount of your run at its end. See [what it costs](#what-it-costs-honestly).

## Found a bug? Want a feature? Want to work together?

| What you want to do | Where it goes |
|---|---|
| **Report a bug** | [Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) and attach `diagnostics.json` — keys, tokens and your home folder are scrubbed by construction; glance at the paths of the files you passed before posting |
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
