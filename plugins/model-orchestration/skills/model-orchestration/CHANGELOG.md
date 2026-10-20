# Changelog

This copy inside the plugin holds the newest releases only: Anthropic's plugin directory does not inspect a file of 256 KiB or more, and the whole history is larger. The full file is `CHANGELOG.md` at the repository root: https://github.com/igorsaevets/ai-second-opinion/blob/main/CHANGELOG.md

## 1.113.7 — 2026-10-19

From the free voices' HTTP 429s of 2026-10-18 and the panel that studied them.

- **One free voice of opencode's shared allowance, one fallback.** opencode's free MiMo v2.6 Flash, GLM 5.3 Flash and DeepSeek V4.1 Flash behave as ONE daily allowance of requests per network address (measured 2026-10-18/19: five free models were refused together, two of them never used from that machine, and the four probed again after 00:00 UTC answered). 1.113.6 started three of them in every cheap panel; on 2026-10-18 they had spent the allowance by 08:25Z and every round died with `provider.quota` until midnight. The cheap panel now starts MiMo v2.6 Flash only. GLM 5.3 Flash (`ocglm53flashfree`) is its run-time fallback: once, after the round, when MiMo ends without an answer, and nothing runs after it. DeepSeek V4.1 Flash (`ocdeepseekv41flashfree`) starts only when named. The cheap panel is 6 models (was 8), «CLI only» 4 (was 6), the standard panel 11 (was 13).
- **No fallback into the same allowance; a refused allowance is skipped until 00:00 UTC.** After a `provider.quota` from a channel of the group no fallback of the group starts (it would be refused too). The refusal's UTC date is written to `~/.claude/model-orchestration.quota-state.json` (this machine's, outside the kit folder, safe to delete - PRIVACY.md); every later run that UTC day, `--ask` included, prints `[skip]` for the group's channels with the hours left, unless you name the channel; an answer from the group clears the date. `MODEL_ORCH_QUOTA_STATE` moves the file (`off`: never read or written). New registry field `quota_group`.
- **The `provider.quota` hint no longer blames «every free user».** It said the free quota is «shared by every free user, so it runs out in bursts whatever your brief holds» - never measured, and false: the allowance is per network address, and the machine's own runs had spent it (395 model requests before the first refusal on 2026-10-18). It now says what was measured, quotes opencode's FAQ («200 requests/day»), names the free models measured outside the allowance (nemotron-3-ultra-free, beam-free, longcat-2.5-preview-free) and says that every run from your network spends it.
- **Nemotron 3 Ultra through opencode carries the same reading note as through OpenRouter.** `ocnemotron3ultrafree` now has `ornemotron3ultra`'s `reading_note`, and `read_order` 3 instead of 1: the note follows the model, not one channel name.
- **Docs (EN + RU).** README «You say» rows and panel tables, INSTALL's opencode section, PRIVACY (the state file), TECHNICAL (the mechanism), TROUBLESHOOTING («A free opencode voice shows `[skip]`»).

## 1.113.6 — 2026-10-18

From the panel on 1.113.5 (eight voices, six complete answers) and from Anthropic's plugin directory.

- **agy's grounding retry has to deliver.** When Gemini's first answer opened no page, the harness asks once more; the second answer now replaces the first only when it opened a page AND delivered a review (text, not the plan, the end marker on its last line). Otherwise the first answer is kept, the run is marked failed, and the record says «RE-RUN OPENED N SOURCE(S) BUT DID NOT DELIVER A REVIEW» with the reason. A first-attempt file that cannot be moved aside (Windows: another process holds it) is copied instead; the attempt files listed are only this call's; a won retry's own `.retry.md` is named as the answer's copy.
- **opencode answers are graded by the root session and by the part after the last error.** Both readers of opencode's own store walk up to the stream's root session, so a sub-agent's session no longer stands for the run. When opencode reports an error and then recovers, the stream is split at the LAST error frame: with nothing before it the run is ok as in 1.113.5; with text before it (a draft that was cut), the run is ok only when the part after the error is complete on its own (no refusal, the end marker on its last line), and the note names both lengths. An exit code other than 0 with no error frame and a complete answer stays ok, now with a ⚠ note and opencode's own verdict.
- **HANDOFF.md says what is on disk.** A channel record naming an answer file that is not on disk gets a 🔴 line. «TRUNCATED-BY-LIMIT» is read only from a line that declares it, never from «not reached» or «: none» in the text.
- **Plan lines.** A switched-off channel: «only its own name starts it (`--only <name>`) - a group word, a panel or the default skips it» (it used to say a group word could start it). The «--panel X would ALSO run / DROP» lines leave out the channels that run only when named.
- **Docs (EN + RU).** Without the opencode CLI its four free voices stay in the plan as `[RUN ]` and each fails at dispatch with «binary not found», spending nothing; «without opencode» leaves them out (README and INSTALL used to say they «do not start»). Nemotron 3 Ultra's OpenRouter route also answers `--ask` on an OpenRouter key when opencode is missing (with opencode it answers there); `ocspark13free` is switched off, its free model left opencode on 2026-10-17; INSTALL's «`--ask` starts with this channel» holds without the Go overlay.
- **Registry notes name the vendor page instead of copying a price.** OpenRouter's Exa search is $0.007 per request on 2026-10-18 (nine notes said $0.005); Meta's Spark 1.3 Contributor allows 150 requests a minute against Standard's 3,000 (the notes said 100 and «30x»); the OpenRouter Spark spend guard's arithmetic names its Standard 1.3 fallback; the OpenCode Go Spark note no longer promises a run-time fall-through that does not exist (a cascade picks one member when the plan is made).
- **Anthropic's plugin directory.** `.gitattributes` no longer asks for CRLF on `*.ps1`: the directory refused the repository before scanning for that rule (PowerShell 5.1 and 7 both run the scripts with LF endings). The plugin folder's `CHANGELOG.md` is now a trimmed copy, the newest releases up to 64 KiB and then a pointer to this full file, because the directory holds for a reviewer every non-image file of 256 KiB or more and named this one (410 KB); `update_check.py` still finds there the section of the version it just installed. The build prints the plugin files that are still 256 KiB or more (the code and the registry).

## 1.113.5 — 2026-10-18

From the first full panel of the three new free opencode voices: seven of eight answers were complete, and the report counted four.

