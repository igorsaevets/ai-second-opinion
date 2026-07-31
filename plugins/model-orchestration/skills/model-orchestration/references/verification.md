<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Verifying that a review actually happened

## 8. The rule that goes in every system prompt


The harness appends this automatically; include it manually if you write your own client.

> If your search finds nothing, write exactly "my search found no confirmation" and do NOT conclude
> the thing does not exist. A non-existence claim is permitted only with positive evidence of
> absence — a complete official list lacking the item, with that list's URL.

This exists because these models turn "my search found nothing" into "this does not exist". It has
produced false negatives repeatedly, including a claim that three real Executive Orders were
fabricated, and a fabricated timestamp offered while "correcting" a figure that had been pulled from
an API. Apply it in reverse too: when a reviewer says something of yours is unverified, check
whether **you** already verified it from a primary source before deleting the claim.

### The rule is not enough on its own — budget for false alarms

**Measured 2026-07-26**, one 180 KB legal document, all three channels, this instruction present in
every brief: **9 false "this is wrong / unverified" claims against 4 genuine findings.** Every
channel produced at least one. The instruction reduces the rate; it does not eliminate it.

So the review is not the last step — **adjudicating the review is.** Before changing a single line
because a reviewer objected:

1. **Re-check your own primary source.** Three separate objections that round died on a `grep` of a
   Federal Register text already sitting on disk. One reviewer said a quoted phrase came from a
   rescinded 2019 rule and that searching for it returned "No results found" — the phrase was
   verbatim in the 2026 rule. **Large FR documents are not indexed phrase-by-phrase by search
   engines, so "I searched for the quote and found nothing" is worthless evidence against a document
   you downloaded and read.**
2. **Check the other reviewers before believing any one of them.** That round, Codex said two facts
   were unconfirmed; Spark confirmed both with dated verbatim quotes. Had only Codex run, two correct
   statements would have been weakened. **This is the concrete payoff of Rule A.**
3. **Discount the overall verdict, keep the itemized findings.** `agy` returned "UNUSABLE IN ITS
   CURRENT FORM" resting on three pillars, two of which were refuted by regulation text. Verdicts are
   the least reliable part of a review; specific citations are the most.
4. **A wrong objection can still improve the document.** The false claim about the quoted phrase is
   why the final text now cites the exact location in the rule. Log these as "challenged and
   survived" so nobody re-litigates them later.

Write the outcome down **in the artifact**, not just in chat: what was accepted, what was rejected
*with the proof*, and where the reviewers contradicted each other. Otherwise the next session
re-opens settled questions.

### Citations are not evidence — spot-check them, cheaply

**Measured 2026-07-27.** In a single `agy` review, three citations were checkable against material
already on disk and **all three were wrong**: `8 CFR 103.37` for "AG decisions bind DHS officers"
(part 103 has no § 103.37 — sections run 103.1–103.10, 103.16, 103.17, 103.38–103.42; the real
provision is § 103.3(c)); a precedent PDF at `…/vol10/1709.pdf` (the decision is at `1312.pdf`);
and Federal Register document `2022-19182` for 87 FR 55472 (it is `2022-18867`). Two further case
cites returned no confirmation on search.

The reasoning around those citations was sound and several findings were genuinely useful. That is
the point: **a reviewer's argument and a reviewer's citation are separate artifacts with separate
reliability.** Take the argument seriously; verify every section number, docket number and URL
before it enters your document. The check is nearly free when the primary source is already local —
which is the strongest practical argument for downloading sources instead of linking them.

### A `sonnet` sub-agent can beat a mid-tier channel at fact-finding

Same round: asked for the status of four things (litigation, a promised policy alert, three form
editions, a replacement NPRM), `agy` returned "my search found no confirmation" on all four with
only homepage-level URLs. A `sonnet` sub-agent with the same question returned dated, page-verified
answers for all four, naming the tool that finally got past `uscis.gov`'s 403.

Rule: **do not spend a review channel's turn on retrieval.** Send retrieval to a `sonnet` sub-agent,
put its verified findings *into* the brief, and spend the channel on judgement. This is Rule B
applied inside a Rule A round — the two are compatible.

### Verify artifacts a sub-agent claims to have downloaded

An agent honestly reported that a Google Books PDF could not be obtained — and still left a
273 KB file named `*.pdf` on disk that was a login page. Check magic bytes (`%PDF`), size, and one
expected phrase yourself. An agent's prose report and the bytes on disk are different claims.

### 🔴🔴 The most expensive one: "the text has not changed" is not "the practice has not changed"

An agency that finds rulemaking expensive changes a **cable** instead. On 27.07.2026 this inverted a
whole section of a finished document: 9 FAM 302.8 was verified unchanged since 2024 — by me and
independently by **all three channels** — and from that I concluded consular practice was unchanged.
It was not. State had paused immigrant visas for 75 countries on public-charge grounds (21.01.2026)
and issued a non-public cable (06.11.2025) expanding health and age screening. Every channel confirmed
the *text* check; none asked the *practice* question, because I did not ask it either.

