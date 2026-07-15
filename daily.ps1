# Optional PowerShell entry (Desktop shortcut uses Launch Flatfinder.bat)
param(
    [switch]$NoAi,
    [switch]$NoOpen,
    [switch]$DryRun,
    [int]$MaxTabs = 0,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating venv and installing..." -ForegroundColor Cyan
    python -m venv .venv
    & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -m pip install -e $PSScriptRoot
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}

# Settings/keys/config are created and managed by the app itself (in your
# FLATFINDER_HOME folder if set, otherwise here) — nothing to seed.

# Default: full menu. Pass -Daily for one-shot hunt without menu.
$args = @("-m", "flatfinder", "menu")
if ($NoAi -or $NoOpen -or $DryRun -or ($MaxTabs -gt 0)) {
    $args = @("-m", "flatfinder", "daily")
    if ($NoAi) { $args += "--no-ai" }
    if ($NoOpen) { $args += "--no-open" }
    if ($DryRun) { $args += "--dry-run" }
    if ($MaxTabs -gt 0) { $args += @("--max-tabs", "$MaxTabs") }
    if ($Verbose) { $args += "-v" }
}

& $py @args
exit $LASTEXITCODE
