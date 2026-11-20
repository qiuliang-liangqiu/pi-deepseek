#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then py=python3; else py=python; fi
exec "$py" "$here/check_no_secrets.py" "$@"
