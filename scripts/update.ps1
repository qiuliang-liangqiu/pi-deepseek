# Update an existing install on Windows: pull the repo, then refresh only the
# config layer. auth.json / settings.json / mcp.json and your local edits are
# never silently lost.
#
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
#   ... -DryRun
#   ... -NoPull
#   ... -KeepLocal
#
# Extra arguments are passed through to scripts\update.py.
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoPull,
    [switch]$KeepLocal,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { $Py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
if (-not $Py) { Write-Host "python not found" -ForegroundColor Red; exit 1 }

if (-not $NoPull -and (Test-Path (Join-Path $Repo ".git"))) {
    Write-Host "== git pull (fast-forward only)"
    try {
        git -C $Repo pull --ff-only --quiet
        Write-Host ("   " + (git -C $Repo log --oneline -1))
    } catch {
        Write-Host "   ! pull failed (local changes or network); using current checkout" -ForegroundColor Yellow
    }
}

$args2 = @("--repo", $Repo)
if ($DryRun) { $args2 += "--dry-run" }
if ($KeepLocal) { $args2 += "--keep-local" }
if ($Rest) { $args2 += $Rest }

& $Py (Join-Path $Repo "scripts\update.py") @args2
exit $LASTEXITCODE
