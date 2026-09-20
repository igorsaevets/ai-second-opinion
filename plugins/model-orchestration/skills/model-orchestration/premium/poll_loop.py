#!/usr/bin/env python3
"""Poll a batch job until it reaches a terminal state, with a randomised interval.

Wraps whichever poller a lane uses (`batch_one.py --mode poll` on any of its
lanes) and re-invokes it until the poller stops returning exit 3 ("still
running").

The interval is drawn fresh from `random.uniform` on every iteration and capped
at 11 s, per the machine-wide rule in ~/.claude/CLAUDE.md: a constant delay is
itself a fingerprint, because human traffic has variance and a metronome does
not. This is politeness and blend-in, not evasion.

Default `--max-minutes` is 240 (4 h) since R92 (v1.66.0), raised from 20. The
operator's premium solpro batch on 2026-09-14 completed in 3 h 45 min — a
20-minute cap declared it dead after 5% of its actual runtime, and the next
session interpreted the absent .parsed.json as a failed model. The vendor's
own `completion_window` is 24 h (OR/OpenAI batch) or unspecified-hours
(Google), so waiting 4 h before handing off is polite in both directions.

Exit codes are the wrapped poller's own: 0 done, 1 failed/partial, 2 usage,
3 gave up while still running (NOT an error — the vendor keeps the result for
its full completion window and a later session can resume by calling this
script again with the same rundir; see the STILL_RUNNING sidecar written by
batch_one.py at every submit and every non-terminal poll, filename shape
`<tag>.still-running.json` inside the rundir).
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True,
                    help="the poll command, quoted; run verbatim through the shell")
    ap.add_argument("--max-minutes", type=float, default=240.0,
                    help="deadline for the whole loop; default 240 (4 h). R92 "
                         "raise from 20 — a 20-min cap declared the operator's "
                         "3h45m solpro batch dead at 5%% of its runtime.")
    ap.add_argument("--min-sleep", type=float, default=4.0)
    ap.add_argument("--max-sleep", type=float, default=11.0)
    ap.add_argument("--rundir", default="",
                    help="the rundir the wrapped batch_one.py writes into; "
                         "optional — used ONLY to print a copy-paste path to any "
                         "STILL_RUNNING sidecars on exit 3, so the next session "
                         "does not have to guess where the alive batches live.")
    a = ap.parse_args()

    if a.max_sleep > 11.0:
        print("REFUSING: max-sleep above the 11 s project cap", file=sys.stderr)
        return 2

    deadline = time.monotonic() + a.max_minutes * 60
    n = 0
    while True:
        n += 1
        p = subprocess.run(a.cmd, shell=True, capture_output=True, text=True)
        tail = (p.stdout or p.stderr or "").strip().splitlines()
        stamp = time.strftime("%H:%M:%S")
        print(f"[{n}] {stamp} exit={p.returncode}  "
              f"{tail[0][:150] if tail else ''}", flush=True)
        for line in tail[1:]:
            print(f"      {line[:170]}", flush=True)

        if p.returncode != 3:
            return p.returncode
        if time.monotonic() >= deadline:
            print("", flush=True)
            print("=" * 72, flush=True)
            print(f"POLL LOOP EXHAUSTED after {a.max_minutes:.0f} min "
                  f"({n} poll attempts).", flush=True)
            print("", flush=True)
            print("THIS IS NOT A FAILURE. The batch is still RUNNING on the "
                  "vendor's server. A later", flush=True)
            print("session can resume by re-running THIS script with the same "
                  "--cmd and --rundir;", flush=True)
            print("the vendor keeps the result for its full completion window "
                  "(24 h on OR/OpenAI", flush=True)
            print("batch, unspecified on Google). batch_one.py wrote a "
                  "STILL_RUNNING sidecar", flush=True)
            print("at every submit and every non-terminal poll — the sidecar "
                  "shape is:", flush=True)
            print("", flush=True)
            print("    <rundir>/<tag>.still-running.json", flush=True)
            print("", flush=True)
            print("with fields _status=\"STILL_RUNNING_SERVER_SIDE\", batch_id, "
                  "created_at_utc,", flush=True)
            print("estimated_deadline_utc, resume_command, and "
                  "interpretation_note (which spells", flush=True)
            print("out this same message for a subsequent AI session that finds "
                  "the file cold).", flush=True)
            print("", flush=True)
            if a.rundir:
                print(f"THIS RUN'S SIDECARS:  ls {a.rundir}/*.still-running.json",
                      flush=True)
            print("Aggregate report will surface these as ⏳ PENDING (not "
                  "❌ FAILED) if you run", flush=True)
            print("premium_panel.py --mode collect on this rundir.", flush=True)
            print("=" * 72, flush=True)
            return 3
        # Fresh draw every time. Never a fixed interval.
        time.sleep(random.uniform(a.min_sleep, a.max_sleep))


if __name__ == "__main__":
    raise SystemExit(main())
