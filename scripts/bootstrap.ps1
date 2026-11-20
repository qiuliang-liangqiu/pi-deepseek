# One-shot setup for pi-agent workbench on Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   ... -DryRun          show what would happen
#   ... -NoSmoke         skip smoke tests
#   ... -Yes             non-interactive
#   ... -Minimal         agent layer only: no PI_WORKBENCH, no toolbox smoke
#
# Idempotent. Never touches auth.json / settings.json / mcp.json
# (settings.json is only created from examples/ when missing).
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoSmoke,
    [switch]$Yes,
    [switch]$Minimal,
    [switch]$Api,
    [string]$KeyFile = "",
    [string]$Provider = "deepseek",
    [string]$Model = "deepseek-flash",
    [string]$BaseUrl = "",
    [switch]$NoVerify,
    [switch]$Tools,
    [switch]$ToolsAll
)
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Dest     = if ($env:PI_AGENT_DIR) { $env:PI_AGENT_DIR } else { Join-Path $HOME ".pi\agent" }
$Stamp    = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup   = Join-Path $Dest "backups\$Stamp-install"

function Write-Step($n, $msg) { Write-Host "`n[$n/6] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [ok] $msg"   -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg"   -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [x]  $msg"   -ForegroundColor Red; exit 1 }
function Do-It([scriptblock]$sb, [string]$desc) {
    if ($DryRun) { Write-Host "    [dry-run] $desc"; return }
    & $sb
}

Write-Host "pi-agent workbench bootstrap" -ForegroundColor White
Write-Host "    repo:  $RepoRoot"
Write-Host "    agent: $Dest"
if ($DryRun) { Warn "dry-run: nothing will be written" }

# ---------------------------------------------------------------- 1. prerequisites
Write-Step 1 "prerequisites"
$Python = $null
foreach ($c in @("python", "python3", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source; break }
}
if (-not $Python) { Fail "python not found (needed by bin\*.py and toolbox)" }
$PyVer = & $Python -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ([version]$PyVer -lt [version]"3.8") { Fail "python $PyVer too old (need >= 3.8)" }
Ok "python $PyVer ($Python)"

$Node = Get-Command node -ErrorAction SilentlyContinue
if ($Node) { Ok "node $(& node -v)" } else { Warn "node not found - eval/mirror checks will be skipped" }

$Pi = Get-Command pi -ErrorAction SilentlyContinue
if ($Pi) { Ok "pi found ($($Pi.Source))" }
else {
    Warn "pi not on PATH"
    Write-Host "      install: npm install -g @earendil-works/pi-coding-agent"
    Write-Host "      (config below is still installed; pi-dependent smoke tests are skipped)"
}

# ---------------------------------------------------------------- 2. backup
Write-Step 2 "backup existing agent dir"
$installed = @()
if (Test-Path $Dest) {
    foreach ($item in @("AGENTS.md", "agents", "bin", "extensions", "prompts", "skills", "knowledge")) {
        $p = Join-Path $Dest $item
        if (-not (Test-Path $p)) { continue }
        Do-It { New-Item -ItemType Directory -Force -Path $Backup | Out-Null } "mkdir $Backup"
        Do-It { Move-Item -Path $p -Destination (Join-Path $Backup $item) } "backup $item"
        Ok "backed up $item"
        $installed += $item
    }
}
if ($installed.Count -eq 0) { Ok "nothing to back up" }

# ---------------------------------------------------------------- 3. install
Write-Step 3 "install agent/ -> $Dest"
Do-It { New-Item -ItemType Directory -Force -Path $Dest | Out-Null } "mkdir $Dest"
foreach ($item in @("AGENTS.md", "agents", "bin", "extensions", "prompts", "knowledge", "skills")) {
    $src = Join-Path $RepoRoot "agent\$item"
    if (-not (Test-Path $src)) { continue }
    Do-It { Copy-Item -Path $src -Destination (Join-Path $Dest $item) -Recurse -Force } "copy $item"
    Ok $item
}

# ---------------------------------------------------------------- 4. settings / auth
Write-Step 4 "settings"
$settings = Join-Path $Dest "settings.json"
$example  = Join-Path $RepoRoot "examples\settings.example.json"
if (Test-Path $settings) {
    Ok "settings.json exists (left untouched)"
} elseif (Test-Path $example) {
    Do-It { Copy-Item $example $settings } "create settings.json"
    Ok "created settings.json from example - edit provider/model"
}
if (Test-Path (Join-Path $Dest "auth.json")) { Ok "auth.json exists (left untouched)" }
else { Warn "no auth.json - put your model key there (see examples\README.md)" }
if (Test-Path (Join-Path $Dest "mcp.json")) { Ok "mcp.json exists (left untouched)" }

