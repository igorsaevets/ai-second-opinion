# Changelog

This copy inside the plugin holds the newest releases only: Anthropic's plugin directory does not inspect a file of 256 KiB or more, and the whole history is larger. The full file is `CHANGELOG.md` at the repository root: https://github.com/igorsaevets/ai-second-opinion/blob/main/CHANGELOG.md

## 1.116.1 — 2026-10-29

Retired the opencode Nemotron channel: its model left opencode.

- **`ocnemotron3ultrafree` is off.** `opencode/nemotron-3-ultra-free` is gone from `opencode models` (v2.1.0 lists only `nemotron-3.5-lightning-free` and `nemotron-3.5-super-vl-free`), and `opencode run` answers `Model unavailable` — the id is retired, not quota-spent (the live free ids answer `Free usage limit reached`). Same class as the free Spark (R154). Off, starts by name only; the cascade still seats ONE Nemotron (NVIDIA endpoint, then the OpenRouter twin); the cheap panel still runs 8 voices.
- **Audit 2026-10-29 (live `opencode models` + live `opencode run` probes + NVIDIA `/v1/models`):** free Spark still gone (known since R154); MiMo/DeepSeek/GLM free ids alive; NVIDIA free endpoint still serves `nemotron-3-ultra-550b-a55b`, `stepfun-ai/step-5-preview`, `z-ai/glm-5.3-flash`. No other registry id is stale on opencode.

## 1.116.0 — 2026-10-29

A free opencode voice now falls back to the paid copy on the same channel, and every Python child runs utf-8.

- **A free opencode model's fallback is the paid copy on the SAME channel (OpenCode Go plan)** — OpenRouter only where opencode has no paid copy of the model. Three new fallback-only channels: `ocmimo26flashgo`, `ocdeepseekv41flashgo`, `ocglm53flashgo` (`opencode-go/*`, subscription, off as voices, local-only: the kit has no Go plan). Rewired: MiMo 2.6 Flash Free → its Go copy (was free GLM, briefly paid OpenRouter), DeepSeek V4.1 Flash Free → its Go copy (new), GLM 5.3 Flash Free → its Go copy (new). The old free→free fallback never fired on `provider.quota` (one shared allowance, refused together); the Go copies carry no `quota_group`, so they survive it. One fallback, never a chain; cascade order untouched; the cheap panel still seats 8 voices.
- **HANDOFF.md names the models when a fallback fires**, not just the channels: `` `X` (modelA) answered for `Y` (modelB) ``.
- **Every Python child inherits utf-8** (2026-10-26 grok 4.6 hang 943ca270: cp1251 + non-ASCII print in a PDF script → `UnicodeEncodeError` → pipe deadlock → 40m TIMEOUT): `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` + `PYTHONUNBUFFERED=1` set process-wide and in every CLI child env (grok, codex, opencode, kimi, musecode, posix), plus a one-line `NO_REPO` preamble (temp `.py` file over multiline `python -c`, `pdftotext -layout` first). Adjudication 2026-10-29: the trigger byte was `é` on G-1055 p.7 — the curly quotes and bullets printed earlier encode fine on cp1251; the exception alone never explained 40 minutes of silence, the parent never drained the pipe (~11 KB before the crash point, ~97 KB total). Residual, not in this release: no inner watchdog on a silent shell tool yet.

## 1.115.2 — 2026-10-24

What the cheap panel found in 1.115.1: the reading list still sent you to Nemotron's answer, and the free-allowance memory kept the wrong second.

