# Changelog

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
