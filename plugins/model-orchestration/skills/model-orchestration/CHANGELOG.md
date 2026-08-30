# Changelog

## 1.41.0 — 2026-08-30

* **A 429 no longer kills a whole review on the OpenAI-protocol transport (backlog
  A5, R70).** Every OpenRouter/direct-API channel now retries a 429 or 5xx up to
  twice, honouring `Retry-After` (capped at 60s, the same cap the Spark path got
  in 1.40.0), before failing. The retry is safe by construction: the conversation
  body is only appended to after a successful round, so the rejected request is
  re-sent verbatim — and an HTTP rejection delivers no completion, so the failed
  attempt billed nothing. Deliberately NOT retried: timeouts and mid-stream drops,
  because an unstreamed call that timed out may have finished — and billed — on
  the vendor's side, and re-sending it would pay twice for one answer.
* **The timeout is now proven functional, not just present.** New selftest suite
  (+5 checks, 782 total) runs against real loopback sockets, still calling no
  vendor: a server answering 429, 429, then a completion must be recovered on the
  third request with the exact waits asserted (3s Retry-After beats the 2s floor;
  4s exponential floor beats the header); a 400 must NOT be retried — the control
  an unconditional retry cannot pass; a server that accepts and never answers must
  cut `_post(timeout=2)` in ~2s rather than the 2400s default; and a census
  requires every `urlopen` call site to pass `timeout=`.
* **README.ru.md fully re-synced with README.md — including a dangerous 23-day-old
  lie.** Since the 1.4.0-era policy inversion the Russian page still promised that
  personal data is «blocked by default, requiring a deliberate flag». The code
  itemises and then SENDS it (with `--strict-pii` as the opt-in hard stop), names
  and street addresses are not detected at all, and the English README and
  PRIVACY.md have said so since 2026-08-07. The Russian page now says exactly what
  the English one says, section for section, including the AI-assistant install
  path and its key-handling security model, which it had never carried.
* **The roadmap sections rotted and are gone — both languages, both files.**
  README's "Roadmap" still promised Kimi and "direct OpenRouter support" as future
  work weeks after both shipped (OpenRouter is now the largest group in the cost
  table), and TECHNICAL.md §11 repeated the same promise two paragraphs above the
  §12 rule that forbids asserting mutable values in documents. Replaced by an
  "Adding a model" section: the registry is the roadmap. Also dropped the
  planted-claim paragraph's channel count ("across all nine" → "across the full
  panel") — no prose copy of the channel count has ever stayed correct.
* **GitHub Releases backfilled.** The Releases page had stopped at v1.27.0 while
  tags ran to v1.40.0, so the repo advertised a wrong "Latest" for 13 shipped
  versions. All 24 missing releases created (v1.2.1 … v1.40.0), each carrying its
  CHANGELOG section; upgrades never depended on Releases (the update check reads
  tags), so this is honesty of the storefront, not a repair of the mechanism.

## 1.40.0 — 2026-08-30

* **New channel `orspark12cont` — the Spark voice for OpenRouter-only installs (R69).**
  `meta/muse-spark-1.2-contributor` through OpenRouter ($0.10/M in, $0.20/M out,
  read from the live catalogue on release day), with an automatic vendor-side
  fallback to `meta/muse-spark-1.2` (Standard, non-training tier) via the
  `models` array — the same failure-only mechanism the GLM channel used, never
  activated by `--set`. Reasoning pinned at `xhigh` explicitly because the
  gateway's own `default_effort` is `medium` — a gateway defaults below its
  vendor's top. `max_tokens` 131072 (vendor declares 943,718; the registry-wide
  cap applies), Exa/native web search on, harness fetch tool on, $1.00 spend
  guard sized to the FALLBACK tier's prices, cheap panel. Until now a
  first-time user holding only an `OPENROUTER_API_KEY` had no Spark voice at
  all. Same Contributor data terms as `spark12cont` — the vendor may train on
  what you send; PRIVACY.md now names all three such channels.

* **`--ask` default is resolved, not hard-coded.** The one-shot path used to
  default to `spark12cont` by argparse literal — for an OpenRouter-only install
  that pointed `--ask` at the one channel it cannot run. The default now comes
  from `ask_default` in channels.json: the first entry that is enabled and
  whose transport key is present (spark12cont first, so machines with a
  `MODEL_API_KEY` behave exactly as before), and the resolved choice is
  printed when it fires. `--ask-channel <name>` still overrides.

* **`spark` now names the model family across transports** — the group covers
  `spark11`, `spark12cont` and `orspark12cont`, so «не используй spark» drops
  all three, exactly as `gemini` and `grok` already work. `orspark12cont` also
  joins the `openrouter` group: `--skip openrouter` excludes it, and it shares
  that key's fate when another member exhausts the account.

* **Docs corrected where they had rotted.** INSTALL.md's OpenRouter section
  claimed one key serves "both kimik3 and qwen38max" — true in 2026-08-06,
  eleven channels stale today. It now says what the key actually serves, tells
  a new user where to REGISTER (openrouter.ai, key at openrouter.ai/keys —
  verified by redirect on release day) and instructs an AI assistant running
  the install to send the user to those pages in their own browser, keeping
  the sign-up, payment and key value out of the conversation. The registry's
  own claim that `--set spark12cont=muse-spark-1.2` is "refused by routing"
  was measured false (the Standard model has been a listed legal model since
  the fallback landed; the override is accepted and printed loudly) and is
  corrected as SUPERSEDED in place.

* **Retry-After cap 30 → 60 s** on the direct-Meta retry loop (backlog A3): a
  vendor answering `Retry-After: 45` was retried 15 s early against a window
  it had just declared, guaranteeing a second 429. Sixty still bounds a
  pathological header; a 429 bills nothing, so the retry is free.

* **CI actions bumped at the source**: `actions/checkout` v5 → v7 and
  `actions/setup-python` v6 → v7 in the shipped workflow, landing the two open
  dependabot PRs where package.py actually reads from — a merge only in the
  generated repo would have been silently reverted by the next build. Both
  bumps ran green on all four CI matrix legs first.

* **23 new selftest checks (777 total locally):** `ask_default` hygiene (real
  channel names, ≥2 candidates), the resolution function pinned on synthetic
  registries (key-present, key-absent, disabled-candidate, empty-registry) and
  on the shipped registry layout-aware, the key-readiness predicate per kind,
  a source census that the argparse literal default is gone — plus the derived
  per-channel checks the new registry entry picks up automatically.

## 1.39.2 — 2026-08-30

* **Verify and strip now obey the same rule (R68 audit).** 1.39.1 moved marker
  VERIFICATION to line-equality but left the two marker-STRIP sites
  (`refusal_check` and the `--ask` answer display) on an endswith-only cut. On
  a suffix-confused tail (`...PREVIEW-DONE-R66` against marker
  `REVIEW-DONE-R66`) that cut removes `len(marker)` characters out of a word
  and shows the reader a mangled last line. New helper
  `_strip_marker_tail(text, marker)` strips the marker LINE when — and only
  when — `_marker_on_last_line` verified it; any other tail is left exactly as
  the model wrote it, visible. On texts that PASS verification behavior is
  unchanged (the outer `.strip()` already neutralized the divergence there),
  so this is consistency hardening, not a production-bug fix.

* **Marker-only answers are now a hard fail.** An output consisting of nothing
  but the end marker passed every check in BOTH the 1.39.0 and 1.39.1
  editions: not empty (the marker is text), marker verified (it owns the last
  line), no refusal flag (the stripped body is empty). Three greens over zero
  work. `refusal_check` now returns a hard `MARKER-ONLY ANSWER` warning.

* **`codex_postmortem` prunes its rollout walk (R66 backlog item).** The
  newest-rollout search walked every session directory codex ever wrote. It
  now prunes date directories whose mtime is older than the run's start minus
  a 2-day slack — and if the pruned walk finds nothing while a start time was
  given, it re-walks unpruned: a wrong prune costs one extra walk and can
  never silently lose the diagnosis.

* **12 new selftest checks (754 total):** 6 behavior probes on
  `_strip_marker_tail` (clean strip, whitespace-decorated strip,
  suffix-confusion left visible, embedded tail left alone, None-safety, empty
  marker), 2 on the marker-only hard fail, and a 4-check source CENSUS — the
  class guard 1.39.1 lacked: zero raw marker-`endswith` anywhere in
  `orchestrate.py` and exact call-site counts for both helpers, so a future
  channel cannot reintroduce the suffix hole invisibly.

* **Source-repo hygiene:** `reviews/` is now gitignored in the source tree (a
  `run.log` had been tracked-and-modified since round 28); the kit's own
  gitignore already refused this class for employees.

## 1.39.1 — 2026-08-23