- **`HANDOFF.md` no longer counts Nemotron's answer among the files to read.** 1.115.1 marked every Nemotron channel `never_read` for `--ask`, but a panel's reading list still counted that file in its totals, and its ready-to-paste resume block said «read every file». The file is still listed (it is on disk), with `never` in the `read` column, and is left out of every count, the resume block and the launch log's `Handoff:` line; when Nemotron's own fallback answers for it, the fallback line says not to open that file either.
- **An answer clears a refusal recorded in the second its run began.** The state file keeps `recorded` in whole seconds, and 1.115.1 kept a refusal written up to a second before the answering run began for the rest of the UTC day; within that second it now clears (one more start at worst).
- **The skip reason names the right voice and time.** With two voices of the group refused in different parts of a run, the state file named the first by name with the later time; it now names the voice whose part of the run set the time, and the reason says «in a part of a run (the round, its retry or its fallback) that began at». 1.115.1's «never later than the refusal» held for that voice only.
- **Each write of the state file uses its own temporary file**, so two runs ending at the same moment no longer write through one shared `<file>.tmp` (there is still no lock).
- **The `provider.quota` hint and the README no longer quote opencode's FAQ figure** («200 requests/day»): opencode removed it on 2026-10-21. Our own measurement stands, and the allowance can change.

## 1.115.1 — 2026-10-24

The free voices' daily-allowance memory, fixed at its edges, and `--ask` without Nemotron's answer in your session.

- **A run that crosses 00:00 UTC no longer costs the free voice the next day.** A refusal of opencode's free allowance is now stamped with the start of the part of the run it came in (the round, its retry or its fallback), never later than the refusal; one from a part that began before 00:00 UTC is not kept, since the allowance has reset by then. Until 1.115.0 a refusal at 23:58 UTC written at 00:04 kept MiMo v2.6 Flash out the whole next day.
- **One run no longer clears another run's newer refusal.** The state file now records when each refusal was written (`recorded`), and an answer from the group clears only a refusal written before its own run began.
- **The messages say what really happened.** The state file's folder is created when missing, and every `QUOTA:` line and note says whether the refusal was remembered, cleared or could not be cleared (naming the file to delete); until 1.115.0 they promised a skip the run had not written. Every refused voice gets the note, not only the first; the last minute before 00:00 UTC reads «under 1 min», not «0 h 00 min».
- **No other channel falls back into a group refused in the same run, and a refused call is not auto-retried.**
- **`python routing.py` shows today's `[skip]` too**, as a run does.
- **`--ask` names whom it really asks.** The line «--ask goes to N channel(s)» is read off the final plan: on a refused day it no longer counts the skipped MiMo Flash or a twin that does not start, and «not started:» says why.
- **`--ask` no longer prints Nemotron's answer into your session.** `channels.json` marks every Nemotron channel `never_read` (run it, never read its answer); `--ask` still runs it and prints one line with its answer file instead.
- Docs: naming a channel on a refused day means its name, an alias, its model id or a `--set` pin (a group word such as "only opencode" does not); PRIVACY describes the state file as it is now; TROUBLESHOOTING says the allowance is counted per network address. On a refused day the cheap panel runs 5 models, the standard one 11, «CLI only» 4 (on a machine with every key and CLI); the README names StepFun's Step among the models.

## 1.115.0 — 2026-10-24

Gemini 4 Argon on the standard panel, Gemini 3.1 Pro off, and «don't use openrouter» that leaves out all of OpenRouter.

