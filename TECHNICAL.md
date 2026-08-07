# Technical description

The plain-language version is in **[README.md](README.md)**. This file is the engineering
account: what runs, what is enforced, what was measured, and why each rule exists.

Everything here is empirical. Each rule exists because it failed at least once on a real run.
Nothing was inherited from a vendor's documentation without being reproduced.

---

## 1. Architecture

One brief, three independent reviewers, in parallel, followed by verification of each answer.

| Channel | What it is | Transport | Typical time | Billed against |
|---|---|---|---|---|
| **spark** (`http`) | Muse Spark 1.1 | Messages API over HTTPS | 1–5 min | metered API key |
| **codex** | OpenAI Codex CLI | local subprocess | **7–35 min** | your subscription's heaviest tier |
| **agy** | Antigravity CLI (Gemini 3.1 Pro) | local subprocess | ~1 min | your Google subscription |

Threads, not asyncio: two of the three are blocking subprocesses, and on Windows asyncio
subprocess support depends on the event-loop policy. Threads just work.

**The point is not redundancy, it is disagreement.** Measured across two rounds on real work the
ordering inverted: once the 55-second channel found the item both slower ones missed, once the
25-minute one did. Which channel wins is not predictable from cost, from speed, or from the
previous round. That is the entire argument for running all seven.

### Files

```
SKILL.md                  operating manual, loaded on demand by Claude Code
orchestrate.py            the harness: dispatch, verification, gates, diagnostics
routing.py                resolves registry + flags + free text into a plan
channels.json             THE registry - every model name lives here and nowhere else
doctor.py                 "is this machine set up?"      - probes, never asserts
selftest.py               "does the code still behave?"  - ~50 behavioural checks
citecheck.py              citation grounding and existence checks
patch_agy_permissions.py  mandatory post-install step for the agy channel
references/*.md           detail read on demand
systems/*.md              system-prompt presets
```

---

## 2. What is enforced in code, not in prose

This kit assumes prose restrains nothing, because that was measured: an instruction in a model's
own system prompt failed **5 times out of 5** to stop it calling a tool it had been told not to
call. Only permission rules worked. So everything that must not happen is a check, not a sentence.

### Outbound payload gate

- **A payload containing a key, token or private key is never sent. There is no override flag.**
  Nine detectors: private-key blocks, vendor key formats, labelled assignments, bearer tokens.
- **A payload containing personal identifiers is blocked** unless `--allow-pii` is passed
  deliberately. Seven detectors: national ID numbers, case/receipt numbers, SSNs, emails, phone
  numbers, labelled dates of birth, labelled passport numbers.
- **The gate reports kind and line number, never the value.** Printing the value would leak it
  into the transcript, which is the same mistake one step earlier.
- Both the brief **and** the system-prompt file are scanned. A hand-written preset is just as
  capable of carrying a name or a key as the brief is.
- It runs under `--dry-run`, so checking costs nothing.

**Once a payload is sent it cannot be recalled. It is at three separate vendors.**

### Inbound / logging gate

Everything written to the console, `run.log` or `diagnostics.json` passes through a substitution
that replaces secret- and PII-shaped runs with `[REDACTED:KIND]`.

It is a substitution, never a truncation. A "masking" expression that kept the first 60 characters
of a 48-character key kept all of it, and that is how a real key once reached a transcript. A
substitution cannot fail that way: either the pattern matched and the text is gone, or it did not
match and nothing claimed otherwise.

Scrubbing happens in the single logging choke point rather than at each call site — found while
testing the crash handler, where an exception whose *message* contained a key printed it in full
because only the diagnostics file was being scrubbed.

### Answer verification

- **A refusal cannot pass as a review.** A model that declines still obeys the formatting
  instruction, appends your end marker, and clears every mechanical check. Measured: a 162-byte
  refusal was reported as a successful channel. Detection combines a refusal tell in the opening
  with a short body; a long review that merely mentions such a phrase does not trip it.
- **The end marker is checked, and now also instructed.** The harness verified the marker for
  months without ever asking the model to emit one — the brief's author was silently expected to
  know. A brief written by anyone who had not read the docs came back `PROBLEM` on every
  channel of the three then configured with a perfectly good review inside. The instruction is now appended automatically when
  the brief does not already contain the marker.
- **Citations are cross-checked against pages actually opened.** One channel fabricated four
  real-looking document numbers in a single session, each under the correct article slug — and one
  was even fetched successfully, because the site resolves by number and ignores the slug.
  Grounding varied 1/6 → 4/4 → 5/5 across runs of the *same* brief, so this is checked every run.
- **Never trust an exit code or a status field on these channels.** Observed, on the same channel
  in the same week: `SUCCESS` with an empty answer, `ERROR` with a complete one, and exit `0` on a
  hard HTTP 400 returned by the vendor's own server.

---

## 3. Citation checking: two different questions

These are constantly conflated, and they need different evidence.

| Question | Name | What it needs |
|---|---|---|
| Did the model actually open this page? | **grounding** | the channel's event log |
| Does this page exist at all? | **existence** | just fetch it |

