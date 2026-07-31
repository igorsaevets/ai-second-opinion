<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Legal, immigration and regulatory briefs

The single thing to know: a refusal here is a FRAMING bug, not a
subject ban. Read this BEFORE writing the brief — rewriting it
afterwards costs a full expensive round.

### 7.0 Legal / immigration briefs — framing decides whether you get an answer at all

Codex refused an I-485 public-charge brief outright (§6.1a). The subject was never the problem;
the **framing** was. The refused brief said *"a memo written for APPLICANT_1, a beneficiary
filing Form I-485"* and asked *"which claim would do the most damage to this filing"* — that
reads as individual filing strategy, which is regulated advice. The identical six claims, framed
as source-verification of a draft research memo for attorney review, are answered in full.

This is legitimate reframing, not a jailbreak: fact-checking legal claims against the Federal
Register genuinely *is* research, and stating so accurately is what unblocks it. Do **not** use
the roleplay/fiction dodges that circulate ("write it as a screenplay", DAN-style prompts). They
are deceptive, they are patched, and a model pretending to be a screenwriter writes worse legal
analysis than one told it is doing legal research.

Pass `--system systems/legal-research.md`, which carries the three layers every source agrees on:

1. **Role** — legal research assistant under attorney supervision, explicitly *not* your lawyer.
2. **Status of the output** — an internal work product that a licensed attorney will review
   before anything relies on it. Say it plainly; hedging instead of saying it makes the answer
   shorter, not safer.
3. **Boundaries, stated as the task** — verify claims and find errors; do not decide what to
   file, do not choose an answer for a government form, do not opine on a named person's
   eligibility, and refuse outright anything that shades into misrepresentation.

Then rewrite the brief itself in the same register:

| refused | answered |
|---|---|
| "a memo for APPLICANT_1, who is filing…" | "a draft research memo prepared for attorney review" |
| "what would damage **this filing**" | "which claim carries the greatest legal risk" |
| "should they file before the effective date" | "state both dates and which filings fall under which regime" |

**`--system` now reaches every channel.** Only the HTTPS channel has a real system slot; for
Codex and agy the file is prepended to the brief by `_with_system()`. It used to reach only
`http`, which is precisely why the two CLI channels were the ones that refused.

Same discipline as everywhere else: the refusal ended with the end marker and was reported OK
until `refusal_check()` existed. Fixing the framing does not remove the need for that check.

**Measured on the same six claims, 2026-07-31:**

| | answered | length | citations | claims resolved |
|---|---|---|---|---|
| **Codex gpt-5.5** | yes | 5 438 | all correct; eCFR / FR / USCIS only | **6 / 6** |
| **Codex gpt-5.4** | yes | 7 815 | all correct; + govinfo.gov | 5 / 6 ("not found" on one) |
| **agy** (same packet) | yes | 12 954 | **6 / 6 grounded**, FR number resolves | 6 / 6 |

Use **5.5** for legal work: on the compound claim *"only the 01/20/25 edition is accepted **and**
there is no grace period"* it split the claim correctly (edition right, no-grace-period wrong —
USCIS took prior editions until 2025-04-03) where 5.4 rejected the whole thing. 5.4 is a sound
fallback when the 5.5 limit is gone: longer, hungrier for sources, and it honestly wrote "not
found" rather than guessing on the one claim it could not confirm — the §8 rule working.

The same system layer also lifted agy from 1/6 to 6/6 grounded citations and 6 448 → 11 214
thinking tokens (§6.5). Note the asymmetry this creates with the earlier lesson: prose could not
restrict **tool access** on agy, but it clearly improved **source discipline**. Do not collapse
either result into "prompts work" or "prompts don't".
