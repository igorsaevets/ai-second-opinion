# Install the model-orchestration skill for the current Windows user.
# Non-plugin path: copies into ~\.claude\skills\ and runs the doctor.
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'plugins\model-orchestration\skills\model-orchestration'
$dst = Join-Path $env:USERPROFILE '.claude\skills\model-orchestration'

if (-not (Test-Path $src)) { throw "not found: $src - run this from the kit root" }
if (Test-Path $dst) {
    # Never overwrite silently: someone may have edited channels.json to point at their own models.
    $bak = "$dst.bak." + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Write-Host "existing install found - backing it up to $bak"
    Move-Item $dst $bak
}
New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
Copy-Item $src $dst -Recurse
Write-Host "installed -> $dst`n"

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host 'Python 3.8+ is not on PATH. Install it, then run doctor.py.'; exit 1 }
& python (Join-Path $dst 'doctor.py')
