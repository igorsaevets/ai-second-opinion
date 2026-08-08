# Privacy

Short version: **this tool has no server, collects nothing, and sends your document to the AI
vendors you choose to run.** That last part is the whole point of it, and it is the part worth
reading carefully.

Last updated 2026-08-08. This document describes `model-orchestration` as published in this
repository. If the behaviour and this text ever disagree, the code is right and this file is a bug.

## There is no operator, so there is nothing to collect

There is no backend, no telemetry, no analytics, no crash reporting, no licence check and no
account. The author receives nothing when you run this. Everything happens on your machine and
between your machine and the vendors whose channels you enable.

The only files written are inside the run's own output folder: the answers each channel returned,
and the report about them. Nothing is uploaded anywhere.

## What DOES leave your machine, and to whom

The purpose of the tool is to send one brief to several independent models, so **the text you pass
to it is transmitted to third parties.** Which third parties depends entirely on which channels you
run:

| channel | who receives your text |
|---|---|
| `spark11`, `spark12cont` | Meta, over HTTPS |
| `codex` | OpenAI, through the Codex CLI you already have installed |
| `agy31pro`, `agy36flash` | Google, through the Antigravity CLI you already have installed |
| `goog36flash` | Google, directly through their API |
| `kimik3`, `qwen38max`, `orgemini36flash` | OpenRouter, which forwards to Moonshot, Alibaba and Google respectively |

Each vendor's own privacy policy and retention rules apply to what you send them. This tool cannot
and does not change them. **Once a brief has been sent it cannot be recalled.**

🔴 **One channel is a training tier and it is labelled as such.** `spark12cont` is a contributor
model: the vendor **may train on the prompts and completions** sent to it. It is cheaper because of
that, not despite it, and there is no non-training variant of that model. The channel registry
carries this warning in its own `data` field, and the preflight prints it before anything is sent.
If a brief is confidential, exclude it: `--skip spark12cont`.

If you enable no channels, nothing is transmitted.

## What is blocked from leaving, and what merely warns

Two different mechanisms, deliberately unequal:

- **Secrets are blocked outright, with no override.** Anything matching a password, API key, token
  or private-key shape stops the run. There is no flag to force it. This is the one rule with no
  escape hatch, because "the user meant to" is indistinguishable from "the user did not notice".
- **Personal data is blocked by default and needs a deliberate flag.** ID numbers, national
  insurance and social-security numbers, email addresses, phone numbers and dates of birth are
  detected and refused unless you explicitly say to proceed. This one has an override because
  legitimate work sometimes involves such documents, and a rule people cannot satisfy is a rule
  people route around.

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

## Your data, your machine

Delete a run's folder and that run is gone from your side. What a vendor retains is between you and
that vendor; consult their policy. For the training-tier channel above, assume retention is
permanent.

## Contact

Questions or a privacy problem with this plugin: open an issue at
https://github.com/igorsaevets/ai-second-opinion/issues, or see `SECURITY.md` for the private
reporting channel if the problem should not be public.
