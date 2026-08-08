# When it breaks — symptom, cause, fix

Moved out of `SKILL.md` §9 on 2026-08-08 because the skill body crossed the 5,000-token
auto-compaction re-attach budget, at which point the *tail* of the file is clipped silently while
the copy on disk still looks whole. A table read on demand arrives complete; a table that fell off
the end of a re-attached skill does not announce itself. The three lines worth having in working
memory stayed behind as a pointer; everything else is here.

Read this when a run fails, or before diagnosing anything by reading source.

| Symptom | Cause | Fix |
|---|---|---|
| `gaierror` / `getaddrinfo failed` | transient DNS | the harness retries 3× with backoff. If it persists, check the network, not the code |
| `FILTERED` / "content management policy" on the probe | cumulative content filter | neutralise sensitive-looking phrasing **in the sent copy only**, never on disk; strip large appendices; re-probe. Do NOT retry unchanged |
| HTTP 401 | key rotated or expired | run `doctor.py` (§1). Never print the key |
| `SECRETS IN THE PAYLOAD` / `PERSONAL IDENTIFIERS` | the pre-send gate fired | fix the brief at the reported line. Secrets have no override; identifiers warn by default and `--strict-pii` blocks |
| `the route and the flags contradict each other` | `--only`/`--set` re-enabled a channel the route excluded | decide which you meant and pass one, not both |
| HTTP 400 mentioning `thinking` | wrong thinking form for the host | the harness flips the form and retries once automatically |
| Codex output empty, exit 0 | still thinking, or buffered through a formatter | check the marker on the last line; check `Get-Process codex` and rollout growth |
| `agy` returns `jetski ... permission` | it tried to read the brief file | shorten the brief so it goes inline via `-p` |
| `agy` returns an EMPTY answer with `status: SUCCESS`, exit 0 | one denied tool discards the whole headless run | `python patch_agy_permissions.py`. Never `--dangerously-skip-permissions` |
| tool_calls = 0 | model never searched | treat all dated facts as unverified; re-run at a higher tier or split the question |
| `python` not found from another directory | you used a relative path | always use the absolute path in §0 |
| a channel is off and nothing explains why | your own settings file | the resolved plan prints its path and every field it changed, at the top. `~/.claude/model-orchestration.local.json` |
| `ROUTE ERROR: ... names a channel that does not exist` | a typo in that settings file | the message lists every real channel name. This is deliberate: silently ignoring an unknown key would look identical to a channel that is off |
| an update made a channel stop running | you edited `channels.json` in place, and the folder was replaced | the resolved plan and `doctor.py` both name the changed fields, against the reference copy shipped beside the registry. `python upgrade.py --migrate` moves such edits out of the folder for good |
| `ROUTE ERROR: ... sets 'model' on channel ..., which this file may not change` | your settings file was chosen by `MODEL_ORCH_LOCAL`, not by your home directory | only depth knobs (`enabled`, `effort`, `reasoning`, `thinking_level`, `max_tokens`, `fetch_tool`, `web`, `timeout`, `label`, `cost`, `notes`) are accepted from a redirected path, because a repository you cloned can set an environment variable for sessions inside it. Move the file to `~/.claude/model-orchestration.local.json` and everything is accepted |
| `ROUTE ERROR: ... names a channel that does not exist` while you were ADDING one | additions must say so | put `"_new": true` in that block. Required so a misspelt name fails loudly instead of quietly becoming a second channel |
| `REFUSING TO SPEND: your settings file changes where documents go` | a transport change has not been accepted | if you made it: `python routing.py --accept-settings`, once. If you did NOT make it, open the file first — that message exists because the settings file survives every update, so one write to it would redirect a channel permanently. `--dry-run` still works without accepting |
| you want a channel this release does not have | add it yourself | a full block in your settings file with `"_new": true`, including `kind`, `label`, `model` and a `models` entry for it. It faces exactly the same validation as anything shipped |
| `doctor.py` says `skill size ... over budget` | a maintainer's problem, not yours | nothing stops working; the assistant just sees less of the manual. `selftest.py` is where it is a hard failure |

## The general rule behind half of these

A status field is not evidence. `agy` reports `SUCCESS` on an empty answer and `ERROR` on a
complete one; `codex exec` exits 0 on a hard HTTP 400; an HTTP 200 from two of the vendors here
means only that the request parsed, not that the parameter you sent was applied. The end marker in
the text, and the counters in the run log, are the signals that have been measured to hold.
