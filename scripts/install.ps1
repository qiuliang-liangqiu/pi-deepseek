# 一条命令装好（或在已有安装上增量更新）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Api -Tools
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun
#
# 或者不下载仓库，直接远程跑：
#   irm https://raw.githubusercontent.com/qiuliang-liangqiu/pi-deepseek-v1/main/scripts/install.ps1 | iex
#
# 参数：-Api 配模型 key；-Tools 挑工具；-Yes 不问；-DryRun 只看；-Minimal 只装规则层。
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

# ---- 找仓库：本地脚本目录，或者远程执行时先拉下来
$root = $null
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "bootstrap.ps1"))) {
    $root = Split-Path -Parent $PSScriptRoot
} elseif ($PSScriptRoot -and (Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\bootstrap.ps1"))) {
    $root = Split-Path -Parent $PSScriptRoot
}

if (-not $root) {
    Say "== 没有本地仓库，先取到 $WorkDir"
    if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null }
    if (Test-Path (Join-Path $WorkDir "scripts\bootstrap.ps1")) {
        Say "  已存在，跳过下载（要更新就跑 scripts\update.ps1）"
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        git clone --depth 1 $RepoUrl $WorkDir
        if ($LASTEXITCODE -ne 0) { Die "git clone 失败：$RepoUrl" }
    } else {
        Say "  没有 git，改用 zip 下载"
        $zip = Join-Path $env:TEMP "pi-deepseek.zip"
        $zipUrl = ($RepoUrl -replace '\.git$', '') -replace '^https://github\.com/', 'https://codeload.github.com/'
        $zipUrl = "$zipUrl/zip/refs/heads/main"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
        $tmp = Join-Path $env:TEMP ("pi-deepseek-" + [guid]::NewGuid().ToString("N"))
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $WorkDir -Recurse -Force
    }
    $root = $WorkDir
}

Say "== 仓库: $root"
$bootstrap = Join-Path $root "scripts\bootstrap.ps1"
if (-not (Test-Path $bootstrap)) { Die "找不到 scripts\bootstrap.ps1（下载不完整？）" }

# ---- 组装 bootstrap 参数
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
