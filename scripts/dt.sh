#!/usr/bin/env bash
# Run a command inside the pi-toolbox container (tools that Windows lacks).
#
#   bash scripts/dt.sh nmap -sV <host>
#   bash scripts/dt.sh gdb -q ./chall
#   bash scripts/dt.sh "python3 -c 'import pwn; print(pwn.__version__)'"
#   bash scripts/dt.sh            # 交互 shell
#
# 容器由 `python scripts/setup-tools.py --target docker` 创建（名字 pi-toolbox）。
# 也可以用 PI_DOCKER_CONTAINER 指定别的容器。
set -euo pipefail
CONTAINER="${PI_DOCKER_CONTAINER:-pi-toolbox}"

command -v docker >/dev/null 2>&1 || { echo "docker 不在 PATH" >&2; exit 1; }
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  echo "容器 $CONTAINER 没在运行。先执行：" >&2
  echo "  python scripts/setup-tools.py --target docker" >&2
  exit 1
fi

if [ $# -eq 0 ]; then
  exec docker exec -it "$CONTAINER" bash
fi
# -i 保证 stdin 可用（gdb/pwntools 需要），-t 只在终端里加
if [ -t 0 ] && [ -t 1 ]; then
  exec docker exec -it "$CONTAINER" bash -lc "$*"
else
  exec docker exec -i "$CONTAINER" bash -lc "$*"
fi
