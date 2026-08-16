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
previous round. That is the entire argument for running all nine.

### Files

```
SKILL.md                  operating manual, loaded on demand by Claude Code
orchestrate.py            the harness: dispatch, verification, gates, diagnostics
routing.py                resolves registry + flags + free text into a plan
channels.json             THE registry - every model name lives here and nowhere else
doctor.py                 "is this machine set up?"      - probes, never asserts
selftest.py               "does the code still behave?"  - ~50 behavioural checks
citecheck.py              citation grounding and existence checks
upgrade.py                install/update in one path; migrates settings out of the tree
patch_agy_permissions.py  mandatory post-install step for the agy channel
echocheck.py              proves a depth knob from the counter the vendor returns
VERSION                   the release this tree is; generated at build time
channels.shipped.json     reference copy of the registry, so edits to it are named field by field
                          (an edit you FORGOT, not one someone is hiding: it sits under the same
                          write permission as the file it describes - see §5b)
references/*.md           detail read on demand
systems/*.md              system-prompt presets
```

Everything above is REPLACED by an update. Your own configuration is therefore not in it — see §5b.

---

## 2. What is enforced in code, not in prose

This kit assumes prose restrains nothing, because that was measured: an instruction in a model's
own system prompt failed **5 times out of 5** to stop it calling a tool it had been told not to
call. Only permission rules worked. So everything that must not happen is a check, not a sentence.

### Outbound payload gate

- **A payload containing a key, token or private key is never sent. There is no override flag.**
  Nine detectors: private-key blocks, vendor key formats, labelled assignments, bearer tokens.
- **A payload containing personal identifiers is reported, itemised by kind and line, and sent** — pass `--strict-pii` to refuse instead. Secrets are refused always, with no override
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
--panel cheap|standard      WHO is in the room; filters down only, never enables
--tier max                  ONE tier, and it is every vendor's ceiling. strategic|deep are
                            aliases kept so older commands keep working
