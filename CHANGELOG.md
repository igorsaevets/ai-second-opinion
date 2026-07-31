# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/igorsaevets/ai-second-opinion/releases/tag/v1.1.0