- **«opencode» names the opencode voices.** The word was the alias of `ocspark13free`, whose free model left opencode on 2026-10-17, so «only opencode» / `--only opencode` ran only that dead model and «without opencode» excluded nothing. It is now a group of every opencode channel: «only opencode» runs the ones that are on (in the kit the four free voices), «without opencode» drops them, and a switched-off member starts only by its own name. «опенкод» works too.
- **A complete opencode answer after a recovered error is graded complete.** opencode exits 1 after a mid-run transport error (ECONNRESET) even when it retried and the model finished; three complete reviews were graded FAILED. When opencode's own store records the last step as finished (`stop`) and the end marker is on the last line, the answer is now graded ok, with a ⚠ note naming the error. Anything less stays FAILED.
- **The agy retry keeps both attempts.** When agy's second attempt replaced the first (a plan instead of the review, or sources cited and none opened), the round wrote the second answer over the first while the note said the first was kept. The first now moves to `<CHANNEL>.first.md`, the note names it, and HANDOFF.md lists both attempt files instead of calling the retry file an orphan.
- **Docs and printed text:** Muse Spark's automatic Standard fallback costs $1.25 / $4.25 per 1M tokens (README, INSTALL); the `--ask` line names an extra whose web searches bill (`ornemotron3ultra`) instead of saying a second opinion costs nothing; INSTALL no longer says the opencode CLI decides which channel answers `--ask` first; without opencode the cheap panel runs four models (README); two stale registry notes.
- **Known, not fixed here:** opencode v2 resumes an interrupted session when a later opencode server starts on the same store («The server restarted while you were working»); in that panel a free voice's brief ran again 23 and 93 minutes later, with nobody collecting the answer. A private data folder per free run was measured to work (a free model answered from a fresh folder in 6 s, the shared store untouched); it ships once its side effects (MCP sign-ins, tool downloads) are measured.

## 1.113.4 — 2026-10-18

The plugin directory's automatic check stopped the plugin on one finding: «Secret in a shipped file» in `doctor.py`. There is no real key in the kit; the finding was the fake key shapes `doctor.py` feeds to the secrets gate to prove it catches them, written out in full. A scanner cannot tell a fake key from a real one, so the kit no longer spells any.

- **`doctor.py` and `selftest.py`:** the seven fake credential values (a bearer header, a private-key header, an AWS-style key id, a Slack-style token, a long `MODEL_API_KEY` test value) are each written as two joined fragments. The values the gate sees are byte-identical, and `doctor.py` still reports every detector live with no false positive.
- **`hooks/hooks.json`:** the three `_doc…` keys, which Claude Code ignores and the directory flags as unknown, are replaced by the documented `description` key; the design notes stay out of the shipped file.
- **A selftest keeps it so:** `suite_prd003_no_credential_shapes` scans every shipped code, doc and registry file for credential shapes; on 1.113.3 it finds all seven.
- No behaviour changed.

## 1.113.3 — 2026-10-18

opencode stopped serving its free Muse Spark on 2026-10-17, so in 1.113.2 the Spark line of every first run failed. Three other free opencode models join the cheap panel, and the docs say that Spark now costs money.

- **Three free opencode voices in the cheap panel**, no key and no account: Nemotron 3 Ultra (`ocnemotron3ultrafree`), GLM 5.3 Flash (`ocglm53flashfree`) and DeepSeek V4.1 Flash (`ocdeepseekv41flashfree`). Run live together with MiMo v2.6 Flash on 2026-10-18: four of four answered and refuted the planted claim, quoting the docstring. GLM and DeepSeek run at effort max (their reasoning tokens at low and at max do not overlap, n=2) with a 65,536-token output limit, as MiMo Flash has; Nemotron takes no effort level, so none is sent.
- **The free Spark is off.** `ocspark13free` (`opencode/muse-spark-1.3-contributor-free`) answers «provider.no-route» and is gone from `opencode models`; it ships switched off. The cheap panel's Spark voice is now `spark13cont` (Meta key) or `orspark13cont` (OpenRouter), $0.10 / $0.20 per 1M tokens (the plan marks the OpenRouter route PAID); our first OpenRouter run cost $0.002.
- **`ocspark13go` has no fallback.** It fell back to the free Spark; a fallback that starts paying on its own is not one we ship.
- **One Nemotron voice.** The opencode Nemotron and `ornemotron3ultra` (OpenRouter) are one cascade: opencode first, OpenRouter only where opencode is missing (standard panel).
- **Hints.** `provider.no-route` has its own now: the model is no longer served, or the effort variant is not one it takes; free models come and go; check `opencode models`. The `provider.quota` hint no longer promises the free Spark, and the `--ask` list line names cascade twins as one voice.
- **Docs.** README and README.ru: the setup table, «There is no free Muse Spark any more», the «How to ask» counts (cheap 8, CLI only 6, standard 13, CLI only standard 7, for a reader who did steps 1-4, plus Codex for the standard rows), both panel tables, the fallback list, the Go paragraph, the data-terms paragraph. INSTALL, PRIVACY and SECURITY follow; INSTALL no longer says that every `opencode/` model is free.

## 1.113.2 — 2026-10-17

The first screen says what its sample run does and does not show. Nine reviewers read 1.113.1's new section: all nine caught the same arithmetic slip, and seven saw that a «dead links» column alone hid how many links were never confirmed.

- **README and README.ru, «What a real run found»** (was «What one run returns», too close to «What one run looks like» further down). A Live column beside Dead, with one line on what each means; † on the three routes the published version leaves off, and the intro says the seven are not the default panel. «26 min 31 s, 15 seconds more than the slowest voice» replaces «27 minutes, the time of its slowest voice», which the table's own 26 min contradicted. The awesome-claude-code rule is quoted whole, with its exception, and linked; MiMo Flash's quote is marked as cut; Spark's two dead links are named as one real page cited twice. The default panel's cost and `--dry-run` close the section, with a link to the console sample.
- **The data-flow sentence** names what the tool does on its own: a version check against GitHub at most once a week, as PRIVACY.md says.
- **The hallucination check** is described as what it checks: dead links, links cited without being opened, quiet refusals.
- The English README's terminal example says `--route "only CLI"` (it was the Russian phrase; both give the same plan); the tagline reads «Every AI model knows different things»; the Russian twin is reworded in eight places.
- No code changed.

## 1.113.1 — 2026-10-17

The README opens with what one run returns. Until now a first-time visitor read the promise and then, as the first section, a table of accounts, keys and CLIs: nothing on the page showed a run's output above that cost.

- **README and README.ru: «What one run returns», right below the tagline.** A real run of 1.113.0 (2026-10-17, seven CLI agents asked where this tool should be listed): time and the citation check per model, a rule our own notes had missed, a disagreement, and the gap between links cited and pages opened. It names the three of the seven that ran through channels the published version leaves off.
- **What leaves your machine is said on the first screen:** sending your document to the vendors you pick is the whole point, and the tool itself has no server and collects nothing (PRIVACY.md).
- No code changed.

## 1.113.0 — 2026-10-17

Muse Wonder starts only when you name it. Measured on 1.112.6: a standard panel without the Muse Code CLI ran the paid `wonder10` (Meta key, no per-run ceiling) or `orwonder` in every round; «only muse», «only openrouter» and «CLI only» pulled Wonder in; «only wonder 1.0» ran the paid `wonder10` even where the Muse Code subscription was ready.

