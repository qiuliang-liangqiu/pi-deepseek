#!/usr/bin/env bash
# Install the agent/ layer into ~/.pi/agent (with a timestamped backup).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PI_AGENT_DIR:-$HOME/.pi/agent}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$DEST/backups/$STAMP-install"

mkdir -p "$DEST"
for item in AGENTS.md agents bin extensions prompts knowledge skills; do
  src="$REPO_ROOT/agent/$item"
  [ -e "$src" ] || continue
  if [ -e "$DEST/$item" ]; then
    mkdir -p "$BACKUP"
    mv "$DEST/$item" "$BACKUP/$item"
    echo "backup: $DEST/$item -> $BACKUP/$item"
  fi
  cp -a "$src" "$DEST/$item"
  echo "install: $item"
done

chmod +x "$DEST"/bin/*.sh "$DEST"/bin/*.py 2>/dev/null || true

cat <<EOF

Done. Agent dir: $DEST
Backup dir:      $BACKUP (if anything was replaced)

Next:
  export PI_WORKBENCH="$REPO_ROOT/toolbox"     # add to ~/.bashrc
  cp examples/settings.example.json ~/.pi/agent/settings.json   # edit provider/model
  # put your model key in ~/.pi/agent/auth.json (see examples/README.md)

Smoke test:
  node "$REPO_ROOT/eval/mirror/score.mjs" verify
EOF
