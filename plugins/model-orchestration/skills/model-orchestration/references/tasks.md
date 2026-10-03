# `--task` — a CLI carries out a task (R138, kit 1.101.0)

Use it when the user wants a CLI to DO something on this machine — write and run a script, build
a file, collect data — instead of giving an opinion. A review stays the default panel; a code
change in a git repository goes to an isolated-export tool with hidden tests and your review
before apply (on the author's machine `~/claude-tools/ai-workers/delegate.py`; a kit install has
none — give a `--task` that writes a patch file into its workdir instead).

```
python <SKILL_DIR>/orchestrate.py --brief TASK.md --task --only grokbuild --out <dir>
```

- **Workdir**: each channel runs in `<workdir>/<channel>` as its cwd (`--workdir DIR`; default a
  fresh folder under TEMP, copied to `<out>/work/` at the end). Keep it outside any project: a CLI
  loads the CLAUDE.md / AGENTS.md above its cwd and sends them to its vendor (the plan warns).
- **Rules the CLI reads** (TASK DIRECTIVE, front of the brief, + preset `systems/task.md`): write
  only in the workdir; read anything else, change nothing there; never open credential files;
  remote systems READ-only unless named with `--allow-remote "<system: purpose>"` (printed in the
  plan). End with a TASK REPORT (STATUS, RESULT, FILES, COMMANDS, REMOTE WRITES, CHECK) and
  `TASK-COMPLETE`.
- **It is steering, not a sandbox.** Permission prompts are off. Check the report against
  `<CHANNEL>-WORKDIR.json` (created / changed / deleted, from disk) before using the result. No
  auto-retry: a task may already have changed something.
- **Wired kinds**: grok build (needs bypass — without it grok is web-only), opencode, mimo. The
  plan refuses other kinds by name until each is measured live. mimo writes its own `.mimocode/`
  folder into the workdir; that shows up in the manifest.
- **Say "run it"** when you want the result of a script. A brief that asks only for the script
  gets a script (Ironmemo 30c, 2026-10-03).
- **Corrections**: every CLI here can resume a session (measured 2026-10-03): grok `--session-id
  <uuid>` then `--resume <uuid>`; opencode / mimo `run --session <ses_id from the NDJSON>`; codex
  `exec resume <id>`; agy `--conversation <id>`; qwen `-r <id>`; kimi `-S <id>`. The harness does
  not wire it yet — a follow-up is a new `--task` run that names the previous workdir.
- **Measured (R138, on the author's machine)**: an explicit
  "write + run" task was executed 6/6 even in the REVIEW frame — against its TEMP-only default —
  and 4/4 by grok + Spark under `--task`, with shorter replies. Unmeasured: whether the remote
  READ-only rule holds when a task explicitly asks for a remote write.