* **Marker line-equality hardening (R66 panel finding → R67).** R66 aligned
  three verification paths (`_verify_http`, `call_gemini_direct`, `_agy_once`)
  from `marker not in text` to `text.strip().endswith(marker)`. The R66 cheap
  panel then converged on a residual weakness: `endswith("REVIEW-DONE-R66")`
  accepts a stray `PREVIEW-DONE-R66` (false-positive suffix match). Named by
  four independent channels — `spark12cont`, `agy36flash`, `goog36flash`,
  `grokbuild` — all strong for code review. Fix replaces `endswith(marker)`
  at all EIGHT verification sites (R66's 3 + 5 pre-existing CLI channels) with
  a single helper `_marker_on_last_line(text, marker)` that returns
  `bool(lines) and lines[-1].strip() == marker`. Empirically all 12 R66 panel
  answers placed the marker on its own line, so no observed regression; the
  new check is strictly stricter on suffix-confusion and same-behavior on
  every other case (trailing punctuation, embedded text, empty output).

* **7 new selftest checks** (742 total): 3 R66 checks updated to assert the
  new helper (was: `"endswith(marker)"` substring), plus 7 behavior checks on
  the helper itself — empty text, PREVIEW-suffix rejection, marker-on-own-line
  acceptance, whitespace-around tolerance, trailing punctuation rejection,
  embedded-in-longer-line rejection (the stricter case, deliberately named),
  and pass-through when marker is empty.

## 1.39.0 — 2026-08-23

* **Timeout drift fix (R66 audit, 3 of 4 R64 backlog items).** Four dispatch
  paths (http, openrouter/oai, gemini, hermes) silently ignored the registry's
  `timeout` field and fell back to the 2400 s default. Channels with explicit
  timeout in `channels.json` (agy, codex, grokcli, xai) were unaffected.
  Fix passes `timeout=_seconds(p.get("timeout"), 2400)` to all four.

* **Marker consistency fix.** Three verification paths (`_verify_http`,
  `call_gemini_direct`, `_agy_once`) used `marker not in text` which checks
  whether the marker appears ANYWHERE — missing the case where a model writes
  the marker mid-answer and then continues. All five CLI channels already used
  `text.strip().endswith(marker)` which catches both absent and misplaced
  markers. Aligned the three paths to `endswith` for consistency.

* **Hermes reporter telemetry.** The reporter's `if kind == ...` chain had
  no branch for `hermes`, so that channel printed no telemetry line at all.
  Same defect class as R46 (dispatch is loud, reporting is quiet). Added a
  minimal telemetry line (exit code + model).

* **15 new selftest checks** (735 total): 12 verifying timeout reaches
  every dispatch kind, 3 verifying `endswith(marker)` in the three fixed
  verification functions.

## 1.38.1 — 2026-08-23

* **Forced-final loop-exhaustion fix in `call_oai_reviewer`.** When an
  OpenRouter/OAI channel spent its full fetch budget (default 8) AND the
  model still tried to fetch on the last outer-loop iteration, the harness
  set `forced_final = True; continue` — and the `for _round in
  range(max_rounds + 1):` loop exhausted silently. The forced-final
  `_stream_once` (with `tool_choice="none"`, asking the model to answer
  from what it already has) never ran, `text_parts[-1]` held the last
  tool-call chunk, and the channel returned `ok=False, END MARKER ABSENT`
  after paying for every fetch. R64 cheap panel: three channels with code
  access (AGY31PRO, AGY37FLASH, GOOG37FLASH) converged on this defect,
  which the prior R64 audit had refuted arithmetically in error. Fix
  performs the forced-final `_stream_once` INLINE right after setting
  the flag, so termination no longer depends on the outer for-loop
  having another iteration available. Symptom before the fix: rare
  `EMPTY OUTPUT` on channels hitting the fetch budget, e.g. the
  recurring `mimo25pro OUTPUT BUDGET EXHAUSTED` and possibly some
  `ornemotron3ultra` empty returns.

## 1.38.0 — 2026-08-22

* **Spark Contributor fallback to Standard tier.** When `spark12cont`
  (muse-spark-1.2-contributor, $0.10/M input) fails for a reason other than
  a content filter, the harness now retries with `muse-spark-1.2` (Standard
  tier, $1.25/M input) automatically. The Contributor tier has 100 RPM vs
  3000 RPM on Standard, so rate-limit failures (429) now recover instead of
  failing the channel. Content-filter blocks are NOT retried — same payload
  to the same vendor's filter gives the same result. Diagnostics report
  `fallback_used: true` and `primary_model` when the fallback fires.
  Rate limits measured from `dev.meta.ai/docs/pricing-rate-limits` on
  2026-08-22: Contributor 100 RPM / 3M TPM, Standard 3000 RPM / 4M TPM.

* **GLM 5.3 moved from cheap to standard panel.** Solo run failed: model
  burned all 11 page fetches on irrelevant content (Python docs, 124K chars)
  and generation timed out (exit 255, 30+ min, no answer). At $1.40/M input
  it belongs in the standard panel. Still runs on `--panel standard`.

* **Contributor tier rate limits corrected.** Prior note said 60 RPM /
  2.1M TPM; live docs (2026-08-22) say 100 RPM / 3M TPM. Cached input
  pricing also documented: $0.002/M (Contributor) vs $0.15/M (Standard).

## 1.37.2 — 2026-08-21

* **Stronger web-search obligation in the system prompt.** The `base-depth`
  preset now explicitly requires models to use web search for checkable
  claims, with a >10% probability framing. Previously the prompt said "do
  not answer from memory" (a prohibition); now it says "you MUST use web
  search" (an obligation). the operator's wording, added verbatim.

## 1.37.1 — 2026-08-21

* **orglm53 effort vocabulary fixed.** R61's cheap panel found the issue:
  `supported_efforts` had `["xhigh", "high", "low"]` (GLM 5.2 vocabulary)
  instead of `["max", "high", "low"]` (GLM 5.3 vocabulary). Z.ai docs say
  GLM 5.3 only accepts `max`/`high`/`low`; the R61 run succeeded because
  Z.ai's Coding Plan path maps `xhigh→max`, but the config now matches the
  documented vocabulary. `measured_usd` updated with R61's first real run:
  $0.39, 651s, 179K in / 22.7K out / 17K reasoning.

## 1.37.0 — 2026-08-21

Two fixes, one visible and one invisible until now:

* **GLM 5.2 replaced by GLM 5.3 (paid only).** The free tier of GLM 5.2
  (`z-ai/glm-5.2:free`) kept falling back to the paid model on 429 rate
  limits from Decart's shared pool, so the "free" channel was paying anyway
  and reporting the wrong model. GLM 5.3 was released 2026-08-18 with
  reasoning always on, efforts low/high/max, and ~4x higher pricing
  ($1.40/M in, $4.40/M out). The channel is renamed from `orglm52` to
  `orglm53`; `spend_guard.max_usd_per_review` raised to $5.00 accordingly.
  `fallback_models` removed (no free tier to fall back from). The old
  `provider_route` pins (decart/streamlake/novita) are cleared — run
  `doctor --online` after the first real run to discover and pin providers.

* **CI has been GREEN for the first time since at least v1.31.1.**
  `suite_spend_guard` tested `call_oai_reviewer` with a monkeypatched
  transport but never injected a dummy API key. On CI (no key),
  `call_oai_reviewer` returned early at the key check with a dict
  missing the `"usd"` field, causing `KeyError('usd')` on every
  platform and Python version. The fix injects a temporary dummy key
  for the duration of the monkeypatched call. Verified both with and
  without the real key locally.

## 1.36.0 — 2026-08-20

**A bug report from an employee running v1.24.1 exposed a class of self-check
that had been RED on every install.ps1 install since 1.9.1 — twelve days for
this reporter, and structurally for every non-plugin install for eleven
releases before that.** The colleague ran the exact command INSTALL.md and
TROUBLESHOOTING.md point at as "the difference between the assistant said it
fixed it and the fix is verified", and it returned 497/498 for reasons the
harness itself could see and never surfaced. His note included the sharpest
line in the bug report: **«красный индикатор, который научились игнорировать,
— это выключенный индикатор»** — a red anchor people learn to ignore is a
disabled anchor, and it teaches everyone to read a failing test suite as
"normal", which is the state next real regression sails past.

Reproduction on 1.35.0 here confirmed the primary bug AND surfaced a second
one of the same class (a different check going red for a different
build-time reason). Both are fixed at the source; a new suite runs the
SHIPPED selftest from the SHIPPED install location on every build so the
class as a whole is caught now, not by the next reporter.

* **`suite_prose_matches_behaviour` — the shipped-docs check now passes on
  every install path.** Added in 1.9.1 because a public README once promised
  a PII block the code did not perform, this check walks parents from
  `selftest.py` looking for `README.md` + `PRIVACY.md`; if neither is
  present, it fails RED on purpose ("not vacuously green"). On a plain-copy /
  install.ps1 / install.sh install ONLY the plugin skill folder is copied
  into `~/.claude/skills/model-orchestration/` — the parents are the user's
  home directory, which has no repo documents — so the check has been red
  for every such install since it was added. package.py now copies the
  substituted `README.md` and `PRIVACY.md` from the build output into
  `plugins/model-orchestration/skills/model-orchestration/kit/`, which is
  the FIRST location the check tries. Plugin marketplace installs already
  had docs 5 levels up; they now also have them at zero levels up.

* **`suite_panels` — kit-excluded channels no longer trigger the "REMOVED
  without a RETIRED note" drift check.** `orgpt56lunapro` was demoted from
  the cheap panel in R55 AND held back from published distribution
  (`PUBLISH_EXCLUDE_CHANNELS` in package.py — see the R54 note above about
  employees walking all three enabled/explicit_only/requires_ack locks). On
  dev the channel is still in `channels.json` (standard panel), so the check
  passed silently; on shipped it is entirely absent, and the check flagged
  it as a lying record when the real cause was a build-time hold-back the
  shipped selftest had no way to know about. package.py now stamps the
  hold-back list into shipped `channels.json` as `_kit_excluded_channels`;
  the check unions this set with the `RETIRED`-marked set. Dev unchanged
  (the key is absent, so the union is empty); shipped now green.

* **`suite_r60_shipped_docs_and_kit_exclusion` — the class as a whole is
  now caught at build time.** Runs `package.py` to a fresh temp dir and then
  runs the SHIPPED selftest from the shipped location. If any check goes red
  from that layout — the shape the reporter's own probe revealed — the build
  fails, before push. The suite silently skips on installs (no `package.py`
  there), which is what dev-tool contracts should do; the point is to run it
  in the ONE place a shipped-tree bug is visible.

Panel adjudication: cheap panel of 8 free channels (agy skipped per
standing instruction). Transcripts in `runs/r60/` in the source tree.
Planted false claim ("Fix A's cost is ~500 KB duplication") refuted by every
channel that reached a verdict on it (real cost: 24 KB — measured README
17,833 B + PRIVACY 6,341 B).

## 1.35.0 — 2026-08-20

**Three fixes for the three panel failures in AOS Round 55, adjudicated by a
9-channel cheap panel that LIVE-REPRODUCED one of them and corrected the
diagnosis mid-round.** Transcripts and adjudication in `runs/r59/` in the
source tree.

* **grokbuild survives a relative-path AND a non-ASCII-path workdir.** The AOS
  R55 error `Failed to read 'reviews\\grokbuild-ws\\PROMPT.md': Системе не
  удается найти указанный путь. (os error 3)` looked like a Cyrillic-path bug
  because the AOS path had Cyrillic components. The R59 panel reproduced the
  IDENTICAL failure on a pure ASCII path inside this same round, proving the
  root cause is different: grokcli resolves a
  relative `--prompt-file` against `--cwd neutral_cwd()` rather than the
  parent's cwd, so any relative workdir points nowhere. Two fixes now, addressing
  two different classes:
  * `os.path.abspath(workdir)` before use in `call_grokcli` — the actual R55 fix.
  * `_ascii_safe_workdir()` (extracted from R37's inline agy fix, shared
    across agy and grokbuild now) — still needed because the same CLIs also
    stumble on non-ASCII path components via their own filesystem calls, and
    the AOS R55 path really was non-ASCII, so BOTH defects fired together.
  Verified live: grokbuild answered a trivial brief inside a Cyrillic-named
  workdir with a relative `--out` argument, marker present, ok=true.

* **ornemotron3ultra can finish a fetch-heavy brief without dropping the answer.**
  In AOS R55 the model made 11 fetches, was told to answer, and returned more
  `tool_calls` anyway — `finish_reason=tool_calls` with zero answer text.
  Two-part fix, both applied:
  * `ornemotron3ultra.fetch_tool.max_calls` raised from 11 to 16 in
    `channels.json` (this channel is free, so wall clock is the only cost of a
    larger budget; paid channels' default 11 stays).
  * The forced-final turn now sends `tool_choice: "none"` — AND KEEPS the
    `tools` array in the payload. The initial R59 draft removed `tools` too,
    which the panel refuted with primary sources: xAI's backend rejects
    `tools:[] + tool_choice:"none"` with `400 A tool_choice was set on the
    request but no tools were specified`; Anthropic (via OpenRouter) rejects
    any history containing `tool_use`/`tool_result` blocks while `tools` is
    absent; removing `tools` busts the prompt-cache prefix. Keeping tools +
    tool_choice="none" is the OpenRouter-SDK-current form and is safe on
    every conformant provider.

* **goog37flash has a proper diagnosis when the recitation filter fires.** In
  AOS R55 it returned `HTTP 400: "Request blocked due to copyright/recitation
  content"` on a legal brief that goog36flash and orgemini37flash both
  answered cleanly. The filter cannot be disabled — verified against Google's
  own docs live (`ai.google.dev/gemini-api/docs/safety-settings`). The
  `KNOWN_FAILURES` table now has a dedicated entry above the generic REFUSAL
  row: it names the recitation cause specifically and recommends routing to
  another Google transport (goog36flash or the Vertex-pinned
  orgemini37flash), or `--skip goog37flash` for legal briefs. Deliberately
  NOT added: a per-channel `system_suffix` telling the model to paraphrase
  quotations — verbatim quotation of statute is what makes a legal review
  verifiable, and shortening it just to placate the filter defeats the point.

* **Panel-driven belt-and-braces (each traceable to a specific reviewer):**
  * **`hashlib.sha256[:12]` instead of `md5[:12]`** in `_ascii_safe_workdir()`
    — 3 of 9 channels named this: `md5()` raises `ValueError: [Beyond FIPS]
    md5 is not allowed` on Linux/Windows enterprise Python builds with FIPS
    mode enabled. The digest is a directory disambiguator, not a
    cryptographic primitive; the 48-bit entropy is unchanged.
  * **Keep `tools` on the forced-final turn** — see the ornemotron3ultra
    bullet above. Provider-safety improvement paid for by ORGLM52's primary
    citations and corroborated by ORGEMINI37FLASH and SPARK12CONT.

**Verification.** selftest **711/711** (was 707 after the R59 additions; 4 new
checks for the panel-driven corrections: abspath, sha256-not-md5, keep-tools,
tool_choice-present). doctor reports all configured channels can run. The
ASCII-mirror helper passes 5/5 unit checks (ASCII passthrough, Cyrillic
mirror, determinism across calls, distinct inputs → distinct mirrors,
`tag_prefix` parameterisation). **Live end-to-end**: grokbuild answered a
trivial brief inside a Cyrillic-named workdir with a relative `--out`
argument in ~60 seconds, marker present, ok=true.

**The planted false claim in the R59 brief** ("60 req/hour per key for free
OpenRouter models") was correctly refuted by 5 of 5 channels that took a
stand — with primary-source counters citing the real OpenRouter free-model
limits (20 req/min, 50 or 1000 req/day depending on credits).

## 1.34.0 — 2026-08-20

**The plugin now notices when Claude Code has auto-updated it, and tells you what changed.**
Also: `doctor.py` checks upstream for a new version once a week and prints a one-block notice
if one exists. Nothing runs in the background; nothing is auto-applied; the network path is
disable-able with two ecosystem-standard env vars as well as our own.

**Design was reviewed by 9 external channels before shipping.** The design brief and full
transcripts are in `runs/r58/reviews/` in the source tree. Two decisions were reversed after
the panel disagreed with the first draft, one in a way that made the round worth the tokens:

* **The plugin's SessionStart hook is LOCAL-ONLY** — it compares the current `VERSION` file
  to a stamp file and prints once when they differ. NO network. This preserves the "nothing
  phones home" line in `INSTALL.md` on the plugin path — which is most users. The only reason
  this shape works is that Claude Code's marketplace already auto-fetches new plugin files;
  the hook just makes their arrival visible.
* **The full network check moved into `doctor.py`** — where the user explicitly asked for it.
  A separate `--install-hook` command adds an equivalent SessionStart entry to your own
  `~/.claude/settings.json` for those who want a proactive daily check on Method 2/3 installs.

**Verified bugs the panel surfaced and this release addresses:**

* **anthropics/claude-code#16538** — plugin SessionStart hooks do not surface
  `hookSpecificOutput.additionalContext` to Claude. The hook emits *both* `additionalContext`
  and `systemMessage` (the field that does render for the user) so the notice reaches someone
  when either half of the plumbing works.
* **This repo's GitHub Releases stopped at v1.27.0** while tags climbed to v1.33.1 (measured
  live). `/releases/latest` returns whichever release object was created last, which is not
  the highest tag. The checker uses `/repos/OWNER/REPO/tags` and picks the max version tuple,
  so a stale Release list does not turn "up to date" into "not really up to date".
* **String version comparison is wrong** — `"1.10.0" < "1.9.0"` in Python string compare. The
  checker compares numeric tuples. Two-sided test cases in `selftest.py`.

**What actually ships:**

* `update_check.py` alongside `doctor.py` — one file, two modes (`--check` full and `--hook`
  local-only). Uses only the standard library. Never fails loud on a network error.
* `plugin/hooks/hooks.json` gains a `SessionStart: startup` entry that runs it in `--hook`.
* `doctor.py` calls `update_check.py --check` at the end of its normal run.
* `CHANGELOG.md` is now shipped inside the plugin subtree (as well as at the repo root) so
  the local-delta message can quote what changed after an auto-update.

**Belt-and-braces engineering** — every one traceable to a specific panel finding:

* **Atomic stamp writes** via `os.replace()`: concurrent Claude Code windows racing on the
  same file do not corrupt it. (All 6 channels flagged this.)
* **ETag + `If-None-Match`** on the tags request: 304 no body when nothing changed. Cuts
  bandwidth and preserves the unauthenticated rate-limit budget. (MIMO25PRO.)
* **Exponential backoff** on network failure: 1h → 2h → 4h → 8h → 16h. An air-gapped machine
  does not eat the 3-second HTTP timeout on every session start. (SPARK12CONT.)
* **Clock-skew guard**: a `last_check` in the future — dual-boot / VM resume — is treated as
  stale, not as "wait a week for it to happen". (SPARK12CONT.)
* **Time-based snooze**, `--snooze[-days N]` (default 7): a security release next week is not
  silenced by yesterday's snooze of the previous minor. (Panel refuted the version-based
  snooze 4/6.)
* **User-Agent carries no version**: with ~50 users, IP + timestamp + version is a fingerprint.
  The maintainer can add opt-in telemetry later if needed. (Panel: 4/6 said drop.)
* **Ecosystem-standard disable env vars honoured**: `MODEL_ORCH_UPDATE_CHECK=0`,
  `NO_UPDATE_NOTIFIER=1`, `CI=1` (any truthy). (GOOG37FLASH: this is the convention users
  already know.)
* **Method-4 (git clone in place)** gets `git pull` as its remediation command instead of
  "re-run the installer". (GOOG37FLASH.)
* **`--show-what-would-be-sent`** prints the exact URL + headers a check will send, without
  making the request. Auditable transparency.

**Test suite: 35/35 two-sided assertions** — every safety property has both a positive case
(X happens when expected) AND a negative case (X does NOT happen when not). One-sided safety
checks can only fail towards too little safety (see R57).

## 1.33.1 — 2026-08-20

**A safety rule that also removes a sanctioned capability is not the strict version of the policy
— it is a different policy.** 1.33.0 denied the Firecrawl and Playwright MCP servers *wholesale*,
reasoning that under `allow mcp(*)` a tool either server gained later would otherwise be
auto-allowed and could bill. The reasoning was sound; the rule was still wrong, because a wildcard
deny takes the **whole** server — a more specific allow cannot rescue one tool from it — and the
owner's policy is *scrape and map are allowed, the rest is not*.

The evidence was already in 1.33.0's own verification run and was misread as success: at step 19
the reviewer reached for `firecrawl_scrape` and got
`Permission denied … Matches user-configured deny rule` — a tool it was supposed to have. That
line was reported as proof the deny mechanism worked. It was, and it was also proof the deny was
aimed at the wrong target.

* **Firecrawl is denied by name again**, not by wildcard: `crawl`, `agent`, `extract`, `parse`,
  `search`, `interact*`, `monitor_*` and the five `research_*` tools. `scrape` (1 credit, the
  sanctioned last resort for a bot-protected page) and `map` (1 credit flat) stay reachable.
  The residual is stated rather than hidden: a Firecrawl tool added upstream is not on the list,
  so `mcp(*)` will allow it — bounded by the fact that every unbounded spender it ships today is.
* **Playwright is no longer denied at all.** Owner's call.
* **`AGY_START_SPACING` default 8 → 11 s.** The race is measured at ~4.1 s, so this is ~2.7×,
  and it is free in wall-clock: a channel runs 50–230 s.
* The self-test assertion is now **two-sided** — the expensive tools must be denied *and* the
  sanctioned ones must not be. The previous one-sided check could only fail in the direction of
  too little safety, which is exactly why it never objected to the over-reach.

If you installed 1.33.0, re-run `python patch_agy_permissions.py`. It only ever **adds** rules, so
the two wholesale denies it wrote will still be in your `settings.json`: remove those two lines by
hand, or `--revert` to the timestamped backup it made and then re-apply.

## 1.33.0 — 2026-08-20

**🔴 IF YOU USE THE `agy` CHANNEL, RUN ONE COMMAND AFTER UPDATING:**

```
python patch_agy_permissions.py
```

The rules it writes live in **your own** `~/.gemini/antigravity-cli/settings.json`, not in this
tree, so pulling a new version does not apply them. `upgrade.py` now detects a stale config and
prints this in a block you cannot miss; `--fix-agy` applies it for you. On a machine with no agy
installed nothing is printed and nothing is checked.

### The permission language, measured — because none of it is documented

`agy --help` covers flags; the vendor's reference page is slash-commands only. So the grammar was
read out of the store the product writes for itself (`~/.gemini/config/config.json`) and then
tested arm by arm against agy 1.1.16, each pair changing one variable.

There are **six rule kinds and no others** — `command()`, `mcp(server/tool)`, `read_file(path)`,
`write_file(path)`, `read_url(domain)`, `execute_url(domain)`. There is **no bare-tool-name rule**:
`run_command` and `RunCommand` match nothing in either list. The model is *capability*-based, so
"every tool" is a closed set of six rather than a race against someone else's tool names.

| what was tried | what happened |
|---|---|
| `allow mcp(*)` | ✅ every server, **including servers added later** |
| `deny mcp(srv/*)` + `allow mcp(srv/tool)` | **deny wins** — a server is all-or-nothing |
| `allow command(echo)` on `echo X` | ❌ soft-denied — `command()` is **exact-match**, not the prefix its own help string claims |
| `allow command(<exact line>)` | ✅ runs |
| …the same, plus `--sandbox` | ❌ soft-denied — **`--sandbox` cancels an allow** |
| `deny command(*)` (± `--sandbox`) | ✅ hard deny, **and the run finishes** |

### 🔴🔴 "Allow everything except deleting files" cannot be written

`*` is an all-token, not a glob. With `allow command(*)` + `deny command(*del*)` a canary file
**was deleted** and no deny fired; the control (no deny rule) deleted its canary too, so the test
was sound. And there is nothing else to deny instead — **agy has no file-deletion tool at all**:
none of its 60 tool configs deletes anything, and the `DeleteFileOrDirectory` symbols in the
binary belong to an IDE-facing gRPC service. Deletion is reachable **only through the shell**.

So the command capability has exactly two usable states, and only one of them closes that route:

* `allow command(*)` (and drop `--sandbox`) — an unrestricted shell, deletion included;
* **`deny command(*)`** — no shell, deletion impossible, and the run survives the refusal.

This release ships the second. **One side effect, stated plainly:** `settings.json` is
machine-wide, so this also stops shell commands in the **interactive agy TUI**. `--keep-shell`
skips exactly that rule and keeps the headless failure; `--revert` undoes everything; a timestamped
backup is written before any change.

### What else changed

* `patch_agy_permissions.py` replaces a 60-entry enumeration of another vendor's tool names with
  `allow mcp(*)` plus a short, reasoned deny list. Firecrawl (bills per page, no ceiling) and
  Playwright (drives a profile holding live logins) are denied **wholesale**, because under
  `mcp(*)` a tool added upstream would otherwise be auto-allowed. Free local fetchers cover both.
* New `--check` (exit 1 if stale, writes nothing) and `--keep-shell`. `--check` exits **0** when
  agy is not installed — a gate that cries wolf on a clean machine is how the class gets ignored.
* **`doctor.py` and the run-time preflight now derive the rule set from `patch_agy_permissions.py`
  itself.** They each used to carry a private copy of "correct" — *"some `mcp()` allow rule exists
  and `firecrawl_crawl` is denied"* — which a completely stale config satisfies. Both would have
  certified this release's own missing rules as green.
* `doctor.py`'s green line now says which of the two shell states the machine is in, instead of
  describing a rule set that is no longer shipped.

### Corrections to earlier releases

* **`--dangerously-skip-permissions` does not unlock Firecrawl.** Since 2026-07-31 our own docs
  gave that as the reason never to use it. Measured now: an `mcp()` deny **still wins** under that
  flag. A `command()` deny does **not**, which hands an unattended reviewer an unrestricted shell —
  so the ban stands, for a different and better reason.
* **R39's "allow-listing the shell headlessly is structurally impossible" was right, its reasoning
  was not.** `command(*)` *does* match — it was tested only in the ALLOW position under `--sandbox`,
  the one condition where an allow is cancelled regardless.

## 1.32.0 — 2026-08-19

**Three ways the Antigravity channel loses a whole run, and none of them is the model's fault.**
All three were measured this round on one brief through one code path, and the one that mattered
most had been recorded a round earlier as «transient and unexplained».

### 🔴🔴 Concurrent `agy` starts race on a shared tool cache; the loser's run is discarded

Two arms, one variable:

| arm | result |
|---|---|
| `agy31pro` **alone**, ×2 | ok — 207 s / 159 s, ~10 KB of review each |
| `agy31pro` **beside its two siblings**, ×2 | **one channel dies at 4.1 s, 0 output tokens, empty answer** |

2 of 6 concurrent launches died. The channel's own CLI log names it:

```
failed to write tool bulk_stealthy_fetch from server scrapling to tmp file:
  ...mcp/scrapling/bulk_stealthy_fetch.json.tmp:
  The process cannot access the file because it is being used by another process
-> building toolbox: tool "mcp_scrapling_open_session" advertises an invalid parameter schema
-> Print mode: run ended with error and no response
```

`agy` rewrites every MCP server's tool schemas into **one shared directory** at startup, through
`.tmp` files. On Windows a file open for writing cannot be opened by a second process, so of N
simultaneous starts one wins and the rest fall back to a path that needs a file nobody wrote.

🔴 **The victim moves** — `agy31pro` in one repetition, `agy36flash` in the next. That is why the
previous round called it transient: its replay ran the failing invocation **alone**, which is the
one condition under which this cannot happen. *Replaying the invocation is not replaying the run.*

Fixed by spacing agy launches (`AGY_START_SPACING`, default 8 s, `0` disables). It costs nothing
on the wall clock — a channel runs 50–200 s and the cache write is over in the first few, so the
slowest channel still sets the round's length. Verified: `being used by another process` appears
**0 times** in both solo logs, **1–2 times in every** unstaggered concurrent log, and **0 times**
in all three channels of a staggered run.

### 🔴🔴 An UNLISTED tool cancels the turn. An explicitly DENIED one does not.

Measured in three arms against the live CLI:

| the tool is… | what happens | cost |
|---|---|---|
| allowed | runs | — |
| **explicitly denied** | ordinary tool error, **the model recovers and finishes** | nothing |
| **in neither list** | `Print mode: soft-denying tool confirmation`, status CANCELED | **the whole run** |

So silence is the dangerous state, not refusal — the opposite of the intuition the allow-list was
built on. A real round died this way on `jina-mcp-server/search_web_deep`: 56 s, 8 searches and
3 898 output tokens discarded because the *server* had gained a tool nobody had written down. The
same shape, on the same server, is recorded in `patch_agy_permissions.py` from nineteen days
earlier and was fixed then by adding one name.

`mcp(<server>/*)` **is** honoured, and **deny still beats it** (measured — the CLI names the rule
it matched). So the free, local, read-only servers are now allowed by wildcard, and the tools a
wildcard pulls into reach are denied in the same change. `firecrawl` and `playwright` are
deliberately **not** wildcarded: one bills per page with no ceiling, the other drives a persistent
profile holding live logins.

🔴 Still open, and now diagnosable in one line instead of one round: the shell tool `RunCommand`
is *unlisted*, so a model that reaches for it loses the run — measured again this round at 48 s
and 2 840 tokens. Denying it in the shared settings would break interactive `agy` for its owner,
so the fix belongs in the project-scoped permission layer the CLI logs as
`ApplyProjectPermissionGrants`, which has not been probed yet.

### Per-channel CLI logs — the instrument that made both findings readable

`agy`'s default log path is `cli-<YYYYMMDD>_<HHMMSS>.log`, timestamped **to the second**, so a
panel's simultaneous children computed one path and shared it — and the failing channel's record
was the one overwritten. Each channel now gets its own via `--log-file`.

🔴 **The log reader excludes the lines that are in every log.** Reading the failing panel's log
for the first time, `You are not logged into Antigravity` repeated twelve times inside one second
looked exactly like the answer. It is in **89 of 89** logs on this machine, successes included, as
is `Agent "deep-researcher" not found`. Either would have made a confident root cause with a
perfect citation. Only a log that did *not* fail can tell them apart.

### The panel ledger is an event log now, not two sets

Fourth instance of one shape in one function, and three reviewers found the hole in 1.31.1
independently: `ADD → REMOVE → ADD` was unsayable, so re-admitting a channel forced deleting
history — 1.31.1's defect with the arrow reversed. Worse, its no-churn property was enforced **by
prose**: a human had to type a magic phrase into a removal reason. `PANEL_EVENTS` is an ordered,
append-only list and the two sets are folded out of it, so churn is a structural contradiction
(two ADDs with nothing between) rather than a sentence somebody must remember to write.

## 1.31.1 — 2026-08-19

**The review panel said the 1.31.0 ledger change was a rationalisation, and it was right.**

- 🔴🔴 **9 of 11 reviewers, independently: «yes, the author is rationalising».** 1.31.0 replaced a
  disjointness assertion on the two cheap-panel ledgers with a check on the CURRENT state. The
  sharpest form of the objection (grokbuild): the replacement *is also a normalisation rule in a
  safety check's clothing* — it says nothing about whether the ledger is coherent. One reviewer
  (mimo25pro) named the concrete undetectable scenario the brief had asked for and the author could
  not produce: **a channel entering both books through churn rather than through one deliberate
  trial**, which a current-state check cannot see because the end state is identical. Another
  (spark12cont) named the property in a phrase: what disjointness was really buying is **no silent
  churn**.
- **Both halves are asserted now, and neither stands in for the other.** (a) the end state — a name
  in both books really is out of the panel; (b) the trail — a removal that cancels an addition must
  explicitly name the addition it cancels. A deliberate trial can write that sentence; accidental
  churn cannot. This keeps 1.31.0's actual fix (an added-then-removed channel is recordable at all)
  without keeping its hole.
- The lesson is the round's own, turned on its author: deleting a safety check because the reason
  it fired is inconvenient is exactly what a panel exists to catch.

## 1.31.0 — 2026-08-19

**The round about a tool whose binary was missing, and an instrument that named the last frame.**
One channel lost two consecutive review rounds. The diagnostics reported two different causes. The
event streams say the same sentence in both, and it is neither of the two that were reported.

- 🔴🔴 **`agy`'s `grep_search` tool could not find `grep`, and whether it could depended on which
  shell launched Python.** The tool shells out to `grep`; Windows has none; Git for Windows ships
  one in `Git\usr\bin`, the single Git directory that is *not* on the ordinary PATH. Measured:
  from PowerShell `shutil.which("grep")` is `None`, from Git Bash it resolves — same machine, same
  code, opposite outcomes, no message either way. New `posix_tools_dir()` locates a directory
  containing `grep.exe` (env `POSIX_TOOLS_DIR` first, then the standard install locations) and
  `_agy_env()` **appends** it to the child's PATH. Append, not prepend: that directory also ships
  `find.exe` and `sort.exe`, which would otherwise shadow the Windows built-ins for everything else
  the child runs. Nothing machine-wide changes.
- 🔴🔴 **The permission denial was a symptom, and our warning prescribed a fix that could not
  work.** In one of the two lost rounds `grep_search` failed three times first; only then did the
  model fall back to a raw shell pipeline, and *that* was denied — and on this channel one denial
  discards the entire run. The warning reported the denial and sent the reader to
  `patch_agy_permissions.py`, which cannot install a missing binary. `errors` was a de-duplicated
  list with no tool name attached, and the summary read `errors[-1]`. New `error_seq` keeps
  `(tool, message)` in the order they happened: the incomplete-output warning now names the
  **first** error and its tool, the permission warning says "this was NOT the first failure" when
  something preceded it, and a missing binary is reported as a cause in its own right.
- **`doctor` gained an `agy grep_search` check that asks the hard question rather than the easy
  one.** `shutil.which("grep")` in the doctor's own environment would pass on a machine where the
  panel is about to fail, because the doctor may itself have been launched from Git Bash — this is
  not hypothetical, it is how the first probe written for this bug reached the wrong conclusion.
  The check reports OK only when `posix_tools_dir()` found a directory on disk, i.e. when
  resolution survives a *different* launcher, and warns explicitly when grep resolves only because
  the current shell happens to supply it. It then executes grep through the env the child will get,
  because a file path is not a capability.
- 🔴🔴 **A COMMENT WAS BEING SENT TO THE VENDOR AS A PARAMETER, AND IT KILLED A CHANNEL FOR A
  WHOLE ROUND.** `orglm52` returned in 0.1 s with
  `HTTP 400 ... provider: Unrecognized key: "order_reason"`. That key was added one release
  earlier as PROSE — a note explaining why the provider order is deliberately not price order —
  and it sat in `provider_route`, which is handed to OpenRouter verbatim as the `provider` block.
  The vendor was being asked to honour a comment. **Nothing caught it because the note was written
  AFTER that release's panel had already run**, so the only channel that could have failed never
  ran again until now. Underscore-prefixed keys are now stripped before the block is sent, the plan
  prints `(annotations not sent: …)`, and a test walks the registry for any `provider_route` key
  that is neither a documented OpenRouter parameter nor an underscore annotation. Fixed and
  verified live: the channel answers again in 11.7 s.
- **A canned cause contradicted its own numbers.** The empty-answer warning said "it discarded a
  run it had already done the work for — 0 output tokens over 0 tool calls", and advised against a
  retry. For a run that died before doing anything, that advice is exactly backwards. The sentence
  is now gated on the meter it describes, and the zero case gets its own text saying a retry IS
  worth one attempt.
- **A test ledger could not record a legitimate event — third instance in one function.** The
  cheap-panel anchor asserted that `ADDED_TO_CHEAP_SINCE` and `REMOVED_FROM_CHEAP_SINCE` are
  disjoint. A channel added as a timed trial and removed when the trial answered no is two real
  events; the check made them unsayable, and the only way to green it was to delete the addition.
  Replaced with the property it was standing in for: a name in both books must really be out of the
  panel now. The comments directly above it already record two earlier instances of the same shape.

## 1.30.0 — 2026-08-19

**The round about a flag that gates more than its name says.** A channel gained a fallback model,
and the fallback would never have fired — not because it was misconfigured, but because a
*different* documented setting silently suppressed it. Most of what follows comes from measuring
that rather than reading about it.

- 🔴🔴 **New registry field `fallback_models`, and the trap that makes it work.** OpenRouter's
  `models: [primary, fallback]` array retries a different MODEL when the first errors. Measured
  across three arms holding the provider pin constant, the primary genuinely failing in each: with
  `provider.allow_fallbacks: false` the error came straight back and **the fallback model was
  never tried**; with `true`, and with the flag omitted, the fallback answered. A channel
  declaring `fallback_models` while pinning `allow_fallbacks: false` therefore has a chain that
  cannot survive the failure it exists for — rate-limiting and downtime are runtime failures.
  `selftest` refuses the combination.
  - 🔴 **The first wording of this entry over-generalised, and a reviewer of this very release
    forced the correction.** It said `allow_fallbacks: false` "suppresses model-level fallback",
    full stop. A separate arm of the same probe refutes that: with the pin set to the *paid*
    model's providers only, `allow_fallbacks: false`, the free model was dropped at **routing**
    time and the paid one answered — so model fallback fires perfectly well with the flag off.
    What the flag governs is whether any further attempt happens after a **dispatched** request
    fails upstream. The disconfirming arm was in hand the whole time and went unreconciled.
- 🟢 **The plan prints the chain before the round, and the report names which model answered.**
  A vendor-chosen model substitution is the same event as the `--set` substitution that once ran a
  whole round on the wrong checkpoint, and the only reason that was ever noticed was a printed
  line.
- 🔴 **`glm-5.2` now leads with the free tier and falls back to paid.** Spelled out in the
  registry because the word "free" hides three things: the free variant is served by a single host
  at **4-bit** quantization (the paid pin uses 8-bit), its context is 256K rather than 1M, and a
  free tier's price *is* the training grant. It is also rate-limited on a shared pool — two probes
  minutes apart both got `429 … upstream_provider_shared_pool` — so the realistic steady state is
  frequent fallback to paid.
- 🟢 **The expensive OpenAI channel moved from GPT-5.6 Terra Pro to GPT-5.6 Sol Pro**, keeping
  every lock (off by default, explicit-only, separate spend acknowledgement). Terra's discount had
  lapsed — its pinned endpoint went from `$1/$6` to `$2/$12` with `discount: 0`. Its measured
  spend figures were **not** inherited onto the new model; they are labelled as another model's
  and as a floor, because the plan prints them and "measured" must not come to mean "measured on
  something else".
- 🟢 **`cost: "expensive"` now belongs to more than one channel.** It decides whether the plan
  prints a cost line at all, and it had been carried by a single channel while the one with the
  only measured runaway in this project's history was tagged the same word as a $0.10 channel.
- 🔴 **The plan explained every skipped channel except the ones that are simply off by default** —
  including the most expensive in the registry, which printed a bare `[skip]` with no reason.
  A reader could not tell "off by default" from "filtered out by your flags" from "broken".
- 🟢 **A test that asserted an exact source line now derives its expectation.** It went red on a
  rename, correctly, but its only available repair was to paste the new name in — after which it
  would have passed while asserting nothing. It now checks that *whatever* the registry marks
  explicit-only is absent from the published tree, and that no exclusion names a channel that no
  longer exists.
- 🔵 **Note for anyone reading a vendor catalogue:** the `/models/<slug>/endpoints` response no
  longer carries `supported_reasoning_efforts`, `default_reasoning_effort` or
  `supports_native_web_search`. The effort ladder still exists — on the per-model object at
  `/models` — so "the vendor removed it" is the wrong reading. A field missing from one endpoint
  is not a field missing from the API.

## 1.29.0 — 2026-08-19

**The round about a record that survives being interrupted.** 1.28.0 made the run's record
crash-proof by writing it twice; nine of the twelve reviewers of that change independently pointed
out that it had, in the same stroke, made a crash *invisible*. This release closes that, and the
things found while closing it.

- 🔴🔴 **A crash during the citation audit used to leave no `diagnostics.json` at all — a loud,
  unambiguous signal. Since 1.28.0 it left a complete-LOOKING record missing only the audit, and
  nothing said so.** The payload now carries `record_status.complete`, false on the early write,
  and `REPORT.md` renders a banner at the very top when it is false. "The audit found no
  citations" and "the audit never ran" must not render the same way.
- 🔴🔴 **The two writes were not atomic.** `open(path, "w")` truncates before it writes, so an
  interruption during the *second* pass could destroy the good record left by the first — a new
  way to lose the very thing the double write exists to protect. `diagnostics.json`, `REPORT.md`
  and `HANDOFF.md` now go through one `_atomic_write`: sibling temp file, `fsync`, `os.replace`.
- 🔴 **`HANDOFF.md` overstated its own guarantee.** It said it was built "from `os.listdir`,
  never from the run's own records" — in a sentence that also promised to list "files on disk that
  no channel record claims", which needs those records. Four reviewers refuted it from its own
  second half. The code always did the join; the prose now says so: the filesystem is
  authoritative for what exists, the records for what was attempted, and the disagreements
  between them are the manifest's most useful output.
- 🔴 **`write_handoff` was being passed `panel=` and `started=` and read neither.** `started` now
  separates files older than the run — a previous panel left in a reused `--out` folder — onto
  their own line, excluded from the read-cost total instead of billed to this round. `panel` now
  appears in the resume prompt, so a fresh context can tell an excluded voice from a failed one.
- **Answer files pin `newline="\n"`.** The same review is now the same bytes on every platform;
  Windows text mode used to expand every `\n`, which is why `bytes` and `answer_bytes` had to be
  documented as "expected to differ". They now agree, so a future divergence is evidence of a
  short write rather than a footnote.
- **The read-cost estimate keeps `bytes // 4` and now ships its measured error band.** Three
  reviewers called the divisor a 2–3× underestimate on Cyrillic and two demanded `bytes // 2`.
  Tokenising 17 real files with `tiktoken o200k_base` put the true ratio at **3.48–4.64 B/token**,
  with the two most Cyrillic-dense files at 4.11 and 4.25 — indistinguishable from English prose.
  Taking that advice would have doubled the estimate and deferred panels that fit.
- **The removals register could record a demotion but not a retirement.** It required that a
  channel removed from the cheap panel still exist in the registry, so the first genuinely
  deprecated vendor model would have failed the suite forever. A reason beginning `RETIRED` now
  relaxes that requirement, and a mirror check asserts a retired name really is gone.
- 🔴 **The Antigravity channel had been sending an argument value the CLI never accepted.** `agy`'s
  `--mode` enum is `accept-edits|plan`; we passed `default`, so every call printed
  `warning: unrecognized --mode value "default"` to stderr and exited 0. In this release's own
  review round that warning *was* one channel's entire 74-byte answer file. `--mode` is no longer
  passed at all, which is what "default" was trying to say. The self-test line that had locked the
  bad value for three releases is replaced: **a test that pins a vendor's argument value cannot
  tell "we chose this" from "the vendor rejects this".**
- Self-test: **617 checks**, including a new suite locking the above and one class-level check —
  `answer_bytes` is assigned in exactly one place in the file, so no dispatcher can forget a field
  it is not permitted to set.

## 1.28.0 — 2026-08-19

**The round about being readable. Every fix here started as a user reading an artifact this
harness wrote and drawing a conclusion the data did not support.**

- 🔴🔴 **`REPORT.md`'s model table listed channels that never ran.** The section headed
  *"Which model actually answered"* iterated the whole registry, so a cheap-panel run printed a
  row `| codex | GPT-5.4 | xhigh |` for a channel that was never launched — and the operator
  read it and asked why codex had been used. The "not run" verdict existed three screens below,
  in a different table, which is a footnote to a false headline rather than a correction. Both
  telemetry tables now contain only channels that ran; the rest are named once, in a sentence
  containing the words **NOT RUN in this round — no request was sent and no model answered**.
  *A table headed with a factual claim may only contain rows for which the claim is true.*

- 🔴🔴 **A channel that produced a 45 KB review reported no size, in six consecutive panels.**
  `bytes` was set independently inside eight dispatcher functions and the ninth — the Spark
  transport — never set it, so every report showed `| spark12cont | OK | 228 | … | - |` beside a
  full review and the run log printed no size line for it at all. The operator concluded the
  channel was broken. It was not. The count now comes from **the write itself**: after the answer
  file is written, the record gains `answer_file` and `answer_bytes` (`os.path.getsize`), which
  no future channel can forget. `bytes` is kept as the model's payload length, and the two are
  documented as different quantities — on Windows the file is larger by exactly its newline
  count. *A fact that N code paths each promise to record is a fact that will be missing from one.*

- 🔴🔴 **`HANDOFF.md`: what the round produced, and what reading it costs.** Built from
  `os.listdir(outdir)` — never from the run's own records, because a manifest derived from the
  records inherits whatever the records already got wrong. Lists every answer file with its size,
  an estimated token cost to read it, and whether it ends with the end marker; then the total;
  then files on disk that no channel record claims; then channels that ran and wrote nothing; then
  a ready-to-paste prompt for a fresh context. Written for a measured failure: one round left
  317 KB across three reviews — including its largest and most expensive — unopened, and reported
  a channel count that was wrong in both directions. The harness states the price and **does not
  decide**: it cannot see how full the caller's context is, and guessing would be asserting a fact
  it has no instrument for.

- 🔴🔴 **`diagnostics.json` and `REPORT.md` are written BEFORE the citation audit, then again
  with it.** A panel that spent $3.97 and wrote 17 answers left no machine-readable record at
  all: the process ended in the gap between the last cost line and the first line of the audit,
  after everything the record needed had already been computed. The only thing that failed was
  the writing, and it failed because the write came last. The audit is additionally wrapped in
  `except BaseException` — its docstring promised it never raises, and prose does not enforce
  anything. *A record only written when nothing goes wrong is a record of rounds that did not
  need one.*

- 🔴 **`reasoning_meter` was the fourth field in the two-counters family, and it was fixed one
  line below the third.** `usage` is the last tool round's object; the record's
  `reasoning_tokens` became a sum in 1.27.0 and this meter kept reading the last round — nine
  channels measured, disagreeing by up to 4.4× (23 793 vs 5 399). Worse than its predecessors
  because this field's declared job is to *prove* a depth knob moved, so it quietly grades a
  working knob as inert. Now reports the summed value with the last round's beside it, under
  names that say which is which. The single-response xAI call site deliberately does not sum,
  with a comment saying why: *a class fix applied where the class does not hold is its own defect.*

- 🔴 **Three diagnoses now read fields the harness had already captured.** An empty answer with
  `finish_reason=tool_calls` is named as **NO ANSWER TURN** — the model ended its turn asking for
  another tool call after the loop stopped granting them — instead of "the connection produced no
  content and gave no reason", which was printed over a record that carried the reason. An answer
  whose text begins with raw `<tool_call>` markup is named as such, instead of drawing a
  "short answer" note and a transport-corruption note that both described something else. And a
  channel graded FAILED *solely* because its end marker is not the last line, over ≥2 000 bytes
  with no other complaint, now reads **⚠ UNVERIFIED — text present, read it**; the `ok` gate is
  unchanged, but "do not parse it" had been printed over a 46 KB review with 11 live citations.

- 🔴 **`doctor.py --online` re-reads provider prices.** Two channels pinned a provider route with
  `allow_fallbacks: false` and a hard-coded order chosen four days earlier from live rates. The
  provider ordered *first* had gone from $0.41/M to $1.69/M — 4.2× — and was the only one of
  three carrying no discount while its siblings carried 60.1% and 10%. Both pins now list only
  discounted providers, cheapest first. The new check found the second channel by itself, within
  a minute of existing. *A registry entry that hard-codes a price ordering is a document
  asserting a mutable value, and it rots exactly like prose — silently, while reading as a
  measured decision.*

- **The OpenRouter ledger line no longer states an invariant it does not have.** It used to
  declare flatly that the two money meters agree whenever they happened to match; across four
  measured rounds they disagreed in three, once by $1.18. It now says they matched *this round*
  and names the largest measured gap.

- **`ordeepseekv4pro` moved from the cheap panel to standard** (it remains reachable, since
  standard includes cheap). It was the cheap panel's single most expensive member — $0.77 of one
  $3.97 round — and a "cheap panel" whose dearest member costs more than the next three together
  is a label that does not describe the thing. Two `role: code` voices remain in cheap.

- **The self-test gained a removals register.** Additions to the cheap panel had a named home
  where each must state why; removals had none, so the only way to record one was to delete a
  name from the anchor set — the exact edit the anchor exists to forbid. 599 checks, including a
  22-check suite locking everything above.

## 1.27.0 — 2026-08-17

**The failure the vendor states, not the one the canned text assumes — and the shipped registry
no longer carries the author-only rationed channel.**

- 🔴 **`finish_reason` is now read off the stream and recorded on every OpenAI-protocol
  channel.** It was on the final chunk of every round and discarded, so a provider that CUT an
  answer (a 32 KB review ending mid-heading with no end marker) was indistinguishable from a
  model that chose to stop. `finish_reason=length` beside a missing marker now names the cutter
  outright: the binding ceiling is the provider's, and raising `max_tokens` on our side cannot
  help. OpenRouter's `native_finish_reason` (the vendor's own spelling) rides beside it.
