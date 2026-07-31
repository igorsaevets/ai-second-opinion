<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Building the brief

## 7. Building the brief


Send **the identical packet to every channel**. The value is in where they disagree; a different
brief per model destroys the comparison.

🔴 **Demand a live web search in the brief itself — every channel, every time** (the operator's rule for
`agy`, 2026-07-26, and it applies to all three). `tools:` is permission, not instruction: a model
that never searches answers dated questions from stale training data, and does it confidently.
Know which channel you can audit afterwards, because it is the opposite of what this file used to
say. Since `--output-format stream-json` (agy 1.1.8) **`agy` is the only channel with per-call
telemetry** — every tool call, `thinking_tokens`, and the cited-vs-opened URL check the harness
prints. **Spark** reports a search *count* and nothing about which pages it opened; **Codex**
reports nothing at all. So for those two the wording of the brief and the URLs in the reply are
the entire evidence base. Paste this into every brief:

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
   that already exists, show the artifact.
6. Required output format, ending with the literal marker on its own last line.

---


For a legal, immigration or regulatory brief, read `references/legal-briefs.md` **before** writing it.
