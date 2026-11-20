#!/usr/bin/env bash
# Point a fresh pi at an API key in one step, without leaking it to shell history.
#
#   bash scripts/set-api.sh                          # 交互：选 provider，粘贴 key（不回显）
#   bash scripts/set-api.sh --key-file ~/key.txt     # 从文件读
#   bash scripts/set-api.sh --provider deepseek --key-file k.txt --model deepseek-flash
#   bash scripts/set-api.sh --provider myproxy --base-url https://gw.example.com/v1 --key-file k.txt
#   bash scripts/set-api.sh --dry-run                 # 只打印会写什么
#
# What it touches (all under ~/.pi/agent, or --agent-dir):
#   auth.json      ← adds { "<provider>": {"type":"api_key","key":"..."} }, keeps other providers
#   settings.json  ← defaultProvider / defaultModel / defaultThinkingLevel
#   models.json    ← only with --base-url: registers the endpoint (keeps existing entries)
#
# The key is never echoed, never put in argv, and never printed in the summary.
set -euo pipefail

AGENT_DIR="${PI_AGENT_DIR:-$HOME/.pi/agent}"
PROVIDER=""; KEY=""; KEY_FILE=""; BASE_URL=""; MODEL=""; THINKING=""; DRY=0; QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --key) KEY="${2:-}"; shift 2 ;;                 # discouraged: lands in shell history
    --key-file) KEY_FILE="${2:-}"; shift 2 ;;
    --base-url) BASE_URL="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --thinking) THINKING="${2:-}"; shift 2 ;;
    --agent-dir) AGENT_DIR="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

c() { printf '\033[%sm%b\033[0m\n' "$1" "$2"; }
ok() { c "32" "  ✓ $*"; }
warn() { c "33" "  ! $*"; }
die() { c "31" "  ✗ $*"; exit 1; }

PY=""
for candidate in python3 python; do command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }; done
[ -n "$PY" ] || die "python3 not found"

[ -n "$PROVIDER" ] || { printf "provider [deepseek]: "; read -r PROVIDER; PROVIDER="${PROVIDER:-deepseek}"; }
[ -n "$MODEL" ] || { printf "model [deepseek-flash]: "; read -r MODEL; MODEL="${MODEL:-deepseek-flash}"; }
[ -n "$THINKING" ] || THINKING="max"

# --- read the key without echoing / without argv
if [ -z "$KEY" ] && [ -n "$KEY_FILE" ]; then
  [ -f "$KEY_FILE" ] || die "key file not found: $KEY_FILE"
  KEY="$(tr -d ' \r\n' < "$KEY_FILE")"
fi
if [ -z "$KEY" ]; then
  printf "paste %s api key (input hidden): " "$PROVIDER"
  stty -echo 2>/dev/null || true
  IFS= read -r KEY
  stty echo 2>/dev/null || true
  printf "\n"
fi
[ -n "$KEY" ] || die "empty key"

case "$KEY" in
  *[[:space:]]*) die "key contains whitespace — check the paste" ;;
esac
[ ${#KEY} -ge 16 ] || warn "key looks short (${#KEY} chars); continuing"

echo
echo "agent dir : $AGENT_DIR"
echo "provider  : $PROVIDER"
echo "model     : $MODEL (thinking=$THINKING)"
[ -n "$BASE_URL" ] && echo "base url  : $BASE_URL"
[ "$DRY" = 1 ] && warn "dry-run: nothing will be written"

if [ "$DRY" = 0 ]; then
  mkdir -p "$AGENT_DIR"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  for f in auth.json settings.json models.json; do
    [ -f "$AGENT_DIR/$f" ] && cp -a "$AGENT_DIR/$f" "$AGENT_DIR/$f.bak-$STAMP" && ok "backup $f"
  done
fi

KEY="$KEY" PROVIDER="$PROVIDER" MODEL="$MODEL" THINKING="$THINKING" \
BASE_URL="$BASE_URL" AGENT_DIR="$AGENT_DIR" DRY="$DRY" \
"$PY" - <<'PY'
import json, os, sys, pathlib

try:                                    # Windows consoles default to GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

agent = pathlib.Path(os.environ["AGENT_DIR"])
provider = os.environ["PROVIDER"]
model = os.environ["MODEL"]
thinking = os.environ["THINKING"]
base_url = os.environ.get("BASE_URL") or ""
key = os.environ["KEY"]
dry = os.environ.get("DRY") == "1"

def load(name, default):
    p = agent / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        print(f"  ! {name} is not valid JSON ({exc}); leaving it alone", file=sys.stderr)
        return None

def save(name, data):
    if dry:
        print(f"  [dry-run] would write {name}")
        return
    p = agent / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    print(f"  ✓ wrote {name}")

auth = load("auth.json", {})
if auth is None:
    sys.exit(1)
auth[provider] = {"type": "api_key", "key": key}
save("auth.json", auth)

st = load("settings.json", {})
if st is None:
    sys.exit(1)
st["defaultProvider"] = provider
st["defaultModel"] = model
st["defaultThinkingLevel"] = thinking
save("settings.json", st)

if base_url:
    cfg = load("models.json", {"$schema": "https://pi.dev/schemas/models.schema.json", "providers": {}})
    if cfg is None:
        sys.exit(1)
    cfg.setdefault("providers", {}).setdefault(provider, {})
    entry = cfg["providers"][provider]
    entry["baseUrl"] = base_url
    entry.setdefault("api", "openai-completions")
    # do NOT set apiKey here: auth.json wins and keeping one copy of the secret is safer
    save("models.json", cfg)
PY

if [ "$DRY" = 1 ]; then
  echo
  warn "dry-run complete"
  exit 0
fi

[ "$QUIET" = 1 ] || cat <<EOF

  next:
    1) restart pi (or run /reload)
    2) verify:  pi auth check --provider $PROVIDER --json
                pi --list-models
                pi -p "reply with the single word: pong"
    3) delete the key file if you used one:  shred -u <file>  (or rm)

  rotate immediately if this key was ever pasted into chat, a ticket, or a screenshot.
EOF

# verify only when we wrote to the dir pi actually reads (pi has no --agent-dir flag)
DEFAULT_DIR="$(cd "$HOME/.pi/agent" 2>/dev/null && pwd || echo "$HOME/.pi/agent")"
ACTUAL_DIR="$(cd "$AGENT_DIR" 2>/dev/null && pwd || echo "$AGENT_DIR")"
if [ "$ACTUAL_DIR" != "$DEFAULT_DIR" ]; then
  echo
  warn "wrote to $AGENT_DIR, which is not pi's default — skipping pi auth check"
elif command -v pi >/dev/null 2>&1; then
  echo
  if pi auth check --provider "$PROVIDER" --json 2>/dev/null | grep -q '"status":"ready"'; then
    ok "pi auth check: ready"
  else
    warn "pi auth check did not report ready — run it manually for details"
  fi
fi