- 🔴 **The empty xAI answer now splits into its two real causes.** One warning text used to
  cover both shapes with a budget-exhausted diagnosis; a measured instance spent 5% of its
  budget and the printed cause still blamed the budget. "OUTPUT BUDGET EXHAUSTED" (tokens
  actually gone) and "VENDOR ENDED THE TURN MID-LOOP" (the server-side agentic runtime ended
  the turn with no message item, budget largely unspent — the 4th measured instance of that
  class across three vendors) are now distinct warnings with distinct fixes, and the vendor's
  `response_id` is recorded for post-mortem retrieval.
- 🔴 **`reasoning_tokens` was read from the LAST tool round while `reasoning_chars` beside it
  summed every round** — the same two-counters defect this project fixed twice on neighbouring
  fields (`usd`, then `cached_in_tokens`). A free-tier review printed reasoning_tokens=26 next
  to 4 948 chars of visible reasoning, and the 26 nearly bought an "inert knob" conclusion; a
  3-arm probe showed the knob moving (181 vs ~102 tokens). Summed now.
- **The rationed strategy channel is deleted from the shipped registry**
  (`PUBLISH_EXCLUDE_CHANNELS`). Its three lock rungs — off by default, named-by-name only,
  explicit spend acknowledgement — were each walked deliberately, and the only lock that
  survives a determined user is absence. Groups are pruned in the same build step; naming it
  now returns the registry's own unknown-channel error. (Honest limit: the model remains in
  the public catalogue and can be re-added by hand; this removes the default availability,
  not the knowledge.)
- **`doctor` now checks every key the registry's enabled channels need, not just the first
  channel's** — derived from the registry and the provider table, resolved through the same
  `_env_key` the harness uses, so the rotated-key divergence warning appears there too. It
  used to report the Spark key and stay silent about the one key a kit install actually has.
- The OpenRouter Grok twin is enabled on the author's machine too (both transports of one
  model): the direct xAI channel's mid-loop deaths all happened inside the vendor's
  server-side loop, and on this transport the harness drives the tool loop client-side.
- Self-test suites are world-aware: a shipped tree asserts the rationed channel's absence,
  the working copy asserts its locks.