- **Gemini 4 Argon joins the standard panel** (`agy4argon`): Google's Gemini 4 Argon through the agy CLI at effort `high`, the top of the low / medium / high agy 1.4.7 offers for it (`xhigh` and `max` exit 1). The cheap panel, the default, does not run it. "gemini 4 cli", «gemini 4 аргон» or `agy4argon` start it alone.
- **What Argon costs:** no money by itself, but on a Google AI Pro plan it has its own weekly limit, and one review of ours used 22% of it (n=1: a release brief with seven attachments, 17.5 minutes) — about four standard runs a week. Past the limit agy spends AI credits if its «Use AI Credits» setting is on. `--skip agy4argon` leaves it out (README, the standard panel).
- **`agy31pro` (Gemini 3.1 Pro) is off.** agy 1.4.7 no longer offered `gemini-3.1-pro` on our Google AI Pro account (2026-10-24, «not recognized as a known model»); naming it still starts it, and where the model is gone agy refuses at the start in seconds.
- **"don't use openrouter" / `--skip openrouter` now leaves out the OpenRouter Spark too.** `orspark12cont` (Muse Spark 1.2 Contributor on OpenRouter, the kit's Spark without a Meta key) was outside the `openrouter` group since 1.113.8, so those words still ran it on your OpenRouter key. A selftest now checks that the group holds every OpenRouter channel.
- **The update check is once a day:** README, PRIVACY and SECURITY said «at most once a week»; the code checks at most every 24 hours, as INSTALL says.
- On a machine with every key and CLI the cheap panel stays 6 models, the standard panel is 12 (was 11), «CLI only» 5, «CLI only, standard panel» 7 (was 6); `python routing.py` prints yours. Without the agy CLI the standard plan still lists `agy4argon`, which fails at dispatch with «binary not found»; Argon has no other transport in the kit.
- The two fixes above came from Gemini 4 Argon's first review (of the 1.113.8 release brief); it refuted the brief's planted false claim, as the six voices before it did.

## 1.114.0 — 2026-10-24

Free NVIDIA voices, a paid MiMo Flash only where opencode is missing, and timeouts that hold.

- **Step 5 Preview joins the cheap panel, free on NVIDIA's endpoint.** With opencode and an `NVIDIA_NIM_API_KEY` it runs as an opencode agent (`ocstep5previewnv`), without opencode as an API call (`nvstep5preview`); one voice either way. The key is free (build.nvidia.com; INSTALL.md, «NVIDIA»). Without it the plan says «NVIDIA_NIM_API_KEY not set (a free key: build.nvidia.com)» and the voice ends at once, spending nothing.
- **Nemotron 3 Ultra runs first on NVIDIA's free API** (`nvnemotron3ultra`) when the key is set, then through opencode (`ocnemotron3ultrafree`), then OpenRouter's free copy on the standard panel.
- **MiMo v2.6 Flash without opencode: a PAID OpenRouter copy** (`ormimo26flash`, $0.14 / $0.28 per 1M tokens, about $0.006 a review; its first live call took 1.9 s and $0.0003). The plan marks it PAID. It is `twin_only`: where opencode is installed it never takes the voice, not when you skip the free copy, not with `--only openrouter` or "without opencode", not on a day the free allowance is spent; name it to pay for it. Until 1.113.9 a machine without opencode lost this voice.
- **The plan names what is missing.** A group with no member that can run here lists each member's gap («opencode CLI not found», a key with its hint) instead of «its key or CLI is missing».
- **An API voice's `timeout` bounds the whole call**, inside the read (headers within 300 s, the per-read limit re-armed to the time left, a 300 s stall limit on NVIDIA); a cut answer is kept as partial and never retried automatically. On 1.113.8 one read ran 60 minutes under a 20-minute timeout.
- **A CLI agent's `timeout` stops its whole process tree** (opencode, mimo, Claude Code). Until 1.113.9 only the launcher (`opencode.CMD` on Windows) was stopped: in our free panel on 2026-10-24 a Step 5 agent held the round 3,980 s against its 1,200 s timeout.
- **opencode on NVIDIA**: opencode reads `NVIDIA_API_KEY`; the tool copies your `NVIDIA_NIM_API_KEY` into that name in the opencode process only, and the plan's billing line names opencode's own `nvidia` provider and the key.
- On a machine with every key and CLI the cheap panel is 6 models (was 5), the standard panel 11 (was 10), «CLI only» 5 (was 4); `python routing.py` prints yours. `nvglm53flash` (GLM 5.3 Flash on NVIDIA's free API) ships off, after the opencode GLM in its group.

## 1.113.9 — 2026-10-23

What a six-model panel found in 1.113.8's notes and README, each claim checked against the shipped code and registry.

- **run.log and diagnostics.json name no home folder.** It is written as `~` in both; the console keeps the real path. In 1.113.8 a run's diagnostics.json held your user name in every path under your home folder. Paths outside it (the brief, `--out`, attachments) are kept, and the README now says to glance at them before a public bug report.
- **The builder refuses a `distribution: kit` channel that is off** (`package.py`, before it removes anything). Until 1.113.8 it switched such a channel back on, so an owner's «off» could ship on, with only a selftest in front of it. Its `local` line now says «a key, a plan or a CLI», not «a vendor key».
- **MiniMax M3.1 Flash through opencode:** `ocminimax31flashgo` runs `opencode-go/minimax-m3.1-flash` on an OpenCode Go plan (it answered a live check on 2026-10-23); `ocminimax31flashfree` is retired, because opencode lists no free MiniMax since 2026-10-23. Both ship off, as before.
- **README:** the cheap panel's price is what we measured (on the OpenRouter key only Muse Spark is paid, and only without a Meta key: $0.002 in our first run, each web search extra), not the $0.06 of the MiMo Pro voice that 1.113.8 switched off; the harness never SENDS a key or a password, while a CLI agent opens files by itself (SECURITY.md); prices carry their dates; OpenRouter's search is Exa only where a channel names it; the direct vendor keys reach other versions than the panels; a provider you add to opencode bills per call; «actually opened» holds where the channel reports it.
- **PRIVACY.md and the README's `--skip` line** add the two free opencode voices, `ocmimo26flashfree` and `ocnemotron3ultrafree`: opencode's Zen page (read 2026-10-23) says MiMo's data may be used to improve the model during its free period, and Nemotron runs on NVIDIA's trial terms.
- **Corrections to 1.113.8's notes.** «An API channel that opens pages stops at its timeout» holds for the pages it opens; one API read is not bounded by the `timeout` yet (in the panel on 1.113.8 one read ran 60 minutes under a 20-minute timeout), and a deadline inside the read is planned. «The kit's Nemotron voice is unchanged (`ocnemotron3ultrafree`; `ornemotron3ultra` without opencode)»: without opencode, `ornemotron3ultra` runs on the standard panel only. The MiniMax channels it announced as needing «a key or a plan beyond the kit's OpenRouter one»: the free one needed neither, and it is gone (above).
- **Selftest.** New suite `suite_r160_i4_home_scrub`; `suite_r160_kit_defaults` (f) (g) test the builder's refusal; the R163 MiniMax mechanics run on a fixture.

## 1.113.8 — 2026-10-23

The owner's choices for the kit's default panel, and everything that ran on the author's machine first since 1.113.7.

- **The cheap panel is 5 models (was 6), the standard panel 10 (was 11).** Grok Build plans Grok 4.6 (was 4.7: the same CLI, subscription and `xhigh` ceiling). MiMo v2.6 Pro is off by default (`ormimopro` on OpenRouter, and `mimov26pro` through the mimo CLI): in the cheap panel it was almost all of the ~$0.06 a run and the slowest voice (median 14 minutes), and on the author's machine one round of the mimo CLI agent read 208K input tokens plus 6.03M cached ones. To keep it: `--only ormimopro` for one run, or `{ "channels": { "ormimopro": { "enabled": true } } }` in your settings file. «CLI only» stays 4, «only opencode» 2, `--ask` 3.
- **The Spark voice is Muse Spark 1.2 Contributor**: `spark12cont` on a Meta key, otherwise `orspark12cont` on OpenRouter ($0.10 / $0.20 per 1M tokens, the price of the 1.3 Contributor; on OpenRouter since 2026-08-21, no end date). Spark 1.3 Contributor (`spark13cont`, `orspark13cont`, the Go-plan `ocspark13go`) is off and starts when you name it; on an OpenCode Go plan the Spark channel to switch on is now `ocspark12go`.
- **Found and fixed before this release:** `package.py` switches a channel with `distribution: kit` that is off back ON when it builds the kit. The unreleased source had switched the OpenRouter Spark 1.3 Contributor off that way, and a real build turned it on again: this kit's cheap panel would have run 7 voices, two of them Spark and one of those two paid. A channel meant to be off in the kit now carries `distribution: both` (`ormimopro`, `orspark13cont`), and a selftest refuses any `kit` channel that is off.
- **Key and credential files (the owner's rule of 2026-10-22).** A channel whose cost is free, or that is a training-data (contributor) tier, may not read `.env*`, `~/.ssh`, `~/.gnupg`, `auth.json` or CLI configs; every other channel may. For your Grok Build this is a change: `grokbuild` (a subscription) no longer gets the key-file deny rules, so it may read such a file when a task needs it, and it is told never to copy a key into its reply. Those rules also made Grok refuse a shell command whose files it could not tell (9 of 1,125 shell calls on the author's machine); `grokbuild` no longer carries those rules. Under `--task` the directive follows the same rule. Grok's shell sees the environment variables of the shell that started the tool unless your Grok config sets a policy (SECURITY.md). **Not done yet:** only Grok has rules for this, so the cheap panel's free opencode voices (`ocmimo26flashfree`, `ocnemotron3ultrafree`) and a Kimi Code CLI channel you switch on are not kept out of key files today.
- **opencode channels say how they bill.** The plan, the preflight, the call log and the `--ask` header name the provider's billing: opencode Zen «free model», the OpenCode Go plan, «PAID per call (cost metered)» with the provider's name for a provider your own opencode config adds, or «billing UNKNOWN». 1.113.7 printed «free model, no API key» for every opencode channel, so a paid provider you added was called free. A channel your settings file adds prints a block of 2-4 lines instead of one plan line per field (on the author's machine, with his added channels, the cheap plan went from 342 lines to 188).
- **Grok: an answer is no longer lost after its turn ended.** When Grok's session says the turn ended but the `grok` process stays alive, the tool stops it 30 s later and keeps the answer (from its output, else from Grok's own session record); the note says `LINGERED` (1 of 456 Grok runs on the author's machine). An HTTP 402 from xAI now reads `GROK SUBSCRIPTION LIMIT` (the subscription's usage is used up - not the brief, not the tool), a refusal says who refused, and each run records the Grok CLI's version.
- **An API channel that opens pages stops at its timeout.** Its `timeout` worked as an idle limit, so one channel ran 75 minutes under a 20-minute timeout; now it opens no page after the `timeout` and answers from what it read (`time_stopped` in the record).
- **Muse Code.** Every Muse Code call passes `--contributor-mode` from the channel's model id: with contributor mode on in Muse Code's own settings, a request for the Standard `muse-spark-1.3` was answered by the Contributor model (measured 2026-10-22). New Muse Code channels for Spark 1.3 (`mcspark13`, fallback `mcspark12`) and Wonder Standard (`mcwonder10`) ship off; «muse code» / `musecode` is a group word. The kit's Wonder seat is still `mcwonder10cont`, by name.
- **New channels that ship off** (they need a key or a plan beyond the kit's OpenRouter one): Nemotron 3 Ultra on NVIDIA's own free endpoint (`nvnemotron3ultra`, with the OpenRouter `:free` copy as its fallback), Step 5 Preview (`nvstep5preview`, `ocstep5previewgo`) and MiniMax M3.1 Flash through opencode (`ocminimax31flashgo`, `ocminimax31flashfree`); the plan says when a channel is the same model as another one over another transport. The kit's Nemotron voice is unchanged (`ocnemotron3ultrafree`; `ornemotron3ultra` without opencode).
- **Docs (EN + RU).** README «You say» rows, the panel tables and totals, INSTALL (`--ask`, OpenRouter, Muse Code), PRIVACY, SECURITY, TECHNICAL («What 1.113.8 changed in a run»), TROUBLESHOOTING («A Grok run says GROK SUBSCRIPTION LIMIT»).
- **Selftest.** New suites `suite_r158_i2_opencode_billing`, `suite_r162_nemotron_nvidia`, `suite_r163_step5_minimax_chain`, `suite_r163_i3_hotfix`, `suite_r164_muse_spark`, `suite_r165_grok_turn_ended`, `suite_r165_i2_task_keys`, `suite_keys_read_policy` and `suite_r160_kit_defaults`.

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

## Older releases

157 older releases: the full `CHANGELOG.md` at the repository root (https://github.com/igorsaevets/ai-second-opinion/blob/main/CHANGELOG.md).