- **No panel, default, «все» or broader group runs Muse Wonder.** `mcwonder10cont`, `wonder10` and `orwonder` are `explicit_only`; the standard panel has 11 models.
- **The model's word starts ONE Wonder, subscription first.** «вондер», «only wonder», «muse wonder», «wonder 1.0», «add wonder» start `mcwonder10cont` where the Muse Code CLI is installed. Where it is not, the run stops before paying and names the paid ones; «only wonder10» or «only orwonder» runs that one. A group whose channels are exactly one cascade (`wonder`) names that model; a broader group (a vendor, `cli`, `openrouter`, a panel) starts none of them, and a member that is also `enabled: false` still needs its own name.
- **«wonder 1.0» / «вондер 1.0» name the model, not the paid API channel.** They were model aliases of `wonder10`; «muse-wonder-1.0», the API's own id, still names `wonder10`.
- **The plan prints one 🔴 line per Wonder family left out**, with the words that start it.
- **INSTALL's Muse Code check is a real call**: `orchestrate.py --ask "Reply with the word OK" --only mcwonder10cont`. The old check, `muse model-profile show`, printed a profile for any id, an invented one too.
- **The «Muse Code is not signed in» hint names `--only wonder10` / `--only orwonder`.** It sent you to `--skip`, which since this version starts no other Wonder.
- **The way back:** `{"channels": {"mcwonder10cont": {"explicit_only": false}}}` in `~/.claude/model-orchestration.local.json` seats Wonder in the standard panel again, on the subscription.

## 1.112.6 — 2026-10-17

Route fixes from the review panel on 1.112.5. Each sentence below was run on 1.112.5 with a paid channel in the slot it turns off, and started a channel the speaker had excluded or never named.

- **«X вместо A и B» replaces both.** «grokbuild вместо kimik3 и codex» started codex; on the standard panel «grokbuild вместо codex и kimik3» ran the paid kimik3. An infix «вместо» joins only the names linked to it by «и» / «and» / a comma; after a comma, «вместо» starts a new clause.
- **The English word «or» no longer selects a channel group.** «only codex or grokbuild» started three paid OpenRouter channels: «or» was an alias of the `openrouter` group. A function word (or, and, the, no, not, и, а, но, или, в, на, с, …) never names a channel or a group.
- **Negations after the name, far from it, or in words the grammar did not list are caught.** «codex не нужен, возьми grokbuild», «добавь grokbuild, codex не трогай», «а codex пропусти», «codex в сторону», «запрети codex», «никакого codex», «за исключением codex», «don't ever use codex», «don't you dare use codex», «codex запрещён», «use neither codex nor grokbuild» and «не‐используй codex» (a Unicode hyphen) all started codex. «за исключением», «исключая», «excluding», «пропусти», «не нужен» are exclusions now; for the other shapes the second check reads the whole clause, also the words after the name, and stops the run with the way to write it. That check still never changes a plan.
- **A replaced channel takes every paid twin of its group with it, also the ones before it.** «grokbuild вместо orspark13cont» with the free Spark not ready ran the paid spark13cont; «grokbuild вместо orwonder, не используй mcwonder10cont» ran wonder10. Twins whose cost is `mid`, `cheap`, `metered` or `expensive` count as paid, so «codex вместо orgemini38flash» also leaves agy38flash off.
- **A free channel you name that is not ready here STOPS the run** instead of handing its voice to its paid twin with a PAID line («добавь kimik3free» without its key ran the paid kimik3). Name the paid channel to pay for it, or leave the free one out.
- **«why not skip codex» excludes codex.** 1.112.5 added it: the double-negation rule read «not skip» as «use». «почему бы не» the same.
- **«только не: codex», «only not: codex», «только не? codex» exclude codex.** Punctuation after «не» made 1.112.5 run codex ALONE.
- **Every n't form excludes:** «shan't use codex», «hasn't», «mightn't», «ain't», «isn't to use codex» added codex.
- **«no one but X», «nobody but X» and «ничего — кроме X» (with a dash) mean only X.** They ran the panel without X (the paid kimik3 then carried the Kimi voice). «without excluding X» keeps X (it dropped the free Spark and ran the paid spark13cont).
- **No more false stops on** «не исключительно codex, но и grokbuild», «добавь grokbuild и не забудь codex», «don't exclude the codex»: 1.112.5 refused them, they run as written.
- **A run-time fallback whose model the route replaced stays off.** «grokbuild вместо ocmimo26flashfree» left the metered mimov26flash armed as mimov26pro's fallback, so a mimov26pro failure would have brought the replaced model back, paid. The plan prints why it is off.

## 1.112.5 — 2026-10-17

Route grammar fixes from the review panel on 1.112.4, each reproduced on the code before it was changed (a paid channel in the slot the sentence turns off), and attachments by path for three more CLI kinds.

- **«только не X» and «всё что угодно, только не X» exclude X.** Every release so far started X ALONE: a bare «не» is no instruction word, so «только» kept its meaning. «only not X» and «лишь не X» the same.
- **English negations exclude.** «don't pick codex», «don't add codex», «never use codex», «doesn't use codex», «won't use codex», and «don’t use codex» typed with a curly apostrophe started the expensive codex; «don't only use codex» ran codex alone. They exclude it now («don't only use codex» adds codex to the panel).
- **«A -> B», «A, замени на B» and «A replace with B» run B instead of A.** Since 1.112.3 they kept A: «kimik3 -> kimik3free» ran the PAID kimik3 and switched the free one off. With more than one name on a side the run stops and asks for «B вместо A». «замени A на B» and «replace A with B» still stop.
- **«Y вместо того чтобы использовать X» runs Y.** Since 1.112.4 it ran X («вместо того» was read as «instead of it»). «instead of using» is an instruction word too.
- **A replaced channel's paid twin stays off when another member of its group is only excluded.** «grokbuild вместо wonder10» with `--skip mcwonder10cont` ran the paid orwonder; the same held for kimik3 and orspark13cont.
- **«вместо A и B возьми C» replaces A and B.** B used to start.
- **A negated exclusion includes.** «не без X», «не исключи X», «don't exclude X», «don't skip X» excluded X; with the free Spark that switched the free channel off and the paid spark13cont on.
- **«только добавь X» adds X to the panel** (1.112.4 ran X alone). «не-используй X» with a hyphen is no longer read as «используй X». «ничего кроме X» means only X (it ran everything except X).
- **When a free channel you named is not ready here and its paid twin carries the voice, the plan says PAID per call.**
- **A second check that does not share the grammar.** When a negation word stands right before a channel's name and the route would still run that channel («только grokbuild и не codex», «добавь grokbuild, но не codex» started codex), the run stops and says how to write it. It never changes a plan.
- **Two readings stop:** «не используй A, B вместо C D» (it ran D and dropped B). Clearer messages for «не используй ничего, только X» and for «вместо него» after two names; «кроме того, …» and «без разницы, …» are connectives, not exclusions.
- **opencode, MiMo Code and Kimi Code read attached material from disk, like codex, Antigravity and Grok Build.**
  With `--attach` / `--attach-dir` these three CLI kinds used to receive every file INLINE in one message (they
  arrived after the rule «CLI agents get paths, not files» and were never added to it), and their working folder
  held no file, so a reviewer could not open, search or run anything it was given: in a 21-file review the Spark
  voice got 229K characters of text while codex and Grok Build read and ran the files by path. They now get the
  absolute paths (of the vetted copy for folders) with the shell-on contract: read only, change nothing. Muse Code
  (file tools limited to its workspace) and the API channels still get the text inline. The plan line, `--help`,
  SKILL.md, `references/briefs.md` and AGENTS.md said attachments go inline to every channel and CLI channels get
  the path as well; the CLI channels get the path only.

## 1.112.4 — 2026-10-17

