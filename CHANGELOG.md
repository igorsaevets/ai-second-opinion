# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-08-07

### Added

- **Seven channels, and every one of them names its model.** `spark11`, `spark12cont`, `codex`,
  `agy31pro`, `agy36flash`, `kimik3`, `qwen38max`. A channel name used to be a vendor (`spark`,
  `agy`, `kimi`); it is now a model, because the panel grew past the point where a vendor name
  identified anything — two Spark checkpoints and two Gemini models run in the same round.
  **One channel = one model, enforced at load**: pointing a channel at a different model is
  refused, not warned about. The old names survive as aliases and as group words, so existing
  commands keep working.
- **A page-fetch tool for the OpenRouter channels.** Web *search* returns query-selected excerpts
  with elision markers, so a verbatim quotation assembled from them can splice two disjoint
  fragments into a sentence no page ever contained. `kimik3` and `qwen38max` can now open a page
  and quote the fetched text. Because the harness performs the fetch, "which URLs did the model
  actually open" becomes a list rather than an inference. The tool refuses non-http(s) schemes and
  any host resolving to a loopback, private, link-local (including the cloud metadata address),
  CGNAT, multicast or reserved address — re-checked after **every redirect**, because a public
  hostname can resolve to `127.0.0.1` and a public URL can redirect there.
- **`REPORT.md`, rendered from `diagnostics.json` on every run**, leading with the depth tier and
  flagging any model that differs from its channel's default. A tier is invisible in the output —
  a shallow review reads exactly like a deep one — so it must not depend on whoever writes the
  summary remembering to mention it. If the tier is missing, the report says so loudly instead of
  printing a tidy placeholder.
- **Token usage and subscription state for the Codex channel.** It reports no *tool* telemetry —
  it never says which pages it opened — and that had been written down as reporting nothing at
  all. It emits full token usage, and the weekly subscription window can be read before a run
  without spending a token.

### Fixed

- 🔴 **A URL-parsing bug in the verification layer was billed as a network failure and retried a
  paid call.** The citation checker could raise on a bracketed IPv6 URL — which happens as soon as
  a review discusses IPv6 at all — and the caller reported it as a transport error and re-ran the
  whole streaming request. An accounting step that runs *after* a paid call must not be able to
  fail that call.
- 🔴 **A counter named for the rarest cause was fed every cause.** One channel's failure counter
  was documented as "a permission denial discarded the run", and it counted ordinary fetch
  timeouts too — so a failed download was reported as the catastrophic bug and sent readers to the
  wrong fix. Denials and tool errors are now separate, and the channel's own stated reason for
  stopping is surfaced instead of being parsed past.
- **A shared helper reported every OpenRouter failure under the first channel's name**, so a
  `qwen38max` error announced itself as a `kimik3` error.

### Changed

- Documentation now states which claims are **measurements from a three-channel round** rather
  than quietly restating them as if they covered all seven. A number is worth the run it came
  from.
- `INSTALL.md` carries an instruction block addressed to AI assistants: do not offer to set the
  user's API keys, do not ask for a key in chat, do not read one back. An assistant that sets a
  key must first receive it, and the conversation is written to disk, replayed into later context
  and often archived — a key that has appeared in a transcript is leaked and must be rotated, not
  deleted.

## [1.2.1] — 2026-08-02

### Fixed

- 🔴 **A command-line channel was reading the instruction file of whatever directory you launched
  from, and sending it to the vendor.** One agent CLI injects the `CLAUDE.md` of its working
  directory into the model's context, and its own `--ignore-rules` flag does not stop it — that flag
  covers the agent's persona files, not this. Asked how it knew a line from a project instruction
  file, the model answered that the file had been *"injected into my initial system context by the
  harness under a Project Context block"*, then quoted the sentence and located it correctly. The
  same probe run from a scratch folder answered *"NOTHING IN CONTEXT"*.

  It cost twice. **Independence**: a reviewer that has read your own instructions is not a second
  opinion, and in one live round a channel cited the project's own instruction file back as
  corroboration. **Confidentiality**: that file reached the vendor on every call, *outside* the
  outbound gate, which only ever scanned the brief — and this harness is normally launched from a
  project directory, whose `CLAUDE.md` routinely names other repositories, clients or matters.

  That channel now runs from a neutral scratch directory. Every path the harness passes to a
  subprocess was already absolute, so nothing else changes. The two other command-line channels were
  unaffected: each already set its own working directory, for unrelated reasons.

