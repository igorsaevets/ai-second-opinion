# Instructions for AI agents

You are reading the repository of **AI Second Opinion** — a tool that sends one document to
several independent AI reviewer models at once and mechanically checks whether each one actually
did the work. If a person handed you this repository to install it, update it, or run a review
with it, this file is your map: it states the rules that decide outcomes and points at the
documents that hold the detail. It deliberately holds no channel list, no counts and no versions.

## Two rules that override anything else you infer

- **Never ask the user to paste an API key into the chat, and never print one.** A key that has
  appeared in a transcript is leaked and must be rotated, not deleted. `doctor.py` reports key
  presence and length only; the tool itself refuses to send secret-shaped content, with no
  override. The full security model is in [README.md](README.md) and [SECURITY.md](SECURITY.md).
- **Never state channel facts from prose — including this repository's own prose.** Which models
  exist, which of them can search the web, what a run costs: every prose copy of that list in
  this repository has been wrong within days of being written. Ask the tool instead. The
  scripts live in the tool's own directory, not at the repository root — change into it first,
  and read every command in this file as running from there:

  ```
  cd plugins/model-orchestration/skills/model-orchestration
  ```

  (after an installer or plain-copy install the same directory is
  `~/.claude/skills/model-orchestration`; call it `SKILL_DIR` — the paths below use that name)

  - `python routing.py` — the live channel plan; spends nothing;
  - `python doctor.py` — versions, key presence, what is actually installed;
  - `python orchestrate.py --dry-run ...` — the full resolved plan for a specific round, free;
  - `channels.json` — the registry, the single home of every model name.

## Operating discipline — each rule exists because it failed on a real run

- **The resolved plan prints before anything is spent. Read it.** It is the one screen that
  exists to be read before money moves.
- **Never auto-retry a failed call on a metered channel.** Report the failure with its output
  and let the person decide. `--dry-run` is free and is a complete preflight. This rule is
  about YOU re-invoking a channel: the harness's own announced, bounded one-time re-runs are
  part of the tool, not a licence for yours.
- **A channel that "ran" is not a review that happened.** Exit codes, `status` fields and even
  the required end marker are all satisfiable by a refusal or an empty answer. The harness
  prints content-level checks — read them rather than re-deriving them from the exit code.
- **Citations are claims, not evidence.** Every cited URL is audited (`citecheck.py`); a review
  citing no URLs has verified nothing, whatever its exit code said.
- **A grounded-looking link does not prove the quotation under it.** Whatever invents a
  quotation invents the link too. Verify the quotation against the source text, separately from
  verifying that the link resolves.
- **Provenance tags are the vocabulary of honesty here**: `[OPENED]` (fetched and read),
  `[SNIPPET]` (search result only, page not opened), `[MEMORY]` (training data, unchecked).
  The system presets define them; expect them in answers, and use them yourself when you report
  findings to the person who asked.
- **API channels cannot read files — attach them or they are invisible.** Channels served by
  a remote API (OpenRouter, direct vendor endpoints) see ONLY the brief text and what
  `fetch_tool` returns (limited to 5 calls). A GitHub URL in the brief does NOT give them the
  code — they must guess paths, and a wrong path is a wasted fetch. Use `--attach <file>` or
  `--attach-dir <dir>`: the harness automatically inlines file contents for API channels and
  passes paths for CLI channels. For large files (>100KB), prefer `--attach` on selected files
  over `--attach-dir` on the whole tree. Measured R119: without `--attach-dir`, an API channel
  spent 2 of 5 fetches on 404s and tagged most findings `[UNVERIFIED]`.
- **The PII line is the operator's job, not the tool's.** Secrets are blocked outright;
  identifiers can be itemised; **names and street addresses are not detected at all** —
  [PRIVACY.md](PRIVACY.md) has the exact boundary. Tokenize before sending; sent is sent.
- **User names a model this release does not know: never silently substitute the channel's
  default, and never edit shipped `channels.json`.** Two mechanisms; pick by intent.
  For a one-off try on an existing channel: `python orchestrate.py --set <chan>=<slug>
  --dry-run` — accepted as a HYPOTHESIS for network-API kinds (http/codex/openrouter/oai/xai/
  gemini/claudecli/grokcli); the plan carries a 🔴 line saying `label / data_policy = UNKNOWN`;
  a paid 4xx from the vendor is the honest verdict. Refused loudly for CLI-fixed kinds
  (agy/opencode/hermes) with the alternative named in the error. For a permanent add:
  `python orchestrate.py --new-channel NAME:KIND:SLUG` — writes atomically to the user's
  overlay (`~/.claude/model-orchestration.local.json`, survives kit updates); refused on
  colliding NAME, unknown KIND, or a redirected overlay.

## Where the instructions live

`SKILL_DIR` below is the directory defined above.

| you are about to | read first |
|---|---|
| run a review round | `SKILL_DIR/SKILL.md` — the one file that runs a round end to end; its first section is the command |
| run the premium batch panel | the README's «Premium» section — a separate script (`SKILL_DIR/premium/premium_panel.py`); always `--mode dry` first, it bills nothing |
| install or update | [INSTALL.md](INSTALL.md) — to update ANY install run `python SKILL_DIR/update_check.py --apply` (downloads the release, backs up, keeps settings; a plugin install goes through `claude plugin update` inside that command), never a hand copy |
| write a brief for a round | `SKILL_DIR/references/briefs.md` |
| write a legal / regulatory brief | `SKILL_DIR/references/legal-briefs.md` — a refusal there is a framing bug; read this BEFORE writing, not after the refusal |
| judge whether a review happened | `SKILL_DIR/references/verification.md` |
| diagnose a failure | [TROUBLESHOOTING.md](TROUBLESHOOTING.md), then the run's own `run.log` and `diagnostics.json` — both scrubbed by construction, safe to read and to paste |
| understand the design | [TECHNICAL.md](TECHNICAL.md) |
| change anything here | [CONTRIBUTING.md](CONTRIBUTING.md), and the warning below |

## If you are asked to change this repository

The subtree `plugins/model-orchestration/skills/model-orchestration/` is **generated** from the
author's working skill. A hand edit there is silently overwritten by the next build. Propose
changes against the repository-root documents or open an issue, per
[CONTRIBUTING.md](CONTRIBUTING.md), and say plainly that the change must also land upstream.

Before you assert anything about this tool's behaviour, run it: `python selftest.py` (from
`SKILL_DIR`, like every command here) costs nothing and contacts no vendor. A claim about a
flag that was never executed is a guess wearing documentation's clothes.