Route grammar fixes from the review panel on 1.112.3, each reproduced on the code before it was changed.

- **«don't use X» excludes X.** The router kept the shorter «use» it found inside «don't use» / «dont use» and read the sentence as «use X» (since 2026-09-14): «don't use codex» started the expensive codex on the cheap panel, and the README's own example «don't use openrouter» excluded nothing (on the standard panel every OpenRouter channel still ran). «just use codex» ran the whole panel. The longest instruction word now wins, and an instruction word counts only where a word starts («refuse codex» is not «use codex»).
- **«не» and «только» keep their meaning next to another instruction word.** «codex не используй, а используй grokbuild» ran codex; «только используй codex» and «только вместо grokbuild возьми codex» ran the whole panel. «не только codex, но и grokbuild» now adds both (it excluded both).
- **«вместо X используй / поставь / возьми Y» switches X off.** X stayed on (on the standard panel «вместо codex поставь grokbuild» kept codex). «возьми» and «бери» are add words now.
- **«вместо него / неё / них / этого» replaces what came before it.** «kimik3, вместо него kimik3free» ran the PAID kimik3 and switched the free kimik3free off.
- **A replaced channel does not come back on its paid twin.** «codex вместо agy38flash» also ran orgemini38flash (the same Gemini through OpenRouter, paid per call), with no reason in the plan. A channel you only exclude («не используй agy38flash», `--skip agy38flash`) still hands its voice to the twin, as before; that plan line now says the twin is PAID per call.
- **A family member named as the one to replace is replaced.** «кими вместо kimik3» and «вместо kimik3 возьми кими» on the standard panel ran the PAID kimik3; now kimik3 is off, and when no other Kimi voice can start (the free ones ship off) the run stops and says so.
- **Unclear sentences stop and say how to write them**, instead of guessing: a trailing «не используй» after another instruction («kimik3 вместо kimik3free не используй» ran the PAID kimik3); «вместо X» with nothing that replaces X; «не используй A, B вместо C» (two readings with opposite costs); «A вместо B вместо C». Clear forms run as before: «grokbuild не используй», «не используй kimik3free вместо kimik3», «только grokbuild, вместо codex agy38flash».
- After a «не», «вместо A B» replaces A with B («не используй grokbuild, вместо codex qwen38maxcli» ran codex).
- Both readers of a route - the one that switches channels and the one that decides what you named - ask one function what each «вместо» means.
- README: «only kimi» on the default cheap panel stops and asks you to name `kimik3` (since 1.112.1); the «You say» table and two paragraphs still said it starts the paid channel.

## 1.112.3 — 2026-10-16

Route grammar fixes from the review panel on 1.112.2, each reproduced on the code before it was changed.

- **«X вместо Y» swaps the right way.** «kimik3free вместо kimik3» («kimik3free instead of kimik3») ran the PAID kimik3 and switched the free kimik3free off; «codex вместо grokbuild» ran grokbuild. When words came before «вместо», the router moved the marker to the front and read the first name as the one to replace (since 2026-09-14). Now the name before «вместо» is the replacement and the name after it is the one replaced - also inside «только ...» / «включая ...». «вместо X возьми Y» and «не X, вместо него Y» are unchanged.
- **«только X, добавь Y» runs Y too.** The «add» switched Y on and the «only» rule switched it off again («not in the 'only' list»), in either word order. A name that «добавь/плюс/включая» or a replacement gives in an «only» sentence now belongs to that «only».
- **Names on both sides of a «не» are refused, not dropped together.** «codex, не используй grokbuild» and «kimi не используй kimik3» excluded every name in the sentence; now the run stops and says how to write it («только codex, не используй grokbuild», «включая ...»). A trailing «grokbuild не используй» still excludes.
- **«вместо» with more than two names.** «вместо grokbuild возьми qwen38maxcli, codex»: codex is a second replacement (it was left off as a new «target»). A group after «вместо» is replaced as a whole («вместо кими возьми grokbuild» turns every Kimi channel off; one Kimi member used to become the replacement).
- **A member you name and also `--skip` does not hold its family.** `--only wonder mcwonder10cont --skip mcwonder10cont` ran an empty round; it now stops with «you excluded it in this run».
- The two readers of a route - the one that switches channels and the one that decides what you «named» - now order the words through one shared function, so they cannot drift apart again.

## 1.112.2 — 2026-10-16

Fixes from the review panel on 1.112.1, and one found while checking them.

- **An exclusion inside «только <group>» is kept.** «только cli без codex» ran codex, and in the kit «только кими, не используй kimik3» ran ONLY the paid kimik3: when a sentence both named a group and excluded one of its members, the group's «only» list switched the excluded member back on (since 2026-08-14), and 1.112.1's free-to-paid stop read the excluded name as «the human named the paid channel». Now the exclusion holds, a name inside «не/без/кроме» or the channel being replaced («вместо») does not count as naming it, and when a run excludes every voice of the family it named, it stops and lists each one with the reason.
- **The free-to-paid stop knows what can run on this machine.** In the kit, «только вондер» on a machine without Muse Code passed the stop (the subscription member counted) and then ran the paid `wonder10`, which has no per-run ceiling. A member that is not ready here (its CLI or key is missing) now counts as one that will not start, so the run stops before spending and says «not ready here».
- **A member you name starts; one you exclude does not hold the family.** `--only kimi kimik3free` stopped «kimik3free will not start» right after naming it; `--only kimi --skip kimik3free` (free Kimi on) ran nothing at all. Both fixed; the stop now says «you excluded it in this run».
- **opencode:** `opencode --version` is read from «opencode vX.Y.Z» (or the last bare version line), so a wrapper's banner printed first cannot pass for the version; on v1 a channel never gets `--standalone`; the v1 error names the binary it found. Checked on opencode 2.0.31 (the npm release since 1.112.1): `--standalone` is still found and the review switches are still in the binary.
- Scope of 1.112.1, stated plainly: the review switches act on opencode's private server (MiMo Flash); the Spark channels run on the shared background service and still send the home folder's `AGENTS.md` - open. The free-to-paid stop concerns a family word on a panel that holds none of that family; the standard panel includes the paid Kimi by design.
- New known failure: AIHubMix's 429 «limit of the free model quota» (the key's free allowance is used up; a re-run fails the same way). A stale registry note that still said `npm install -g opencode-ai` is marked superseded.

## 1.112.1 — 2026-10-16

Fixes from the review panel on 1.112.0.

