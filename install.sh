#!/usr/bin/env bash
# Install the model-orchestration skill for the current user (macOS / Linux).
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)/plugins/model-orchestration/skills/model-orchestration"
dst="$HOME/.claude/skills/model-orchestration"

[ -d "$src" ] || { echo "not found: $src - run this from the kit root" >&2; exit 1; }
if [ -d "$dst" ]; then
  bak="$dst.bak.$(date +%Y%m%d-%H%M%S)"
  echo "existing install found - backing it up to $bak"
  mv "$dst" "$bak"
fi
mkdir -p "$(dirname "$dst")"
cp -R "$src" "$dst"
echo "installed -> $dst"
echo
command -v python3 >/dev/null || { echo 'Python 3.8+ not found. Install it, then run doctor.py.'; exit 1; }
python3 "$dst/doctor.py"
