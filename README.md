# AI Second Opinion

[![selftest](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml/badge.svg)](https://github.com/igorsaevets/ai-second-opinion/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](INSTALL.md)

**One AI agreeing with you proves nothing. Three of them arguing is worth reading.**

Send the same document to three different top-tier AI models at once. Get back what each one
found, where they contradict each other — and a mechanical check of whether they actually did
the research or quietly made it up.

[Русская версия](README.ru.md) · [How it works, in technical detail](TECHNICAL.md) ·
[Install](INSTALL.md) · [When something breaks](TROUBLESHOOTING.md)

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

- **Three independent models, same document, at the same time.** They do not see each other's
  answers, so agreement means something and disagreement means more.
- **It shows you the disagreement.** That is the actual product. Two models calling a claim fine
  and one calling it fatal is the most useful thing you will read all week.
- **It checks the receipts.** Every source link each model cites is opened and reported as
  live / moved / dead. Where the channel supports it, the tool also checks whether the model
  *actually opened* the page it cited, or just listed it.
- **It catches a model that quietly refused.** A model that declines a task still formats its
  reply correctly, so it passes every naive "did it finish?" check. This catches that.
- **Nothing with a password or key ever leaves your machine.** Blocked outright, no override.
  Personal data — ID numbers, SSNs, emails, phone numbers, dates of birth — is blocked by default
  and requires a deliberate flag.

## Who this is for

| You are | You use it to |
|---|---|
| **Founder / CEO** | Pressure-test a strategy memo, a board deck, an investor update or a pricing decision before anyone external sees it. Three models, three sets of objections, before your board finds them. |
| **Product manager** | Review a spec or PRD for holes, check competitive claims you are about to publish, stress-test a launch plan's assumptions. |
| **C-level / operations** | Verify claims in a vendor proposal or a consultant's report. Check that a regulation you are relying on is still current and says what someone told you it says. |
| **Legal / compliance** | Verify that every citation in a research memo resolves to a real document that actually says what the memo claims. This is source-verification work, done properly and at speed. See the note below. |
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
[spark] OK  97s    22 sources cited, 0 dead
[codex] OK  407s   32 sources cited, 1 dead (deliberate negative check - correct)
[agy]   OK  44s    11 sources cited, 3 dead  <- PROBLEM: cited pages it never opened
2/3 channels returned a verified review.
```

Plus one file per model containing the actual review, and a diagnostics file if anything went
wrong.

## The one habit worth stealing

**Put a claim you know is false into every document you send for review.**

It costs nothing. A model that "confirms" your planted falsehood has just told you exactly what
all its other confirmations are worth. In the runs behind this tool, all three models caught both
planted claims — which is the only reason to believe the things they *did* confirm.

## What it costs, honestly

Three separate accounts, none of which this tool provides:

| Channel | What you need | Rough cost |
|---|---|---|
| **Spark** | An API key | Metered per use — you pay per review |
| **Codex** | A paid OpenAI plan that includes Codex | Included in the subscription, with weekly limits |
| **Antigravity (Gemini)** | An eligible Google account | Included, with limits |

**You do not need all three.** Missing a key or a CLI is a normal condition, not an error — the
tool runs whatever is available and tells you plainly what it skipped. You can start with one.

**Do not share one API key across a team.** It bills to whoever owns it, nobody can be
attributed, and revoking it cuts everyone off at once. One key per person, or have those people
run the other two channels.

## Install

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

## Roadmap

Planned for the next versions:

- **Kimi K3 via Hermes** — added as a fourth reviewer channel through the Hermes gateway.
- **Direct OpenRouter support** — one credential reaching many models, so you can add reviewers
  without a separate account and CLI for each. This makes running four or five independent
  reviewers practical rather than an installation project.
- Per-run cost reporting, and a summary that drafts the disagreement table for you.

Both additions are configuration, not new code: every model this tool knows about lives in a
single `channels.json` registry, and adding a channel is an edit to that file.

## What this is not

- **Not a fact database.** It reads the live web through the models' own search tools. It can be
  wrong, which is exactly why it shows you three answers instead of one.
- **Not a replacement for an expert.** It is very good at finding what an expert should look at.
- **Not "deep research" mode.** Those are separate, separately-billed products at both vendors and
  are not reachable from a normal subscription. This runs the models at maximum depth with a
  source-discipline instruction, which is the same shape — with the advantage that you get three
  of them and an audit of the citations, which no deep-research product gives you.
- **Not automatic.** You still read the disagreement and decide. The tool's job is to make sure
  you are deciding with the objections in front of you.

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