`citecheck.py --answer FILE --resolve-urls` answers the second **without an event log**, which
makes it the only mechanical citation check possible on the Codex channel, which exposes no
telemetry whatsoever.

Each cited URL is reported as `LIVE` / `MOVED` / `DEAD` / `BLOCKED` / `UNKNOWN`.
**`BLOCKED` and `UNKNOWN` are never reported as fabrication.** Inferring "fake" from "could not
check" is exactly the move this harness forbids the models; it would be worse coming from the
harness. Non-public hosts are refused rather than fetched, because a cited URL is model-generated
text and should be treated as untrusted input.

Measured on one round: spark 22 URLs / 0 dead; codex 32 / 1 (a deliberate negative probe — the 404
*was* the answer); agy 11 / 3 dead, having opened zero pages.

### Automatic re-ask on zero grounding

When the agy channel cites sources and opened **none** of them, the harness re-runs it once with
an explicit instruction naming that specific failure. Measured, same brief, back to back:

| | attempt 1 | escalated re-run |
|---|---|---|
| citations grounded | **0 of 3** | **8 of 8** |
| dead URLs | 3 of 11 | **0 of 8** |
| tool calls / search+fetch | 14 / 10 | **72 / 43** |
| thinking tokens | 4 387 | **9 257** |

Design constraints worth keeping: exactly one extra attempt; the cost announced before it is
spent; both transcripts kept; and if the re-run also grounds nothing, the **first** answer is
returned — cleaner conditions, no nagging — with both flagged unverified, because two ungrounded
runs say something about the brief, not just the model.

This is the fourth data point on one asymmetry: **prose cannot restrict what tools a channel may
touch, but prose reliably improves how it uses them.** Do not collapse either half into "prompts
work" or "prompts don't".

---

## 4. Routing

Every model name lives in `channels.json` and nowhere else. Adding, disabling or re-pointing a
channel is an edit to that file, never to `orchestrate.py`.

```
--only spark codex          restrict to named channels (any alias works)
--skip gemini               exclude
--set codex=gpt-5.4         pin a specific model
--route "<free text>"       parse an instruction as written, in Russian or English
--tier quick|standard|strategic|deep
--system legal-research     system-prompt preset
--dry-run                   complete preflight, spends nothing
```

`--route` is deterministic rule-based parsing, not a model call. It handles negation
(«не использовать», «без», «кроме», "don't use"), substitution («вместо», including the «вместо
нее» anaphora referring back to the just-refused model), and «только»/"only".

**Design rules, each earned:**

- The resolved plan is **printed before anything is spent**, with a reason per channel and an
  `EXPENSIVE` marker. A router that silently picks a costly model defeats its own purpose.
- An unparseable route is a **hard stop** listing known aliases — never a guess.
- **A flag contradicting the route is also a hard stop**, not a precedence rule. `--only codex`
  combined with a route excluding codex used to *run codex*, printing "excluded by name" on the
  line directly above `[RUN] codex`. Neither source outranks the other; a genuine contradiction
  names both sides and stops. A safety printout that contradicts itself is worse than none.
- Colliding aliases are rejected **at registry load time**, where they are a config typo, rather
  than at spend time, where they are the wrong model.
- Effort is clamped to what each model actually exposes, and **ties break upward** — an
  under-thought review is confidently wrong, which costs more than the quota.
- Precedence: registry < environment variables < flags/route.

---

## 5. Partial installs are a supported state

A missing API key or a missing CLI disables exactly one channel and nothing else. The preflight
**warns but never blocks**: a stale check that vetoes a working channel is worse than a warning.

Verified by `selftest.py`, which covers: each CLI missing individually, both missing at once, and
the API key absent from *both* the environment and the Windows registry fallback — the trap being
that emptying the environment variable is not enough, because the code reads `HKCU\Environment`
next and would find the real key.

---

## 6. Diagnostics

Every run writes `run.log` and `diagnostics.json` into the output directory.

`run.log` is appended **per line as it happens**, not buffered. If the run is killed or the
machine dies, a buffered log is empty at exactly the moment it is needed.

`diagnostics.json` carries `schema`, `how_to_read_this`, the invocation, the environment (Python,
OS, both CLI versions probed live, key presence and length only), the resolved plan, the
preflight, per-channel results and telemetry, the full console transcript, and — when the tool
itself crashes — the traceback.

`problems[]` is the part to act on: each entry pairs the raw detail with a plain-language
`likely_cause` and a `suggested_fix`, matched from a table of known failure signatures.

An unexpected exception is turned into this file rather than a bare traceback. A traceback on a
terminal is lost when the window closes, and it is the one thing a user cannot usefully relay
("it says something about line 812").

Both files are scrubbed by construction, which is what makes the intended workflow safe: hand
`diagnostics.json` to an AI assistant and ask it to fix the cause.

---

## 7. System-prompt presets