## 1.26.0 — 2026-08-17

**Reviewers may now volunteer what nobody asked, and a rotated key can no longer hide behind a
stale shell.**

- **New: the UNASKED section, appended to every channel's system layer on every round** (not on
  `--ask`, which is a lookup). Reviewers are asked to end with anything important the brief did
  not ask about — a wrong assumption, a risk, a better alternative — items they would defend,
  with an explicit "nothing beyond the questions" allowed so the section cannot manufacture
  content. This was a brief-writing habit before; the R46 panel's hand-written version of the
  same question returned four findings that shipped the same round, which is the argument for
  making it structural.
- 🔴 **A key rotated with `setx` was masked by the stale process environment.** `_env_key`
  prefers the process copy (an inline override must keep working), so every already-running
  session kept sending the dead key and the failure wore the vendor's clothes — HTTP 429 on a
  key replaced minutes earlier. The helper now prints one warning per variable per run when
  the process copy and `HKCU\Environment` disagree, naming the fix (restart the shell). The
  two remaining inline registry readers (Spark's, Gemini's — the channel that hit this) were
  replaced with `_env_key`, so the warning covers every key.
- 4 new self-test checks (562 source / 526 kit).

## 1.25.0 — 2026-08-16

**Documents by reference for the command-line reviewers, and the round now surfaces the two
facts its own table used to bury: a reviewer that grounded nothing, and what the round really
cost the OpenRouter key.**

- **New: `--attach FILE` and `--attach-dir DIR`.** One document, two delivery modes decided by
  what a channel can physically reach: CLI channels (codex, agy, grok build) receive the
  **absolute path** and read it from disk themselves — so they can also consult surrounding
  material — while API channels receive files **inlined** and are told folders exist and are
  unreadable, rather than being left to imagine reading them. The read-only contract rides in
  the brief (the measured strong position) AND is mechanical per channel: codex runs in its
  read-only sandbox, agy behind its permission allowlist, grok build with read tools granted
  only in refs mode and no write tool in any mode. Attached files are secrets-scanned exactly
  like the brief — a planted key in an attachment refuses the round with no override — and
  folder scans print every skipped file by name. Verified by execution on all three CLI
  channels: each opened the attached file, quoted its contents verbatim, and read the
  supporting folder. 🔴 The plan prints the trade every run: refs mode TRUSTS the attachment,
  because a hostile document can steer a reviewer's read tools.
- 🔴 **A channel with working web access that cites nothing now says so.** A real round produced
  three reviews containing literally zero URLs — with search configured, result annotations
  attached, and a fetch tool offered — and the only trace was a `fetches: null` nobody could
  interpret. The first human theory was a broken internet. It was not: the models answered
  from the brief plus training data. Now: `fetches` distinguishes 0 (tool offered, unused) from
  null (not offered); a `ZERO WEB GROUNDING IN THE TEXT` note names the fact and what it means;
  and the run-summary depth field (`depth=`, `thinking_level=`) prints on the kinds that lacked
  it.
- **New: the OpenRouter KEY ledger as a cross-meter.** When any OpenRouter-billed channel runs,
  the harness reads `GET /api/v1/credits` before and after and prints the account's own delta
  beside the sum of per-response `usage.cost` fields, plus remaining credits. Two meters over
  one spend, because this project has already measured what one wrong meter does ($12.08
  printed as $0). Search fees bill outside `usage.cost`, so a small gap is normal and the line
  says so.
- **Measured on the live endpoint: `x-ai/grok-4.20` via OpenRouter does not reason AT ALL unless
  asked** — 0 reasoning tokens, sub-second answers, and wrong arithmetic on the control probe —
  and this kit's `orgrok420` reasoning budget is the switch that turns it on (973–1419 tokens,
  correct answers). All enabled forms land in one band: the model has one depth, so nothing
  deeper is being missed. Same shape confirmed on `ormimo25pro` (0 vs 1638). Both channel notes
  now carry the measurement instead of an inference.
- `environment_report` derives its CLI list from `CLI_RESOLVERS` — it was a frozen three-name
  tuple, blind to the grok binary one release after 1.24.1 fixed exactly this class in doctor
  and the preflight. `report.py` derives its Environment version rows the same way, and its
  depth column now renders `thinking_level` and budget-form knobs instead of `-`.
- 23 new self-test checks (refs mode end to end including the attachment secrets gate, the
  meters, the report columns).

## 1.24.1 — 2026-08-16

**1.24.0 turned the Grok Build channel on and shipped it broken for everyone except the author.
Asked "would a colleague's install actually work?", the answer was measured rather than assumed —
by pointing `GROK_BIN` at a path that does not exist — and it was no.**

- 🔴🔴 **The channel CRASHED instead of degrading.** A missing binary produced a raw
  `FileNotFoundError` in the operating system's own language, was reported as
  `(no stock diagnosis)`, and exited 1. `codex` and `kimi` have had a `except FileNotFoundError`
  guard for months returning `binary not found: <path>`; the fourth CLI kind was added without
  it. Now identical to its siblings — verified by running all of them with a bad path and
  comparing: one problem each, no crash, same wording.
- 🔴 **The advice named every `<CHANNEL>_BIN` variable except the one that would have helped.**
  The text listed `CODEX_BIN / AGY_BIN / HERMES_BIN`, so the single channel that failed was the
  single channel not offered a fix. The comment above that line had already predicted this —
  "repeating a frozen list here could only ever go stale" — and then froze a list. Derived from
  `CLI_BINARIES` now.
- 🔴🔴 **`doctor.py` checked two literal binaries and was blind to half the command-line
  channels.** It knew `codex` and `agy`; it did not know `hermes` (added six weeks earlier) or
  `grokcli`. So the one command a confused user would run reported a clean bill of health while
  a channel failed every round. Derived from `CLI_RESOLVERS`; it now reports four.
- **A first attempt at the fix printed the same diagnosis twice** — the error string and an extra
  `warnings` entry both matched the same pattern. Caught by comparing against `codex` side by
  side rather than by reading the output alone. A channel that reports a failure differently
  from its siblings is a reporting bug even when every fact in it is true.
- **Six new checks** make a fifth CLI impossible to half-wire: the two halves of the CLI registry
  must name the same kinds, every CLI kind in the channel registry must have a resolver, the
  missing-binary advice must name every variable, `doctor` must not go back to literals, and the
  grok channel must keep its guard. **534/534.**
- **Documentation.** `INSTALL.md` had no Grok Build section at all and `TROUBLESHOOTING.md` did
  not mention it once. Added: install and sign-in, the `GROK_BIN` fallback, why the three shipped
  flags must not be undone, a "command-line reviewer is not installed" entry, and the symptom
  that is hardest to recognise — a few hundred bytes of *planning* narration and
  `stopReason: cancelled`, which is a denied tool and not a lazy model.

## 1.24.0 — 2026-08-16

**A channel that "would not finish a long review" was being denied a tool, and three rounds of
explanation never looked at the event stream. Grok Build shipped disabled in 1.23.0 with a note
listing four experiments and concluding "the flags were never the cause". The flags were exactly
the cause. One run with `--output-format streaming-messages-json` produced the answer that four
arms of guessing had not.**

### The root cause, and the two theories that were wrong before it

- 🔴🔴 **`stopReason: cancelled` meant a DENIED TOOL, not a model that would not sit still.** The
  turn ends on a `tool_result` carrying `is_error: true` and the literal text
  *"User cancelled the execution for tool `web_fetch`"*. One denied tool discards the entire turn
  — the same failure the agy channel has documented since 2026-07-31, on a different binary and a
  different vendor. `--output-format json` shows only the final stopReason, which is why three
  rounds of theories all sounded plausible and none was checked.
- **Two refuted theories, recorded because they cost a round each.** The agent is literally named
  `grok-build-plan`, so plan mode was the obvious suspect: `--no-plan` changed nothing. The system
  prompt says "an interactive CLI tool that helps users with software engineering tasks" and
  "communicate concisely", so the persona was the next suspect: `--system-prompt-override` with a
  reviewer persona changed nothing (3 turns, still cancelled).
- **Denial 1 — the HOST, isolated with one variable.** Same mode, same tool, same prompt shape:
  `dontAsk` + a non-x.ai host → cancelled; `dontAsk` + `docs.x.ai` → `end_turn`. That is why a
  short smoke test passed in 1.23.0 while every review failed — the smoke test only ever fetched
  the vendor's own documentation, and reviewing means reading somebody else's.
- 🔴 **The grant is spelled differently from the allowlist, and the wrong spelling is silent.**
  `--allow WebFetch` grants it; `--allow web_fetch` is accepted and grants nothing; `--tools`
  requires the snake_case form. Same tool, two conventions, no error either way.
- 🔴🔴 **Denial 2 — `--tools` does not bound the MCP gateway.** After seventeen successful fetches
  the model called `search_tool` and then `use_tool`. The allowlist named three tools and the
  model still had two more, because those are not built-ins. They are now REMOVED with
  `--disallowed-tools` rather than granted: `--permission-mode auto` also completes, and is not
  used, because the CLI loads the user's MCP servers in headless mode with their credentials and
  the entire input to this channel is an untrusted brief.
- **Result:** the same 13.6 KB brief that produced 158 bytes now returns **31 178 bytes**,
  `end_turn`, marker on the last line, zero denied tools, 26 fetches, 14 turns, 433 s.

### A security claim from 1.23.0 that was false

- 🔴🔴 **`--cwd` bounds where the agent STARTS, not what `read_file` can OPEN.** 1.23.0 granted
  `read_file`/`list_dir`/`grep` with the comment that they were "harmless here only because
  `--cwd` is a neutral empty directory". Measured with exactly that cwd in force, `read_file`
  served a file out of `~/.grok/skills/`. Removed; the successful run above had them removed, so
  no capability was traded for the fix.

### A false positive our own zero-false-positive check could not have found

- 🔴 **`transport_damage()` could not tell a damaged answer from an answer ABOUT damage.** 1.23.0
  measured its false-positive rate as zero across 14 real answers — honestly, but none of those
  answers had any reason to write a broken bracket. The first review that did — Grok Build's first
  working output, reviewing the corruption bug itself — tripped it with 7 orphan closers, every
  one a backticked quotation of a damaged statutory citation. Fenced blocks and inline code spans
  are now stripped before counting. Validated both directions: the genuinely corrupt answer still
  reports exactly 35 missing, the review about corruption comes out clean, and the real 14-answer
  round still flags exactly 1.

### Settings

- **Page-fetch budget 8 → 11 per channel.** The note arguing for 8 was corrected rather than
  deleted: the runaway is bounded by BYTES, not CALLS — `FETCH_RUN_BUDGET` (1 000 000 cumulative
  per channel per review) and `FETCH_MAX_BYTES` (400 KB per page) already cap the worst case at
  about two and a half maximum-size pages regardless of the call count.
- **Default panel `standard` → `cheap`.** Offered and declined twice before; taken now. The plan
  printer is symmetric, so every cheap run prints `--panel standard would ALSO run: codex,
  kimik3, qwen38max, spark11` before anything is spent.
- **Personal-identifier gate default warn → off**, with `--warn-pii` to restore the itemised list
  and `--strict-pii` to refuse. Secrets are unchanged: a hard refusal, no override at any setting,
  verified by a negative control that still exits 3 with the identifier gate off. "Off" prints one
  summary line rather than nothing — a gate whose output is indistinguishable from a crashed gate
  is worse than no gate.
- **`goog36flash` / `goog37flash` `max_tokens` 60000 → 65536**, the vendor's declared
  `outputTokenLimit`. 🔴 The negative control fired on the way: `/v1beta/interactions` accepts
  `max_output_tokens: 99000000` with HTTP 200, so acceptance proves nothing about this endpoint.
  The field was confirmed live by the meter instead — output tracked the cap across two
  order-of-magnitude-apart values on both models.

### Tests

- **528/528.** Twelve checks went red on the panel change, every one against correct code: they
  derived the expected set from `enabled` alone rather than from `default_panel`. Two further
  distinctions are now explicit — `--only <group>` crosses panel boundaries because naming is
  explicit selection, and "default-off" in the non-resurrection invariant means `enabled: false`,
  not "outside the default panel".

## 1.23.0 — 2026-08-16

**A vendor was caught silently deleting characters from its own answers — 35 of them from one
26 KB legal review, turning `INA § 208(a)(2)(D)` into `INA § 208(a)2)(D)` — while every check
this harness had reported the review as healthy. That is the failure this whole project exists to
prevent, found in the one place nothing was looking: not whether the answer arrived, but whether
it arrived intact.**

### The corruption, and why nothing saw it

- 🔴🔴 **A channel's text can be wrong without anything being missing.** End marker present, byte
  count healthy, `ok` true, citation audit content — and 35 `(` gone. Five arms isolated the
  cause: no tools → clean; tools offered but the search never fired → clean; **search actually
  running and streaming → corrupt**; the same search unstreamed → clean, twice. The damage was
  already on the wire, proved by reconstructing the answer from raw SSE frames in a program
  sharing no code with the harness, and it sat exactly on a frame boundary
  (`'  INA 2'` | `'08(a)'` | `'2)(D)'`). So it is the vendor's stream framing during its citation
  post-processing, not our assembler.
- **Streaming is now a per-provider choice** and the affected channel runs unstreamed. Measured
  44.4 s and 42.3 s unstreamed against 53.9 s streamed, so nothing was traded for it.
- **`transport_damage()` runs on EVERY channel**, at the single point where each answer is written
  to disk — keyed on the data, not on a call site, so a channel added next month is covered
  without anyone remembering. Replayed over the real 14-answer round it flags exactly one answer:
  the corrupt one. **Zero false positives.**
- It is a NOTE, never a warning. `ok` is `not warn` everywhere in this codebase, so a warning
  would throw away a 26 KB usable review over 35 characters, and an unpaired bracket in prose
  would fail a perfectly good one. Same judgement the citation check already makes.

### Grounding evidence that was collected and never shown

- 🔴 **The "actually opened" column read one field while the record carried three.** Channels
  whose VENDOR does the opening store it in `vendor_opened`, so they printed `-`, which the
  paragraph underneath explained as "reports no tool telemetry, grounding unknown". In one real
  round that mislabelled the three channels where grounding was actually provable: 50 citations
  with 10 pages opened, 3 with 3, 13 with 2. They now print `N — VENDOR-STATED`, deliberately
  **not** summed with harness-fetched counts — testimony and evidence stay in different grades.
- **A new column shows the vendor's own citation count**, which disagreed with the URLs in the
  prose on seven channels in that round (49 annotations against 2 URLs on one of them). A wide
  gap is not misconduct; it means the model read sources and wrote about them without linking, so
  the citation audit has almost nothing to check.
- The report's TIER row printed the word you typed rather than the tier that ran, so a round
  launched with the retired `--tier strategic` was filed under a name that has meant nothing
  since the tiers were collapsed. It now prints both.

### Two channels

- **Grok 4.6 through xAI's own CLI, on a subscription** — session-authenticated, no API key, free
  at the margin like the other CLI channels. Ships at `xhigh`, the top of the ladder the vendor
  publishes for this model, proved from the meter rather than from the flag being accepted:
  `low` produced [828, 1697] reasoning tokens over two runs and `xhigh` [1918, 4089] — disjoint.
  An invented effort value is rejected locally by this CLI, which is not true of every CLI here.
  🔴 It reads `CLAUDE.md` and `AGENTS.md` from its working directory upward, so it is launched in
  a neutral directory; its machine-wide rules directory cannot be neutralised by any working
  directory and is therefore counted in the preflight on every run.
- **GLM 5.2 via OpenRouter**, pinned to three providers already proven reachable on this account.
  🔴 **The gateway renames the depth ladder.** The vendor's own documentation says the top rung is
  `max`; the gateway's catalogue calls the same rung `xhigh` and defaults a rung below it. Both
  are correct for their own surface, and a channel that copied the vendor's spelling would be
  sending an unknown value. `max_tokens` is the **minimum** across the pinned providers, not the
  best of them: they disagree, any of them may serve the call, and asking for more than the
  smallest allows is a 400 that would read as a channel failure.

### Tests

- The ladder check read only the nested spelling of `effort`, so it would have silently skipped
  every CLI channel that declares a ladder — passing green while covering nothing.
- The cheap-panel roster test equated "what the owner dictated" with "the current roster", so
  every legitimate addition went red and trained the reader to edit the expected value. Split
  into an anchor that may never shrink and a named additions list, each entry stating why.
- The vendor-concentration test asserted which panel escalates. Adding two channels correctly
  diluted the largest bloc below the threshold, and the pinned test called that a regression. Now
  derived from the actual seat share.

## 1.22.0 — 2026-08-15

**Depth stops being a choice: every channel runs at the maximum its own vendor accepts, in every
mode, and only the number of reviewers differs. Two channels were found below their ceiling, one
had been silently truncating itself for a whole round, and the most expensive channel in the
registry turned out to be startable by two vendor words nobody thought of as naming it.**

### Depth is maximal everywhere, and it is asserted rather than claimed

- 🔴🔴 **One tier.** `strategic` and `deep` are now aliases of `max` and still parse, so no
  stored command breaks; the plan prints which word it honoured. `quick` is still refused by
  name. The pair that survived from four differed, at the end, in a timeout and two multipliers —
  depth was already identical on Spark (`xhigh`; `max` returns 400, re-probed with an invented
  value as the negative control), on the Gemini CLI, on the direct Gemini API, and on the xAI
  model that refuses the field at every value and placement. **A knob whose range shrinks to a
  point every time the underlying values improve is not a knob.**
- **Each channel declares its own ceiling** — `supported_efforts` (highest first) for the
  OpenRouter models, `thinking_levels` for the direct Gemini ones — and the self-test asserts the
  configured value is the top of that list. A vendor adding a rung is now a red test rather than
  a silent shortfall.
- 🔴 **Two channels were below their ceiling.** One ran `high` where the catalogue offers
  `xhigh`. The other sent an explicit reasoning-token budget to a model that does not support
  token-budget reasoning at all, so the gateway converted it to roughly *medium* — on a model
  whose own default is *max*. That setting had been added to stop a real failure and it worked;
  what had changed underneath was a later measurement showing the effort ratio is a **ceiling,
  not a reservation**, which makes a generous `max_tokens` sufficient on its own.
- **`max_tokens` follows one rule now:** `min(the vendor's declared max_completion_tokens,
  131072)`. Raised on eight channels.
- 🔴 **A panel may never change depth** — asserted by resolving every panel and comparing every
  surviving channel's depth fields against the unfiltered plan.

### The empty answer that was really a truncation

- 🔴🔴 **`OUTPUT BUDGET EXHAUSTED BY REASONING`**, a new and specific diagnosis. On these
  protocols `max_tokens` bounds reasoning *and* answer together, so a hard brief can end with the
  trace having consumed the whole allowance and no answer written. Measured: 766 seconds, 60,002
  reasoning tokens against a 60,000 cap, zero bytes — reported at the time as *"the provider sent
  no error event and gave no reason"*, **while the reason sat three fields away in the same
  record**, and with stock advice ("lower the tier") that changed nothing on that channel. An
  equivalent diagnosis had existed on a sibling transport since 1.9: it was written for the
  channel that failed rather than for the class, so the identical failure met the generic
  sentence again nine days later.
- The detector reads the **last round's** usage, never the sum: output tokens accumulate across
  tool rounds and legitimately exceed a per-call ceiling, and a gate that fires on healthy runs
  teaches you to ignore the whole category.

### A group word no longer starts a channel that ships off

- 🔴🔴 **`explicit_only`.** A channel may declare that it runs only when something names *it*.
  `enabled: false` was not a lock: `--only` overrides it by design — that is the documented
  opt-in path — and `--only <group>` reached the most expensive channel in the registry through
  **two** different vendor words. The plan then described it as "named explicitly", which was
  false, because by that point the pipeline could no longer tell a group from a name. The gate is
  computed from the words **before any group is expanded**, and enforced at a single choke point
  so that adding a third selection path later cannot reopen it.