- **The install line installed the wrong opencode.** README, README.ru and INSTALL said `npm install -g opencode-ai`; that package is opencode **v1** (1.19.2 on 2026-10-16). The harness speaks **v2**: `npm install -g @opencode/cli`. On v1 the free Spark voice (the cheap panel's Spark seat and the default `--ask` channel) failed in 1.5 s and MiMo Flash ran without its output limit. The docs name the v2 package now (with `npm uninstall -g opencode-ai` for anyone who followed the old line). The harness asks `opencode --version` once per run; on v1 it stops a `model#variant` channel with that fix in the error instead of «UnknownError», and notes it on the others.
- **A family word no longer turns a free habit into a paid call in silence.** In the kit, «только кими», `--only kimi` and «только кими бесплатно» started the paid OpenRouter Kimi (about $1.38 a run): both free Kimi routes ship off, and the plan never said «paid». Now, when every member a family word would start is paid per call and the family declares a free or subscription member that will not start, the run stops before spending and names both; `--only kimik3` runs the paid one on purpose. A family with no free route keeps the 1.110.0 rule (Wonder), and every paid member such a word starts says «PAID per call» in its plan line.
- **opencode reviewers no longer get your home folder's `AGENTS.md`.** Measured on a local stand-in provider: opencode 2.0.29 walked up from the reviewer's working folder (under your home on Windows) and put `~/AGENTS.md` into every review prompt. Reviews now set `OPENCODE_DISABLE_PROJECT_CONFIG` (and its twin name); a `--task` run keeps the project's files. This acts on the private server (`--standalone`, MiMo Flash); a run on opencode's shared background service gets that service's environment. `~/.claude/CLAUDE.md` was not in opencode's prompt.
- **No reviewer inherits your shell's `PWD`.** opencode took its working folder from it (1.112.0 fixed opencode alone); the harness now drops `PWD` before any channel starts, for every CLI.
- New known failure: AIHubMix's 403 «concurrent request limit» (the same key busy in another run) is named, with what to do.
- Smaller: the opencode limit merge names a limit it replaced and no longer raises on a non-object `limit`; a model id without `provider/` gets its own note; the `--standalone` check looks past a big file without the flag (npm's platform packages); a failed registry read for `opencode_limit` is logged; README FAQ: the standard panel is about $2.5 a run, not $3.2; «every paid channel has a ceiling» now says most do and `wonder10` does not; two stale registry notes are marked superseded.

## 1.112.0 — 2026-10-15

Kimi and opencode, after a panel on 2026-10-15 lost two of six voices and printed «the model stopped
before finishing» for both:

- **`nvkimik3` is off.** NVIDIA retired `moonshotai/kimi-k3` on 2026-10-15 at 08:00 UTC: every
  request now answers HTTP 410 «has reached its end of life». The cheap panel runs without a Kimi
  voice (5 models) until another free route exists; `kimik3` (OpenRouter, paid) answers in the
  standard panel. Naming `nvkimik3` on purpose still starts it, and the run ends on the 410, named.
- **«only kimi» runs a Kimi again.** The rule «a family word names its family when the chosen panel
  holds no voice of it» (1.110.0) counted a member as present when it was merely DECLARED on the
  panel. With `nvkimik3` off and `kimik3free` local-only, the kit's `--only kimi` and «только кими»
  ended «running 0 channel(s): NONE» (exit 2). A member now counts only if the panel would start
  it, so the word names `kimik3` (OpenRouter, paid) and the plan says why. Checked over all 11 group
  words, as a flag and as a phrase, in the shipped and in the source registry: this is the only plan
  that changes.
- **A retired model is named.** When Kimi Code CLI's session log shows the turn ended on an HTTP
  status, the round says which; a 410 reads «the vendor has retired this model» with what to do,
  and the vendor's own end-of-life sentence maps to the same cause on any channel.
- **opencode's output limit is named.** When opencode's own store says the run's last step ended
  with `finish: length`, the round prints OPENCODE OUTPUT LIMIT REACHED with the reasoning / answer
  split and the limit, instead of a missing end marker.
- **MiMo Flash Free (`ocmimo26flashfree`) gets a larger output allowance.** opencode's catalogue gives
  the free model 32 000 output tokens, and reasoning counts against them: on 2026-10-15 it reasoned
  32 002 tokens into a review and wrote nothing (8 of 88 such runs since 10-01). The channel now
  carries `opencode_limit` (context 200 000, output 65 536), which the harness passes as the run's
  `OPENCODE_CONFIG_CONTENT` with `opencode run --standalone` - a private opencode server started
  with that run's settings. Measured: opencode then asks for the configured limit instead of
  32 000, and the free gateway wrote 86 562 tokens in one step when allowed 131 072. 65 536 leaves
  most of the 200 000-token window for the brief. An opencode without `--standalone` keeps the
  catalogue limit and the run says so.
- **opencode reviewers work in the harness's neutral folder.** opencode takes its working folder
  from an inherited `PWD` over the folder it is started in, so a harness launched from Git Bash ran
  its opencode reviewer inside the folder the shell was in - usually your project, with its
  `AGENTS.md` and files (measured in both the shared-service and the standalone mode). The child now
  gets `PWD` = its own folder.
- **opencode does not update itself during a run.** Harness runs set `OPENCODE_DISABLE_AUTOUPDATE=1`;
  an opencode update replaces the installed package through npm, and for those minutes there is no
  `opencode` command at all. An opencode window you open yourself can still update; setting the
  same variable for your user account stops that too.
- **README and INSTALL** stop describing the NVIDIA route: step «2b» is gone, the cheap panel is 5
  models («CLI only» 4), the standard one 12 with Kimi K3 through OpenRouter, its cost about $2.5 a
  run; the channel off-switch example now uses `ocmimo26flashfree`.

## 1.111.5 — 2026-10-14

MiMo Code (`mimov26pro`):

- **Your Claude Code instructions no longer ride every MiMo review.** MiMo Code's Claude Code
  compatibility put `~/.claude/CLAUDE.md` into each reviewer prompt, and on Windows the folder the
  harness runs it from (inside the system temp folder, under your home) let it also pick up an
  `AGENTS.md` from your home folder. MiMo's own record of the prompt held both files in every
  harness run checked (132 of 132). The harness now starts MiMo with
  `MIMOCODE_DISABLE_CLAUDE_CODE_PROMPT=1` and, for reviews, `MIMOCODE_DISABLE_INSTRUCTIONS=1`; a
  `--task` run keeps the project's own instruction files and still drops the Claude Code one.
  Measured on that record: the system prompt went from 76 690 to 39 553 characters, and the tools
  offered did not change. The run log names what was kept out.
- MiMo's tools and MCP servers are unchanged in this release.

## 1.111.4 — 2026-10-13

Grok Build, after Grok's own review of 1.111.3 (each item below was checked in the code or with a
live Grok run before it was changed):

- **A grok that would load your Claude Code setup is not started.** When `grok inspect` shows
  imported items active despite the switches (a newer grok that renamed one), the brief is not sent
  and the error names the surfaces and how to turn them off; 1.111.3 only noted it and ran. A switch
  that is on with nothing behind it stays a 🔴 note, and an inspect answer whose switch cells have
  no true/false value is «not verified», never «verified off».
- **Each run names its Grok session** (`--session-id`, a fresh UUID), and the note after a timeout
  reads that session by name. 1.111.3 said a timeout never quotes another run's session; two runs
  sharing one `--task` folder at the same time still could. A session folder without Grok's
  `events.jsonl` is now named as such, and a tool call a deny rule refused is counted as refused -
  it used to read as «started and never finished», the sign of a stalled run (found on a live run of
  this release).
- **With bypass on, the reviewer cannot read Grok's own session records** (they keep each MCP
  server's command line and headers; on the author's machine 297 of them carry a key or token), Grok's
  MCP OAuth store (`mcp_credentials.json`), or the older kimi-cli config. Measured on Windows: the
  deny rules match backslash and forward-slash paths, and they also stop `cat <path>` in Grok's shell
  (1.111.3's comment said the shell could still read the file).

## 1.111.3 — 2026-10-13

Grok Build, after a review panel on 1.111.2 (six reviewers; each item below was checked in the code
or measured before it was changed):

- **Each run checks that Grok honoured the import switches.** Before the review the harness asks
  `grok inspect --json` (no model call, 0.2 s measured) and logs «imports verified off»; if a grok
  version ignores a switch, the run carries a 🔴 note instead of silently sending your Claude Code
  setup to xAI. 1.111.2 called the switches undocumented: ten of the eighteen are in xAI's settings
  reference (`docs.x.ai/build/settings/reference`); the `SESSIONS` and `CODEX` ones are not.
- **A timeout never quotes another run's session.** The sessions already in the run's folder are
  noted when it starts, so a retry in the same process or a second grok in a `--task` folder is not
  taken for this run (a newer file time used to win); if two changed, the note says so. Grok's
  slug + `.cwd` form for long folder paths, macOS's `/var` → `/private/var`, a relative `GROK_HOME`
  and one unreadable folder no longer hide the record.
- **«No session record» no longer advises a longer timeout**: it says Grok stopped before opening a
  session and how to check by hand. A transport decode error on its own is reported as the stall it
  is.
- **With bypass on, the reviewer cannot `read_file` the other CLIs' credential stores**:
  `~/.claude.json`, Claude Code's `.credentials.json`, every `auth.json`, `oauth_creds.json`, Claude
  Desktop's and Cursor's MCP configs, Kimi's config.
- **macOS / Linux**: the Grok binary is found as `grok`, also in `~/.grok/bin` (it was looked up as
  `grok.exe` only), and the `~/.grok/rules` preflight follows `GROK_HOME`.
- 1.111.2's notes said Grok no longer imports from Claude Code: Claude Code plugins still load (Grok
  has no switch for them), and so do the files in `~/.grok/rules/`. INSTALL.md now says so in the
  heading.

