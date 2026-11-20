# One-command installer for pi-agent workbench (Windows).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Api -Tools
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun
#
# Remote one-liner (no clone needed):
#   irm https://cdn.jsdelivr.net/gh/qiuliang-liangqiu/pi-deepseek-v1@main/scripts/install.ps1 | iex
#
# Env vars work too (useful for the piped form, where flags are not available):
#   PI_API=1 PI_TOOLS=1 PI_YES=1 PI_DRYRUN=1
#
# NOTE: this file is intentionally ASCII-only and has NO BOM. A UTF-8 BOM makes
# `irm | iex` fail to parse ([CmdletBinding] + param at the start).
[CmdletBinding()]
param(
    [switch]$Api,
    [switch]$Tools,
    [switch]$Yes,
    [switch]$DryRun,
    [switch]$Minimal,
    [string]$KeyFile = "",
    [string]$RepoUrl = "https://github.com/qiuliang-liangqiu/pi-deepseek-v1.git",
    [string]$WorkDir = "$HOME\pi-agent",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)
$ErrorActionPreference = "Stop"

function Say($m)  { Write-Host $m -ForegroundColor Cyan }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Die($m)  { Write-Host $m -ForegroundColor Red; exit 1 }

# ---- environment overrides (for `irm | iex`, which cannot pass flags)
if ($env:PI_API -eq "1")      { $Api = $true }
if ($env:PI_TOOLS -eq "1")    { $Tools = $true }
if ($env:PI_YES -eq "1")      { $Yes = $true }
if ($env:PI_DRYRUN -eq "1")   { $DryRun = $true }
if ($env:PI_MINIMAL -eq "1")  { $Minimal = $true }
if ($env:PI_KEY_FILE)         { $KeyFile = $env:PI_KEY_FILE }
if ($env:PI_WORKDIR)          { $WorkDir = $env:PI_WORKDIR }
if ($env:PI_REPO_URL)         { $RepoUrl = $env:PI_REPO_URL }

# ---- locate the repo: local script dir, or download it first
$root = $null
if ($PSScriptRoot) {
    if (Test-Path (Join-Path $PSScriptRoot "bootstrap.ps1")) {
        $root = Split-Path -Parent $PSScriptRoot
    } elseif (Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\bootstrap.ps1")) {
        $root = Split-Path -Parent $PSScriptRoot
    }
}

if (-not $root) {
    Say "== no local repo, fetching into $WorkDir"
    if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null }
    if (Test-Path (Join-Path $WorkDir "scripts\bootstrap.ps1")) {
        Say "   already present, skipping download (use scripts\update.ps1 to update)"
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        git clone --depth 1 $RepoUrl $WorkDir
        if ($LASTEXITCODE -ne 0) {
            Warn "git clone failed, trying the zip download"
            Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path (Join-Path $WorkDir "scripts\bootstrap.ps1"))) {
        $base = ($RepoUrl -replace '\.git$', '') -replace '^https://github\.com/', 'https://codeload.github.com/'
        $zipUrl = "$base/zip/refs/heads/main"
        Say "== downloading $zipUrl"
        $zip = Join-Path $env:TEMP "pi-deepseek.zip"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
        $tmp = Join-Path $env:TEMP ("pi-deepseek-" + [guid]::NewGuid().ToString("N"))
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
        Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $WorkDir -Recurse -Force
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    $root = $WorkDir
}

Say "== repo: $root"
$bootstrap = Join-Path $root "scripts\bootstrap.ps1"
if (-not (Test-Path $bootstrap)) { Die "scripts\bootstrap.ps1 not found (incomplete download?)" }

# ---- forward to bootstrap.ps1 (which runs as a file, so its BOM is fine)
$args2 = @()
if ($Api)     { $args2 += "-Api" }
if ($Tools)   { $args2 += "-Tools" }
if ($Yes)     { $args2 += "-Yes" }
if ($DryRun)  { $args2 += "-DryRun" }
if ($Minimal) { $args2 += "-Minimal" }
if ($KeyFile) { $args2 += @("-KeyFile", $KeyFile) }
if ($Rest)    { $args2 += $Rest }

& powershell -NoProfile -ExecutionPolicy Bypass -File $bootstrap @args2
exit $LASTEXITCODE
