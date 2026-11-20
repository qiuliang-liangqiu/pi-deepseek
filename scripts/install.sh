#!/usr/bin/env bash
# 一条命令装好（或在已有安装上增量更新）。
#
#   bash scripts/install.sh                  # 自动：没装就装，装过就增量更新
#   bash scripts/install.sh --api --tools    # 顺便配 key + 挑工具
#   bash scripts/install.sh --dry-run        # 只看会做什么
#
# 也可以直接远程跑（会自动把仓库拉到 ~/pi-agent）：
#   curl -fsSL https://raw.githubusercontent.com/qiuliang-liangqiu/pi-deepseek-v1/main/scripts/install.sh | bash
#
# 所有参数原样传给 scripts/bootstrap.sh。
set -euo pipefail

SLUG="${PI_REPO_SLUG:-qiuliang-liangqiu/pi-deepseek-v1}"
BRANCH="${PI_BRANCH:-main}"
SSH_URL="git@github.com:${SLUG}.git"
HTTPS_URL="https://github.com/${SLUG}.git"
ZIP_URL="https://codeload.github.com/${SLUG}/zip/refs/heads/${BRANCH}"
WORKDIR="${PI_HOME:-$HOME/pi-agent}"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ---- 是否已经在仓库里（本地文件 or 远程管道）
SELF=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

PY_OR_PY3="$(command -v python3 || command -v python || true)"
if [ -n "$SELF" ] && [ -f "$SELF/bootstrap.sh" ]; then
  REPO_ROOT="$(cd "$SELF/.." && pwd)"
else
  say "== 没有本地仓库，先拉到 $WORKDIR"
  if [ -d "$WORKDIR/.git" ]; then
    git -C "$WORKDIR" pull --ff-only --quiet || warn "pull 失败，继续用现有 checkout"
  elif command -v git >/dev/null 2>&1; then
    # SSH 先试（国内常比 HTTPS 稳），再 HTTPS
    if ! git clone --depth 1 "$SSH_URL" "$WORKDIR" 2>/dev/null; then
      warn "SSH 不行，换 HTTPS"
      git clone --depth 1 "$HTTPS_URL" "$WORKDIR" || {
        warn "HTTPS clone 也失败，改用 zip"
        rm -rf "$WORKDIR"
      }
    fi
  fi
  if [ ! -d "$WORKDIR" ] || [ ! -f "$WORKDIR/scripts/bootstrap.sh" ]; then
    TMPZ="$(mktemp -d)"
    say "== 下载 zip: $ZIP_URL"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL --connect-timeout 10 --max-time 120 "$ZIP_URL" -o "$TMPZ/repo.zip"
    elif command -v wget >/dev/null 2>&1; then
      wget -q -O "$TMPZ/repo.zip" "$ZIP_URL"
    fi
    if [ -s "$TMPZ/repo.zip" ]; then
      if command -v unzip >/dev/null 2>&1; then unzip -q "$TMPZ/repo.zip" -d "$TMPZ/x"
      else "$PY_OR_PY3" -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMPZ/repo.zip" "$TMPZ/x"
      fi
      mkdir -p "$WORKDIR"
      cp -a "$TMPZ"/x/*/. "$WORKDIR"/
    fi
    rm -rf "$TMPZ"
    [ -f "$WORKDIR/scripts/bootstrap.sh" ] || die "仓库获取失败。手动下载后：bash scripts/install.sh"
  fi
  REPO_ROOT="$WORKDIR"
fi

say "== 仓库: $REPO_ROOT"
exec bash "$REPO_ROOT/scripts/bootstrap.sh" "$@"