## 1.111.2 — 2026-10-13

The Grok Build channel (`grokbuild`) had stopped answering: on 2026-10-13 no panel got a review from it, and each run sat until its 40-minute timeout.

- **Grok no longer imports Claude Code, Cursor or Codex.** Out of the box Grok Build loads the MCP servers in `~/.claude.json`, Claude Code's skills, rules (the global `CLAUDE.md`), agents and hooks, and the same parts of Cursor and Codex. One MCP server that existed only in `~/.claude.json` made two servers fail at start and a built-in tool start and never return — six of six test runs hung, while the same call with the imports off answered in 10 seconds. The imports also sent your own instructions (about 9,700 tokens) and a list of your skills to xAI with every brief. The harness now sets the 18 `GROK_<CLAUDE|CURSOR|CODEX>_<SKILLS|RULES|AGENTS|MCPS|HOOKS|SESSIONS>_ENABLED=0` switches for each review; Grok's own config, MCP servers and your shell logins stay. Claude Code plugins still load: Grok has no switch for them.
- **A timeout now says where Grok stopped.** Grok prints its result only when it exits, so a stopped run used to report just «took longer than its allotted time». The harness now reads Grok's own session record and names the tool that started and never finished and the MCP servers that failed at start, with their own diagnosis. Grok runs in a folder of its own per process, so two panels running at once cannot read each other's record, and a timeout now also stops the MCP servers Grok had started.

## 1.111.1 — 2026-10-13

The Muse Code channel (`mcwonder10cont`) after its first review panel and a look at what it could reach.

- **Built-in web tools only.** With approvals off, every MCP server in the user's Muse Code settings started and the reviewer could reach its tools without a prompt — on the maintainer's machine 9 servers and 163 tools, among them creating GitLab issues and merge requests, paid Firecrawl crawls and monitors, and a browser driving the user's own Chrome. `--disable-write` and `--disable-shell` do not cover MCP tools, and Muse Code refuses to switch off a configured server for one run. The channel now passes `-c run.toolset=["web_fetch","web_search"]`: the servers still start, but the model is offered only those two tools. Measured on a live run: 2 tools offered, 0 MCP calls, the web question answered with the page quoted. The brief and its attachments arrive inline, so no file tool is needed.
- **No `MUSE_*` variables reach the CLI.** The Muse Code binary reads 87 `MUSE_*` names; the old filter (drop names containing KEY, TOKEN, SECRET or PASSW) let all of them through, among them `MUSE_CUSTOM_HEADERS` (extra HTTP headers, where a credential can ride), `MUSE_TRANSPORT_TRACE`, and the session ids Muse Code sets for its own child processes. The run needs none of them.
- **A run is OK only when it says so.** A crash after part of the answer had streamed (no final record, exit 1) used to be graded OK. Now the run must end with a completed record and exit 0; anything else is a warning, and the partial text is still saved.
- When `muse export` gives no token counts for a session, the report says so instead of leaving the columns silently empty. A toolset that names a tool the machine lacks (Muse Code then refuses the whole run) has its own diagnosis.

## 1.111.0 — 2026-10-13

Muse Wonder through the Muse Code CLI, on your Muse Code subscription.

- **New channel `mcwonder10cont` (kind `musecode`).** Meta's Muse Code CLI running `muse-wonder-1.0-contributor`, the Contributor variant of Wonder that only Muse Code subscriptions offer (the Model API answers 404 for that id). Standard panel only. It is first in the Wonder cascade: a round runs it wherever the Muse Code binary is installed, and `wonder10` / `orwonder` only where it is not. Nothing is billed per token; the run counts against your plan.
- **No API key reaches it.** Muse Code prefers `META_API_KEY` over your login and would bill that key instead of the plan, so the CLI gets an allowlisted environment: no `META_API_KEY`, no `MODEL_API_KEY`, no other vendor's key. Not signed in: the run stops at once with `missing meta credentials`, and the report says to run `muse login`.
- **No writes, no shell, no approval prompts.** The CLI runs with `--disable-approval --disable-write --disable-shell`: it reads and searches, writes no file and has no shell; its OS sandbox stays on. The built-in read-only permission profile was tried first and hung: the first web fetch asked for a network approval that a headless run cannot give. Approvals off means the safety directive rides the brief, as on the other CLI channels without prompts. The prompt goes in through a file, never the command line; on Windows the harness runs the Muse Code binary itself, not the `muse.cmd` launcher.
- **Effort `xhigh`.** Measured on 2026-10-13 on a subscription: the CLI accepts `max`, but `max` thought no more than `xhigh` (reasoning 397–428 vs 441–455 tokens, n=2 each, all answers correct). The channel's ladder stops at `xhigh`; a tier or an overlay asking for `max` is clamped to it.
- **The prompts of a Contributor CLI seat:** the MCP page-reader hint and the standing training-tier note, as on the other Contributor CLI channels.
- Tokens come from the session's own log (`muse export`), the served model from the server's stream record. No automatic fallback to the per-token `wonder10` when the plan's limit is spent; name it yourself (`--only wonder10`).
- `wonder` / `вондер` and `muse` / `муза` now include the new channel.

## 1.110.0 — 2026-10-13

Muse Wonder, Meta's most capable model, joins the standard panel.

