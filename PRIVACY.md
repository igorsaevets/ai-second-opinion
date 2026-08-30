# Privacy

Short version: **this tool has no server, collects nothing, and sends your document to the AI
vendors you choose to run.** That last part is the whole point of it, and it is the part worth
reading carefully.

This document describes `model-orchestration` as published in this repository. If the behaviour and
this text ever disagree, **the code is right and this file is a bug** — please report it.

> This file used to live only in the published repository, with no source in `kit/` where every
> other repository document is authored. It therefore drifted: it described the personal-data gate
> as blocking by default months after that was inverted, and it listed a channel set that had
> since grown. Both are corrected below. A privacy document that overstates its protections is
> worse than one that admits their limits, because people rely on it.

## There is no operator, so there is nothing to collect

There is no backend, no telemetry, no analytics, no crash reporting, no licence check and no
account. The author receives nothing when you run this. Everything happens on your machine and
between your machine and the vendors whose channels you enable.

The only files written are inside the run's own output folder: the answers each channel returned,
and the report about them. Nothing is uploaded anywhere.

## What DOES leave your machine, and to whom

The purpose of the tool is to send one brief to several independent models, so **the text you pass
to it is transmitted to third parties.** Which ones depends entirely on which channels you run.

Do not take the list below as the current set — channels change most weeks. **`python routing.py`
prints exactly which channels are enabled right now, and `--dry-run` prints each one's data policy
before anything is sent.** Both are free.

| how you reach it | who receives your text |
|---|---|
| `MODEL_API_KEY` | Meta, over HTTPS |
| the Codex CLI | OpenAI, through the CLI you already have installed |
| the Antigravity CLI | Google, through the CLI you already have installed |
| `OPENROUTER_API_KEY` | **OpenRouter, which forwards to the model's owner** — which company depends on the channel's model id (Meta, Moonshot, Alibaba, Google and others); the resolved plan names the one that applies before anything is sent |
| `GEMINI_API_KEY` | Google, directly through their API |
| `XAI_API_KEY` | xAI, directly through their API |
| `MIMO_API_KEY` | Xiaomi, directly through their API |

Note the OpenRouter row carefully: it is a **reseller**, so enabling one key can route your brief to
any of several separate companies. Which one is decided by the channel's model id, and it is
printed in the plan.

Each vendor's own privacy policy and retention rules apply to what you send them. This tool cannot
and does not change them. **Once a brief has been sent it cannot be recalled.** If you enable no
channels, nothing is transmitted.

## Three channels are cheap because of their data terms

Some tiers are discounted, or free, **in exchange for permission to train on what you send**. As
published, three channels are on such a tier: `spark12cont` (Meta's "contributor" tier, reached
directly), `orspark12cont` (the **same** contributor tier reached through OpenRouter — so the
payload passes OpenRouter's own policy on the way to Meta's contributor terms), and
`ornemotron3ultra` (a free tier, which on OpenRouter requires the account-level prompt-training
setting to be on).

This is not a defect and it is not hidden: each channel's data policy is printed in the plan before
anything is spent, and it is the reason those channels cost almost nothing. If a brief should not
be trained on, drop them for that run:

```
--skip spark12cont orspark12cont ornemotron3ultra
```

Assume retention on those tiers is permanent.

## What is blocked from leaving, and what merely warns

Two mechanisms, deliberately unequal:

- **Secrets are blocked outright, with no override.** Anything matching a password, API key, token
  or private-key shape stops the run. There is no flag to force it, at any setting. This is the one
  rule with no escape hatch, because "the user meant to" is indistinguishable from "the user did
  not notice".

- **Personal data warns loudly and is SENT.** 🔴 This is the opposite of what this file used to
  say. ID numbers, case and receipt numbers, national-insurance and social-security numbers, email
  addresses, phone numbers and dates of birth are detected and **itemised by kind and line number —
  never by value — and then the run proceeds.** Pass `--strict-pii` to make it a hard stop instead.

  The reason for the inversion, stated plainly because it is a weakening of a protection: the gate
  had a high false-positive rate on exactly the legal and medical documents people bring to a tool
  like this, and a gate that cries wolf teaches you to pass its override by reflex — which disables
  it for the real case. That was measured: in one run, fifteen refused spans were fictional, and
  five genuine ones sat in the same document unnoticed behind the habit of overriding. A warning
  that cannot be switched off is a better instrument than a block with a reflexive bypass. If you
  would rather have the block, `--strict-pii` restores it.

Detection is pattern-based. **It is a seatbelt, not a guarantee.** It will not recognise every
format in every jurisdiction, and it cannot know that an ordinary-looking sentence is confidential.
Read what you are sending.

Separately, the diagnostics file is scrubbed before it is written, because it is designed to be
pasted into a chat or attached to a public issue, and "safe as long as the author remembered" is
not safe.

## Credentials

Where a channel needs an API key it is read from your environment at the moment of the call. Keys
are never written to the output folder, never printed, and never included in a diagnostics file.
The tool does not store, forward or transmit your credentials anywhere except to the vendor that
issued them.

**Never paste a key into a chat with an AI assistant**, including while installing this. A key that
has appeared in a transcript is leaked, and the only correct response is to rotate it. See
`INSTALL.md`.

## Your data, your machine

Delete a run's folder and that run is gone from your side. What a vendor retains is between you and
that vendor; consult their policy.

## Contact

Questions or a privacy problem with this plugin: open an issue at
https://github.com/igorsaevets/ai-second-opinion/issues, or see `SECURITY.md` for the private
reporting channel if the problem should not be public.
