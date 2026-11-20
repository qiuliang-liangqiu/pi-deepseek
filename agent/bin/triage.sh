#!/bin/bash
# 30-60 second shallow fingerprint for one 靶场平台 target.
# Usage: triage.sh <container_addr> [outfile]
set -u
addr="${1:?usage: triage.sh <addr> [outfile]}"
out="${2:-/tmp/triage.txt}"
case "$addr" in
  http://*|https://*) url="$addr" ;;
  *) url="http://$addr" ;;
esac
hostport="${url#*://}"
hostport="${hostport%%/*}"
host="${hostport%%:*}"
port="${hostport##*:}"
[ "$port" = "$host" ] && port=80
body="$(mktemp)"
{
  echo "addr: $addr"
  echo "== headers =="
  curl -sS -m 5 -D - -o "$body" "$url" 2>&1 | head -15
  echo "== title =="
  grep -o '<title>[^<]*' "$body" 2>/dev/null | head -1
  echo "== paths =="
  for p in / /robots.txt /openapi.json /.git/HEAD /src.zip /login /api /admin; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' -m 3 "$url$p" 2>/dev/null || echo ERR)"
    echo "$p $code"
  done
  echo "== js =="
  grep -oE 'src="[^"]+\.js[^"]*"' "$body" 2>/dev/null | head -8
  echo "== tcp banner =="
  if command -v nc >/dev/null 2>&1; then
    timeout 4 nc -w 2 "$host" "$port" </dev/null 2>&1 | head -5 | tr -d '\000' || true
  else
    pybin="$(command -v python3 || command -v python || true)"
    if [ -n "$pybin" ]; then
      "$pybin" - "$host" "$port" <<'PY' 2>&1 | head -5 || true
import socket, sys
try:
    s = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)
    s.settimeout(2)
    try:
        data = s.recv(512)
    except Exception:
        data = b""
    print((data or b"(no banner)").decode("utf-8", "replace").replace("\x00", ""))
except Exception as e:
    print("banner error:", e)
PY
    fi
  fi
} >"$out" 2>&1
rm -f "$body"
cat "$out"
