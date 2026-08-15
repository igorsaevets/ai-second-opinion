# System presets — depth, language and domain framing

Moved out of `SKILL.md` in round 42 to keep that file under the 5,000-token budget an
auto-compaction re-attaches. Nothing here changed in the move; if this file and `SKILL.md`
ever disagree about a preset, this one is the detail and `SKILL.md` carries only the pointer.

`--system` takes a **preset name**, a path, or nothing. Resolution tries the literal path first,
then the skill's own `systems/` directory, so a bare name works from any project directory.

| preset | when |
|---|---|
| `base-depth` | **the default**, applied when `--system` is omitted |
| `legal-research` | any legal / immigration / regulatory brief — read `references/legal-briefs.md` first |

`base-depth.md` is the amplifier that used to be pasted into briefs by hand: maximum depth, first
intuition may be wrong, enumerate alternatives, check for contradictions, name the
unofficial-but-lawful route beside the official one, no length cap, escalate fetch tools, never
reconstruct a citation from memory, and say so when nothing could open a page.

**All presets force English output.** The report is consumed by the orchestrating model, not read
directly by a human, and Cyrillic costs roughly twice the tokens for the same content. A Russian
brief still gets an English answer; quoted sources stay verbatim in their original language with
a translation beside them.

⚠️ **The presets are not interchangeable, and the difference is deliberate.** `base-depth` asks
for "unofficial, grey routes alongside the official one". `legal-research` deliberately omits
that clause: in a regulated domain it reads as *suggest a way around the rule*, which is exactly
what gets the brief refused and what makes the output useless to an attorney. Do not merge them,
and do not add the grey-routes line to the legal preset "for consistency".

`--dry-run` validates the preset name and the brief path before anything is spent; a mistyped
preset fails loudly and lists what exists.

## Timing

Most channels land in 1–4 min. **Codex is the long pole at 8–35 min** and sets the round's
wall-clock — which is also why `--panel cheap` finishes so much sooner: codex is standard-only.
Run a full round in the background. Sanity check first with `--ask`, ~20 s.