## [1.2.0] — 2026-08-01

### Added

- **Every run now ends with a citation existence check.** What used to be a separate command you
  had to remember (`citecheck.py --resolve-urls`) runs automatically; `--no-citecheck` disables it.
  Results are printed and recorded in `diagnostics.json` under `citations`.

  The reasoning is worth stating, because it decides the design. There are two questions about a
  citation and only one is answerable everywhere: *did the model open this page* needs the
  channel's own tool telemetry, and the Codex channel reports none at all; *does this page exist*
  needs only a fetch, so it works on every channel. Existence is the weaker question and the
  universal one, and it catches the dangerous shape — a fluent, correct-sounding review citing
  pages that were never opened. And a verification step that depends on remembering runs least
  often when the run was rushed, which is the same moment nobody re-reads the citations by hand.

  It deliberately **does not affect the exit code**. One measured "dead" citation was a query for
  a release tag that does not exist — the 404 *was* the answer. Failing a run on that teaches
  people to ignore the check. `BLOCKED` and `UNKNOWN` are never reported as fabrication, and when
  the per-channel cap applies, the number of unchecked URLs is stated rather than dropped quietly.
- **Contact and reporting section** in both READMEs: issues, discussions, private security
  advisories, and the maintainer's professional links. There is deliberately no email — see below.
- Maintainer identity is now generator configuration rather than literal text, so a fork rebuilds
  with its own contact details instead of inheriting the author's.

### Fixed

- 🔴 **The published `LICENSE` named the wrong copyright holder** for the entire life of the 1.1.0
  release. The generator rewrites the author's given name out of technical documents so that no
  machine-specific identity ships, and that substitution also hit the copyright line, replacing the
  first name with the generic placeholder and leaving the surname beside it. The build reported
  clean throughout, because a per-file allowlist had told the leak sweep to skip `LICENSE` —
  **an allowlist entry is a promise that a file is fine, and nothing ever re-checks the promise.**
  The allowlist is gone; the maintainer credit is generator configuration filled in *after* the
  substitution runs, and the sweep exempts exactly that configured value.
- **`SECURITY.md` pointed at a vulnerability-reporting channel that did not exist.** It told
  researchers to use GitHub's "Report a vulnerability" button while private vulnerability reporting
  was disabled on the repository, so the button was not there. Enabled, and verified anonymously.
- **The "binary not found" advice named specific channels** (`--skip codex`, `CODEX_BIN=…`), so a
  user whose *other* channel failed was told to reconfigure Codex. It now names none.
- **`selftest.py` had hardcoded the channel list**, so adding a channel to `channels.json` turned
  every *exclusion* case red while the tool was working correctly. Expectations are derived from
  the registry: an inclusion case may name its input, an exclusion case must compute the complement.

### Known limitations

- Deep-research modes are not reachable — they are separately metered products at both vendors, not
  a switch on a chat model. See `TECHNICAL.md` §9 for the vendor's own refusal message.
- Citation *grounding* (did the model open the page) needs a channel event log, which only the
  Antigravity channel exposes. For Codex only *existence* can be checked — which is what the
  automatic check does.
- **There is no contact email, on purpose.** The address on this repository's commits is GitHub's
  no-reply relay: it attributes commits correctly and has **no mail exchanger at all**, so mail to
  it is discarded without a bounce. A reporting channel that silently swallows a bug report is
  worse than an absent one. Use issues, discussions, or the private advisory form.

## [1.1.0] — 2026-07-31

First public release. Everything below was verified by running it, not by reading it.

### Added

- **Diagnostics for AI-assisted debugging.** Every run writes `run.log` (appended per line, so it
  survives a kill) and `diagnostics.json` (structured: environment, plan, per-channel telemetry,
  problems). Each problem carries a plain-language cause and a suggested fix drawn from a table of
  known failure signatures. Both files are scrubbed of secrets and personal data by construction,
  so they can be pasted into a chat or attached to an issue without review.
