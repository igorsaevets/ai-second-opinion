<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Building the brief

## 7. Building the brief


Send **the identical packet to every channel**. The value is in where they disagree; a different
brief per model destroys the comparison.

🔴 **Demand a live web search in the brief itself — every channel, every time** (the owner's
standing rule since 2026-07-26). `tools:` is permission, not instruction: a model that never
searches answers dated questions from stale training data, and does it confidently. Which
channels can prove their searching varies and rots fast when written down — the run's own
summary prints, per channel, the tool counts, the grounding line and the cited-vs-opened audit
where the transport exposes one; where it prints none, the wording of the brief and the URLs in
the reply are the entire evidence base. Two scope limits, both measured: a claim scoped to
ATTACHED material is decided from the attachment alone — do not let a channel "verify" your own
document against the open web; and for proprietary material, restrict the web demand to claims
that are public by nature, so internal wording is not shipped into search queries. Paste this
into every brief:

> Проверь все датированные и проверяемые утверждения **актуальным поиском в вебе** — не полагайся на
> знания из обучения, они устарели. По каждому проверенному факту дай **URL и дату**. Если поиск
> ничего не нашёл, напиши прямо «поиск не подтвердил» и **не** делай вывод, что явления не
> существует.

Then verify: a review with zero URLs has verified nothing, whatever its exit code said.

1. Role: "You are an independent reviewer. You are NOT the author. Find what is wrong."
2. Five to ten lines of real context — what the work is and why it matters. Reviewers given no
   stakes return generic advice.
3. Materials embedded with `ATTACHMENT N: <path>` separators. State what is and is not included,
   and add "do not claim you read files that are not embedded here."
4. 🔴 **Tokenize PII in the sent copy, every channel, before the first call.** Names, case and
   account numbers, address, phone, email → `APPLICANT`, `[CASE-NUMBER]`. Tell the model the
   placeholders are expected. **The harness does NOT do this — it is on you.** Once a payload is
   sent it cannot be recalled. Never embed secrets or `.env` contents.
5. The question, without revealing your preferred answer. Exception: when reviewing an artifact
   that already exists, show the artifact. The reason is mechanical, not etiquette: **channel
   agreement measures the brief, not the source** — an author's error carried in the brief comes
   back confirmed in unison, and one measured round returned the brief's own mistake from every
   reviewer in the room at once. The cheap defence: **plant one deliberately false claim** and demand it be
   confirmed or refuted FROM THE ATTACHED TEXT, quoting it (the wording is the lever — the loose
   form gets half the refusals). Write the planted claim into your round notes BEFORE sending;
   an unrecorded canary is unfalsifiable an hour later. The measured practice is kit
   `TECHNICAL.md` §10, "Verification practice".
6. For every load-bearing quotation — the few the verdict rests on, whether you asked for them
   or the reviewer introduced them — demand **the sentence before and the sentence after it**,
   plus a **source-native locator** (page, section, line, key path or timestamp), not only a
   URL. The trio is a tool
   for YOUR side: three consecutive sentences are one grep against the source, and a fabricated
   quotation rarely survives it. It is NOT a barrier on the channel's side — a channel has
   fabricated the quotation, BOTH neighbours and an `[OPENED]` tag in one answer — so treat
   returned neighbours as material to verify, never as verification (`references/verification.md`).
7. In a repeat round, add a block **"already decided by the owner — do not reopen"**: each closed
   decision verbatim, with its date. Reviewers cannot know a question is closed unless told, so
   they spend the round relitigating it — and a recorded owner decision has beaten a unanimous
   panel here at least once. The block buys the round back for what is actually open. One
   carve-out, stated in the block itself: a reviewer may not relitigate the decision, but may
   flag — separately, as new information — a fact that postdates the decision and changes its
   premise.
8. Require a closing field: **"the weakest point of this review, and what evidence would change
   your conclusion"**. Channels answer it more honestly than a direct "how confident are you" —
   and in one adjudicated round the only reviewer who was right said so in precisely that field,
   not in its verdict. Read that field first when grading.
9. Required output format, ending with the literal marker on its own last line.

## Code-review briefs — three extra lines

A code brief follows every rule above, plus three of its own, each paid for on a real round:

- Ask every reviewer: **"which INPUT makes this code return a wrong result SILENTLY?"** The crash
  they would have found anyway; the silent wrong answer is what the review is for. The first
  round that asked this question surfaced five silent defects in one pass.
- Anchoring works the OTHER way round for code: the intended purpose MUST be in the brief. A
  reviewer left to guess intent from the code reviews its style instead of its correctness.
  State the intent as a specification — inputs and expected outputs, ideally one concrete
  example not taken from the comments — never as your preferred verdict on the code; and say
  plainly that docstrings and comments are the author's CLAIMS, not facts about behaviour.
- Name the environment: OS and shell, encodings actually present in the data (Cyrillic, CRLF,
  NBSP `\xa0`), path conventions. Portability bugs live exactly where the reviewer's default
  environment differs from yours.

---


For a legal, immigration or regulatory brief, read `references/legal-briefs.md` **before** writing it.
