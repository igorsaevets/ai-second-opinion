# Reading what a panel returned — the context problem, and the two artifacts that answer it

Read this when a round lands, before deciding whether to read the answers in the current turn.
`SKILL.md` §10 carries the one-line rule; this file carries the reasoning and the measurements.

## The problem, in the operator's own words

the operator, 2026-08-19:

> Пока ИИ запускает оркестрацию, у него уже окно часто заполнено на 350K+. А чтобы читать и
> анализировать ответ, лучше чтобы окно было почти пустое.

He is describing a measured failure, not a worry. The session that ORDERS a panel has spent its
context getting to the point of being able to order one — reading state, reading code, writing the
brief. The panel then returns more text than that session has room to think about.

**What it looks like when it happens.** Round 46 of the AOS project: an 18-channel panel, $3.97
spent, 17 answers on disk. Three of them — GROKBUILD 199 738 B, SPARK12CONT 66 955 B, SPARK11
40 782 B, **317 KB, including the largest and most expensive answer of the round** — were never
opened by anyone. The round then reported "18 launches, 17 answers"; both numbers were wrong. No
refusal, no error, no warning. Just three files nobody read and a summary that did not know they
existed.

The proximate cause was a hand-built reading list. The reason the list could be built by hand and
still look complete is that the run log was short by exactly the channels whose size was never
recorded — see `HANDOFF.md` below. But the reason a reading list gets built at all, rather than
"just read all of them", is context pressure. Both halves are now instrumented.

## The two artifacts

**`HANDOFF.md`** — written from `os.listdir(outdir)`, never from the run's own records. Lists
every answer file with bytes, an estimated token cost to read it, and whether its last non-empty
line is the end marker; then the total read cost; then files on disk no channel record claims;
then channels that ran and wrote nothing; then a ready-to-paste prompt for a fresh context.

Why a directory listing and not a projection of `results`: a manifest built from the records
inherits whatever the records already got wrong, which is exactly the blind spot it exists to
catch. A `listdir` can report a file nobody remembered — and can catch a file some other process
wrote into the same folder.

**`REPORT.md`** — telemetry only: who answered, with which model at which depth, in how long, at
what token and dollar cost, with how many citations and how many of them resolve live. It says
**nothing about what any reviewer actually said**. That boundary is deliberate; see below.

## The decision, and who makes it

The harness prints the read cost and **does not decide**. It cannot see how full the caller's
context is, and a harness that guessed would be asserting a fact it has no instrument for — the
class this project keeps paying for.

The rule for the caller:

> **If the answers total more than ~40 000 tokens and the session is already large, do not read
> them in the same turn.** Report the round's telemetry, hand back the `HANDOFF.md` prompt, and
> let a fresh context do the reading. A panel read carelessly at 350K is worth less than the same
> panel read properly after `/compact` — and costs the same money either way.

The number is a rule of thumb, not a measurement: what matters is the ratio to the room left, and
only the caller can see that. When in doubt, defer — the cost of deferring is one paste, and the
cost of not deferring is a round nobody can prove was read.

## Alternatives considered, and why they were not built

**Have a model summarise the answers.** Rejected. The value of a panel is the *disagreement*
between independent reviewers; a summariser is a single point of failure that can drop the one
finding that mattered, and it would be the fourteenth model in a round whose whole point is that
no single model is trusted. It also costs money to hide information.

**Sub-agents read the files and return findings.** This is what the failing round actually did.
Sub-agents are legitimate — they read in their own context and return conclusions — but the
failure was in the *file list*, not in the reading, and a sub-agent inherits no auto-memory, so
the brief has to carry every critical rule verbatim. Use them WITH `HANDOFF.md`, never with a
hand-assembled list. **Count the files before you count the tasks.**

**A mechanical cross-channel digest** — extract each review's own headings and its bottom-line
block, so 320 KB becomes ~10 KB with the full text still on disk. Not built, and it is the most
interesting of the three: it needs no model, costs nothing, and cannot fabricate. It is on the
backlog. The reason it is not obviously right: it keys on structure the reviews are *asked* to
have and are not *forced* to have, so a channel that formats differently would be silently
under-represented — a digest that quietly drops one voice is worse than no digest, because it
looks like coverage.

## The boundary: the harness measures the RUN, not the ANSWERS

Worth stating plainly, because it surprises people: nothing in this harness reads a review for
meaning. There is no comparison script. `REPORT.md` will tell you that eleven channels answered
and which of their URLs are dead; it will not tell you that nine of them said the same thing and
two disagreed. **That synthesis is done by hand, by whoever reads the files.**

That is a deliberate boundary and not an oversight — mechanical text comparison across models
that answer in different languages and formats produces confident nonsense, and the one thing this
project refuses to ship is a confident-looking artifact that has not verified what it asserts.
It is also the harness's biggest open question; it is in the panel brief for round 48 as question 2.