- 🔴 **The same rule now covers every default-off channel, and it closed a money bug.** Testing
  the phrasings a user actually types, «только грок» resolved to *two* channels — the direct
  vendor key and its OpenRouter twin, which ships disabled precisely because the direct key
  exists. Two bills for one voice. The registry already contained this argument, written about
  panels and never carried across to groups.
- Naming still works, in both flag and prose form, and that half is tested just as hard: **a lock
  nobody can open is an outage, not a safeguard.**

### Words people actually type

- **«запусти все» now selects the standard panel.** It named nothing before and died with "no
  channel matched" — the most natural way to ask for everything was the one sentence the router
  could not read. Safe where `full` was not: it cannot be mistaken for a depth word, and there is
  no depth axis left to confuse it with.
- **«включая» / «including» are instruction words now.** The sentence that authorises the opt-in
  channel by name was itself a hard route error until this release.
- A bare name with no instruction word still refuses rather than guessing between "only that one"
  and "that one as well" — two very different bills — but the refusal now lists all four modes
  instead of three.

## 1.21.1 — 2026-08-15

**The last three reviewers of the twelve landed after 1.21.0 was tagged, and two of them found
the same thing: a `--panel` that is accepted and then not applied. Both paths failed in the
expensive direction and printed nothing, which is the inverse of this project's own rule.**

- 🔴🔴 **`--panel X` against a registry with no `panels` was accepted and ignored.** argparse
  takes its `choices` from the registry and falls back to `None` when that file cannot be read,
  so the flag stayed spellable while `if reg.get("panels")` skipped every filter — a flag
  accepted, a narrowing not applied, and the round running EVERY enabled channel while looking
  restricted. Now a hard `RouteError`. **Two reviewers, independently.**

- 🔴🔴 **A near-miss panel word was silently dropped.** «дешовая панель без grok» — one
  transposed letter — matched no alias, so the word vanished, the channel exclusion was honoured
  normally, and the round ran the DEFAULT (expensive) panel with no message at all. A route that
  looks like it names a panel (the head noun `панель`/`panel`, or a token starting like one of
  the aliases) and matches none is now refused, listing the words that do work. The stems are
  derived from the alias table, so they cannot drift away from it. **Two reviewers.**

- **The vendor-concentration share is printed for every panel; only the 🔴 escalates.** The
  first draft printed the line only above 50%, so moving from `cheap` (google 6/11 = 55%) to
  `standard` (6/15 = 40%) made the warning DISAPPEAR while the same vendor still held three
  times the next bloc — and a warning that vanishes reads as "fixed".

- **`measured_usd` filled from the first real review, and the ceiling's arithmetic corrected
  twice.** The entry said "$2.00 sits 6–16× above a normal round" and then cited *probe* calls
  of $0.0002–$0.0006, against which $2.00 is 3000–10000×: a ratio quoted beside a denominator
  that does not produce it. The measured figures are now $0.1334 for a 93 KB review with four
  page fetches (15×) and $0.0256 for a small one (78×). The number survived both corrections and
  its justification was wrong both times — which is the argument for writing the arithmetic down
  rather than the number.

- `SKILL.md` no longer calls the cheap panel "sub-$1/M": Novita, one of the three allowed
  DeepSeek hosts, is $1.168/M.

