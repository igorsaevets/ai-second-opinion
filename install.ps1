# Install or update the model-orchestration skill for the current Windows user.
#
# -Destination exists because the old script hardcoded the path with no way to redirect it. An
# assistant asked to update an install somewhere else would either have to hand-copy files (losing
# your settings) or run this and quietly overwrite a DIFFERENT installation. Found by giving a
# cold reviewer the published repo and an install in a non-default folder: it refused to run the
# installer at all, and was right to.
param([string]$Destination = (Join-Path $env:USERPROFILE '.claude\skills\model-orchestration'))
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'plugins\model-orchestration\skills\model-orchestration'
$dst = $Destination

if (-not (Test-Path $src)) { throw "not found: $src - run this from the kit root" }
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host 'Python 3.8+ is not on PATH. Install it from python.org, then re-run.'; exit 1 }

# Installing over an existing install is an UPGRADE. upgrade.py backs the old tree up, carries
# your settings into a file no future update can overwrite, prints what changed, and runs doctor.
& python (Join-Path $src 'upgrade.py') --from $src --to $dst
