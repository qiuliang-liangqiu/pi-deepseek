#!/usr/bin/env bash
# 只装前置：python / node / npm / pi / git（缺什么装什么）。
#
#   bash scripts/prereq.sh            # 先看缺什么
#   bash scripts/prereq.sh --install  # 直接装（会问一次）
#   bash scripts/prereq.sh --install --yes   # 不问
#
# 远程一行（不需要先 git clone）：
#   curl -fsSL https://raw.githubusercontent.com/qiuliang-liangqiu/pi-deepseek-v1/main/scripts/prereq.sh | bash -s -- --install
set -euo pipefail

# 多个镜像：raw 在国外最快，jsdelivr 在国内可用（实测 raw 会超时）
OWNER_REPO="${PI_REPO_SLUG:-qiuliang-liangqiu/pi-deepseek-v1}"
BRANCH="${PI_BRANCH:-main}"
MIRRORS=(
  "https://raw.githubusercontent.com/$OWNER_REPO/$BRANCH"
  "https://cdn.jsdelivr.net/gh/$OWNER_REPO@$BRANCH"
  "https://fastly.jsdelivr.net/gh/$OWNER_REPO@$BRANCH"
)
SELF=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

PY=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done

# 仓库里：直接用本地的 prereq.py
if [ -n "$SELF" ] && [ -f "$SELF/prereq.py" ]; then
  [ -n "$PY" ] || { echo "没有 python：先装 python 3.10+（本脚本自己也需要它）" >&2; exit 1; }
  exec "$PY" "$SELF/prereq.py" "$@"
fi

# 远程：只下 prereq.py（几 KB），不 clone 整个仓库
[ -n "$PY" ] || { echo "没有 python：先装 python 3.10+" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fetch() {  # fetch <url> <out>
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 8 --max-time 40 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --timeout=40 -O "$2" "$1"
  else
    "$PY" -c "import urllib.request,sys;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" "$1" "$2"
  fi
}

GOT=0
for base in "${MIRRORS[@]}"; do
  echo "== 下载 prereq.py <- $base"
  if fetch "$base/scripts/prereq.py" "$TMP/prereq.py" && [ -s "$TMP/prereq.py" ]; then
    GOT=1
    break
  fi
  echo "   失败，换下一个镜像"
done
[ "$GOT" = 1 ] || {
  echo "所有镜像都拿不到 prereq.py；请手动下载仓库后用仓库内的 scripts/prereq.sh" >&2
  exit 1
}
exec "$PY" "$TMP/prereq.py" "$@"
