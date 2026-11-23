#!/usr/bin/env bash
# One-shot setup for pi-agent workbench (Linux / macOS / Git-Bash on Windows).
#
#   bash scripts/bootstrap.sh              # install + smoke test
#   bash scripts/bootstrap.sh --dry-run    # show what would happen
#   bash scripts/bootstrap.sh --no-smoke   # skip smoke tests
#   bash scripts/bootstrap.sh --yes        # non-interactive (no prompts)
#   bash scripts/bootstrap.sh --minimal    # agent layer only: no PI_WORKBENCH, no toolbox smoke
#   bash scripts/bootstrap.sh --api        # after install: configure the API key + verify (one step)
#   bash scripts/bootstrap.sh --api --key-file ~/k.txt --provider deepseek --model deepseek-flash
#   bash scripts/bootstrap.sh --tools      # 装完后：检查并（问过后）安装推荐工具
#   bash scripts/bootstrap.sh --prereq      # 缺啥装啥（python/node/npm/pi/git）
#   bash scripts/bootstrap.sh --update      # 已有安装：增量覆盖（不碰 auth/settings）
#   bash scripts/bootstrap.sh --reinstall   # 已有安装：整体重装（仍会备份旧目录）
#
# Idempotent: re-running detects an existing install and does an incremental update.
# Never touches auth.json / settings.json / mcp.json (only creates examples' copies).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PI_AGENT_DIR:-$HOME/.pi/agent}"
DRY=0; SMOKE=1; YES=0; MINIMAL=0; API=0; KEY_FILE=""; PROVIDER="deepseek"; MODEL="deepseek-flash"; BASE_URL=""; VERIFY=1
TOOLS=0; TOOLS_ALL=0; ALLOW_SUDO=0; PREREQ=0; MODE=auto; WIN_GAPS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --no-smoke) SMOKE=0; shift ;;
    --yes|-y) YES=1; shift ;;
    --minimal|--no-toolbox) MINIMAL=1; shift ;;
    --api) API=1; shift ;;
    --key-file) KEY_FILE="${2:-}"; shift; shift ;;
    --key-file=*) KEY_FILE="${1#*=}"; shift ;;
    --provider) PROVIDER="${2:-}"; shift; shift ;;
    --model) MODEL="${2:-}"; shift; shift ;;
    --base-url) BASE_URL="${2:-}"; shift; shift ;;
    --no-verify) VERIFY=0; shift ;;
    --tools) TOOLS=1; shift ;;
    --tools-all) TOOLS=1; TOOLS_ALL=1; shift ;;
    --allow-sudo) ALLOW_SUDO=1; shift ;;
    --prereq) PREREQ=1; shift ;;
    --win-gaps) WIN_GAPS="${2:-}"; shift; shift ;;
    --update) MODE=update; shift ;;
    --reinstall) MODE=install; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# *//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

c() { printf '\033[%sm%b\033[0m\n' "$1" "$2"; }
ok() { c "32" "  ✓ $*"; }
warn() { c "33" "  ! $*"; }
die() { c "31" "  ✗ $*"; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "    [dry-run] $*"; else "$@"; fi; }

c "1;36" "pi-agent workbench bootstrap"
echo "    repo:  $REPO_ROOT"
echo "    agent: $DEST"
[ "$DRY" = 1 ] && warn "dry-run: nothing will be written"

# ---------------------------------------------------------------- 1. prerequisites
c "1;36" "\n[1/6] prerequisites"
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || die "python3 not found (needed by bin/*.py and toolbox)"
PYVER="$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
case "$PYVER" in
  3.8|3.9|3.1[0-9]) ok "python $PYVER ($PY)" ;;
  *) die "python $PYVER too old (need >= 3.8)" ;;
esac

if [ -f "$REPO_ROOT/scripts/prereq.py" ]; then
  if ! "$PY" "$REPO_ROOT/scripts/prereq.py" --quiet >/dev/null 2>&1; then
    warn "前置不完整（python/node/npm/pi/磁盘/网络），详见：python scripts/prereq.py"
    if [ "$PREREQ" = 1 ] && [ "$DRY" = 0 ]; then
      "$PY" "$REPO_ROOT/scripts/prereq.py" --install $( [ "$YES" = 1 ] && echo --yes )
    else
      echo "      自动安装：bash scripts/bootstrap.sh --prereq"
    fi
  else
    ok "prereq: python/node/npm/pi/disk 齐全"
  fi
fi