- **Round outcome, for the record:** 11 of 12 channels returned a verified review;
  `mimo25pro` produced empty output with no error event from the provider. Cost reported by the
  vendors: **$0.2624** across the four channels that report one (grok420 $0.1029,
  ordeepseekv4pro $0.1334, orgemini37flash $0.0191, ornemotron3ultra $0.0070 — the "free"
  channel's price is its Exa searches). Eight channels report no price; that total is a floor.

## 1.21.0 — 2026-08-15

**Two axes instead of one. `--tier` has always answered "how deep does each reviewer go"; the new
`--panel` answers "who is in the room". A cheap panel of eleven voices costs cents; the standard
panel adds four more VENDORS, which is what it is really selling. Plus an eighth vendor family:
DeepSeek V4 Pro.**

- 🟢 **`--panel cheap|standard`.** Membership is declared per channel (`"panel": "cheap"`) and
  the ladder in a new `panels` object, so `standard` INCLUDES everything `cheap` has — «standard»
  has to mean *what normally runs*, and the default is bit-for-bit the behaviour that shipped
  before panels existed. Composes freely with `--tier`: `--panel cheap --tier deep` is few voices
  thinking hard. Russian route words work too (*«дешевая панель, без grok»*).

- 🔴🔴 **A panel FILTERS DOWN and never enables anything, and that is the whole design.** `--only`
  deliberately resurrects a channel the registry has `enabled: false` — the documented opt-in
  path. A panel must not, because `enabled` is exactly the field `package.py` flips per
  `distribution`. Implementing `cheap` as a **group** would have been a one-line config edit with
  no code at all, and it would have been wrong invisibly: `--only cheap` would have resurrected
  every direct-vendor channel in an install that has no such keys, and every OpenRouter twin in
  one that does — paying twice for a single voice. Same word, opposite semantics; they cannot
  share a mechanism. The suite now asserts the invariant against every panel × every default-off
  channel, and asserts the asymmetry with `--only` as a control.

- 🔴 **The plan counts VENDORS, not just channels.** This harness reaches one company through up
  to six transports — three Geminis via the Antigravity CLI, two via Google directly, one via
  OpenRouter. When those six agree, that is one opinion reported six times, and a channel count
  presents it as six. Every plan now prints the vendor tally of the resolved set and warns when
  one vendor holds half the seats. Measured on the shipped registry: `cheap` = 11 channels from
  **6** vendors, six of them Google; `standard` = 15 channels from **9**. What the cheap panel
  actually costs is vendor diversity, not depth.

- 🟢 **New channel `ordeepseekv4pro`** — DeepSeek V4 Pro (1.6T MoE, 49B active, 1M context) over
  OpenRouter, eighth vendor family, and the cheap panel's `role: code` seat. Live end-to-end:
  25.6 s, three generations, 3 pages fetched, marker present, **$0.025587** reported by the
  provider; it refuted a planted false claim by quoting the source.

- 🔴🔴 **The catalogue lists what EXISTS; the account decides what is REACHABLE.** That channel
  first shipped pinned to the first-party `deepseek` endpoint on a genuinely good argument —
  cheapest non-requantised, best uptime, the only cheap endpoint advertising implicit caching.
  Every word of it true, and the first live call returned `404 No endpoints available matching
  your guardrail restrictions and data policy`. Neither `/models` nor `/endpoints` reflects an
  account's privacy settings. All seven non-fp4 candidates were then probed: six answer, that one
  does not. Shipped pin is `only: ["baidu","streamlake","novita"]`, verified by four live calls
  (all served from inside the list) **plus a negative control in the same field**. General rule
  now written into the registry: a provider slug read from a catalogue is a hypothesis until one
  call comes back from it.

- 🔴 **`reasoning.effort` on the new channel is recorded as SENT-AND-UNPROVEN.** Six calls, one
  provider pinned so the arms differ by one variable, judged by the `reasoning_tokens` that come
  back: `high` = [205, 295, 274], `xhigh` = [245, 387, 464]. The means move 41% the expected way
  and the ranges OVERLAP, so by this project's own disjoint-ranges rule it is not established. A
  plausible mean is exactly what a decorative parameter also produces.

- 🟢 **`role` is no longer a decorative field.** Four channels declared it, `_decorate` copied it
  into the plan, and nothing read it — the same shape as two fields this project has already
  caught. It is printed now, and the omission only started to cost something in this release:
  `--panel cheap` drops `kimik3`, the sole `role: code` seat, and nothing said so. That is why
  the new channel carries `role: code`.

- 🔴 **`--only` and `--panel` join the flags that REFUSE when routing is unavailable.** The
  fallback path listed `--route`, `--skip` and `--set`; `--only` had been missing since the list
  was written. Every flag there means *run a different set of channels than the default*, so
  ignoring one does not degrade gracefully — it runs the set the user just excluded and reports
  only that routing is unavailable.

- Route-parsing details that are silent when wrong: a **negated** panel word
  (*«не используй дешевую панель»*) is refused rather than obeyed backwards — the first draft
  selected the cheap panel from that sentence; naming **two** panels is refused; filler left
  after a panel word (*«запусти на дешевой»*) resolves, while an **instruction** left with no
  channel behind it (*«дешевая панель, без грокк»*) is refused, because that is a misspelt
  channel name and swallowing it would silently include or exclude the wrong reviewer.

- `SKILL.md` §0.2 moved to `references/systems.md` to stay under the 5,000-token budget an
  auto-compaction re-attaches (4,899 after the move, from 4,905 before it).

### Applied the same day, from a twelve-reviewer panel on this diff

Run without Codex, Kimi, Qwen or the OpenAI channels, at the user's instruction. Every one of
these was a defect in the FIRST draft of this release, found by an outside reader and fixed
before it shipped.

- 🔴 **`--tier` on `routing.py` had no `choices` at all** — so `python routing.py --tier quick`
  was accepted silently and printed a plan resolved at the default, while `channels.json` said
  in as many words that *"`--tier quick` is now an argparse error naming the two that exist,
  because a silently-accepted dead tier is the decorative-knob defect this file keeps
  recording."* One sentence, two programs, verified against only the one it was written about.
  Both flags on both scripts now derive their choices from the registry. **Found by one reviewer
  alone**, and it is the sharpest finding of the round.

- 🔴 **`«A вместо B»` was refused as a negation.** The panel extractor tested one prefix for both
  NEG and SUBST markers, so *«стандартная панель вместо дешевой»* answered *"a panel cannot be
  negated"* — naming the word the human was discarding. Substitution now selects the panel
  BEFORE the marker, the same anaphora rule `--route` has always used for models, and the marker
  is cut out with the words it joined so the leftover is not a bare *«вместо»*. **Three
  reviewers independently.**

- 🔴 **A word that answers "how deep" must not select a panel.** `full`, `полная`, `полную`,
  `полной` were panel aliases; *«run a full analysis»* is asking for `--tier deep`. Measured
  before the fix: the route silently set `panel=standard`, swallowed the word, and then failed
  with *"'run a  analysis with grok' mentions grok420 but no instruction word"* — the wrong
  action taken and an error about something else. Removed; `full panel` and `полная панель`
  stay, because the noun disambiguates. **Two reviewers independently.**

- **Missing Russian case endings** (`дешевом`, `дешевые`, `дешевых`, `экономную`, `экономной`,
  `экономном`, `стандартном`, `стандартный`, `стандартные`) — a missing ending is silent in the
  expensive direction. **Four reviewers.** The registry note that said a missing ending "does
  not error, it falls through to `default_panel`" was itself wrong and is corrected: with a
  channel elsewhere in the sentence it runs the default panel silently, and with none it dies
  with an error about channels rather than about panels.

- **`order` added to the DeepSeek provider route**, after measurement: without it four samples
  went StreamLake ×3 / Baidu ×1 — OpenRouter load-balances inside `only` — and a provider swap
  *between tool rounds* throws away the prompt cache this harness depends on (21,357 of 28,837
  input tokens came back cached on the first real run). With `order`, Baidu 3/3, and 4/4 with
  the full shipped triple.

- **Rejected with proof, and the proof is a measurement, not an argument.** Two reviewers called
  the missing `allow_fallbacks: false` a BLOCKER — *"OpenRouter will silently fall back to
  unapproved providers"* — quoting the vendor's own documentation. That sentence is about
  `order`; `only` is a separate and harder field. Negative control in the same field:
  `only:["anthropic"]`, a provider that does not serve this model at all, returned `404 No
  allowed providers are available for the selected model` rather than answering from somewhere
  else. The flag is set anyway, because it costs nothing and the next reader will have the same
  doubt — but the *reason* it is set is now the probe, not the paragraph.
  Also rejected: *"the $2.00 ceiling does not stop a 7.4M-token runaway ($2.58)"* — $2.58 is
  above $2.00, which is when a STOP stops; *"the cheap panel is ~4 channels in the generated
  kit"* — it is 10, measured by running the built kit; *"the plan does not print data policy,
  web access, spend ceiling or tier effect"* — it prints all four.

- **Corrected:** the price band across the DeepSeek allow-list is **3.36×** ($1.168 / $0.348),
  not the 2.9× first written, which forgot that StreamLake is in the list. **One reviewer, by
  doing the division.**

- **Recorded, not fixed:** three transports of one Gemini model sit in the cheap panel, and two
  reviewers asked why they are not deduplicated. They are there on purpose — the transport
  comparison is the measurement that produced `goog37flash` — but the vendor tally now makes the
  concentration visible, which is the honest half of the answer.

## 1.20.0 — 2026-08-15

**A metered channel ran away and the harness reported the round as cheap. One review billed
$12.08 across eight tool rounds, produced no output at all, and exhausted the OpenRouter key's
monthly cap — which then killed four channels in the next round, including the free one. The
round summary printed `$0.9250`. This release makes money measurable, bounded, and separately
authorised.**

- 🔴🔴 **The per-channel cost was the LAST tool round, not the sum.** `usd` read
  `usage.get("cost")` while `in_tokens`/`out_tokens` were accumulated across rounds — two meters
  over one event, on adjacent lines, and the one spending money was the wrong one. Proven against
  the vendor's own generation log: `qwen38max` billed $0.0984 + $0.102 + $0.159 + $0.185 + $0.43
  across five generations and this harness reported **$0.4297**, the last one to four decimals.
  Now summed, with `usd_rounds` saying how many calls the figure covers.

- 🔴🔴 **A channel that failed reported no telemetry at all.** Both exception handlers returned
  `{channel, ok, error}` and dropped everything measured, so the channel that spent $12.08 before
  its ninth call met the key cap appeared under *"these channels report no price"*. A cost report
  is most wrong on the round that spent the most — the exact inversion that makes a panel look
  affordable. Partial tokens, dollars, fetches and seconds now survive the failure path, and the
  round total marks such channels with `*`.

- 🟢 **`spend_guard` in the registry — a STOP, never a depth cap.** A channel may declare
  `max_usd_per_review`; when the vendor's own returned cost meter crosses it the harness stops
  granting page fetches, tells the model to answer from what it has, drops `tools` and takes one
  final turn. Nothing about effort, reasoning budget or answer length changes. Set to `4.0` on
  `orgpt56terrapro`, whose good runs measured $1.76–$1.81.

- 🟢 **`--accept-spend <channel>|all`: selecting a channel and authorising its bill are two
  different acts.** The runaway was launched by an agent session whose `--only` enumerated fifteen
  channels — which satisfied "ask for it by name" without anyone choosing anything. A default-off
  channel that declares `requires_ack` now refuses to launch (exit 2) until the flag is passed,
  naming the measured price and the exact flag. `--dry-run` never needs it: seeing what a round
  would cost must not require agreeing to pay for it.

- 🟢 **The plan prints the price.** The money line used to key on `cost == "expensive"`, a word
  only `codex` carries, so the most expensive channel in the registry — tagged `metered`, the same
  word as a $0.10 channel — printed nothing at all. It now keys on the presence of `spend_guard`.
  `--only` also stopped resurrecting a default-off channel silently: the flag path now writes
  *"named it explicitly (overrides default-off)"* into the plan, which only the prose path did.

- 🟢 **Three new stock diagnoses, each for a failure that printed an empty "cause and fix" block
  in a real round.** (a) `Key limit exceeded` — ordered BEFORE the generic rate-limit pattern,
  because the generic advice ("route to another channel") is actively wrong when every OpenRouter
  channel shares the capped key, free ones included. (b) transport drops, keyed on the exception
  CLASS (`RemoteDisconnected`, `ConnectionReset`, errno `10054`) and never on the OS message,
  which Windows localises — the same failure arrived in English on one channel and in Russian on
  another in one round. (c) a turn that reasoned past its output budget and never emitted an
  answer.

- 🟢 **The forced-answer round has an exit.** After the harness removes `tools` and demands the
  answer, the loop now breaks unconditionally on the next turn. Previously nothing stopped a
  transport that echoed a tool call anyway from re-entering the same branch every iteration and
  re-sending the whole conversation each time — benign against a well-behaved vendor, which is
  not a property a spending stop should depend on.

- 🔴 **The self-test was making one real paid call, per run, to the most expensive channel.** The
  1.19.0 reachability check ran the real CLI with a real brief and no `--dry-run` against
  `orgpt56terrapro` — invisible for the same reason the overspend was: nothing printed a price, so
  a green line and a paid call looked identical. Now `--dry-run`, plus a check that reachability is
  proved without paying, plus the two-step contract (selected → refused → authorised).

**Applied the same day from an eight-channel review of this diff. Every item below was found by a
reviewer, verified against the code, and fixed before release; three were named independently by
more than one of them, which is the part worth trusting.**

- 🔴🔴 **The guard was aimed at the visible failure, not the expensive one** (3 of 8, independently).
  Over the same month the account's two largest spenders were the DEFAULT-ON channels — Kimi K3
  $70.70 (34.8%) and Qwen3.8 Max $44.30 (21.8%) — against the rationed channel's $21.90 (10.8%).
  Guarding only the rationed one left 56.6% of the bill unbounded, and a session that learns the
  gate refuses Terra Pro simply shifts the work to the next most expensive default. Both now carry
  `max_usd_per_review: 3.0` (~3x their worst observed round) and **no** `requires_ack`: an ack on
  a default-on channel would refuse every ordinary round, which is breakage, not rationing.

- 🔴 **The trigger now reserves headroom for the final call.** The arithmetic: $3.90 spent, one
  round crossing at $2.10, then a forced answer at $2.10 = **$8.10 against a $4.00 setting**. It
  now fires when `usd_tot + largest_round_so_far >= ceiling`, where the estimate is the biggest
  round THIS run actually billed — measured, never a price from a config file. It still cannot be
  exact, and the plan now says "a STOP, not a depth cap … enforced only while the vendor returns a
  cost meter" instead of promising a hard number.

- 🔴 **`requires_ack` is armed by the declaration alone**, not by "was it overridden". The first
  cut fired only when a default-off channel was re-selected, so anything flipping the channel on
  by default — including a user's own settings overlay, which no test covers — turned
  `requires_ack: true` into a decorative field.

- 🔴 **`max_usd_per_review: 0` now ARMS the guard.** `float(x) if x else None` read the strictest
  possible setting — "never spend anything here" — as no setting at all.

- 🔴 **`cached_in_tokens` had the same last-round bug as `usd`, two lines below it**, and survived
  because the round was framed as "fix the money field" rather than "find every field read from
  `usage` after a loop that overwrites `usage`". Summed now.

- 🟢 **A ceiling that cannot be enforced says so.** The whole mechanism depends on the vendor
  returning `usage.cost`; with no meter it was a dead switch, silently. There is no honest
  fallback — a token estimate needs a price table, which is what this design refuses to trust — so
  a declared ceiling with no meter behind it is now a WARNING that makes the channel a PROBLEM.
  The probe that missed this returned a cost on every round; it now has a costless arm.

- 🟢 The refusal now addresses automated sessions directly ("do not re-run with that flag unless a
  human named this channel"), because three reviewers pointed out that the message prints its own
  bypass and retry-on-error is exactly what agents do. This gate is hard against accident and soft
  against an agent; nothing mechanical can be otherwise when the caller owns the command line.

- 🟢 `usd_rounds` is printed beside the price — $4 over two generations and $4 over eight are
  different facts about whether the fetch loop is the problem.

- **Rejected, with proof.** "`--dry-run` is not exempt from the spend gate" — it is; the dry-run
  return precedes the gate and both a live run and a self-test check prove it. "The forced-answer
  branch sends a `tool` message with no preceding `assistant` message, so the vendor will 400" —
  the assistant message is appended inside that same branch, seventeen lines above. "A failed free
  channel is excluded from the billed-and-failed list by truthiness" — a channel that reported
  $0.00 did not spend money; the marker means what it says.

- 33 new self-test checks against a deliberately uncooperative fake transport. 355 total.

## 1.19.0 — 2026-08-14

**The plan-instead-of-review failure class on the CLI channel is root-caused, fixed at three
layers, and made mechanically detectable. Trigger: a 9-channel round on an 87K-char brief where
BOTH agy channels returned the IDE's implementation-plan artifact — "I am presenting the plan
here for your approval" — with the required end marker appended, so the marker gate graded runs
that did no work as OK.**

- 🔴🔴 **Root cause was self-inflicted: the harness passed `--mode plan` to the CLI.** The
  2026-07-31 measurement ("unvalidated, invisible in telemetry") remains true and had been read
  as "inert" — but invisible is not inert. A/B on the SAME 87K brief, one variable: `--mode
  plan` → plan artifact (4 template headings, 34 tool calls, marker under the plan); `--mode
  default` → the actual review (0 headings, 53 calls, 26 searches, 14 pages opened). The flag
  now passes `default`. A knob the meter cannot see can still act.

- 🟢 **`AGY_ENV_CONSTRAINT` — the harness appends two environment rules to every agy brief:**
  no shell commands (they are denied headlessly, and one denial discards the whole run), and no
  plans (nobody is present to approve one). Placement is load-bearing and was measured both
  ways the same day: the same words steered the model off `run_command` **2/2 in the brief and
  0/1 in the workspace persona alone** — agent.md is the weak position. The persona carries a
  copy as the second layer. This also fixes the R39 flake where `agy31pro` died 4/4 reaching
  for shell on briefs that need page-parsing.

- 🟢 **Plan-shape detector + one announced re-run.** `_agy_plan_shape()` fires on ≥2 of the
  five stable plan-template headings **rendered as line-start markdown headings** — not
  substrings. The substring first cut false-positived in production within the hour: a review
  brief that audits the detector names its headings, so a real review quoting them inline got
  re-run for nothing. Validated 11/11 on real outputs (2 plans caught; 9 real reviews
  untouched, including three that quote or discuss the trigger headings in prose). On
  detection `call_agy` re-runs once with a do-the-work escalation and keeps both transcripts;
  if the retry also fails to deliver, the channel is marked PROBLEM — "marker present" alone
  no longer grades a plan as a review. The retry's own zero-grounding warning is preserved
  (deliberately not a third call: the cost bound is one extra attempt, announced).

- 🟢 **Workspace `.agents/hooks.json` is the THIRD workspace mechanism tested negative.** An
  IDE agent tried to fix the shell denials by writing a PreToolUse auto-allow hook into the
  run workspace; a probe that FORCED a shell call still died on the denial with the hook file
  present. The docstring now records it so nobody re-adds one. (Also measured on the way:
  `command()` rules in the CLI's global settings are exact-string — wildcards are literal.)

- 🟡 **Diagnosis coverage: "END MARKER NOT ON LAST LINE" now matches the same known-failure
  entry as "END MARKER ABSENT"** — two spellings of one failure from two code paths, and the
  second was recorded with `likely_cause: null`, which left the console's "cause and fix for
  each" block EMPTY under its own header. Unmatched problems now print their recorded detail
  instead of nothing, and two new entries cover provider 5xx mid-stream and plan-instead-of-
  review.

## 1.18.0 — 2026-08-14

**Third transport for `gemini-3.7-flash` (`goog37flash` — direct Google Interactions API),
bringing 3.7-flash to parity with 3.6-flash which already had three transports. The reason
is not symmetry: it is that R38c's `orgemini37flash` empty-content class turned out to be a
stochastic vendor failure inside OpenRouter's fetch loop that no config value fixes.
the operator challenged the R38c "lower the cap" hypothesis by execution — R39 ran the sweep at
every integer cap 8→2 and the pattern is not a threshold. Direct-Google API sidesteps the
whole class by pushing retrieval to Google's side (no harness fetch loop → no accumulation).**

- 🟢 **`goog37flash`** — `gemini-3.7-flash` over `POST https://generativelanguage.googleapis.com/v1beta/interactions`
  on a personal `GEMINI_API_KEY`. `kind: "gemini"` dispatches through the same `call_gemini_direct`
  as `goog36flash`; `_registry_default` reads model, thinking_level, tools, and max_tokens from
  the new entry, so this is a pure registry addition — no code change, no new dispatch branch.
  **18 channels total, 14 enabled by default.** Distribution `local` (same as goog36flash: needs
  a Google account, kit users get `orgemini37flash` on the one OpenRouter key they already have).
  `groups.gemini` and `groups.direct` extended. Live-tested this round: 15 s, 846 chars, marker
  on last line, three `google_search` calls, three grounding-api-redirect citations recovered to
  publisher URLs by `citecheck.resolve_wrappers`.

- 🔴 **Two things verified live for `gemini-3.7-flash`, not copied from `goog36flash`.**
  (1) `thinking_levels: ["low", "medium", "high"]` — NOT `minimal`. Google's own docs list this
  model as the FIRST flash to drop `minimal`, and the endpoint returns `400 "'minimal' is not a
  supported thinking level for this model. Allowed values are: high, low, medium."` — a clean
  named-enum negative control. Recorded in the channel's own `_effort_ladder_has_no_minimal`.
  (2) Content still lives in `steps[N].content[0].text` where `steps[N].type == "model_output"`
  — the convenience field `output_text` may be empty even at 200 OK with output tokens billed.
  Same as goog36flash; `parse_gemini_steps` handles it correctly.

- 🔴🔴 **The `orgemini37flash` cap-reduction hypothesis is REFUTED by execution.** R38c proposed
  lowering `fetch_tool.max_calls` from 8 to 3-4 as a possible fix; the operator asked why the jump and
  suggested running incrementally instead. R39 did the sweep, one integer per arm, 8 down to 2:
  `EMPTY at cap 7, 6, AND 2`; `CONTENT at 8, 5, 4, 3`. Three EMPTY points interleaved with four
  CONTENT arms, and `cap=3` produced 863 chars ending in the marker with three fabricated URLs
  (all three returned 404) plus «N/A (unverified)» in the answer file. Prompt-token totals do
  not predict outcome either. **This is a stochastic vendor failure, not a config-tunable one.**
  The `_LIVE_MEASURED_EMPTY_OUTPUT_2026_08_14` note on the channel is now framed accordingly:
  the fix for the class is `goog37flash`, not a cap value.

- 🔴 **A visible empty answer is safer than confident prose with fabricated citations** — new
  rule saved in memory this round. The `cap=3` arm would be graded OK by the panel's own
  OK/PROBLEM triage (marker present, output non-empty), while every URL it cited was invented.
  If you have a choice between a knob that turns EMPTY into fabricated CONTENT, keep the
  failure loud.

**Cost of the R39 measurements:** ~$0.60 on the OpenRouter side (50%-off promo on 3.7-flash is
currently live: $0.1875/M in, $0.9375/M out). Direct Google and Antigravity are on other keys.

## 1.17.0 — 2026-08-14

**One config change (Terra Pro effort → `max` on the operator's instruction, deliberately UNTESTED)
and one live-measured failure recorded honestly (Gemini 3.7 Flash on OpenRouter returns empty
content after the fetch loop). No code changes.**

- 🟢 **`orgpt56terrapro.reasoning.effort`: `xhigh` → `max`.** the operator: «раз там есть Max, давай для
  orgpt56terrapro него сделаем. Но не тестируй, а то дорого.» `max` is in the endpoint's own
  `supported_reasoning_efforts` list read live from the catalogue, so it is documented rather
  than invented. 🔴 It has NEVER been sent from this project — the first real use of this channel
  is also the first execution of this parameter. Two consequences to expect, neither a bug: it
  could fail with a paid 400 (never auto-retry), and if accepted it will cost more than the
  $1.76–1.81 measured at `xhigh`. Rationale for matching codex at `xhigh` no longer applies: the
  channel is opt-in and reserved for strategic questions, so its ceiling IS the point.

- 🔴🔴 **`orgemini37flash` — live-run failure recorded, not fixed.** the operator asked for a live run
  because he could see no prior invocation in the OpenRouter logs. His instinct was right: the
  channel had never actually run. Two runs on the same brief with the fetch loop returned **0
  characters of content** with reasoning present and no error event — signature identical to the
  R29 grok420 and R36 kimik3 failures. A third run that made only 2 fetches produced 850 output
  tokens but **fabricated 4 URLs** (2 returned 404, 2 were MOVED). By the project's own logic
  (a stream that ends inside the vendor's agentic loop without emitting a message item) this is a
  vendor behaviour, not a harness config bug. **Third channel in a row with the same failure
  class** (kimik3 08-03, grok420 R36, orgemini37flash R38); the harness cannot fix any of them.
  Recorded in the channel's `_LIVE_MEASURED_EMPTY_OUTPUT_2026_08_14` field so the next round
  starts from measurement, and left `enabled: true` because disabling it or capping its fetch
  budget would both trade something and neither is justified by one working data point that also
  fabricated URLs.

- 🟡 **A scripted registry edit went to the wrong channel.** One of the diagnostic runs above
  used a scripted anchor that matched an earlier `"enabled": true` line and hit `kimik3.fetch_tool`
  instead of the intended `orgemini37flash.fetch_tool`. Caught by a semantic diff against a
  backup, restored to byte-identical. Third round in a row where a "find X and edit it" script
  needed a more unique anchor — the class fits [[one-subject-two-skills-rots]]: **any
  find-and-edit that lands somewhere else silently is a script that trusted the shape of the
  target instead of naming it.** Fixed in the workflow (semantic diff before commit is now the
  reflex), not in the code.

## 1.16.0 — 2026-08-14

**GPT-5.6 Terra Pro becomes the first OPT-IN channel — off unless you ask for it by name.
Making that true exposed two more defects in the router, both of the same class as the one
1.15.0 fixed: a name that stopped being unambiguous when a sibling arrived.**

- 🔴 **`orgpt56terrapro` is now `enabled: false` — strategic questions only.** the operator, after
  seeing the measured bill: «только для стратегических вопросов, а не всех подряд. И по
  дефолту отключена, только если явно скажут ее использовать.» Same rationing as `codex`,
  reached from the opposite direction: codex is slow and expensive per question, this one
  measured **$1.76–1.81 per review, ~7× kimik3**. A default panel run no longer includes it —
  which matters most for kit users, whose first run should not silently cost $1.80 more.
  🔴 `enabled: false` here means **default-off, never unreachable**: `--only orgpt56terrapro`,
  «только 5.6 Terra Pro» and «добавь терра-про» all still run it.

- 🔴 **Router bug: naming a default-off channel in prose selected nothing.** The route's
  only-branch could turn channels OFF but never ON, so «только 5.6 terra» removed the other
  twelve and left the named one disabled — `running 0 channel(s): NONE`, no error. The `--only`
  FLAG had always been right (`else: enabled = True`), so the two selection paths disagreed and
  only the prose one was wrong. Unreachable until a channel was off by default, which is why it
  survived this long.

- 🔴 **A bare `5.6` alias routed to the wrong model.** `5.6` meant `gpt-5.6-sol` on the codex
  channel — unambiguous until `openai/gpt-5.6-terra-pro` arrived. In «используй все модели и 5.6
  Terra Pro» the scanner consumed `terra pro` first, leaving `5.6` free to match codex, and the
  route refused with «mentions codex». The bare alias is removed; `5.6 sol`, `5.6-sol`, `sol`
  and `соль` are unambiguous and still work. **Third instance of one class in two releases**
  (after the `openai` alias and the `gemini` group): a version number, a vendor name or a family
  word is safe only until the thing it names gets a sibling.

- 🟢 **New route mode: ADD — "the default set PLUS this one".** «используй все модели и ещё 5.6
  Terra Pro», «а также», «добавь», «плюс», «вместе с», `and also`, `plus`, `add`. `ONLY` could
  not express it (it drops everything else) and without it the operator's own sentence was a hard route
  error. 🔴 The bare «и» is deliberately **not** a marker: it is the commonest word in Russian
  and would turn half of every sentence into a selection verb — the same over-matching that made
  a bare `5.6` route to the wrong model.

- 🟢 **Selftest 262 → 265 in the kit, 293 → 296 in the source.** New assertions: the opt-in
  channel stays off by default *and* stays reachable by name; both prose-selection forms; ADD
  keeps the default set; a plain negation still leaves the opt-in channel off. (The kit gains
  fewer than the source because several route cases are derived from the enabled set, which is
  one channel smaller here.)

## 1.15.0 — 2026-08-14

**A 17th channel: OpenAI's GPT-5.6 Terra Pro over OpenRouter, pinned to OpenAI's own
endpoint. Adding it exposed two defects that had nothing to do with it — a vendor alias that
was about to start lying, and a fetch budget that paid twice for the same page.**

- 🟢 **New channel `orgpt56terrapro` — `openai/gpt-5.6-terra-pro`, provider-pinned to
  `openai`.** The first OpenAI model on the OpenRouter transport, so the panel now reaches
  one vendor two ways (`codex` via the CLI, this via HTTP) — the comparison that isolates
  transport from model. Effort `xhigh` to match codex, `max_tokens` 120000 against a hard
  128000 ceiling.
  🔴 **The pin was proved by a negative control in the same field, not by a 200.**
  `only: ["openai"]` → the response's `provider` field says OpenAI, $0.001727;
  `only: ["azure"]` → says Azure, $0.00345 on the same token counts. The Azure arm costs
  exactly 2×, which is what the catalogue predicts ($2/$12 per M vs $1/$6) — so the pin
  steers rather than being accepted and ignored.
  🔴 **The `?endpoint=<UUID>` in an OpenRouter URL is still UI-only** (the round-36 trap).
  Resolved by reading the model page's embedded payload, where the endpoint object carries
  both `id` and `provider_slug`: `a775a298-…` → `openai`, variant `standard`. The same
  method re-confirmed round 36's `google-vertex/global` pin, which needed no correction.

- 🟢 **Native web search: measured, not inferred.** Same question with and without the
  plugin, deliberately dated past the model's 2026-02-16 cutoff. Without: 0 citations and a
  confidently wrong answer invented from weights. With: the right answer and a citation URL
  carrying `utm_source=openai` — positive proof the search was OpenAI's own rather than Exa
  wearing a native label. On a 2 744-character brief with three planted falsehoods the
  channel returned **4/4 correct verdicts**, every claim `[OPENED]` with verbatim quotes.

- 🔴 **New `openai` GROUP, created before the alias could lie.** `openai` was an alias on the
  `codex` channel — true while codex was the only OpenAI voice, false the moment a second
  one existed. That is exactly what happened to `gemini` on 2026-08-07, discovered only
  after «не используй gemini» had been silently dropping two of three channels. This time
  the group ships in the same commit that creates the condition: `--only openai` now means
  both OpenAI channels, `--only codex` still means the CLI.

- 🔴 **Fetch-budget bug: a `#fragment` is not a different page.** The page-fetch budget keyed
  its already-tried map on the raw URL string, so `…/provider-selection` and
  `…/provider-selection#base-slug-matching` counted as two fetches of one HTTP request — a
  fragment is resolved client-side and never reaches the server (RFC 3986). Found in the new
  channel's first live run: 2 of 8 budget slots spent re-fetching byte-identical pages, each
  wasted slot adding a tool round that re-sent a 400 KB page. The tell was that two counters
  disagreed — `fetched_by_us` said 6 (it normalises) while the log said 8 (it did not), and
  the one spending money was the naive one. New `_fetch_key()` keys on what the origin
  actually sees: fragment dropped, query string KEPT (`?page=2` is a different page), path
  case preserved. 8 new regression assertions, including the over-merge controls — telling a
  model «already tried» about a page it never received is the worse failure of the two.

- 📋 **This channel is the most expensive in the registry and the number is now written down.**
  Two consecutive live runs on an identical 2.7 KB brief billed **$1.7641 and $1.8071** —
  roughly 7× kimik3. Cause, identical both runs: the model fetches OpenRouter's entire model
  catalogue (399,995 chars, truncated at the 400 KB cap) to answer a question about one
  model, and every later tool round re-sends it. Deliberately **not** «fixed» by capping
  fetches or page size: both buy money with grounding, and grounding is why the channel earns
  its seat. Recorded in `channels.json` for a human to price.

## 1.14.0 — 2026-08-14

**Two panel-found bugs fixed. Grok gets more room to think per the operator's «focus on quality»;
agy stops warning about non-ASCII paths and transparently works around them. The employee
update path was verified end-to-end for anyone still asking «if I send them a link, will it
just update?»**

- 🟢 **`grok420.max_tokens` raised 60000 → 131072.** the operator: «Про Grok, фокус на качество,
  а значит токенов на размышление урезать не надо» — the fix cannot cap reasoning, only
  raise the combined ceiling. Probed against xAI's own `/v1/responses` first: it accepts
  `max_output_tokens: 200000` on this model without a 400, so 131072 sits well below any
  refused limit. `max_tokens` is a CEILING, not a reservation — a call that only needs 5K
  bills for 5K, so the raise is free on light briefs and prophylactic on heavy ones.
  🔴 **Honest scope of the fix.** This removes the ceiling as a potential future exhaust
  on very heavy reasoning briefs. It does NOT fix the AOS R29 failure class (2/2 срыва,
  8456 output tokens with 8453 reasoning + zero-byte text) — that call was NOWHERE NEAR
  the 60000 ceiling, so raising the ceiling cannot have caused or cured it. The R29
  pattern is a stream that ends inside the agentic loop without emitting a message item;
  the empty-text warning already reports it with rich diagnostics (status,
  incomplete_reason, output_item_kinds, reasoning/output ratio). Per the operator's standing rule
  «Never auto-retry a billable failure», that class is a `--only grok420` re-run when it
  happens, not a silent retry. Recorded in `channels.json` so nobody «restores» the old
  value or «adds» a retry.

- 🟢 **agy Cyrillic path — transparent %TEMP% workspace instead of a preflight warning.**
  agy corrupts non-ASCII path components in its own stream-json output
  («...\\???????????\\...»), which broke workspace-scoped agent discovery on Cyrillic
  paths. Fix in `_agy_once`: when the workdir contains non-ASCII, mirror it deterministically
  under `%TEMP%\\orch-agy-ws\\<basename>-<md5-of-original>` (retries reuse the same folder),
  run agy there, and leave the OUTFILE at the path the caller asked for — Python writes
  the outfile after agy is done and handles non-ASCII paths correctly. Preflight still
  names the situation so the `%TEMP%` line in agy's own log is not surprising. Previously
  measured on AOS R29: the runner did this by hand («беру ASCII-путь») because no
  automatic workaround existed. Fresh-install case: a user with a Cyrillic Windows
  username can now run reviews from their `~/reviews/` folder without editing anything.

- 🟢 **Employee update path verified end-to-end.** Simulated a v1.13.0 install with (a)
  an existing overlay entry (`orgemini37flash.max_tokens: 40000`) and (b) a channels.json
  edit (`goog36flash.enabled: true`), then ran `upgrade.py` against the R37 dev tree.
  Result: overlay preserved untouched; channels.json edit MIGRATED into the overlay
  automatically; new tree files copied; backup made at `.bak.<timestamp>`; doctor ran
  automatically. All three supported update paths (plugin auto-update, installer script,
  `upgrade.py`) preserve overlay settings by design; the plugin path additionally relies
  on Claude Code's 14-day cache window to recover pre-1.7.0 channels.json edits. No R37
  code change to the update path — this is a verification that R30's design still works
  under a new release.

## 1.13.0 — 2026-08-13

**Two new Gemini 3.7 Flash reviewers. Plus `provider_route` — OpenRouter endpoint pinning that
is now a first-class registry field, so the plan can honestly print what a run will cost when a
model is served by more than one endpoint at prices differing 18x.**

- 🟢 **`agy37flash` — Gemini 3.7 Flash via the Antigravity subscription CLI.** Third `agy`-kind
  channel joining `agy31pro` (deep) and `agy36flash` (fast). Base slug `gemini-3.7-flash` +
  `--effort` in `{low, medium, high}`, same shape as `agy36flash`. Verified against `agy models`
  before the entry was written. Groups updated: `agy` grew from 2 to 3 channels (label
  «both» → «all»), `gemini` grew from 4 to 6.

- 🟢 **`orgemini37flash` — Gemini 3.7 Flash via OpenRouter, pinned to `google-vertex/global`.**
  4x cheaper than `orgemini36flash` on both directions ($0.375/M in, $1.875/M out at the pinned
  endpoint vs $1.50/$7.50 for 3.6-flash) — a newer flash undercutting the older one is unusual
  and worth noticing before anyone «corrects» the price to match neighbours. Effort ladder
  {high, medium, low} — NO `minimal` (unlike 3.6-flash). Pinned to `google-vertex/global` on
  the operator's explicit request (endpoint UUID `e28074f6-26f6-4ba2-adf9-0bb807bc970e` in his URL).

- 🟢 **New registry field: `provider_route`.** A dict passed verbatim to OpenRouter's
  `provider` request block (openrouter.ai/docs/features/provider-routing). Accepts
  `only`/`order`/`ignore`/`allow_fallbacks`/`sort`, all documented there. WITHOUT the pin,
  `google/gemini-3.7-flash` routes across SIX endpoints — three Vertex Global tiers (flex
  $0.1875/$0.9375, normal $0.375/$1.875, priority $0.675/$3.375) and three AI Studio tiers
  ($0.375/$1.875 flex up to $1.35/$6.75 priority). Cheapest is 18x under most expensive; a plan
  cannot honestly print what a run will cost when the endpoint is chosen at request time. The
  pin also fixes the DATA POLICY: Vertex Global's terms say prompts are not used for model
  training, AI Studio's are different. A channel whose data policy varies per-request is a
  policy nobody agreed to.

- 🟢 **The provider_route from the registry must REACH the call, not stay on the printout.**
  Selftest grew to 280 checks (was 279) with a mechanical assertion that any launched channel
  carrying `provider_route` receives it in its call kwargs. Same discipline as `web.enabled`
  and `fetch_tool.max_calls` — a knob that resolves and prints but never reaches the function
  is the defect class this repository has recorded seven times (channels.spark.model, the four
  dispatch literals, telemetry keyed on old names, `tools` on goog36flash, the renamed flag
  that missed its own reporter, ...).

- 🟢 **Selftest crash-isolation now DERIVES the expected-to-crash set from the registry.** A
  hard-coded `("agy31pro", "agy36flash", "ghost")` list broke the moment `agy37flash` was
  added, because the test wanted to prove ALL non-crashing channels survive when ONE (any
  agy-kind) crashes — but a hand-maintained list is not «all agy channels», it is
  «two agy channels». Now derived: `{kind == "agy"} ∪ {"ghost"}`. This is a corner of the
  same defect class the surrounding paragraph WARNS about — the exact silent-drop pattern the
  test was written to catch. Fixed inline; the comment now names the round.

**About the `provider_route` field, for anyone building a similar registry:** the OpenAI-shaped
channel already had a top-level `provider` field (openrouter | mimo | xai — WHICH vendor). The
new field's name is DELIBERATELY different to avoid the two-things-one-name defect this project
keeps measuring elsewhere. `provider` remains the dispatch key for OAI_PROVIDERS; `provider_route`
is what OpenRouter's own body sees as `body["provider"]`. Read them together in the source and
the names carry the distinction; read them apart and confusion is a nightmare.

## 1.12.0 — 2026-08-08

**The R33 dup-key defense now follows the plugin, not the machine — and the CI that used to
run only `selftest.py` now runs the pre-commit guards too, on tag pushes as well as branches.**

Round-35 panel (nine channels: spark11, spark12cont, codex, kimik3, mimo25pro, grok420,
goog36flash, qwen38max, ornemotron3ultra) reviewed a six-item improvement plan and rejected
three claims the maintainer had asserted from memory. The changes in this release are what
survived the panel, applied verbatim.

- 🟢 **Plugin hook: `<plugin>/hooks/hooks.json` runs the dup-key guard on `Edit|Write|MultiEdit`.**
  Scope is "sessions where THIS plugin is enabled", not machine-wide. Written after five
  independent reviewers (goog36flash, kimik3, mimo25pro, grok420, codex) named machine-wide
  hazards the original proposal did not see: the hook cannot un-write the file (per
  code.claude.com/docs/en/hooks: "PostToolUse hooks fire after a tool has already executed
  successfully"), a bug in the checker would block JSON edits in every unrelated project on
  the host, Python subprocess spawn on Windows is 200-500 ms per edit, and the machine-wide
  `~/.claude/settings.json` is itself a load-bearing config an over-broad hook could lock the
  user out of. The plugin route was surfaced by spark12cont quoting the docs verbatim
  ("Plugin `hooks/hooks.json` | When plugin is enabled") and is documented at
  code.claude.com/docs/en/hooks. The wrapper (`tools/check_json_dup_keys_hook.py`) is
  fail-open on every internal error path — a safety-gate false positive is worse than a miss.

- 🟢 **Wrapper contract, tested from every angle.** Selftest grew by 14 assertions
  (260 → 274 in source; kit stays at 243 because dev-tooling suite skips in kit layout).
  New tests cover: empty stdin, non-Edit tools ignored, non-JSON extensions ignored, missing
  files fail open, clean JSON stays silent (the CLEAN path must not produce noise or the
  guard trains its own removal), real dup-key produces exit 2 with `stderr` naming the
  duplicate, `plugin-hooks.json` parses and its matcher covers Edit|Write, and the referenced
  path uses `${CLAUDE_PLUGIN_ROOT}` (plugin-relative, not machine-wide).

- 🟢 **CI now runs the pre-commit guards, not just `selftest.py`.** The existing
  `.github/workflows/selftest.yml` already ran on `push[main] + pull_request + workflow_dispatch`
  across matrix `ubuntu/macos/windows × Python 3.9/3.13` — but ran only `selftest.py`, so a
  fork contributor's PR was unguarded against the R34 pre-commit checks. This release adds a
  step (`pre-commit run --all-files --show-diff-on-failure`) after selftest and adds
  `tags: ['v*']` to the trigger set. The tag-triggered run is the one that matters at release
  time; the branch runs catch drift earlier.

- 🟢 **`.git/hooks/pre-commit` installation gets a soft check.** Codex's round-35 finding:
  the guards exist in the tree but `pre-commit install` is manual, so on a fresh clone
  `.git/hooks/pre-commit` may be absent (or the git-sample), and the entire pre-commit chain
  runs zero times without anyone noticing. Selftest now checks: **if** a `pre-commit` hook file
  exists in `.git/hooks/`, it references the pre-commit framework. It does **not** fail on
  absence — a fresh clone is legitimately in that state before the first `pre-commit install`,
  and CI installs pre-commit explicitly per-run so the check would be counterproductive there.

- 🟢 **`kit/CHANGELOG.md` warns loudly when VERSION doesn't match the top entry** (unchanged
  behaviour, mentioned because it is what caught the maintainer twice this session).

**Round-35 panel corrections the maintainer's plan absorbed** (nothing to install; each is a
one-line rule to remember):

- 🔴 `restic` **IS** on winget (`winget install --exact --id restic.restic --scope Machine`
  produces `restic 0.19.1`) — spark12cont refuted "install is manual" from a primary source.
- 🔴 CI was **not** absent before this release; the workflow existed and ran on push+PR. The
  gap was pre-commit and tag pushes, not CI itself. Original claim was fabricated from memory.
- 🔴 A false claim planted in the review brief ("R34 saved ~500 KB of duplicated JSON keys")
  was refuted 5/5 by every substantive channel. Codex verified from the commit diff:
  `channels.json` went 99.5 KB → 99.6 KB across the R33 fix (grew 0.1 KB, did not shrink),
  and the R34 release added a detector — did not remove any keys itself.

## 1.11.0 — 2026-08-08

**The R33-class of bug (silent duplicate JSON keys) is now caught before commit — for you too.
And running the same guard on the maintainer's own tree turned up one real bug in `echocheck.py`
that had been sitting undiscovered.**

- 🟢 **Pre-commit guardrails at the kit root.** `.pre-commit-config.yaml` and `ruff.toml` are now
  part of the kit, along with a small `tools/check_json_dup_keys.py` helper. Install once
  (`pip install pre-commit && pre-commit install` from the kit root) and every `git commit` in
  your fork checks: JSON syntax, YAML syntax, TOML syntax, no merge-conflict markers, no
  accidentally-added large files, Python via `ruff-check` (undefined names, unused imports,
  unused variables), and — this is the one the round-33 review found — duplicate keys inside
  any JSON object at any nesting level.

- 🟢 **Why the custom dup-key hook exists.** `json.loads` (and every other mainstream JSON
  parser) collapses duplicate keys silently and returns the LAST one. The standard `check-json`
  hook from `pre-commit-hooks` uses the same parser, so it inherits the same blind spot
  (pre-commit-hooks issue #554, open since 2019). The custom hook uses Python's
  `object_pairs_hook` to see every key BEFORE the parser collapses them; it exits 1 with
  `duplicate key '<name>'` and blocks the commit. This is exactly the failure the round-33 panel
  caught by hand in `channels.json.hints` — the tool now catches it mechanically.

- 🟢 **One real bug found in `echocheck.py` by turning ruff on the maintainer's own tree.**
  The main-loop call site was passing `lo_out, hi_out` (undefined names in that scope) instead
  of `lo_o, hi_o` (the local variables actually populated on the two lines above). Would have
  crashed at runtime whenever the code path exercising the output-token fallback fired. Ruff's
  `F821` (undefined name) caught it in the first run.

- 🟢 **Four minor cleanups in the same sweep**, none behavioural: an unused `os` import in
  `report.py`, an unused `probe_url` import in `orchestrate.py`, an unused local `seen` in
  `orchestrate.py`, one f-string without placeholders in `probe_firecrawl_tools.py`, and a
  semicolon-joined statement in `selftest.py`. All caught by the same ruff config that now ships
  with the kit.

- 🟢 **Ruff config is minimal on purpose.** Only rule categories `E` (syntax-shaped defects) and
  `F` (undefined/unused) are enabled. `E741` (ambiguous `l`/`O`/`I` names) and `E501` (long
  lines) are explicitly ignored — the first is an established loop-variable pattern across the
  codebase, and the second matters less than block-quote comments reading well. The philosophy
  is round-33's rule: a safety-gate false positive teaches you to switch the guard off, so a
  hook that fires on style trains distrust.

- 🟢 **Selftest grew by 10 assertions** (250 → 260). The new suite `suite_dev_tooling` runs
  the dup-key script against a planted fixture, against the real `channels.json` (regression
  sentinel: R33 stays fixed), and against a nested-inside-array case; it also verifies that
  `.pre-commit-config.yaml` still names the custom hook by its id and that `ruff.toml` still
  ignores `E741`. If a future edit removes any of these guards, selftest goes red.

## 1.10.0 — 2026-08-08

**The MCP fallback hint stopped naming servers; the standing-note wrapper stopped suppressing.**
Both changes were requested by the maintainer, and the panel review that landed the same day found
five defects in the first cut. All five are closed here.

- 🟢 **One MCP fallback hint, three CLI channels.** Codex and both Antigravity channels used to
  carry per-CLI paragraphs naming specific MCP servers by name. A tool a channel does not have is
  worse than silence: the model reports the missing tool as an error, and by an earlier round that
  was the leading failure mode. The merged hint names ZERO MCP servers by design — a channel finds
  what its own tool discovery mechanism finds, and the hint's job is only to say "try what you have
  and don't shell out." `channels.json.hints.mcp_fallback` is now the single source; the two old
  keys are gone. History lives beside it as archaeology, so nothing about the reasoning is lost.

- 🟢 **The standing-note wrapper became memory + anti-bias, not suppression.** On the two channels
  whose tier permits training use, the system prompt is part of the licensable payload. What used
  to be *"Do not mention this note in your answer and do not let it affect any finding"* now reads
  *"Remember this and keep it in mind. Do NOT let it affect any finding in the review below; it is
  background context, not part of the material under review, and no answer to this note is
  required."* The change addresses an earlier finding that a suppression instruction was travelling
  into the training corpus welded to a name; the anti-bias fence — which the first draft of this
  release had dropped — is now back too, because dropping it created immediate review contamination
  on the very channels the reword was for.

The panel review of the first cut found five things worth fixing before this release:

1. 🔴🔴 **Duplicate keys in `channels.json.hints`.** The edit that added new archaeology notes
   left the OLD copies in place. `json.loads` collapses to the later key, so the new notes were
   silently overwritten by the old ones. One reviewer verified live. Deleted the duplicates.
   Rule for the file: after any edit that adds a JSON key, grep the key name once — a duplicate is
   invisible to the eye and to the parser's exit code.

2. 🔴 **The merged hint self-contradicted its own rule.** It named three specific fetcher endpoints
   in a DON'T-USE paragraph while the whole point of the merger was not to name specific tools.
   Even a negative constraint gives the model vocabulary to reason about the named tool. Fixed to
   describe the CLASS: *"billing-heavy fetchers — anything that bills per page or per token with
   no ceiling. If your session offers a whole-site crawler or an unbounded page-extraction agent,
   treat those as the meter, whatever they are named."*

3. 🔴 **The hint's shell paragraph was written for one machine and shipped absolute.** *"On this
   machine the shell path is either denied by policy or unable to spawn"* is a fact about one
   setup, wrong when someone else installs the kit. Made conditional: *"unless you have already
   verified the shell works in this environment. On many CLIs the shell path is either denied by
   policy or unable to spawn."*

4. 🔴 **The first cut of the wrapper reword dropped the anti-bias line entirely.** One reviewer
   found it with primary-source verification of the vendor's own docs (Contributor tier IS
   training-in-exchange-for-discount; system text IS a "behavior instruction") and called it
   "immediate review contamination." Restored, now beside the memory framing. Both are
   load-bearing and neither can be traded for the other; they serve different receivers on
   different timescales.

5. 🔴 **A self-defect caught mid-edit.** A content-filter marker in the transcript-secrets hook
   would have silently passed real private keys through as "fixtures" because the upstream regex
   captures only the header line, not the key body. Removed. (This hook is not shipped in the
   kit; noted here because it belongs to the same round.)

Selftest went from 242 to 250 checks, all green. Panel of ten channels adjudicated seven
substantive reviews. Corroboration was strong on the shape of the corrections rather than the
scope: five reviewers named an unrelated hook defect (basename versus canonical path) independently,
one channel verified the JSON duplicates by running `json.loads` on the file itself.

## 1.9.1 — 2026-08-08

**The README promised a block the code does not perform.** Found by a reviewer of 1.9.0, verified
against the published file, fixed, and now guarded mechanically.

- 🔴🔴 **`README.md` described the personal-data gate as refusing by default and needing a flag to
  override. `PRIVACY.md` and the code say the opposite: found, itemised, reported — and SENT.** The
  policy was inverted on 2026-08-07; `PRIVACY.md` was rewritten and the front page was not, so the
  published README overstated what the tool protects. Corrected, and it now also states the thing
  that had never been written down anywhere: **names and street addresses are not detected at
  all**, at any setting. There are seven personal-data detectors and none of them is a name.
  *(The offending sentence is deliberately not reproduced here. This project has now been bitten
  three times by writing a matchable string into prose — a credential-shaped example in a comment
  once got the whole repository refused by three scanners — and the new check below fired on this
  very changelog when the first draft quoted it. Name the shape, never spell it.)*
- 🟢 **A new check compares the prose to the behaviour** (`selftest`, section 3b). Every other
  check in the suite asks whether the code is right; this one asks whether the sentence is, because
  a reader's belief about what leaves their machine is set by the prose and by nothing else. It
  asks `pii_gate` what the default actually is rather than trusting a constant, scans the shipped
  documents for a claim that contradicts it, and carries a positive control plus three negative
  ones — a check that cannot fail is decoration, and a check that fires on correct text teaches
  people to delete it. Calibrated against the real published README, where it fires exactly once.

The reviewer's framing is worth keeping: *"the harness audits model citations mechanically, but it
has no equivalent audit for the human-facing safety story that determines what the operator
believes will be sent."* That gap is what this release closes.

## 1.9.0 — 2026-08-08

**The citations that were never in the prose — and a documentation example that was not a schema.**

- 🔴🔴 **`goog36flash`'s sources were auditable all along, and the harness was discarding them.**
  Its citations arrive as structured annotations pointing at opaque
  `vertexaisearch.../grounding-api-redirect/` wrappers, so the citation audit — which reads URLs
  out of the answer *text* — found none of them and printed "cited no URLs" for a channel that had
  just cited six. Two things fix it, both free:
  - Every annotation carries `title`, and **`title` is the publisher DOMAIN** (20 of 20
    domain-shaped when probed). The parser now reports them, guarding on the shape rather than
    trusting the field. "Cited uscis.gov" and "cited youtube.com" are different reviews.
  - **The wrapper resolves.** `302 Location: https://en.wikipedia.org/wiki/UEFA_Euro_2024`. The
    standing note said "resolving one proves Google's redirector is up and nothing else" — true of
    an EXISTENCE check, false of URL RECOVERY. Two questions had shared one sentence, and while
    they did, the best-grounded channel in the panel was filed as unauditable.
- 🔴 **`n_cited` counted annotation SPANS on this channel and distinct URLs on every other.**
  Measured in one call: 14 annotations, 5 distinct wrappers, 4 distinct publishers — a ~3.5x
  overstatement that made one channel look better grounded than its neighbours through an artefact
  of how Google slices citations. Both numbers are kept now, under names that say which is which.
- 🔴🔴 **AND THEN THE TERMS WERE READ, SO FOLLOWING THOSE LINKS IS OFF BY DEFAULT.**
  `ai.google.dev/gemini-api/terms`, under *Grounding with Google Search → Use Restrictions*, names
  the capability by example: *"it is a violation of these terms to use Grounding with Google Search
  to extract or collect one or more of these components for another purpose (for example, using
  programmatic or automated means to collect Links, ... or using Links to identify destination
  pages for crawling or scraping)"* — and defines Links to include *"titles or labels provided with
  those means to fetch web pages"*. Whether a single-user citation audit is "another purpose" is
  genuinely arguable, and this kit is public, so the default is what strangers run.
  - **On by default:** the publisher **domains**, shown beside the answer to the person who asked
    for it. No fetch, nothing followed, no request at all.
  - **Off by default:** following the Links. `--resolve-grounding-links` turns it on for someone
    who has read that paragraph and judged their own use.

  The shape of how this was found is the point: the API's *documentation* was re-read this round
  and its *terms* were not, and a reviewer citing the terms for an unrelated reason is what sent
  anyone to look. **Re-reading the docs is not re-reading the contract.**
- 🟢 **Resolution happens in its own hop.** The single-pass version was tried and lost data: the
  existence prober follows the redirect and keeps going, so a slow publisher (a `uefa.com` wrapper,
  TimeoutError) destroyed the identity of the source along with its existence. Two questions, two
  requests, and the slow half now fails alone. Paced with a fresh random interval per request.
- 🔴 **Two reporting lies, both caught by running the thing rather than reading it.** With the
  Links unfollowed, the audit announced that *N wrappers "did not answer and were probed as-is"* —
  nothing had been asked and nothing probed; it reported our own decision as the vendor failing.
  And the guard that rejects a non-domain `title` discarded in silence, so a run showing two
  wrappers and zero publishers could not be told from one where Google sent no titles at all. Both
  are counted and named now, and **absent** is reported apart from **malformed**, because sending a
  reader to inspect a value that does not exist wastes the report's only credibility.
- 🟢 **The response parser is a pure function now** (`parse_gemini_steps`), so it can be tested
  without spending an API call. It had two defects at once and no test could reach either. 15 new
  checks including two negative controls — a headline in `title` must not be reported as a
  publisher, an annotation with no `url` must not be counted. Suite: **229 checks**.
- 🔴 **The rule worth more than the fix: a response example in vendor documentation is an
  illustration, not a schema.** Google's page for this endpoint shows publisher URLs in the field
  that live responses fill with wrappers. Getting a *request* parameter wrong returns HTTP 400,
  loudly. Getting the *response shape* wrong is silent — your code finds nothing in the field, and
  an always-empty column looks exactly like a model that cited nothing. Dump one real response
  before writing the parser.

## 1.8.1 — 2026-08-08

Two findings from the last reviewers of 1.8.0, which arrived after it was tagged.

- 🔴🔴 **The harness now checks WHICH MODEL actually answered.** Every verdict it produces attaches
  to a channel *label*, and nothing verified that the thing answering behind a router is the model
  that label names — so "this model lowered its effort" and "the router served something smaller"
  were the same observation. The provider states the model on every response chunk; that is now
  recorded as `model_served`, separately from the one we asked for, and a mismatch is a warning on
  the result. Same rule as the rest of this release, one layer up: judge by what came back.
  Verified live: requested `nvidia/nemotron-3-ultra-550b-a55b:free`, served the same, no warning.
- 🔴 **The registry-drift report is described honestly now.** Its reference copy sits under exactly
  the write permission it exists to monitor, so anyone who can edit `channels.json` can update the
  reference and silence it. No location fixes that, and a signature would need a key on the same
  disk. So: **it detects an edit you forgot, not an edit someone is hiding.** The gate against a
  hostile write is the acceptance step, which does not detect — it stops the spend.

## 1.8.0 — 2026-08-08

**A depth knob you have only sent is not a depth knob — and the settings file stops treating its
owner as the threat.**

- 🟢 **`echocheck.py` — new.** Every other check in this kit answers *"was the argument
  dispatched?"*. This one asks whether the vendor did anything with it, by comparing the
  `reasoning_tokens` that come back at two settings of the same knob. It samples each arm several
  times, interleaves and shuffles the arms so a vendor's change of mood cannot masquerade as a
  knob, and says **CONFIRMED only when the two ranges are disjoint** — overlapping ranges are
  reported as UNPROVEN with both ranges printed, never rounded up. It exists because an HTTP 200
  has twice meant "accepted and ignored" in this project's own measurements, and because one
  earlier round called a working knob inert on a single sample.
  It also prints the **output**-token counts beside the reasoning ones: a single counter can be
  *moved* rather than reduced, and reading one column alone made a model that thought out loud look
  like a model that had stopped thinking.
- 🔴 **The settings file's trust is now keyed on PROVENANCE, not on which field you set.** 1.7.0
  refused `model`, `provider`, `kind` and `prompt_suffix` from your own settings file. That was
  aimed at the wrong axis: your settings file and `channels.json` have identical write permissions,
  so refusing a field in one only pushed the change into the other — and the other was the file
  nothing announced at run time. Now:
  - at `~/.claude/model-orchestration.local.json` you may change **anything**, and **add** channels
    and tiers (`"_new": true` required, so a typo cannot quietly become a second channel);
  - under `MODEL_ORCH_LOCAL` only the "how hard does it work" knobs are accepted, because a
    project's own `.claude/settings.json` can set environment variables for sessions run inside it
    — so a repository you cloned can choose that path, and cannot choose your home directory;
  - transport changes are **marked 🔴 in the resolved plan**, in the same list as everything else:
    a separate "dangerous changes" section reads as a section about somebody else.
- 🔴🔴 **…and then three reviewers of that change found what it missed, independently, and a paid
  round now refuses until you accept a transport change once.** The permission-equivalence
  argument holds for an attacker who is already resident on the machine; it fails for a one-shot
  one. `channels.json` is *self-healing* — the next update replaces it — while your settings file
  is update-proof by construction. So opening it up handed the permanent file the powers the
  ephemeral one had, and a single write (a mistyped command, an AI assistant acting on a poisoned
  instruction) would have redirected a channel forever, silently. Now:
  `python routing.py --accept-settings`, once, printing exactly what you accept. Reformat the
  file, re-order it, or change a quiet field beside a sharp one and the acceptance still holds;
  change what is sent or where it goes and the refusal returns, naming the change. `--dry-run`
  works before acceptance on purpose: seeing what *would* happen must never require accepting it.
- 🔴 **`cost` was filed under "cosmetic / bookkeeping" and is not cosmetic** — it decides whether
  the plan warns "EXPENSIVE channel" before you spend, and which channels `--ask` fans out to. It
  is no longer accepted from a relocated settings file.
- Diagnostics now record **which usage key each meter was read from**, and where the path broke
  when it was absent. That is the check that would have caught the `output_tokens_details` /
  `completion_tokens_details` mix-up above on the day it was written, instead of months later.
- 🟢 **Tiers are settings too.** They were the one knob a user could not reach, and the omission
  had teeth: `gemini_thinking_level` lives on the tier and *overrides* the channel's own value, so
  lowering it in your settings file would have watched the tier put it straight back.
- 🔴 **The plan now reports edits you made to `channels.json`, by field.** 1.7.0 shipped a
  `channels.sha256` that could answer only yes/no, only inside `doctor.py` — which nobody runs
  before a round. A reference copy (`channels.shipped.json`) replaces it, so both `doctor` and the
  plan can name the fields, and `upgrade.py` has a real baseline instead of an inference.
- 🟢 **`upgrade.py` now carries every edit the new version's loader accepts**, one at a time, and
  prints the loader's own reason beside each one it cannot. 1.7.0 carried `enabled` and left the
  rest behind on a "this might not load in the new release" that was answerable by asking.
  `--carry-all` now means "also re-add whole channels this release removed".
- 🔴 **Fixed: a broad `except` in the upgrade path swallowed a plain programming error** and
  degraded silently to "nothing could be validated". It prints now. A tolerant fallback written for
  a partial install will also tolerate the author's typo.
- A channel missing `kind`, `label` or `model` is refused at load time with a sentence, rather than
  printing `[RUN ]` in the plan and failing at dispatch. A tier naming a `gemini_thinking_level` no
  channel declares is refused for free, instead of costing a paid 400.

## 1.7.0 — 2026-08-08

**Updating an install no longer destroys the settings the install guide told you to make.**

- 🔴 **Every update path silently threw your configuration away, and this is the release that
  admits it.** `INSTALL.md` said: open `channels.json`, set `"enabled": true` on the channel you
  want. That file lives *inside* the folder an update replaces. So the installer (which moved the
  old tree to `.bak.<timestamp>` and copied a fresh one), the "just copy the files" instructions,
  and the plugin path — which the docs recommend, and which updates itself with nobody running
  anything — all had the same outcome: the channel you turned on was off again, with no message.
- 🟢 **Your settings now live outside the skill folder**, in
  `~/.claude/model-orchestration.local.json` (`MODEL_ORCH_LOCAL` to move it). Nothing that updates
  this tool can reach it, so **every update from 1.7.0 onward is correct on every method**,
  including the naive ones — the fix is not a smarter merge, it is a file in a different place.
- 🔴 **The one hop INTO 1.7.0 is the exception, and it is worth reading before you update.** A
  reviewer of this release refused the sentence "this makes every update method correct", and was
  right: nothing can rescue a 1.6.x edit on a path that never runs `upgrade.py` — which is the
  *recommended* path, since a plugin updates itself with nobody running anything. There is a
  documented rescue window: Claude Code keeps each installed version in a separate cache directory
  and orphans the previous one for 14 days
  ([plugins reference](https://code.claude.com/docs/en/plugins-reference), read 2026-08-08), so
  `upgrade.py` now scans `~/.claude/plugins/cache` for an older copy of this plugin and offers to
  carry its settings across. **If you are on 1.6.x: run `upgrade.py` once, or write the one line
  of JSON yourself, before the fortnight is out.**
- 🔴 **That file is default-deny on fields, because a reviewer of this very release pointed out
  what the fix had created.** It may set `enabled`, `effort`, `reasoning`, `thinking_level`,
  `max_tokens`, `fetch_tool`, `web`, `label`, `cost`, `notes`. Anything deciding *which vendor
  receives your documents* or *what text is added to them* is refused by name: a file that
  survives every update and can name a transport would hand anything able to write one file in
  your home directory a persistent, update-proof redirection of where your documents go — and the
  per-run disclosure only helps if a human reads it, which the auto-updating plugin path removes.
  A renamed channel still resolves, through the alias table, so a rename upstream cannot stop the
  tool starting for everyone who named the old one.
- 🟢 **The resolved plan prints that file's path and every value it changed, on every run**, even
  when it changed nothing. An invisible settings file would be a worse trap than the one it fixes:
  the failure it prevents is "why is this channel not running", asked while looking in the wrong
  file. A name that is not a real channel is **refused with the list of real ones**, because a
  typo in a config file otherwise looks exactly like a channel that is off for another reason.
- 🟢 **`upgrade.py`**: back up, copy, carry your settings across, and report the version you had,
  the version you are getting, which channels are new, which are gone, and what it carried and did
  not. `--dry-run` shows all of it and writes nothing; `--migrate` only moves in-place edits out
  of the skill folder. `install.ps1` / `install.sh` now call it whenever an install already
  exists, so "install" and "update" are one tested path instead of two that drift.
- 🔴 **An installed copy now carries a version number. Until this release, none did.** The only
  version string that shipped was in `plugin.json`, which sits *outside* the folder the installer
  and the manual instructions copy — so on any non-plugin install "am I on the latest?" was
  unanswerable, and an assistant asked to update one had nothing to read. There is a `VERSION`
  file now, `doctor.py` prints it, and it is generated from the same constant as the manifest.
- 🟢 **`doctor.py` warns if `channels.json` has been edited in place** — checked against a
  fingerprint shipped beside it — and points at `upgrade.py --migrate`. That edit was previously
  invisible right up until the update that erased it.
- 🟢 **`--ask` now also runs every channel the registry prices `free`**, alongside the one you
  chose, and prints both answers. The set is **read from `channels.json`**, so a free channel
  added in a later release joins on its own rather than waiting for someone to remember a list.
  `--only` or `--skip` narrows it. Both of today's cheapest channels are contributor tiers whose
  vendors may train on what you send; the plan prints each channel's data policy before anything
  is sent.
- 🔴 **The direct Gemini channel now thinks at `high` on the default tier, and the number behind
  that changed.** 1.6.0 recorded `high` producing *fewer* thought tokens than the vendor default —
  one sample per arm, on a question too easy to think about. Re-measured on a question that
  requires reasoning, 3 interleaved samples per arm: `minimal` 0, `low` 770, `medium` 1 635,
  `high` 1 963, with medium's maximum below high's minimum. Thought tokens bill as output, so this
  is a deliberate ~20% increase on that channel — and `deep` now says
  `nothing this tier can raise on this channel` there rather than reprinting a value it did not
  change. All 15 answers were correct, so this measures what depth **costs**, not what it buys.
- Loading the registry from the command line reported failures as a Python traceback instead of
  the sentence it had prepared. Reachable for the first time by a typo in the new settings file.

## 1.6.0 — 2026-08-08

**Two tiers instead of four, one meaning per field, and a capability we had written off.**

- **`--tier` now takes `strategic` (default) or `deep`. `quick` and `standard` are gone** and are
  refused by name rather than silently defaulted. The reason is worth stating plainly: the two
  tiers that survived used to differ by a **timeout and nothing else** — identical effort on every
  channel — so the word "deep" advertised a depth the configuration did not contain. `deep` now
  doubles the reasoning ceiling and the page-fetch budget on every OpenRouter/MiMo channel, raises
  the direct Gemini channel's `thinking_level`, and extends timeouts. `strategic` is bit-for-bit
  the previous default, so nothing you already run costs more.
- **The plan now tells you, per channel, what the tier resolved to** — including
  `nothing this tier can raise on this channel`, which is the honest line for a vendor already at
  its ceiling. Before this, a control that reached four of eleven channels read as global.
- **The plan also tells you, per channel, how it reaches the live web.** Four channels with real
  search used to print nothing at all about it, because the line was only emitted for channels
  carrying a particular config block. A capability that is on but invisible gets doubted and
  eventually reimplemented.
- 🔴 **`opened_urls` meant two different things and has been split.** On some channels it counted
  pages **the tool fetched** — bytes on your disk, quotable, checkable. On others it counted pages
  **the vendor says it opened**, which nothing can verify. Reports compared them as one number.
  Now: `fetched_by_us` / `fetched_urls`, `vendor_opened` / `vendor_opened_urls`, `n_grounded`
  (backed by our fetches only), `n_vendor_grounded`, and `grounding_basis` ∈ *harness · vendor ·
  both · none*. If you have automation reading `diagnostics.json`, this is the breaking change.
- 🔴 **A large page fetch is a token bomb. There is now a ceiling on the total.** One 400 KB page
  pulled into a review billed **273,018 input tokens** for an 813-character question, because each
  tool round re-sends the whole conversation — the cost is quadratic in the number of steps, and
  the old budget counted *pages*, not bytes. A single panel run then pulled a 224 KB, a 238 KB and
  a 386 KB page on three different channels, so this is the common case, not the tail. Two
  changes: a page over 100 KB is called out at the moment it is fetched, and a channel may now
  fetch **1 MB of page text per review** in total, after which further fetches are refused with an
  explanation the model can act on. The per-page ceiling is deliberately unchanged — truncating a
  long statute mid-section is a worse failure than an expensive review. The ceiling is set above
  the heaviest honest run measured here (706 KB across 8 pages), not at a round number.
- 🔴 **The Gemini direct channel does have a depth knob after all.** The previous release stated
  it did not. That conclusion came from sending `thinking_level` at the top level of the request,
  getting `400 Unknown parameter`, and reading it as "the feature does not exist". It belongs
  inside `generation_config`. Measured by the token meter, one sample per arm: no knob → 391
  thought tokens, `minimal` → 0, `high` → 306. **A 400 answers "not like that", never "not at
  all".**
- **Codex gets the same page-opening fallback the Gemini CLI channel already had.** The previous
  release said this was impossible because codex had no MCP tools — a conclusion drawn by *asking
  the model*, which answered `NONE`. Codex loads its tools lazily and only sees them after a tool
  search, so the question was answered from an empty list. It has nine servers and can call them.
- **Two reporting corrections.** The one-shot `--ask` path said the citation check was "disabled
  with `--no-citecheck`", naming a flag you never passed. And the direct Gemini channel printed no
  telemetry at all — no tokens, no searches, no grounding — because the reporting block is keyed
  on channel kind and that kind was never added to it.
- Self-test grew to **115 checks**, including: the tier list has exactly one home, a removed tier
  is refused, `deep` really doubles what it claims to double, no channel returns the retired
  field name, and every dispatchable channel kind both describes its web access and prints
  telemetry.

## 1.5.0 — 2026-08-08

**Three new vendors, and a documented switch that does nothing.**

- **New channels: MiMo v2.5 Pro (Xiaomi), Grok 4.20 (xAI) and Nemotron 3 Ultra (NVIDIA).** The
  Nemotron one is **free** — the first channel here whose model costs nothing, so the only reason
  to drop it is wall-clock. All three are reachable with the `OPENROUTER_API_KEY` you already
  have; MiMo and Grok can also run on the vendor's own key, which buys more (below).
- **One key now reaches six model families.** `OPENROUTER_API_KEY` alone gets Kimi, Qwen, Gemini,
  MiMo, Grok and Nemotron. That is enough of a panel to be useful without opening a single extra
  account, which is the shape a first install should have.
- 🔴 **Both new vendors return HTTP 200 for an invented parameter.** Neither validates unknown
  top-level fields — they silently drop them. So on those APIs a 200 is *not* evidence that a
  setting took effect, and every wire parameter below was judged by a meter or an error instead.
  If you are configuring either vendor yourself, assume nothing from a successful response.
- 🔴 **MiMo's documented search switch does not work.** The vendor's FAQ says online search is
  enabled with `forced_search: true`. Measured: that and four other spellings all return 200 and
  all leave the model unable to search. What works is the tool form — and it is strong: one call
  ran 5 searches and **opened 25 whole pages**, returning a citation with a title and summary for
  each. MiMo also has thinking **off** by default; the harness switches it on explicitly.
- 🔴 **xAI has no server-side search on `/chat/completions` at all.** `live_search` there is now
  `410 Gone`. Search lives on the Agent Tools API (`/v1/responses`), where it runs an agentic loop
  that opens pages, and its citations carry character offsets into the answer. `x_search` over X
  is available too, off by default. That model also **rejects `reasoning_effort` outright**, so
  `--tier` does not reach it — stated rather than faked with a setting that parses and is ignored.
- 🟢 **One channel now reports what the call cost.** xAI returns a per-call price, calibrated here
  against the published rates to the cent. No other channel does this.
- **Channels can now be on here and off in your copy, or the reverse** (`distribution` in
  `channels.json`). Three models are reachable both through OpenRouter and through the vendor's
  own API; the direct route is off by default because it needs another account. `--dry-run` shows
  which is which, and turning one on is one `enabled: true`.

Fixes, all three found while wiring the above:

- **A provider error mid-stream was reported as our own empty output.** OpenRouter delivers such
  errors as an `error` event inside an HTTP **200** response, and the parser never read them. A
  rejected request looked like a silent failure with no cause. It now names the provider's reason.
- **`tools` never reached the call.** One channel's registry entry declared its tool list and the
  code fell through to a hard-coded default that happened to be identical — so the setting was
  decorative and editing it would have changed nothing.
- **The self-test could have stopped isolating the network.** It replaced a function by name, and
  in Python assigning to a name a module no longer has *creates* it rather than failing — so a
  rename would have left the suite making real, billable calls while passing. Now asserted.

`PRIVACY.md` is corrected in this release: it described the personal-data gate as blocking by
default, which stopped being true in 1.4.x. It warns and sends; `--strict-pii` restores the block.
A privacy document that overstates its protections is worse than one that admits their limits.

## 1.4.0 — 2026-08-07

**A ninth channel, and the finding that made it worth building.**

- **`goog36flash`** — Gemini 3.6 Flash on Google's **own** Interactions API (`GEMINI_API_KEY`).
  That is now three transports to one Gemini: the Antigravity CLI, OpenRouter, and Google direct.
  Not redundancy — a control. Same model id, so any difference is the transport.
- 🔴 **Measured: the transport decides the grounding, and the advantage is Google's
  infrastructure.** `agy36flash` read `uscis.gov/policy-manual/volume-7-part-b-chapter-4` in
  1.12 s — a URL that returned HTTP 403 to a plain fetch three times. Probing `url_context` on a
  bare API key opened the same page, which settles it: the reach ships with the API, not with the
  subscription CLI.
- 🟢 **Citations with character spans.** Google's `url_citation` annotations carry `start_index`
  and `end_index` into the answer, so "which sentence does this source support" is mechanical.
  No other channel offers this. 🔴 Caveat: `google_search` citations are
  `vertexaisearch.../grounding-api-redirect/...` wrappers, not publisher URLs — only
  `url_context` citations are real, and the harness counts them separately.
- **`--ask "question"`** — one-shot lookup, answer printed to stdout, ~20 s, cheapest channel by
  default. The full round was previously the minimum unit of work.
- **The PII gate now warns and sends**; `--strict-pii` restores the refusal. `--allow-pii` still
  parses and is a no-op so existing commands keep working. **Secrets are refused always, with no
  override at any setting** — that has not changed and will not.
- **Cached input is reported.** Vendors disagree on whether their `input_tokens` field already
  contains the cached part (Meta: no, OpenAI: yes), so every channel now states its own rule and
  no report applies one rule to another's row.
- 🔴 **`billed in` is a billing meter, not a prompt size.** On a channel with server-side search
  the vendor re-runs inference per search and reports the SUM, so the figure routinely exceeds
  the model's context window. Relabelled after a 2 026 852 reading against a 1 048 576 window was
  correctly challenged as impossible.
- **Fixes:** bare shortness was graded as a refusal, failing correct short answers; a blocked
  host drained the page-fetch budget one URL at a time; `report.py` read a flag that had been
  renamed and would have printed a false reassurance on every future run.

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] — 2026-08-07

### Fixed

- 🔴 **1.3.0 shipped without `report.py`, so the run report it advertises could not be generated.**
  The file list that decides what gets published is hand-maintained, `report.py` was never added
  to it, and every check passed anyway: the registry loaded, the self-test scored 91/91 *inside*
  the published tree, the privacy audit exited clean, and the build printed `clean`. The import
  sits behind a `try`/`except` that logs a note, so the feature would have failed politely and
  permanently on every machine that cloned the repository.

  The fix is not "remember to add the file". **The build now derives the requirement from the
  code**: it scans every shipped `.py` for imports of sibling modules and refuses to publish if
  any of them is missing. Verified by removing the entry again and confirming the build exits 1.

  This is the third time in this repository that a hand-maintained list has silently stopped
  matching reality. The general shape is worth stating: *a list that describes what the code needs
  is a claim, and a claim that nothing re-checks is eventually false.*

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