# ---------------------------------------------------------------- 5. PI_WORKBENCH
Write-Step 5 "PI_WORKBENCH"
if ($Minimal) {
    Warn "-Minimal: skipped (the agent layer does not need toolbox)"
} else {
    $envFile = Join-Path $Dest "workbench.env"
    Do-It { "`$env:PI_WORKBENCH = `"$RepoRoot\toolbox`"" | Set-Content -Path $envFile -Encoding UTF8 } "write workbench.env"
    Ok "wrote $envFile"

    $profilePath = $PROFILE.CurrentUserAllHosts
    $marker = "# >>> pi-agent workbench >>>"
    $hasMarker = (Test-Path $profilePath) -and (Select-String -Path $profilePath -SimpleMatch $marker -Quiet)
    if ($hasMarker) {
        Ok "$profilePath already sources workbench.env"
    } else {
        $go = $Yes -or $DryRun
        if (-not $go) {
            $answer = Read-Host "    append to $profilePath ? [Y/n]"
            $go = -not ($answer -match '^[nN]')
        }
        if ($go) {
            Do-It {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $profilePath) | Out-Null
                Add-Content -Path $profilePath -Value @("", $marker, ". `"$envFile`"", "# <<< pi-agent workbench <<<")
            } "update profile"
            if ($DryRun) { Write-Host "    [dry-run] would update $profilePath" } else { Ok "updated $profilePath" }
        } else {
            Warn "skipped; add manually:  . `"$envFile`""
        }
    }
    $env:PI_WORKBENCH = Join-Path $RepoRoot "toolbox"
}

# ---------------------------------------------------------------- 6. smoke
Write-Step 6 "smoke tests"
$script:Failed = $false
function Smoke([string]$name, [scriptblock]$sb) {
    if ($DryRun) { Write-Host "    [dry-run] $name"; return }
    try { & $sb *> $null; if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "exit $LASTEXITCODE" }; Ok $name }
    catch { Warn "FAILED: $name"; $script:Failed = $true }
}
Smoke "scan_injection --help" { & $Python (Join-Path $Dest "bin\scan_injection.py") --help }
Smoke "triage_gate status"    { & $Python (Join-Path $Dest "bin\triage_gate.py") status }
if (-not $Minimal) { Smoke "framework cli --help"  { $env:PYTHONPATH = (Join-Path $RepoRoot "toolbox"); & $Python -m framework.cli --help } }
$tk = Join-Path $RepoRoot "scripts\toolkit.py"
if (Test-Path $tk) { Smoke "toolkit --check" { & $Python $tk --check } }
Smoke "no-secrets check"      { & $Python (Join-Path $RepoRoot "scripts\check_no_secrets.py") --quiet }
if ($Node -and (Test-Path (Join-Path $RepoRoot "eval\mirror\score.mjs"))) {
    Smoke "eval/mirror verify" { & $Node (Join-Path $RepoRoot "eval\mirror\score.mjs") verify }
}

# ---------------------------------------------------------------- 7. api (optional)
if ($Api) {
    Write-Host "`n[7/7] API (-Api)" -ForegroundColor Cyan
    $DEFAULT = Join-Path $HOME ".pigent"
    # compare full paths, not Resolve-Path: a missing dir would make null -eq null "true"
    try { $isDefault = ([IO.Path]::GetFullPath($Dest) -eq [IO.Path]::GetFullPath($DEFAULT)) }
    catch { $isDefault = ($Dest -eq $DEFAULT) }   # non-Windows path from Git-Bash/MSYS
    $ready = $false
    if ($isDefault -and (Get-Command pi -ErrorAction SilentlyContinue)) {
        try {
            $out = & pi auth check --provider $Provider --json 2>$null
            $ready = ($out -join "") -match '"status":"ready"'
        } catch { }
    }
    if ($ready) {
        Ok "$Provider already ready"
    } elseif ($DryRun) {
        Warn "[dry-run] would configure $Provider (key from -KeyFile or hidden prompt)"
        Warn "[dry-run] would write auth.json / settings.json"
    } else {
        $key = ""
        if ($KeyFile) {
            if (-not (Test-Path $KeyFile)) { Warn "key file not found: $KeyFile" }
            else { $key = (Get-Content $KeyFile -Raw).Trim() }
        } else {
            $sec = Read-Host "paste $Provider api key (hidden)" -AsSecureString
            if ($sec) {
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
                try { $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
                finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
            }
        }
        if (-not $key) {
            Warn "no key provided - run: pi auth login $Provider --api-key"
        } else {
            New-Item -ItemType Directory -Force -Path $Dest | Out-Null
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            foreach ($f in @("auth.json", "settings.json", "models.json")) {
                $fp = Join-Path $Dest $f
                if (Test-Path $fp) { Copy-Item $fp "$fp.bak-$stamp"; Ok "backup $f" }
            }
            $authPath = Join-Path $Dest "auth.json"
            $auth = if (Test-Path $authPath) { Get-Content $authPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
            $auth | Add-Member -NotePropertyName $Provider -NotePropertyValue ([pscustomobject]@{ type = "api_key"; key = $key }) -Force
            $auth | ConvertTo-Json -Depth 8 | Set-Content $authPath -Encoding UTF8
            Ok "wrote auth.json"

            $stPath = Join-Path $Dest "settings.json"
            $st = if (Test-Path $stPath) { Get-Content $stPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
            $st | Add-Member -NotePropertyName defaultProvider -NotePropertyValue $Provider -Force
            $st | Add-Member -NotePropertyName defaultModel -NotePropertyValue $Model -Force
            $st | Add-Member -NotePropertyName defaultThinkingLevel -NotePropertyValue "max" -Force
            $st | ConvertTo-Json -Depth 8 | Set-Content $stPath -Encoding UTF8
            Ok "wrote settings.json"

            if ($BaseUrl) {
                $mp = Join-Path $Dest "models.json"
                $mc = if (Test-Path $mp) { Get-Content $mp -Raw | ConvertFrom-Json } else { [pscustomobject]@{ } }
                if (-not $mc.PSObject.Properties["providers"]) { $mc | Add-Member -NotePropertyName providers -NotePropertyValue ([pscustomobject]@{}) }
                $entry = [pscustomobject]@{ baseUrl = $BaseUrl; api = "openai-completions" }
                $mc.providers | Add-Member -NotePropertyName $Provider -NotePropertyValue $entry -Force
                $mc | ConvertTo-Json -Depth 8 | Set-Content $mp -Encoding UTF8
                Ok "wrote models.json"
            }
        }
    }
    if (-not $NoVerify -and -not $DryRun -and $isDefault -and (Get-Command pi -ErrorAction SilentlyContinue)) {
        try {
            $reply = & pi -p "reply with the single word: pong" 2>$null
            if (($reply -join "") -match "pong") { Ok "end-to-end: model replied" }
            else { Warn "model did not reply - check: pi auth check --provider $Provider --json"; $script:Failed = $true }
        } catch { Warn "verify failed: $_"; $script:Failed = $true }
    }
}

# ---------------------------------------------------------------- 8. tools (optional)
if ($Tools) {
    Write-Host "`n[8/8] tools (-Tools)" -ForegroundColor Cyan
    $tk = Join-Path $RepoRoot "scripts\toolkit.py"
    if (-not (Test-Path $tk)) {
        Warn "scripts\toolkit.py not found - skipping"
    } else {
        $missing = -1
        try { $missing = [int](& $Python $tk --count-missing 2>$null) } catch { $missing = -1 }
        if ($missing -eq 0) {
            Ok "all catalog tools already installed"
        } elseif ($missing -lt 0) {
            Warn "could not read tool status - run: python scripts	oolkit.py --check"
        } else {
            Warn ("{0} tools missing" -f $missing)
            $pick = if ($ToolsAll) { "all" } else { "jq,ripgrep,fd,7z,nmap,pwntools,pycryptodome,z3,binwalk,exiftool" }
            if ($DryRun) {
                Warn "[dry-run] would run: toolkit.py --install $pick"
            } else {
                $go = $Yes
                if (-not $go) {
                    $answer = Read-Host "    install $pick ? [Y/n]"
                    $go = -not ($answer -match '^[nN]')
                }
                if (-not $go) {
                    Warn "skipped; run later: python scripts\toolkit.py --install"
                } else {
                    & $Python $tk --install $pick --yes
                    if ($LASTEXITCODE -eq 0) { Ok "tools installed" } else { Warn "some tools failed - run: python scripts\toolkit.py --check" }
                }
            }
        }
    }
}

# ---------------------------------------------------------------- summary
Write-Host ""
if ($script:Failed) { Write-Host "bootstrap finished with warnings - see FAILED lines above" -ForegroundColor Yellow }
else { Write-Host "bootstrap OK" -ForegroundColor Green }
Write-Host @"

  agent dir : $Dest
  backup    : $Backup  (if anything was replaced)
  workbench : `$env:PI_WORKBENCH = "$RepoRoot\toolbox"

  next:
    1) edit $Dest\settings.json (provider/model)
    2) put the model key in $Dest\auth.json  (see examples\README.md)
    3) restart pi, then:  /reload
    4) doctor:  python $RepoRoot\scripts\doctor.py
"@
if ($script:Failed) { exit 1 }
