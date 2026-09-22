<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Policy: who to ask, when, and at what cost

Merged from `second-opinion-consult` on 2026-09-21 (R115 Kit-Б-13 И-3). That skill is stubbed;
this file is the single home. The global `~/.claude/CLAUDE.md` §Second opinions carries the
one-line summaries of Rules A and B; this file carries the evidence and the detailed protocol.

## Rule A — a second opinion goes to ALL THREE. Always.

When the operator asks for «второе мнение», a review, a verification of a document/plan/analysis,
or names any one of the channels — **run all three groups in parallel on the same packet.** This
is the default, not an escalation, and it does not depend on stakes.

**Evidence (25.07.2026):** each channel caught something the other two missed, and one was flatly
wrong on a point the others got right. One reviewer is not a second opinion.

Send the same brief to all three, then report separately what was **accepted**, what was
**rejected with proof**, and where they **disagreed with each other**. The reporting protocol
is in `SKILL.md` §10; the brief template is in `references/briefs.md`.

## Rule B — the cost ladder governs everything else

For ordinary work that is *not* a commissioned second opinion — lookups, fact-finding, research:

| Order | Group | Cost | Use it for |
|---|---|---|---|
| 1 | **Spark** (the `spark` group) | **cheap** | default. Web-grounded fact checks, primary-source verification, "does X exist", blind-spot probing, anything routine |
| 2 | **Antigravity** (the `agy` group) | **mid** | when Spark is inconclusive; large-context reading; also the fastest |
| 3 | **Codex** (`codex` channel) | **expensive** | **strategic questions only** — architecture and strategy calls, line-level audit of a finished artifact |

**Test before spending Codex:** would a competent researcher with a search engine settle this?
If yes → Spark. If it needs someone to weigh trade-offs, find the flaw in a plan, or decide
what matters → Codex.

Rule B never overrides Rule A. Spending Codex on a lookup is the same class of mistake as
spending Fable 5 on web search.

## Sub-agent discipline

- **If the user named a channel, that channel does the reasoning.** Internal agents only prepare
  material for it — they do not duplicate its work. And if the ask is a second opinion, run all
  three even when only one was named.
- **All three are advisory, never authoritative.** Verify their claims; on disagreement, re-check
  primary sources, decide yourself, and report all positions with reasoning.
- When a reviewer says something of ours is "unverified", check whether **we** already verified
  it from a primary source and say so — do not silently delete our own verified claim.
- **Testing whether a document is followable? Use the WEAKEST plausible model, not the strongest.**
  A capable model reconstructs what the document failed to say and returns a falsely clean pass.
  And a sub-agent shares your env and binaries, so it can never test setup instructions at all.

## What was NOT carried forward

The former `second-opinion-consult` skill held Spark endpoint params, Codex CLI flag tables,
agy invocations and completion control. All of that rotted in four places by 2026-07-31 while
looking authoritative — the entry for each one already said "mechanics live in
model-orchestration". Those now live solely in `references/channels.md` and are maintained
against the installed binaries.

Brief template, PII tokenization, citation requirements and the non-existence-claim wording
are in `references/briefs.md`. Report-back format is in `SKILL.md` §10. Model names and
efforts are in `channels.json` and are never written in prose.