NODE_BIN=""; NODE_VER=""
if command -v node >/dev/null 2>&1; then NODE_BIN="node"; NODE_VER="$(node -v)"; fi
if [ -n "$NODE_BIN" ]; then ok "node $NODE_VER"; else warn "node not found — eval/mirror checks will be skipped"; fi

if command -v pi >/dev/null 2>&1; then
  ok "pi $(pi --version 2>/dev/null | head -1)"
else
  warn "pi not on PATH"
  echo "      install: npm install -g @earendil-works/pi-coding-agent"
  echo "      (config below is still installed; smoke tests that need pi are skipped)"
fi
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')" || warn "git not found (optional)"

# ---------------------------------------------------------------- 2. backup
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$DEST/backups/$STAMP-install"

# 是否已有安装（决定 install vs update）
EXISTING=0
if [ -f "$DEST/.workbench.json" ] || [ -f "$DEST/AGENTS.md" ] || [ -d "$DEST/skills" ]; then
  EXISTING=1
fi
if [ "$MODE" = auto ]; then
  [ "$EXISTING" = 1 ] && MODE=update || MODE=install
fi

c "1;36" "\n[2/6] backup existing agent dir"
if [ "$EXISTING" = 1 ] && [ "$MODE" = update ]; then
  ok "增量模式：不整体搬走，逐文件备份到 backups/<时间戳>-update/"
