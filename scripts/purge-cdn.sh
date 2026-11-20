#!/usr/bin/env bash
# jsdelivr caches @main for a while; after pushing to GitHub the CDN can still
# serve the old file. Run this after a push that touches scripts/.
#
#   bash scripts/purge-cdn.sh            # purge the files users fetch
#   bash scripts/purge-cdn.sh --all      # purge everything under scripts/
set -euo pipefail
SLUG="${PI_REPO_SLUG:-qiuliang-liangqiu/pi-deepseek-v1}"
BRANCH="${PI_BRANCH:-main}"
BASE="https://purge.jsdelivr.net/gh/${SLUG}@${BRANCH}"
FILES=(scripts/install.sh scripts/install.ps1 scripts/prereq.sh scripts/update.sh)

if [ "${1:-}" = "--all" ]; then
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  FILES=()
  while IFS= read -r f; do FILES+=("$f"); done < <(cd "$REPO" && git ls-files 'scripts/*')
fi

fail=0
for f in "${FILES[@]}"; do
  printf '%-28s ' "$f"
  if out=$(curl -fsS --max-time 30 "$BASE/$f" 2>&1); then
    printf 'purged  '
    printf '%s' "$out" | grep -o '"status": *"[a-z]*"' | head -1 | tr -d '\n'
    echo
  else
    echo "FAILED (network?)"
    fail=1
  fi
  sleep 1
done

echo
echo "验证（第一个字符应该是 # 或 { ，不是 BOM）:"
printf '  %s\n' "curl -s https://cdn.jsdelivr.net/gh/$SLUG@$BRANCH/scripts/install.ps1 | head -c 3 | xxd"
exit "$fail"
