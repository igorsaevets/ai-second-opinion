#!/usr/bin/env bash
# Install or update the model-orchestration skill for the current user (macOS / Linux).
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)/plugins/model-orchestration/skills/model-orchestration"

# One explicit argument -> single install (the update path). No argument -> every known
# skill location so that "second opinion" works for every agent without a path:
# Claude Code (~/.claude), agentskills.io spec (~/.agents) for Cursor/Codex/Gemini/etc.,
# opencode (~/.config/opencode), plus per-agent dirs some CLIs also read.
if [ -n "${1:-}" ]; then
  dsts=("$1")
else
  dsts=(
    "$HOME/.claude/skills/model-orchestration"
    "$HOME/.agents/skills/model-orchestration"
    "$HOME/.codex/skills/model-orchestration"
    "$HOME/.config/opencode/skills/model-orchestration"
    "$HOME/.gemini/skills/model-orchestration"
  )
fi

[ -d "$src" ] || { echo "not found: $src - run this from the kit root" >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3.8+ not found. Install it, then re-run.'; exit 1; }

for dst in "${dsts[@]}"; do
  echo "Installing to $dst ..."
  python3 "$src/upgrade.py" --from "$src" --to "$dst"
done
