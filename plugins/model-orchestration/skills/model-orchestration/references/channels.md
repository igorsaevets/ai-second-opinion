<!-- Reference file for the model-orchestration skill. Not loaded automatically:
     SKILL.md points here and the model reads it on demand. Keeping it out of SKILL.md
     is what keeps that file under the 5,000-token budget an auto-compaction re-attaches. -->

# Channels — wire parameters and CLI traps

## 4. HTTPS channel wire parameters


Only needed when writing your own client or debugging a bad reply. The harness sends this:

```jsonc
{
  "model": "muse-spark-1.1",
  "max_tokens": 131072,
  "system": "<system prompt>",
  "messages": [{"role": "user", "content": "<the brief>"}],
  "thinking": {"type": "enabled", "budget_tokens": 60000},
  "output_config": {"effort": "xhigh"},
  "tools": [{"type": "web_search_20250305", "name": "web_search"}],
  "stream": true
}
```

Headers: `Authorization: Bearer <key>`, `Content-Type: application/json`,
`Accept: text/event-stream` when streaming.

**`output_config.effort`** is a real API parameter, not a vendor extension. Values `low` · `medium`
· `high` (default) · `xhigh` · `max`. Use the highest the endpoint accepts; **never below `high`**.
`adaptive` is a *thinking* mode and is never a valid effort value.

**`budget_tokens`** minimum 1024, must be below `max_tokens`, and is a *target* not a cap.

**Probe first.** The harness sends the real system prompt and real message with `max_tokens: 64`
and no thinking, before the expensive call. Content filters on this endpoint are **cumulative over
a long payload**, so a packet that passes in pieces can fail whole. Learning that for 64 tokens
beats learning it for 100,000.

> **Provider divergence.** `thinking.type:"enabled"` + `budget_tokens` is what works on this Meta
> endpoint. On Anthropic's own API the same form is **deprecated on Claude 4.6 and rejected with a
> 400 on 4.7+**, where the current form is `{"type":"adaptive","display":"summarized"}`. The harness
> catches a 400 mentioning `thinking`, flips the form, and retries once.

Expected response block types: `redacted_thinking`, `server_tool_use`, `web_search_tool_result`,
`text`. Only `text` blocks are the answer.

**Streaming above a 32,000 thinking budget is mandatory, not a preference** — so the `strategic`
and `deep` tiers stream and the other two do not. Anthropic's own guidance: *"Avoid setting a large
max_tokens value without using streaming. Some networks may drop idle connections"*, and *"For
thinking budgets above 32k, use batch processing to avoid networking issues."* A blocking call at
that budget **sometimes** works, which is worse than always failing, because you will trust it.

---

## 5. Codex CLI


```powershell
Get-Content -Raw brief.md | codex exec --sandbox read-only --skip-git-repo-check `
  -C "$env:TEMP\codex-ws" --color never -c tools.web_search=true `
  -c 'mcp_servers.firecrawl.disabled_tools=["firecrawl_search","firecrawl_crawl","firecrawl_agent","firecrawl_extract","firecrawl_parse","firecrawl_interact","firecrawl_interact_stop","firecrawl_monitor_create","firecrawl_monitor_update","firecrawl_monitor_run","firecrawl_research_search_papers","firecrawl_research_search_github","firecrawl_research_related_papers","firecrawl_research_read_paper","firecrawl_research_inspect_paper"]' `
  -o "$env:TEMP\codex-out.md" -