--system legal-research     system-prompt preset
--dry-run                   complete preflight, spends nothing
```

**There is ONE axis you choose: who is in the room.** Membership is declared per channel
(`"panel": "cheap"`) and the ladder in the `panels` object, so `standard` INCLUDES everything
`cheap` has: «standard» has to mean "what normally runs", and it does — the default is
bit-for-bit the behaviour that shipped before panels existed.

🔴 **A panel filters DOWN and never enables anything — and since 1.22.0 neither does a GROUP.**
`--only openrouter` used to resurrect every disabled member; it now runs only the members that
are already on. That change closed a real cost: on a machine holding a direct vendor key, the
group word woke the OpenRouter twin of a channel it was already buying directly — two bills for
one voice. `enabled` is exactly the field the packaging step flips per `distribution`, so
resurrecting on a group word means calling a vendor whose key the user may not have. **Naming a
channel still works and is now the only way in**: `--only <name>`, or «включая <name>» in prose.

🔴 **A channel may declare `explicit_only`.** Then even naming its group is not enough — it runs
only when something names *it*. One channel ships that way (the most expensive one), because
`enabled: false` alone was not a lock: `--only` overrides it by design, and `--only <group>`
reached it through two different vendor words.

🔴 **What the cheap panel really costs is vendor diversity, not depth.** The plan prints the
vendor tally of whatever resolves and warns when one vendor holds half the seats, because six
channels reaching one company's weights that agree with each other are one opinion reported six
times. Read that line before treating convergence as corroboration.

**Why ONE tier and not two.** There were four, then two, now one. The pair that survived
differed, at the end, in a timeout and two multipliers — depth was already identical on Spark
(`xhigh`; `max` returns 400), on the Gemini CLI (`high` is the model's ceiling), on the direct
Gemini API (top of `thinking_levels`) and on the xAI model (which refuses the field at every
value and placement). A knob whose range shrinks to a point every time the underlying values
improve is not a knob, so the second tier was removed rather than re-justified.

**Every channel now runs at the maximum depth its own vendor accepts, always.** Each one's
ceiling is declared beside it — `supported_efforts` for the OpenRouter models, `thinking_levels`
for the direct Gemini ones — and the self-test asserts the configured value is the top of that
list, so a vendor adding a rung shows up as a failing test rather than as a silent shortfall.
Two channels were found below their ceiling when this was first checked: one running an effort
one rung down, and one whose "reasoning budget" was being converted by the gateway into roughly
*medium* on a model whose own default is *max*.

**A panel may never change depth.** That is asserted mechanically: every panel is resolved and
every surviving channel's depth fields are compared field-by-field against the unfiltered plan.
Only the number of reviewers may differ between modes.

`--tier quick` is still refused by name (`invalid choice`). `strategic` and `deep` resolve to
`max` and the plan says which word it honoured — accepted-and-ignored is the failure this
project has recorded most often, and a rename is exactly when it happens.

🔴 **`max_tokens` covers reasoning AND the answer on these protocols.** If a channel returns
nothing, the run reports `OUTPUT BUDGET EXHAUSTED BY REASONING` with both numbers: that is a
truncation, not an empty response, and the fix is a larger ceiling or a smaller brief. Measured:
766 seconds, 60,002 reasoning tokens against a 60,000 cap, zero bytes of answer — while the
diagnosis at the time said "the provider gave no reason" and the reason sat three fields away in
the same record.

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

## 5b. Configuration lives outside the tree, because updates replace the tree

Until 1.7.0 the documented way to enable a channel was to edit `channels.json` — a file inside the
folder that every update replaces. All four install methods destroyed that edit, and none of them
mentioned it. The plugin path was the worst of them precisely because it is the one recommended:
it updates itself, so the loss happened with nobody running a command.

The fix is not a merge algorithm. It is a location — with one honest exception, below:

```
~/.claude/model-orchestration.local.json        # yours; nothing can reach it
<skill>/channels.json                           # shipped; replaced wholesale
```

The overlay is merged over the registry **before** validation, so anything it changes faces exactly
the same checks as anything shipped — a validated-then-mutated registry is how a config file
becomes an unchecked code path.

🔴 **1.7.0 was default-deny on fields. 1.8.0 keys trust on PROVENANCE instead, and the difference
is not a relaxation — it is a correction.** One of the review channels had named a real
consequence of the first design: a file that survives every update, is merged before validation,
and can name a transport hands anything able to write one file in a home directory a *persistent,
update-proof redirection of where your documents are sent* — "and the per-run print of what the
overlay changed only helps if a human reads it, which the plugin path specifically removes."

The finding was right; the remedy was aimed at the wrong axis, and looking rather than reasoning
showed why:

- `~/.claude/model-orchestration.local.json` and `<skill>/channels.json` have **identical write
  permissions**. Anything that can write the first can write the second. Refusing `model` in the
  overlay never stopped an attacker; it sent them one file to the left.
- And that file was **the quiet one**. The plan printed every overlay change on every run, while
  an in-place edit of `channels.json` was fingerprinted only by `doctor.py`, which nobody runs
  before a round. The gate was steering the most sensitive class of change into the least visible
  place. 1.8.0 closes that half: the plan reports registry drift by field, every run.
- Meanwhile it fired on correct use — and the hand on the keyboard is usually an AI assistant
  helping its owner configure their own machine. A gate that fires on the intended workflow is one
  people learn to switch off.

So: **at the home path, anything.** Channels and tiers, edited or added (`"_new": true` required
for an addition, so a typo cannot become a second channel). Under `MODEL_ORCH_LOCAL`, only the
"how hard does it work" knobs — `enabled`, `effort`, `reasoning`, `thinking_level`, `max_tokens`,
`fetch_tool`, `web`, `timeout`, `label`, `notes` — because a project's own `.claude/settings.json`
can set environment variables for sessions run inside it, so a repository you cloned can choose
that path, and it cannot choose your home directory. There is deliberately no environment variable
to turn *that* off: an escape hatch that can be set once and forgotten is the same defect as a
registry that documents the way around its own gate.

🔴🔴 **And then three reviewers of *that* design, independently, found what it had missed — the
permission-equivalence argument is true for a RESIDENT attacker and false for a ONE-SHOT one.**
`channels.json` is **self-healing**: the next update replaces it. That is the defect this release
fixed and, at the same time, a security property nobody had named. The home settings file is
update-proof by construction. So opening it up handed the *permanent* file the powers the
*ephemeral* one had. In their words: a single compromised assistant session writes `model` into
the home overlay once, and every future run — including after the tool updates — silently
re-points a channel. That threat is not hypothetical for this product, whose own premise is that
an AI assistant edits the configuration on the user's behalf.

**So a transport-affecting change is applied but not SPENT against until it is accepted once**, by
a command the user runs (`routing.py --accept-settings`). The acknowledgement stores a digest of
the sharp section only: re-order the JSON, reformat it, or edit a quiet field beside a sharp one
and it still matches; change what is sent or where, and the refusal returns naming the change. A
file write is no longer sufficient — the attacker would also have to make a human type a second
command after reading a refusal that describes the redirect.

`--dry-run` deliberately still works before acceptance: seeing what *would* happen must never
require accepting it first.

`cost` is **not** a quiet field, despite looking cosmetic: it drives the "EXPENSIVE channel"
warning and the set `--ask` fans out to. That one was found by taking a reviewer's general frame
seriously and then checking his example, which turned out to be harmless — verify the finding,
discard the proof.

🔴 **What the registry-drift report is NOT.** A fourth reviewer pointed out the circularity: the
reference copy lives under exactly the write permission the drift check exists to monitor, so
anyone who can edit `channels.json` can update the reference and silence the detector. That is
true, and no location fixes it — a signature would need a key on the same disk. So the claim is
scoped honestly: **it detects an edit you forgot, not an edit someone is hiding.** Its job is to
stop your own change dying at the next update, which is the failure this project actually
measured. The gate against a hostile write is the acceptance step above, and that one is not a
detector — it stops the spend.

Four properties make it safe to have a second source of truth at all, and each exists because the
alternative was measured or reasoned to be worse:

| property | why |
|---|---|
| The resolved plan prints the file's path and every field it changed, **every run**, even when it changed nothing | A settings file mentioned only when it acts is one people forget they wrote. The question it must answer is "why is this channel not running", asked by someone reading the wrong file |
| An unknown channel name is **refused**, with the list of real ones | Ignoring it would look identical to a channel that is off for another reason. Silence is a config overlay's characteristic failure |
| A renamed channel still **resolves**, through the same alias table `--only` uses | Strict rejection plus a rename upstream is a hard startup failure on upgrade day, for people who did nothing wrong. All four original channels here were renamed once already |
| `doctor.py` fingerprints `channels.json` against a hash shipped beside it | An in-place edit is otherwise invisible right up to the update that erases it |

`upgrade.py` performs the one-time migration and reports it. Three-way where a pristine baseline
exists (written from 1.7.0 onward, **outside the tree** — a baseline stored inside it is destroyed
by exactly the thing it exists to survive), two-way and **labelled INFERRED** where it does not:
comparing an installed file against an incoming one cannot distinguish a user's edit from a default
the release changed on purpose, so in that mode it restricts itself to the one field the docs ever
told anyone to change, and says so rather than pretending to know.

### 🔴 The exception: the hop INTO 1.7.0, on a path that never runs the script

The guarantee above is about updates *from* 1.7.0. The one-time migration still needs something to
run, and the recommended install path — the marketplace plugin — updates itself with nobody running
anything. A reviewer of this release put it plainly: it "makes future updates correct only after
the state has already been relocated", and `upgrade.py` "cannot rescue a path that never invoked
it."

There is a documented window. Claude Code copies marketplace plugins into `~/.claude/plugins/cache`,
keeps **each installed version in its own directory**, and orphans the previous one for **14 days**
before deleting it ([plugins reference](https://code.claude.com/docs/en/plugins-reference), read
2026-08-08; the layout `<cache>/<marketplace>/<plugin>/<version>/` was also confirmed on a real
machine). So `upgrade.py` scans that cache for an older copy of this plugin, compares its `enabled`
flags with the incoming release, and offers to carry the difference into the overlay. It is
best-effort by construction and silent when it finds nothing — a rescue that crashes is worse than
one that misses.

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
