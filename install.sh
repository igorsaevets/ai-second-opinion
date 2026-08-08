#!/usr/bin/env bash
# Install or update the model-orchestration skill for the current user (macOS / Linux).
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)/plugins/model-orchestration/skills/model-orchestration"
# First argument overrides the destination. Without it, an assistant asked to update an install
# somewhere else has to hand-copy files - or runs this and overwrites a different installation.
dst="${1:-$HOME/.claude/skills/model-orchestration}"

[ -d "$src" ] || { echo "not found: $src - run this from the kit root" >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3.8+ not found. Install it, then re-run.'; exit 1; }

# Installing over an existing install is an UPGRADE. upgrade.py backs the old tree up, carries
# your settings into a file no future update can overwrite, prints what changed, and runs doctor.
python3 "$src/upgrade.py" --from "$src" --to "$dst"