- **Crash handler.** An unexpected exception now produces a scrubbed diagnostics file instead of a
  bare traceback that vanishes with the terminal window.
- **`selftest.py`** — ~50 behavioural checks covering graceful degradation, channel selection and
  redaction. Costs nothing, contacts no vendor.
- **Automatic end-marker instruction.** The harness verified the end-of-review marker but never
  asked the model to emit one, so a brief written by anyone who had not read the docs came back
  `PROBLEM` on all three channels with a good review inside. The instruction is now appended when
  the brief does not already contain the marker.
- **Automatic re-ask on zero citation grounding** for the Antigravity channel. Measured 0/3 → 8/8
  grounded, 3 dead URLs → 0, tool calls 14 → 72. Exactly one extra attempt, announced before it is
  spent, both transcripts kept.
- **`citecheck.py --resolve-urls`** — checks whether cited URLs *exist*, with no event log
  required, which makes it the only mechanical citation check possible on the Codex channel.
  `BLOCKED`/`UNKNOWN` are never reported as fabrication.
- Repository documentation: plain-language `README.md` (+ Russian translation), `TECHNICAL.md`,
  `INSTALL.md` with four install methods including plain file copy, `TROUBLESHOOTING.md`,
  `SECURITY.md`, `CONTRIBUTING.md`.
- CI running `selftest.py` on Linux, macOS and Windows. It needs no credentials, because no test
  contacts a vendor.

### Fixed

- **Personal-data gate fired on its own documentation.** The date-of-birth and passport patterns
  accepted *any* character after the label, so the sentence "blocks a labelled date of birth
  unless you pass `--allow-pii`" tripped the gate. Both now require a value that actually looks
  like a date or an identifier. This class of bug is worse than it looks: a check that fires on
  clean text teaches users to pass the override by reflex, and the override disables the whole
  class. The prose that broke it is now a permanent negative control.
- **Fourth instance of the same trailing-`\b` trap**, found while fixing the above: a trailing
  `\b` after a label that ends in a full stop — `d\.?o\.?b\.?\b`, `passport no\.\b` — can never
  match, because between the final `.` and the following space there is no word boundary. Both are
  non-word characters. The abbreviated forms were therefore undetectable.
- **A credential in an exception message printed to the console in full.** The diagnostics *file*
  was scrubbed but stdout was not, and stdout is archived and replayed into model context — the
  same exfiltration surface. Redaction moved to the single logging choke point.
- `bearer` in the secret table sat in the labelled-assignment branch, which requires the delimiter
  *after* the label, while a real header is `Authorization: Bearer <token>` and puts it before.
  The one shape it was added for was the one shape it could never match. Now its own pattern.
- Two routing faults: `--only http` was documented and accepted but died on an internal lookup;
  and a flag could silently overturn the route on the expensive channel, printing "excluded by
  name" directly above the line that ran it. The latter is now a hard stop naming both sides, not
  a precedence rule.
- `--system` resolved preset names against the current directory, so they only worked while
  standing in the skill directory; `--dry-run` returned before validating the brief and preset.

### Security

- Outbound gate: 9 secret detectors (no override) and 7 personal-identifier detectors
  (`--allow-pii` to override). Reports kind and line number, never the value.
- Redaction is a substitution, never a truncation. A "mask" that kept 60 characters of a 48-character
  key kept all of it — that is how a live key once reached a transcript.
- `doctor.py` now probes **every** pattern in both tables, derived from the tables themselves, so a
  newly added pattern fails the check until it is given a probe line. Coverage had been lopsided in
  the wrong direction: the class with a human override had six tests, the class with no override
  had one.

### Known limitations

- Deep-research modes are not reachable — they are separately metered products at both vendors, not
  a switch on a chat model. See `TECHNICAL.md` §9 for the vendor's own refusal message.
- Citation *grounding* (did the model open the page) needs a channel event log, which only the
  Antigravity channel exposes. For Codex only *existence* can be checked.
- `--resolve-urls` is a separate command; it is not yet run automatically at the end of a review.

[1.2.0]: https://github.com/igorsaevets/ai-second-opinion/releases/tag/v1.2.0
[1.1.0]: https://github.com/igorsaevets/ai-second-opinion/releases/tag/v1.1.0