- **New channels `wonder10` and `orwonder`.** `wonder10` is Meta's Model API `muse-wonder-1.0` on your `MODEL_API_KEY`; `orwonder` is OpenRouter's `meta/muse-wonder` for an install with only an OpenRouter key (spend guard $5 per run). Both at effort `max`. They form one cascade, so a round runs one Wonder, the Meta key first. Standard panel only: $4 in / $20 out per 1M tokens, 40× / 100× Spark Contributor. On the API Wonder exists in Standard mode only (Contributor mode is not offered for it).
- **Words.** `wonder` / `вондер` start Wonder. `muse` / `муза` / `meta` now mean both Meta lines (Spark and Wonder). `spark` / `спарк` still mean Spark alone.
- **`--only wonder` under the default cheap panel no longer runs an empty round.** When the chosen panel holds no voice of a named group, that group's channels count as named. A group that has a voice in the panel still cannot pull in its other members.
- **An http channel's own `effort` is no longer overwritten by the tier.** The tier set Spark's ceiling (xhigh) on every model behind the Meta endpoint, so Wonder's `max` would have gone out as `xhigh`. The Spark channels declare no effort of their own and are unchanged. `echocheck.py` now varies such a channel's own effort.
- Measured on 2026-10-13: through the Meta API, `max` vs `xhigh` gave 988–1019 vs 697–792 output tokens (n=2 each, disjoint). Through OpenRouter, `max` is accepted: reasoning 1151–1431 vs 1019–1197 tokens (overlap at n=2). All 8 answers were correct.

## 1.109.0 — 2026-10-12

Where a run writes: no new folders in a drive root, and no CLI started under a project's instruction files.

