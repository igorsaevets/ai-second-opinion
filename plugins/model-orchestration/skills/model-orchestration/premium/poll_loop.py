#!/usr/bin/env python3
"""Poll a batch job until it reaches a terminal state, with a randomised interval.

Wraps whichever poller a lane uses (`batch_one.py --mode poll` on any of its
lanes) and re-invokes it until the poller stops returning exit 3 ("still
running").

The interval is drawn fresh from `random.uniform` on every iteration and capped
at 11 s, per the machine-wide rule in ~/.claude/CLAUDE.md: a constant delay is
itself a fingerprint, because human traffic has variance and a metronome does
not. This is politeness and blend-in, not evasion.

Exit codes are the wrapped poller's own: 0 done, 1 failed/partial, 2 usage,
3 gave up while still running (not an error — a batch may legitimately take
up to 24 h, and the job survives this process exiting).
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
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--min-sleep", type=float, default=4.0)
    ap.add_argument("--max-sleep", type=float, default=11.0)
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
            print(f"gave up after {a.max_minutes} min — the job is still RUNNING "
                  f"server-side and can be polled from a later session.", flush=True)
            return 3
        # Fresh draw every time. Never a fixed interval.
        time.sleep(random.uniform(a.min_sleep, a.max_sleep))


if __name__ == "__main__":
    raise SystemExit(main())
