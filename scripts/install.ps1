# Install the agent/ layer into %USERPROFILE%\.pi\agent (with a timestamped backup).
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Dest = if ($env:PI_AGENT_DIR) { $env:PI_AGENT_DIR } else { Join-Path $HOME ".pi\agent" }
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $Dest "backups\$Stamp-install"

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

foreach ($item in @("AGENTS.md", "agents", "bin", "extensions", "prompts", "knowledge", "skills")) {
    $src = Join-Path $RepoRoot "agent\$item"
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $Dest $item
    if (Test-Path $dst) {
        New-Item -ItemType Directory -Force -Path $Backup | Out-Null
        Move-Item -Path $dst -Destination (Join-Path $Backup $item)
        Write-Host "backup: $dst -> $Backup\$item"
    }
    Copy-Item -Path $src -Destination $dst -Recurse
    Write-Host "install: $item"
}

Write-Host ""
Write-Host "Done. Agent dir: $Dest"
Write-Host "Backup dir:      $Backup (if anything was replaced)"
Write-Host ""
Write-Host "Next:"
Write-Host "  `$env:PI_WORKBENCH = `"$RepoRoot\toolbox`""
Write-Host "  Copy-Item examples\settings.example.json `$HOME\.pi\agent\settings.json"
Write-Host "  # put your model key in `$HOME\.pi\agent\auth.json (see examples\README.md)"
Write-Host ""
Write-Host "Smoke test:"
Write-Host "  node `"$RepoRoot\eval\mirror\score.mjs`" verify"
