# Install or update the model-orchestration skill for the current Windows user.
#
# -Destination exists because the old script hardcoded the path with no way to redirect it. An
# assistant asked to update an install somewhere else would either have to hand-copy files (losing
# your settings) or run this and quietly overwrite a DIFFERENT installation. Found by giving a
# cold reviewer the published repo and an install in a non-default folder: it refused to run the
# installer at all, and was right to.
#
# By default installs to EVERY known skill location so that "second opinion" works for
# every agent without telling it a path: Claude Code (~/.claude), agentskills.io spec
# (~/.agents) used by Cursor/Codex/Gemini/Kimi/Qwen/Copilot, and opencode
# (~/.config/opencode), plus per-agent dirs that some CLIs also read.
# A single -Destination still installs to that one place only (used by upgrade.py for updates).
param([string]$Destination = "")
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'plugins\model-orchestration\skills\model-orchestration'

if (-not (Test-Path $src)) { throw "not found: $src - run this from the kit root" }
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host 'Python 3.8+ is not on PATH. Install it from python.org, then re-run.'; exit 1 }

# One explicit destination -> single install (the update path). No argument -> all known.
if ($Destination) {
  $dsts = @($Destination)
} else {
  $dsts = @(
    (Join-Path $env:USERPROFILE '.claude\skills\model-orchestration'),
    (Join-Path $env:USERPROFILE '.agents\skills\model-orchestration'),
    (Join-Path $env:USERPROFILE '.codex\skills\model-orchestration'),
    (Join-Path $env:USERPROFILE '.config\opencode\skills\model-orchestration'),
    (Join-Path $env:USERPROFILE '.gemini\skills\model-orchestration')
  )
  # Hermes (Windows, not on PATH) keeps skills under %LOCALAPPDATA%
  $hermes = Join-Path $env:LOCALAPPDATA 'hermes\skills\model-orchestration'
  if ($hermes) { $dsts += $hermes }
}
foreach ($dst in $dsts) {
  Write-Host "Installing to $dst ..."
  & python (Join-Path $src 'upgrade.py') --from $src --to $dst
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
