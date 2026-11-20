#!/usr/bin/env bash
# Update an existing install: pull the repo (if it is a git clone) and refresh
# only the config layer. User config and local edits are never silently lost.
#
#   bash scripts/update.sh              # pull + plan + ask + apply
#   bash scripts/update.sh --dry-run    # plan only (still pulls, use --no-pull to skip)
#   bash scripts/update.sh --no-pull    # skip git pull
#   bash scripts/update.sh --keep-local # keep your versions of edited files
#
# Everything after -- is passed to scripts/update.py.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PULL=1
PASSTHRU=()
for a in "$@"; do
  case "$a" in
    --no-pull) PULL=0 ;;
    *) PASSTHRU+=("$a") ;;
  esac
done

PY=""
for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done
[ -n "$PY" ] || { echo "python not found" >&2; exit 1; }

if [ "$PULL" = 1 ] && [ -d "$REPO/.git" ]; then
  echo "== git pull (fast-forward only)"
  if git -C "$REPO" pull --ff-only --quiet 2>/tmp/pi-update-pull.log; then
    echo "   $(git -C "$REPO" log --oneline -1)"
  else
    echo "   ! pull 失败（本地有改动或网络问题），继续用当前 checkout："
    sed 's/^/     /' /tmp/pi-update-pull.log | head -3
  fi
fi

exec "$PY" "$REPO/scripts/update.py" --repo "$REPO" "${PASSTHRU[@]}"