```

**The second `-c` is the Firecrawl credit policy.** Codex keeps its own MCP registry, entirely
separate from Claude Code's, so `permissions.deny` in `~/.claude/settings.json` does **nothing**
for a Codex run. Codex's equivalent is `mcp_servers.<name>.disabled_tools`.

Since 2026-07-26 the same 15 names are **permanent** in `~/.codex/config.toml`, so a bare
`codex exec` is already protected and the flag above is belt-and-braces. the operator granted a one-time
exception for that single edit; the standing rule below still holds for everything else in that
file. Patch script, which never prints the file's contents: `patch_codex_firecrawl.py`
(idempotent, backs up, rolls back on a parse failure).

⚠️ **Keep the two lists in sync.** `-c` *replaces* the config value rather than merging, so if
`disabled_tools` in `config.toml` ever grows, `FIRECRAWL_DENY` in `orchestrate.py` must grow with
it or a run through the orchestrator silently reverts to the shorter list.

Still usable: `firecrawl_scrape`, `firecrawl_map` (1 credit flat, any number of URLs).
Verified 2026-07-26 — `codex mcp get firecrawl --json` reports all 15 back with no flag passed.
Soft-policy companion: `~/.codex/AGENTS.md`.

| Wrong | Symptom | Right |
|---|---|---|
| `--search` | `unexpected argument '--search' found` | `-c tools.web_search=true` |
| `-a never` | `unexpected argument '-a' found` | omit; `exec` is already non-interactive |
| positional prompt while stdin is piped | hangs on `Reading additional input from stdin...` | pipe the prompt, end argv with `-` |
| `-s read-only` when it must write | cannot write the report | `-o <file>` still works under read-only |
| `-C` at a non-repo dir | git repo check failure | `--skip-git-repo-check` |
| ~~passing `-m` or an effort override~~ | **superseded 2026-07-26** | see the box below — the override is now REQUIRED |

> 🔴🔴 **Model override is mandatory, and the model has ONE home: `channels.json` →
> `channels.codex.model`.** `orchestrate.call_codex` reads it there (`_registry_default`) since
> 2026-08-02; env `CODEX_MODEL` / `CODEX_EFFORT` still override per invocation. Hand-rolling a
> call? **Open `channels.json` and pass what it says** — never copy a version out of prose,
> including this line. Current value **2026-08-02: `gpt-5.4` / `xhigh`** (the operator: «поменяй модель
> 5.5 на 5.4»; 07-30 gpt-5.5, 07-26 gpt-5.4, before that gpt-5.6-sol/max — it rotates).
>
> 🔴 Two traps, both found 2026-08-02 while making that one-word change:
> * the value lived in **two** places, the registry entry and a literal in `orchestrate.py`, and
>   **the literal won** — so editing the registry alone changed nothing, while its own `_comment`
>   promised "exactly one place to edit when a weekly limit runs out";
> * `codex-model-current` in **auto-memory** was named canonical here and in the global
>   `CLAUDE.md`. Auto-memory is **per project**: there were copies in two projects, none in a
>   third, and nothing keeps them in step. A machine-wide setting cannot have a per-project home.
> Until 2026-08-02 `call_codex` also never returned which model it ran on, so `АНАЛИТИКА.md`
> could not show it. **A setting invisible in the output is indistinguishable from one that never
> applied** — which is exactly how the decorative registry entry survived unnoticed.
>
> This reverses the older "never pass `-m`" rule, which existed only to stop the configured profile
> being *downgraded* by accident. `~/.codex/config.toml` still declares `gpt-5.6-sol` + `max` and is
> **still off limits — never edit it, never quote it, it holds third-party keys.** Overriding per
> invocation is precisely what lets both rules hold at once. Never go below `xhigh`.

**Completion control.** Codex emits partial output and keeps thinking. **A file whose last line is
not the marker is incomplete** — do not parse it. Poll ~60 s. Check liveness with `Get-Process
codex` *and* the growth of the newest `~/.codex/sessions/*.jsonl`; a process that exists but whose
rollout has been static for minutes may still be reasoning, so use both. Effective context measured
**~272K tokens on CLI 0.144.6**, well under the advertised window — treat that as a floor and
re-measure if it matters, because the CLI reached 0.146.0 within five days of the measurement
(`doctor.py` prints the installed version; do not trust a version written in prose).

**Never pipe the run through `Out-String`** — stdout buffers and the output file stays empty until
exit, which is indistinguishable from a hang.

---

## 6. Antigravity CLI (`agy`) — NOT on PATH


Re-measured end to end 2026-07-31; the full study with raw event logs is in
`(the author's raw measurement log, not shipped)`. Several rows of the old table were wrong.
Ask `doctor.py` for the installed version — this file used to pin one and it went stale in a week.

```powershell
& "$env:LOCALAPPDATA\agy\bin\agy.exe" -p $brief --model "gemini-3.1-pro" --effort high `
   --agent deep-researcher --mode plan --sandbox `
   --output-format stream-json --print-timeout 25m
```

### 6.0 🔴 `--mode` is unvalidated, and it is not what makes the run read-only

Measured 2026-07-31 in headless `-p`, four values including a deliberate nonsense one:

| passed | exit | `permission_mode` in the `init` event |
|---|---|---|
| `--mode plan` | 0 | `request-review` |
| `--mode default` | 0 | `request-review` |
| `--mode accept-edits` | 0 | `request-review` |
| `--mode definitely-not-a-mode` | 0 | `request-review` |

Two consequences, and the second is the one that matters. **The flag is not validated** — a typo
such as `--mode paln` runs happily, silent, exit 0. And **its effect is not observable in the
telemetry**, so nothing in the event log can confirm it was applied.

Therefore: do not treat `--mode plan` as the read-only guarantee. What actually constrains the run
is the permission configuration written by `patch_agy_permissions.py` (§6.1), which `doctor.py`
checks and which shows up as the granted-tool list in `init`. This is the third time in this one
channel that the operator's rule has held — a system prompt does not restrict tool access, an agent
persona does not, and a documented CLI flag does not either. Only permission rules do.

Note also that the CLI's own bundled docs (`~/.gemini/antigravity-cli/builtin/skills/
antigravity_guide/references/cli.md`) say **nothing** about `--mode`. When agy was asked to verify
a claim about that flag it cited "the official CLI reference" while having opened only that file —
it restated the claim it was given as though it were documented. Treat a channel confirming a
claim about *its own* CLI as the weakest possible evidence.

### 6.1 🔴 One denied tool discards the whole run

Headless mode cannot show a permission prompt, so any tool left at the default `ask` is
auto-denied — **and a single denial throws the entire run away**. Measured: 29 successful tool
calls, 10 web searches, 6 pages opened, then one `mcp(jina-mcp-server/read_url)` denial, and the
CLI returned `response: ""` with **`status: "SUCCESS"` and exit code 0**. Five attempts out of
five died this way. The only visible symptom is an empty answer.

Fix it once: **`python patch_agy_permissions.py`** (in this directory). It adds allow-rules for
the free read-only web tools and deny-rules for every metered Firecrawl tool, to
`~/.gemini/antigravity-cli/settings.json` — the **only** config scope this build honours.
Backed up, idempotent, `--dry-run` and `--revert` included. Needs the operator's go-ahead: it is his
file, and the harness classifier blocks it otherwise.

After the fix, same brief: 49 tool calls, 23 search/fetch, thinking 1 831 → **5 891**.

What does **not** work, all tested: workspace `.agents/settings.json` (not read), workspace
`.agents/mcp_config.json` (not read), agent frontmatter `inheritMcp: false` (governs subagents
only), and **telling the model in its system prompt not to call MCP tools — failed 5/5**. The
MCP servers' own tool descriptions outrank the agent prompt. Prose does not restrict tools;
permission rules do.

Never `--dangerously-skip-permissions`: it also unlocks `firecrawl_crawl` (1 credit per page,
unbounded). Until this patch, agy had the entire Firecrawl toolset open — the denies that
protected the Codex channel since 2026-07-26 had never been applied here.

### 6.1a 🔴 The end marker can be satisfied by a refusal

Not agy-specific — it bit **Codex** on 2026-07-31 and it applies to every channel. Sent a
tokenised I-485 public-charge review, Codex returned 162 bytes:

> *"I can't provide the requested review because I'm not allowed to give legal advice or analyze
> an individual immigration filing strategy."* — followed by a clean `AOS-REVIEW-COMPLETE`.

Exit 0, marker present, non-empty → reported **OK**. A declining model still obeys the
formatting instruction. **A marker proves the model reached the end of its turn, not that it did
the work.** `refusal_check()` now runs on all three channels: a refusal tell in the first 400
characters *plus* a short body is a refusal; a short body alone is "suspiciously short". Both
demote the channel to PROBLEM. A long review that merely contains "I cannot provide a date
for X" does not trip it.

**Do not conclude from this that Codex is unusable on legal work — that was the first reading
here and it was wrong.** The refusal was a framing bug, not a subject ban: with
`--system systems/legal-research.md` and the brief written as source-verification, Codex answers
the identical six claims in full and produces the **best-cited** review of any channel. See §7.0.

### 6.2 `status` and the exit code both lie — gate on the marker

| observed | reality |
|---|---|
| `status: "SUCCESS"`, exit 0 | empty answer after a permission denial |
| `status: "ERROR"`, exit 0 | **complete** 1 417-char answer; one late MCP call hit a transient 503 |

The literal end marker in the text is the only honest completion signal.

### 6.3 Corrections to the old table

| Old claim | Actual |
|---|---|
| `--effort` is rejected; effort is in the model name | `--effort low\|medium\|high` exists since 1.1.5. It is rejected **only when it contradicts a suffixed slug**: `--model gemini-3.1-pro-high --effort low` → exit 1 in 3 s, *"conflicts with --effort=low"*. Use the **base** slug (`gemini-3.1-pro`) plus `--effort`; `orchestrate.py` now strips a recognised suffix automatically. |
| `--output-format` does not exist | Added in 1.1.8: `text\|json\|stream-json`, plus `--json-schema`. `stream-json` is the **only** way this channel reports tool use — always use it. |
| no telemetry JSON | Per run: `permission_mode`, all 56 offered tools, every tool call with parameters and errors, and `usage` with `input/output/`**`thinking`**`/cache_read` tokens, `num_turns`, `duration_seconds`. |
| `agy models` hangs in a non-TTY | works fine in 1.1.9 |

Effort levels are **per model**: `gemini-3.1-pro` has only `low` and `high` — asking it for
`medium` is another exit-1 launch failure. `gemini-3.6-flash` has all three. `channels.json`
records this and `routing.py` clamps, breaking ties upward.

### 6.4 The schema trap that costs an hour

`stream-json`'s discriminator is **`event`**, not `type`, and each payload is nested under a key
named after the event: `{"event":"result","result":{…}}`. Reading `ev["type"]` matches nothing on
every line — indistinguishable from a model that did no work. MCP calls arrive wrapped as
`call_mcp_tool(ServerName, ToolName, Arguments)`; unwrap them or every server collapses into one
meaningless counter.

### 6.5 🔴 Tool counts prove activity, not grounding

On a real review this channel issued **24 search queries, opened exactly one page, and cited six
URLs**. Two of the five unopened ones were fabricated — real-looking Federal Register document
numbers glued to the correct article slug:

- cited `…/2022-19422/public-charge-ground-of-inadmissibility` → that number is *"Proposed
  Collection; Comment Request"*, 87 FR 54985
- cited `…/2026-15432/public-charge-ground-of-inadmissibility` → *"Agency Information Collection
  Activities…"*, 91 FR 48060

`orchestrate.py` now cross-checks cited URLs against opened URLs and prints `CITATIONS: only N of
M cited URLs were actually opened`. `citecheck.py` does it standalone and also lists pages opened
but never cited — where a contradicting source quietly disappears. **A verdict reached from
search snippets is not a verified verdict**, and this channel reaches them happily.

Grounding varies wildly run to run on the same brief — 1/6, then 4/4, then 5/5. It is not a
property you validate once; run the check every time.

**"Opened" is still not "the page said so".** `citecheck.py --resolve` closes the rest of the gap
for Federal Register citations, because that is where it kept failing — four fabricated document
numbers in one session, each with the *correct* article slug:

```
2022-19286 -> "Amendment of ... (RNAV) Route T-232; Fairbanks, AK"
2022-19422 -> "Proposed Collection; Comment Request"
2026-15432 -> "Agency Information Collection Activities..."
2022-18869 -> "Information Collection Request; Direct Loan Making"   <- and this one was GROUNDED
```

The last one was genuinely fetched: federalregister.gov resolves by document **number** and
ignores the slug, so a wrong number under a right slug returns HTTP 200 and looks researched.
Only resolving the number against the FR public API (no key needed) catches it. The channel's
*conclusions* in that run were correct and independently verified — the receipts under them were
not. That combination is the dangerous one.

Tool output is **not** recorded in the event log (`tool_info.output` carries only a size summary
for `view_file`), so content-level verification of an arbitrary page is not possible from
telemetry. Outside federalregister.gov, spot-check by hand — see §8.

### 6.6 Unchanged and still true

| Wrong | Symptom | Right |
|---|---|---|
| long brief inline via `-p` | fails past roughly **30K characters** (Windows argv cap) | write it to a file, `--add-dir` its folder, keep `-p` to one short instruction |
| `--cwd` | `flags provided but not defined: -cwd` | `--add-dir`, or set the subprocess's cwd |
| default `--print-timeout` | truncates at 5 min | `--print-timeout 25m` |

The `deep-researcher` persona ships inside `orchestrate.py` and is written into the run's own
workspace at `<ws>/.agents/agents/deep-researcher/agent.md` — workspace-scoped **agents** are
discovered even though workspace-scoped settings are not. Its measured contribution is shape
(evidence table, explicit "what I could not verify"), not volume: against a bare run it moved
thinking 5 434 → 5 891 while making *fewer* search calls. The permission fix is what mattered.

---

### 2026-08-08 (round 28): the T54 verdict on `agy` is falsified — it is the PROFILE, not the model

T54 concluded *"agy31pro is structurally unusable headless, use agy36flash instead."* Round 28
killed that: **agy36flash died the same way**, and the event log names the exact step.

```
step_index 10, state ERROR, tool run_command
  CommandLine: python -c "import urllib.request, json;
               res=urllib.request.urlopen('https://pypi.org/pypi/scanquorum/json'); print(..."
```

17 events, 15 steps, dead at 10. So the failure does not track the model at all. The
`deep-researcher` profile **shells out a fresh one-off `python -c` whenever it wants structured
JSON**, and the script text differs every time - which is why three agy31pro runs in T54 each hit a
different denied command. An exact-command allow-list cannot cover a generator.

**Why T54 got it wrong:** agy36flash survived that round because that brief never pushed it toward
a JSON API. This brief embedded PyPI JSON URLs, it reached for `python -c`, and it died. The earlier
"fix" was luck misread as a fix - the failure is conditional on brief content, not on the channel.

**The only allow-rules that would cover it are `command(python *)` or
`--dangerously-skip-permissions`**, i.e. arbitrary execution by an unattended agent. Neither is
authorised, and one vote is not worth it. **Treat `agy` as unavailable in headless runs until the
profile stops generating commands.** The Gemini perspective is still in the panel through
`orgemini36flash` and `goog36flash`, which are transport-different and both delivered.

⚠️ Unchanged from T54 and worth restating: the harness's own failure text advises running
`patch_agy_permissions.py`. That script reports `nothing to do - already patched` and fixes a
different thing. The advice is wrong for this failure.