elif [ -d "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
  run mkdir -p "$BACKUP"
  for item in AGENTS.md VERSION agents bin extensions prompts skills knowledge scripts; do
    [ -e "$DEST/$item" ] || continue
    run mv "$DEST/$item" "$BACKUP/$item"
    ok "backed up $item"
  done
else
  ok "nothing to back up"
fi

# ---------------------------------------------------------------- 3. install / update
if [ "$EXISTING" = 1 ] && [ "$MODE" = update ]; then
  c "1;36" "\n[3/6] 检测到已有安装 → 增量覆盖（保留 auth/settings 与本地改动）"
  if [ -f "$DEST/.workbench.json" ]; then
    BASE_COMMIT="$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("commit","?"))' "$DEST/.workbench.json" 2>/dev/null || echo "?")"
    ok "基线 commit: $BASE_COMMIT"
  else
    warn "没有基线清单（旧版本装的）：会先备份再覆盖"
  fi
  if [ "$YES" = 0 ] && [ "$DRY" = 0 ] && [ ! -t 0 ]; then
    warn "非交互运行：增量更新需要确认，已跳过（要自动更新请加 --yes）"
    SKIPPED_UPDATE=1
    UARGS=()
  else
    UARGS=(--agent-dir "$DEST" --repo "$REPO_ROOT")
    [ "$DRY" = 1 ] && UARGS+=(--dry-run)
    [ "$YES" = 1 ] && UARGS+=(--yes)
  fi
  if [ "${#UARGS[@]}" -eq 0 ]; then
    :
  elif ! "$PY" "$REPO_ROOT/scripts/update.py" "${UARGS[@]}"; then
    warn "增量更新未完成；要整体重装：bash scripts/bootstrap.sh --reinstall"
    exit 1
  fi
else
  if [ "$EXISTING" = 1 ]; then
    c "1;36" "\n[3/6] 整体重装 agent/ -> $DEST（旧目录已备份到 $BACKUP）"
  elif [ "$MODE" = update ]; then
    c "1;36" "\n[3/6] 没检测到已有安装 → 按全新安装处理"
  else
    c "1;36" "\n[3/6] 全新安装 agent/ -> $DEST"
  fi
  run mkdir -p "$DEST"
  for item in AGENTS.md VERSION agents bin extensions prompts knowledge skills scripts; do
    [ -e "$REPO_ROOT/agent/$item" ] || continue
    run cp -a "$REPO_ROOT/agent/$item" "$DEST/$item"
    ok "$item"
  done
fi
if [ "$DRY" = 0 ]; then
  chmod +x "$DEST"/bin/*.sh "$DEST"/bin/*.py 2>/dev/null || true
  # 记录基线，便于以后 `bash scripts/update.sh` 做增量覆盖（不碰 auth/settings）
  if [ -f "$REPO_ROOT/scripts/update.py" ]; then
    if "$PY" "$REPO_ROOT/scripts/update.py" --agent-dir "$DEST" --record-only >/dev/null 2>&1; then
      ok "baseline manifest written"
    else
      warn "could not write baseline manifest"
    fi
  fi
fi

# ---------------------------------------------------------------- 4. settings / auth
c "1;36" "\n[4/6] settings"
if [ -f "$DEST/settings.json" ]; then
  ok "settings.json exists (left untouched)"
else
  if [ -f "$REPO_ROOT/examples/settings.example.json" ]; then
    run cp "$REPO_ROOT/examples/settings.example.json" "$DEST/settings.json"
    ok "created settings.json from example — edit provider/model"
  fi
fi
if [ -f "$DEST/auth.json" ]; then
  ok "auth.json exists (left untouched)"
else
  warn "no auth.json — put your model key there (see examples/README.md)"
fi
[ -f "$DEST/mcp.json" ] && ok "mcp.json exists (left untouched)" || true

# ---------------------------------------------------------------- 5. PI_WORKBENCH
c "1;36" "\n[5/6] PI_WORKBENCH"
if [ "$MINIMAL" = 1 ]; then
  warn "--minimal: skipped (the agent layer does not need toolbox)"
else
  if [ "$DRY" = 0 ]; then
    mkdir -p "$DEST"
    printf 'export PI_WORKBENCH="%s"\n' "$REPO_ROOT/toolbox" > "$DEST/workbench.env"
  fi
  ok "wrote $DEST/workbench.env"
  RC=""
  case "$(basename "${SHELL:-bash}")" in
    zsh) RC="$HOME/.zshrc" ;;
    *)   RC="$HOME/.bashrc" ;;
  esac
  MARK="# >>> pi-agent workbench >>>"
  if [ -f "$RC" ] && grep -qF "$MARK" "$RC" 2>/dev/null; then
    ok "$RC already sources workbench.env"
  else
    if [ "$YES" = 1 ] || { [ "$DRY" = 0 ] && [ -t 0 ] && \
         { printf "    append to %s? [Y/n] " "$RC"; read -r a; case "$a" in n|N) false;; *) true;; esac; }; }; then
      if [ "$DRY" = 0 ]; then
        { echo ""; echo "$MARK"; echo "[ -f \"$DEST/workbench.env\" ] && . \"$DEST/workbench.env\""; echo "# <<< pi-agent workbench <<<"; } >> "$RC"
      fi
      ok "updated $RC"
    else
      warn "skipped; add manually:  source $DEST/workbench.env"
    fi
  fi
  export PI_WORKBENCH="$REPO_ROOT/toolbox"
fi

# ---------------------------------------------------------------- 6. smoke
c "1;36" "\n[6/6] smoke tests"
FAIL=0
smoke() { # name, command...   (exit 0 = pass)
  local name="$1"; shift
  if [ "$DRY" = 1 ]; then echo "    [dry-run] $name"; return; fi
  if "$@" >/dev/null 2>&1; then ok "$name"; else warn "FAILED: $name"; FAIL=1; fi
}
smoke_codes() { # name, allowed-codes, command...
  local name="$1" allowed="$2"; shift 2
  if [ "$DRY" = 1 ]; then echo "    [dry-run] $name"; return; fi
  local rc=0
  "$@" >/dev/null 2>&1 || rc=$?
  case " $allowed " in *" $rc "*) ok "$name" ;; *) warn "FAILED: $name (exit $rc)"; FAIL=1 ;; esac
}
smoke "scan_injection --help" "$PY" "$DEST/bin/scan_injection.py" --help
smoke_codes "stall_gate show" "0 1 2" "$PY" "$DEST/bin/stall_gate.py" show /tmp/pi-bootstrap-nonexistent
smoke "triage_gate status"    "$PY" "$DEST/bin/triage_gate.py" status
if [ "$MINIMAL" = 0 ]; then
  smoke "framework cli --help"  env PYTHONPATH="$REPO_ROOT/toolbox" "$PY" -m framework.cli --help
fi
if [ -f "$REPO_ROOT/scripts/toolkit.py" ]; then
  smoke "toolkit --check" "$PY" "$REPO_ROOT/scripts/toolkit.py" --check
fi
if [ -f "$REPO_ROOT/scripts/check_no_secrets.py" ]; then
  smoke "no-secrets check" "$PY" "$REPO_ROOT/scripts/check_no_secrets.py" --quiet
fi
if [ -n "$NODE_BIN" ] && [ -f "$REPO_ROOT/eval/mirror/score.mjs" ]; then
  smoke "eval/mirror verify" "$NODE_BIN" "$REPO_ROOT/eval/mirror/score.mjs" verify
fi

# ---------------------------------------------------------------- 7. api (optional)
if [ "$API" = 1 ]; then
  c "1;36" "\n[7/7] API（--api）"
  # pi always reads ~/.pi/agent; skip live checks when we installed elsewhere (tests, containers)
  DEFAULT_DIR="$(cd "$HOME/.pi/agent" 2>/dev/null && pwd || echo "$HOME/.pi/agent")"
  DEST_REAL="$(cd "$DEST" 2>/dev/null && pwd || echo "$DEST")"
  IS_DEFAULT=0; [ "$DEST_REAL" = "$DEFAULT_DIR" ] && IS_DEFAULT=1
  if [ "$IS_DEFAULT" = 0 ]; then
    warn "installed to $DEST (not pi's default) — writing credentials there, skipping live checks"
  fi
  if [ "$IS_DEFAULT" = 1 ] && command -v pi >/dev/null 2>&1 && pi auth check --provider "$PROVIDER" --json 2>/dev/null | grep -q '"status":"ready"'; then
    ok "$PROVIDER already ready, nothing to do"
  else
    warn "no usable credentials for $PROVIDER — handing over to set-api.sh"
    if [ "$DRY" = 1 ]; then
      warn "[dry-run] would run: scripts/set-api.sh --provider $PROVIDER --model $MODEL --agent-dir $DEST"
    else
      ARGS=(--provider "$PROVIDER" --model "$MODEL" --agent-dir "$DEST" --quiet)
      [ -n "$KEY_FILE" ] && ARGS+=(--key-file "$KEY_FILE")
      [ -n "$BASE_URL" ] && ARGS+=(--base-url "$BASE_URL")
      if [ -z "$KEY_FILE" ] && [ ! -t 0 ]; then
        warn "no terminal and no --key-file: pass --key-file <path> to configure non-interactively"
        FAIL=1
      elif bash "$REPO_ROOT/scripts/set-api.sh" "${ARGS[@]}"; then
        ok "credentials written"
      else
        warn "set-api.sh failed — run it manually"
        FAIL=1
      fi
    fi
  fi
  if [ "$VERIFY" = 1 ] && [ "$DRY" = 0 ] && [ "$IS_DEFAULT" = 1 ]; then
    if timeout 90 pi -p "reply with the single word: pong" 2>/dev/null | grep -qi pong; then
      ok "end-to-end: model replied"
    else
      warn "model did not reply — check: pi auth check --provider $PROVIDER --json"
      FAIL=1
    fi
  fi
fi

# ---------------------------------------------------------------- 8. tools (optional)
# 交互安装时主动问一句，免得用户以为"装完就全好了"
if [ "$TOOLS" = 0 ] && [ "$YES" = 0 ] && [ "$DRY" = 0 ] && [ -t 0 ]    && [ -f "$REPO_ROOT/scripts/setup-tools.py" ]; then
  printf "  现在挑一个目标系统安装工具吗？（Windows / WSL / VM） [y/N] "
  read -r _ans || _ans=""
  case "$_ans" in y|Y|yes|YES) TOOLS=1 ;; esac
fi
if [ "$TOOLS" = 1 ]; then
  c "1;36" "
[8/8] tools（--tools）"
  ST="$REPO_ROOT/scripts/setup-tools.py"
  if [ ! -f "$ST" ]; then
    warn "scripts/setup-tools.py not found (partial download?) — skipping"
  else
    TARGS=()
    [ "$TOOLS_ALL" = 1 ] && TARGS+=(--all-groups)
    [ "$DRY" = 1 ] && TARGS+=(--dry-run)
    [ "$YES" = 1 ] && TARGS+=(--yes)
    [ "$ALLOW_SUDO" = 1 ] && TARGS+=(--allow-sudo)
    [ -n "$WIN_GAPS" ] && TARGS+=(--win-gaps "$WIN_GAPS")
    # 交互安装时把控制权交给选择菜单；非交互默认当前系统
    if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then
      TARGS+=(--auto)
    fi
    "$PY" "$ST" "${TARGS[@]}" || warn "setup-tools reported problems — run it again to see details"
  fi
fi

# ---------------------------------------------------------------- summary
echo
if [ "$FAIL" = 1 ]; then
  c "33" "bootstrap finished with warnings — see FAILED lines above"
else
  c "32" "bootstrap OK"
fi
cat <<EOF

  agent dir : $DEST
  backup    : $BACKUP  (if anything was replaced)
  workbench : $( [ "$MINIMAL" = 1 ] && echo "skipped (--minimal)" || echo "export PI_WORKBENCH=\"$REPO_ROOT/toolbox\"" )

  next:
    1) edit $DEST/settings.json (provider/model)
    2) put the model key in $DEST/auth.json  (see examples/README.md)
    3) restart pi, then:  /reload
    4) doctor:  $PY $REPO_ROOT/scripts/doctor.py
EOF
[ "$FAIL" = 1 ] && exit 1 || exit 0