- **A new folder directly in a drive root is refused.** `--out D:\r149-panel` (or `--workdir` with `--task`, or the default `./reviews` started from `D:\`) stops with exit code 2 before anything is created, and the message names a place made for scratch: the per-user folder (`%LOCALAPPDATA%\ai-second-opinion\runs`, `~/Library/Application Support/...`, `~/.local/state/...`), or a top-level `AI_Scratch` folder when the drive already has one. 1.108.4 created the folder and its `run.log` even on `--dry-run`. A top-level folder that already exists is not new, so a panels folder kept there for months keeps working. Why: one project's sessions left 60 folders in the root of one drive, a fresh `<round>-panel` / `-tmp` / `-out` each time, because every instruction said only where NOT to write.
- **`--out` inside a project: the answers stay, the CLI working folders leave.** codex (`-C`) and agy (cwd) run inside their `<channel>-ws` folder, which sat inside `--out`; with `--out` below a `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `.git`, a CLI started there loads that project's instruction files and sends them to its vendor (the leak `neutral_cwd()` already closed for every other kind). Now the `-ws` folders go to a fresh folder under TEMP (swept after 3 days) and the plan and `run.log` say so; the answers, `REPORT.md` and `run.log` stay where you asked. Refusing such an `--out` was not an option: of 463 runs on one machine, 212 had it, in 8 projects. A marker in the home folder itself is user-level and does not count (TEMP sits below it on Windows, so moving could not help).
- `--help` explains both rules; SKILL.md's example no longer puts the brief and the run in TEMP.

## 1.108.4 — 2026-10-11

- **MiMo CLI: Claude-import disabled in the child process.** `mimo.exe` scans every changed Claude transcript on startup — measured 8.65 GB peak RSS on the operator's machine (×14 per transcript byte), versus 0.75 GB with the import off. The harness now sets `MIMOCODE_DISABLE_CLAUDE_IMPORT=1` in the subprocess environment so that panels never trigger the scan, regardless of the parent session's own env. Timeouts unchanged.

## 1.108.3 — 2026-10-11

- **Kimi K3 (NVIDIA) timeout raised from 60 to 90 minutes.** P310 in iron-benchmark timed out at 60 minutes with 16 API requests done and 24K output tokens written — Kimi had captured routes and archives but had not started the review itself. The NVIDIA free endpoint's 45–56 s first-token latency on every step means a 6-question brief with SSH and dual-repo access does not fit in an hour.

## 1.108.2 — 2026-10-08

A garbled vendor reply is now named instead of offered for reading; REPORT shows, per model, what it was sent and what it wrote; opencode's token and cost figures come from its own store.

- **A reply that is not language is marked CORRUPTED OUTPUT.** Since 6 October NVIDIA's free `moonshotai/kimi-k3` endpoint has returned, on every Kimi run that produced text (4 of 4 here, 0 of 43 before), its own control tokens (`<|close|>`, `<|reserved_token_N|>`) mixed with fragments of words in several languages. Our request, the Kimi CLI and our code were unchanged; another client reported the same output from the same endpoint (GitHub `Alishahryar1/free-claude-code#2018`). 1.108.1 graded such a reply «UNVERIFIED — text present, read it», put it first in the reading order and blamed the brackets on the transport. Now a reply with three or more control tokens glued to text, or six per cent or more of mixed-script words, is `ok: false` with a «CORRUPTED OUTPUT» warning and banner, REPORT says «🔴 CORRUPTED», HANDOFF tells the reader not to open it, it does not count as an answer, and a run-time fallback still fires. A review that only quotes or discusses these tokens is not flagged (code spans and fences are blanked first); checked on 1 575 saved answers: the 4 garbled ones, no others.
- **REPORT: «Tokens per model».** For every channel that ran: what we SENT it (the system layer plus the brief as that channel received it, inline attachments and the bypass safety directive included) and what it WROTE, in characters and ≈ tokens (UTF-8 bytes ÷ 4, the harness's existing rule), beside the vendor's own meter, which now says `unknown` instead of `-` where the vendor reports nothing. The same `sent` / `wrote` figures are in `diagnostics.json`. A CLI agent's billed input is routinely 10-50× what was sent: it reads files on its own and re-sends the conversation at every step.
- **opencode channels: tokens and cost from opencode's own store.** The CLI's JSON stream sometimes carries no per-step usage frame, so the harness under-counted: a live run recorded 93 output tokens and no cost where opencode's store said 2 546 and $0.0097. The harness now reads the session's totals, including sub-agent child sessions, from `opencode.db` (read-only, by the stream's session id, numbers only; never `auth.json`), also after a timeout, and notes when the stream fell short.
- **Muse Spark on an OpenCode Go plan: the `ocspark13go` channel, off by default.** With an OpenCode Go subscription, `{ "channels": { "ocspark13go": { "enabled": true } } }` in `~/.claude/model-orchestration.local.json` runs Spark on the plan (`#xhigh`, its ceiling: `#max` is refused) and keeps the free `ocspark13free` as its run-time fallback: one Spark per round, and the free one runs only when the plan run gave no answer. A plan-limit stop arrives as `provider.quota`, the same code as the free quota's; its diagnosis now names both and the fallback.

## 1.108.1 — 2026-10-06

Fixes from the review panel on 1.108.0: a failure that already cost money is no longer re-run automatically, and the Qwen salvage keeps every answer it can.

- **A channel that already billed money is not re-run by itself any more.** The automatic retry after a transient stream error (since 1.84.0) also re-ran channels whose failed attempt had been billed: on 6 October GLM 5.3 billed $1.75, died on an upstream 429, and the retry billed again ($2.87 in all). Such a channel is now listed as «NOT AUTO-RETRIED - already billed», with what it cost and how to try again (the same command with `--only <channel>`); failures that cost nothing are still retried once. A metered API channel that reported no price but did generate tokens counts as billed.
- **Qwen Code CLI salvage, from the panel's findings.** HANDOFF.md now tells an answer read back complete from the CLI's session log («📜 Recovered») from a resumed, partial one («🛟 Salvaged»), and gives neither label to a salvage that produced no finished review. If the resume wrote its answer and then stopped, the answer is read from the log instead of being lost. The session is resumed even when its log was not found (an unknown session id fails in about a second). The turn-cap stop (exit 53) is named like the budget stop (exit 55). A stopped run that printed an empty error result, or noise on stdout, is still salvaged, and its cause is read from stderr. A draft followed by a tool call is not taken as the answer, and a salvaged text under 2,000 characters is not graded as a review.
- **Time:** the first run's limit now leaves room for both calls' 60-second kill margins, so the seat stays inside its 45-minute timeout (1.108.0 could overrun it by about two minutes); the salvage gets whatever time the first run left.
- Smaller: `max_tool_calls: 0` now means no tool calls (it became 50); Qwen's automatic skill writing (`memory.enableAutoSkill`) is off for panel runs too; inherited `QWEN_RUNTIME_DIR` and `QWEN_CODE_MEMORY_LOCAL` are cleared, so the session log stays where the salvage looks; the per-run memory folder is removed even when the call fails with an error; the «alias snapshot» note accepts real dates only (`-0902`, `-04-02`, `-2603`, `-2024-11-20`, not `-9999` or `-4096`); HANDOFF.md no longer lists the harness's own quote-check files as answers or as foreign files; the plan line and the registry describe the Qwen timeout as it now works.

## 1.108.0 — 2026-10-06

Qwen 3.8 Max: a run that stops at its budget keeps its answer instead of being run again, and the OpenRouter Qwen is no longer graded a failure for its own snapshot.

- **The OpenRouter Qwen answers; we graded it wrong.** OpenRouter serves the name `qwen/qwen3.8-max` as its dated snapshot `qwen/qwen3.8-max-0902`, and the harness called that a model substitution and failed answers of 22–63 KB. A served model that is the requested name plus a date (`-0902`, `-2026-09-02`) is now a note in the report («ALIAS SNAPSHOT»), not a failure; `-preview` or another model still warns. The README said «0 of our 8 runs answered»: our cost survey had also counted copies of one run as separate runs. Counted once per run, all 3 Qwen 3.8 Max runs since 1 September answered (16 of 20 since 7 August).
- **README prices recounted the same way** (median per distinct run, 1 September – 5 October): Qwen 3.8 Max ≈ $0.59 (was $1.02), GLM 5.3 ≈ $0.19 (was $0.54), DeepSeek V4 Pro ≈ $0.18 (was $0.11), Nemotron ≈ $0.07, paid Kimi ≈ $1.38 (n=3); the standard panel about $1.1 a run (was $1.8), $2.5 with the paid Kimi (was $3.2). «GLM's median is 44 minutes» was one run counted six times: its median is 16.
- **Qwen Code CLI (`qwen38maxcli`, local installs): a budget stop is saved, not re-run.** When the CLI stops itself at its tool-call cap or time limit (exit 55; the turn cap, exit 53, too - corrected in 1.108.1), or reaches our timeout, the harness first takes a finished answer from the CLI's own session log if there is one, otherwise resumes that session once with tools off and asks for the review now. HANDOFF.md marks such a seat «🛟 Salvaged»: a partial review that says which files it did not read. Measured: since 5 October 7 of 8 runs had stopped at our own 50-call cap and 1 at the time limit, and the automatic retry repeated the stop (14–17 more minutes a round). A budget stop is not retried any more; when the salvage fails too, the report names the budget that fired (it used to show only a gitlab MCP warning). The 45-minute timeout is split into 36 minutes of work and up to 9 for the salvage, so a round waits no longer than before (corrected in 1.108.1: that split left out the two 60-second kill margins and could overrun the timeout by about two minutes; it is now 34 minutes of work).
- **Qwen Code runs without sub-agents and without its auto-memory** (`--exclude-tools agent`; the memory is switched off by a per-run system settings file and an empty memory folder, so `~/.qwen/settings.json` is not changed; corrected in 1.108.1: the CLI still reads it for its key and MCP servers, and when a machine-wide Qwen settings file is in use the memory extraction stays as configured, with an empty memory folder). Measured: the memory extractor made one more model call after every answer and wrote «lessons» that later panels recalled.
- Smaller: the registry's `max_tool_calls` now reaches the call (channels.json had it since R128 and nothing read it); a timeout stops the Qwen CLI's whole process tree (best effort on Windows - corrected in 1.108.1); an auto-retried channel keeps its first attempt's cause and time in the report.

## 1.107.0 — 2026-10-05

Everything left open around Kimi K3.

- **«только кими» asks the free Kimi.** `kimi`, `кими`, `к3`, `k3`, `moonshot` and the Kimi model words were aliases of the PAID OpenRouter channel `kimik3`, so «только кими» paid about $1.38 a standard run while the free NVIDIA Kimi was ready. They now name the Kimi family (a group, like `grok` and `mimo`), and the cascade keeps one voice: in the default (cheap) panel the free `nvkimik3`; in the standard panel the paid one only when the free one cannot start. «не используй кими» now drops every Kimi (the NVIDIA one used to stay). `kimik3` by its own name, or «kimi openrouter», still runs the paid one.
- **A timed-out Kimi run stops its agent's commands too.** Measured: a Kimi run stopped at its timeout returned on time, but a 6-minute command its agent had started kept running afterwards. The CLI's whole process tree is now killed (`taskkill /T` on Windows, the process group elsewhere). The panel's worry that the call could hang past its timeout did not reproduce; the same kill covers it.
- **Kimi's numbers in the report.** Tokens (uncached, cached, output) and the model actually served are read from the CLI's own session log after every run; the report showed «-» for them. When another Kimi session on the same machine ran at the same time, the run says so: on one free key they share the rate limit (measured on 22 single-reviewer sessions: 15% of requests refused with 429 when overlapping, 6% alone; 9 of 12 overlapping sessions still finished, so there is no lock).
- **The brief file Kimi reads is deleted when its run ends** (it holds the whole brief; under a non-ASCII output folder it sat in a temp copy for up to three days).
- **The AIHubMix Kimi route has its agent on the same environment allowlist** as the NVIDIA one; it used to see every other vendor's key. That route (`kimik3free`, local installs only) is switched off while AIHubMix keeps answering `no_available_channel` (since 2026-10-02): in the cascade it stood before the paid Kimi.
- Smaller: the env-route check reads the ~160 MB script-installed binary in chunks instead of whole; SECURITY.md no longer says Kimi runs with `--auto`.

## Older releases

147 older releases: the full `CHANGELOG.md` at the repository root (https://github.com/igorsaevets/ai-second-opinion/blob/main/CHANGELOG.md).
