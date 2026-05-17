# fix-stale-projects.ps1
# One-click updater for old projects with stale .claude control plane.
# It uses the safe in-place updater (preserves locked log files in artifacts/).
#
# Recommended usage (run from cc-router-codex root):
#   powershell.exe -ExecutionPolicy Bypass -File tools\fix-stale-projects.ps1

$ErrorActionPreference = "Stop"

$SourceRoot = Split-Path -Parent $PSScriptRoot
$Updater = Join-Path $SourceRoot "tools\safe-inplace-update.py"

if (-not (Test-Path $Updater)) {
    Write-Host "ERROR: safe-inplace-update.py not found" -ForegroundColor Red
    exit 1
}

Write-Host "=== cc-router-codex stale project updater ===" -ForegroundColor Cyan
Write-Host "This will safely refresh scripts + hooks + settings without deleting artifacts/logs"
Write-Host ""

& python $Updater

Write-Host ""
Write-Host "Done. Re-open the updated projects in Claude Code or Grok TUI." -ForegroundColor Green
Write-Host "Press Enter to close this window..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