> When a brief asks "has document X been updated", **always pair it with**: "has the practice under X
> changed by any other instrument" — cable, memo, operational guidance, suspension, proclamation,
> enforcement priority. Two questions, not one.

### Reviewer signatures, measured across five rounds on one project

| Channel | Reliable at | Fails at | Handling |
|---|---|---|---|
| **Spark** | finding what nobody else finds | inventing plausible specifics around a real finding | take the finding, verify every detail separately |
| **agy** | substance and structural gaps | **seven invented URLs**; wrong sub-paragraph numbers | check every link with an actual request; re-derive every citation |
| **Codex** | opening the source; saying "I could not confirm" | still drifts one step from the text when paraphrasing | open the page yourself before quoting what it quoted |

The practical consequence: **the disagreement between them is the product, and the verification is
yours.** In this round each channel independently found a *different* missing gate in the same funnel —
three reviewers, three distinct gaps, zero overlap. One reviewer would have delivered a third.

### 🔴 The expensive one: a reviewer's **characterisation** of a document is also a claim

**Measured 2026-07-27, cost: two full review rounds.** A channel described a precedent as "deaf-mute
diabetic **on SSI**, found not likely to become a public charge". The citation was correct, the
holding was correct, the PDF was **already on my disk** — and the benefit type was wrong. The
decision says the applicant received "a monthly Social Security check … **as the disabled son of a
United States citizen**": an earned Title II benefit, not means-tested at all.

On that single wrong word I built a headline finding — that winning on this precedent would be
incompatible with a public-charge bond, since the new rule makes *any* means-tested benefit a breach.
It survived a second round in which **two other channels endorsed it** ("a key systemic
vulnerability", "broadly agree"). None of the three opened the PDF. Neither did I.

The previous subsection said: verify **citations**. That was too narrow. The full rule:

> **If a reviewer characterises the *contents* of a document you already possess, open it before the
> characterisation propagates.** Section numbers and URLs are the cheap failure mode. The expensive
> one is a plausible one-line summary of a document nobody re-read — because unlike a bad URL, it
> does not look wrong, and downstream reasoning silently inherits it.

Cheapest reliable check: extract the text and count occurrences of the decisive term. Here,
`SSI` = 0, `Supplemental Security` = 0, `welfare` = 0 settled it in one command. **Grep the primary
source for the word the whole conclusion rests on.**

Corollary observed the same round: **the channel that disagreed was right for the wrong reason.**
Only `agy` rejected the thesis — calling it "a dangerous practical misconception" — while `Spark`
and `Codex` partly agreed. It could not say *why*, because it had not read the PDF either. Treat a
lone dissent as a pointer to where to re-read the source, not as an argument to be scored.

### A future access date is a fabrication tell — check the dates, not just the URLs

**Measured 2026-07-27.** Spark ran 34 web searches on a strategy brief that
explicitly demanded "URL и дату по каждому проверенному факту", returned confident
per-country statistics — and cited **no URLs at all**, only captions like "WER bias
study" and "Sonix Latin American accents claim". It then stamped the block
`Evidence accessed 2026-07-30` while the real date was **2026-07-27**.

Two cheap checks that catch this in seconds and belong in every round:

1. **Count URLs per channel.** `(Select-String -Path CHANNEL.md -Pattern 'https?://' -AllMatches).Matches.Count`.
   A channel making dated claims with a near-zero URL count has verified nothing,
   whatever its search counter says. Searching and *recording* are different acts.
2. **Grep the reply for dates later than today.** A model that invents the date it
   "accessed" a page did not access it. Treat every fact in that block as a lead.

Demanding URLs in the brief reduces this; it does not eliminate it. The tool-call
counter proves the model searched — it does not prove the answer came from what it
found. Accept such a channel's factual claims only where a second channel confirms
them independently; keep its structural and strategic observations, which are
unaffected.

### Do not put research sub-agents on the critical path

Same round: five `sonnet` sub-agents. The three with a narrow job (two
transcreations, one orthography pass) finished in 3-5 minutes. The two given a
broad web-research brief with a long MCP escalation ladder ("try WebFetch, then
jina, then tavily, then scrapling, then firecrawl") were **still running after
90 minutes** and never returned.

Rules that follow:

- Sequence the work so the deliverable can be finalised **without** them. Here the
  page copy was frozen using facts already verified in the repo, and the research
  agents were demoted to enrichment. If they had been a dependency the run would
  have stalled.
- The external channels have hard timeouts and always return; sub-agents do not.
  For anything time-critical, prefer a channel.
- Give a research sub-agent an explicit budget in its prompt ("stop after ~25 tool
  calls and return what you have, marking the rest as not verified"). An open-ended
  escalation ladder plus "be thorough" is an invitation to never stop.
- Check for completion by waiting for the notification, not by reading the agent's
  `.output` file — that file is the full JSONL transcript and reading it destroys
  your own context.

### Check a channel against its own previous round

Same round, `Codex` proposed building a strategy on a mechanism it had itself called out one round
earlier as its "most sharp" disagreement. **Channels do not remember their prior answers.** When a
recommendation touches something a channel previously rejected, quote its own earlier words back at
it in the brief — or catch the contradiction at integration time.

---
