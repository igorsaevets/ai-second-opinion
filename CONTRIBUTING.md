# Contributing

## Read this first: the published tree is generated

**Do not send a pull request that edits `plugins/model-orchestration/skills/model-orchestration/`
by hand.** That directory is built from a working skill by a generator. A hand edit there is
overwritten on the next build without warning.

This is deliberate, and it is the project's own rule proven on itself. Two files once documented
the same command-line tool; the copy that was only *read* drifted to four wrong facts while
reading as perfectly authoritative, because the copy that is *executed* gets corrected by failure
and the copy that is only read has no error signal at all. So there is exactly one home for every
fact, and the distributable is generated rather than forked.

**What to do instead:** open an issue describing the behaviour, or send a PR against the
repository-root documents (`README.md`, `AGENTS.md`, `TECHNICAL.md`, `INSTALL.md`,
`TROUBLESHOOTING.md`, `SECURITY.md`) *and say in the PR that the change also needs to land
upstream in the skill.*

## Before you propose a change

```
python plugins/model-orchestration/skills/model-orchestration/selftest.py
```

The suite prints its own count — do not trust any number a document states for it. It costs
nothing and contacts no vendor, and it covers the three
properties the tool actually promises: partial installs degrade instead of crashing, channel
selection is obeyed exactly, and nothing secret-shaped can reach a console, a log or a diagnostics
file.

**A change that breaks one of those is not a trade-off to discuss — it is a regression.**

### Pre-commit guardrails (recommended)

From the kit root:

```
pip install pre-commit
pre-commit install
```

Now every `git commit` runs a small non-destructive set of checks — JSON syntax, YAML syntax,
no accidentally-added large files, `ruff-check` on any Python touched, and a custom hook that
detects **duplicate keys in any JSON object** (which `json.loads` silently collapses to the last
one, and which the stock `check-json` hook does NOT catch — pre-commit-hooks issue #554). The
duplicate-key hook lives at `tools/check_json_dup_keys.py` and uses `object_pairs_hook`, so it
sees every key at every nesting level before the parser hides the earlier ones.

The `ruff.toml` in this repo is deliberately minimal — only `E` and `F` categories, with `E741`
and `E501` explicitly ignored. It exists to catch bugs (undefined names, unused imports, real
syntax defects), not to enforce style. A style rule that fires many times on clean code trains
you to run `git commit --no-verify` and lose the whole class of protection.

## House rules, each of which was learned the hard way

**Run it; do not read it.** Every real bug in this project's history was found by execution and
none by inspection. Four separate regular expressions in the secret/PII gate were dead on arrival
— including one for the exact shape it was added to catch — and every one of them looked correct.
If you change a pattern, add a positive probe *and* a negative control to `doctor.py`.

**A false positive in a safety check is a bug, not a nuisance.** A gate that fires on clean text
teaches people to pass the override by reflex, and the override disables the whole class. One such
bug shipped here: the phrase "a labelled date of birth" in the tool's *own documentation* tripped
its own personal-data gate, because the pattern accepted any character after the label. The prose
that broke it is now a permanent negative control.

**Never assert a mutable value in a document.** Both command-line tools this project drives changed
version inside one week. A document asserting a version does not look stale, it looks like
documentation. Replace the assertion with a probe.

**Never gate on an exit code from these channels.** Observed on one channel in one week: `SUCCESS`
with an empty answer, `ERROR` with a complete one, and exit `0` on a hard HTTP 400 from the
vendor's own server.

**Never print a credential, not even through a transform you wrote.** Report presence and length.
See [SECURITY.md](SECURITY.md) for the incident that made this a rule.

## Adding a reviewer channel

Add it to `channels.json` — a default model, a `models` map with aliases, a cost class, and the
valid efforts per model. Model names live there and nowhere else; adding a channel should require
no change to `orchestrate.py`.

Then add it to `selftest.py`'s routing cases, including at least one alias and one negative
(`--skip <newchannel>`).

## Translations

`README.md` and `README.ru.md` are a knowing duplication — the one exception to the one-home rule,
because a landing page in a language the reader does not speak helps nobody. **They must be
updated together.** If you can only do one, say so explicitly in the PR so the other is not
assumed current.

## Reporting a bug

[Open an issue](https://github.com/igorsaevets/ai-second-opinion/issues) and attach `diagnostics.json` from the
failing run. It is scrubbed of keys and personal data by construction, so it is safe to attach
without reading it first, and it contains the environment, the resolved plan and the failure —
considerably more than a screenshot.

**A security problem is the one exception:** use the
[private advisory form](https://github.com/igorsaevets/ai-second-opinion/security/advisories/new) rather than a
public issue. See [SECURITY.md](SECURITY.md).

For questions and ideas that are not yet a bug, use
[Discussions](https://github.com/igorsaevets/ai-second-opinion/discussions). For anything commercial, the contact
links are at the bottom of the [README](README.md#found-a-bug-want-a-feature-want-to-work-together).

## Code style

Standard library only. No dependencies, in either the tool or the tests — the install story is
"copy one folder", and every dependency is a way for that to stop being true.

Comments explain **why**, especially when the why is an incident. A comment saying what the next
line does is noise; a comment saying "this has no trailing `\b` because `d.o.b.` ends in a dot and
the boundary never matches" is the reason the next person does not reintroduce the bug.