`systems/base-depth.md` is the default: maximum depth, first intuition may be wrong, enumerate
alternatives, check for contradictions, unofficial-but-lawful routes named alongside official
ones, no output-length cap, escalate fetch tools, never reconstruct a citation.

`systems/legal-research.md` is the regulated-domain variant. Three layers: role (research
assistant under professional supervision, explicitly not your lawyer); status of the output
(internal work product reviewed before reliance); boundaries stated as the task (verify claims and
find errors; do not decide what to file, do not choose an answer for a government form, do not
opine on a named person's eligibility).

⚠️ **Do not merge the two presets.** `base-depth` asks for "unofficial, grey routes alongside the
official one", which is right for engineering and wrong for a regulated domain, where it reads as
*suggest a way around the rule* — the exact trigger that gets a legal brief refused. The omission
is deliberate and is documented inside both files.

**Measured, same six claims:** framed as filing strategy, the strongest channel returned a
162-byte refusal. Framed as source verification with the preset, the identical claims were
answered 6/6 with correct citations from official domains only. On another channel the same
reframing moved citation grounding from 1/6 to 6/6 and tripled the tool calls.

All presets force English output: the report is machine-read by the orchestrating model, and
Cyrillic costs roughly twice the tokens. A Russian brief still returns an English answer.

Every preset also carries the rule that stops a failed search from becoming a false negative:
*if your search finds nothing, say so, and do not conclude the thing does not exist.* A
non-existence claim requires positive evidence of absence, with a URL.

---

## 8. Cost discipline

1. **Spark first** for anything a competent researcher with a search engine would settle.
2. **agy** when that is inconclusive, for large-context reading, and as the fast sanity check.
3. **Codex last.** It is for judgement calls and line-level audits of a finished artifact.
   Sending it a lookup wastes half an hour and the most expensive quota you have.

That ladder governs ordinary work. It does **not** apply to a commissioned second opinion: there,
run every available channel on the identical packet, because the disagreement is the product.

**Do not answer an exhausted subscription limit by opening a metered API path.** That is usually
several times more expensive than waiting or switching channel. Re-point the round in prose
instead — that is what the routing layer is for.

---

## 9. Deep research

Not available, and the reason matters more than the answer.

At both vendors "deep research" is a **separate agent product behind a metered API key**, not a
depth switch on a chat model — OpenAI's is on the Responses API, Google's on the Interactions API.
The decisive evidence is not documentation but the vendor's own server:

```
$ codex exec -m o3-deep-research
{"type":"error","status":400,"error":{"type":"invalid_request_error",
 "message":"The 'o3-deep-research' model is not supported when using Codex with a ChatGPT account."}}
```

So the block is the subscription, not the CLI. `codex exec` returned **exit 0** on that hard 400,
which is one more reason never to gate on an exit code.

What this harness does instead is the same shape — agentic multi-step web research at maximum
effort with a source-discipline system prompt. Honest differences: shorter autonomous horizon, no
built-in clarification loop, no vendor report formatting. Honest advantage, and not a consolation
prize: three independent models that disagree, plus a mechanical audit of whether the citations
were opened and whether they exist. Deep-research products emit citations with no audit trail.

If you want a vendor's deep research, run it by hand in the browser, save the report, and pass it
to this harness as an input document to be attacked. That costs nothing extra and plays to what
these channels are actually good at.

---

## 10. Verification practice

**Plant a deliberately false claim in every verification brief.** It costs nothing, and a channel
that "confirms" it has priced all of its other confirmations. In the three-channel round behind
this document, all three refused both planted claims — which is the only reason to credit what they did
confirm.

**Demand a live web search, with a URL and a date for every dated claim.** A review citing no URLs
has verified nothing, whatever its exit code said.

**Know which channel you can audit.** Only the agy channel exposes per-call telemetry
(`--output-format stream-json` → every tool call and a cited-vs-opened check). Spark reports a
search count. Codex reports nothing at all — for that channel the answer and its URLs are the only
evidence there is, which is exactly why `--resolve-urls` accepts an answer with no event log.

---

## 11. Extending it

Add a channel by editing `channels.json`: a default model, a `models` map with aliases, a cost
class, and the valid efforts per model. Aliases in that file are what free-text routing matches
against.

Planned additions (see the roadmap in [README.md](README.md)): **Kimi K3 via Hermes**, and
**direct OpenRouter support** — one credential reaching many models, which is what makes four or
five independent reviewers practical instead of an installation project per reviewer.

---

## 12. Two habits that outlive any specific bug

**Never assert a mutable value in a document.** Both command-line tools changed version inside one
week. A document that asserts a version does not look stale — it looks like documentation.
Replace the assertion with a probe: that is what `doctor.py` is for, and why its last line is your
run command with the real path filled in.

**When two files cover one subject, one holds the decision and the other holds a pointer — never a
second summary.** Measured here: two skills documented the same CLI, and the copy that was only
*read* drifted to four wrong facts while reading as authoritative, because the copy that is
*executed* gets corrected by failure and the copy that is only read has no error signal at all.
That is also why the distributable is **generated** from the working skill rather than forked from
it.
