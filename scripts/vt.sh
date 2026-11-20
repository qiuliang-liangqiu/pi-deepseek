#!/usr/bin/env bash
# Run a command on the VM (the one setup-tools installed into) from Windows.
#
#   bash scripts/vt.sh nmap -sV <host>
#   bash scripts/vt.sh gdb -q ./chall
#   bash scripts/vt.sh "python3 -c 'import pwn; print(pwn.__version__)'"
#   bash scripts/vt.sh                 # 交互 shell
#
# 目标从 ~/.pi/agent/vm.env 读（setup-tools 装完会自动写），也可以用环境变量覆盖：
#   PI_VM_HOST=192.168.1.10 PI_VM_USER=kali bash scripts/vt.sh id
#
# 和 scripts/dt.sh（Docker）的区别：VM 能跑 pwn 回连、能用宿主 VPN、工具最全。
set -euo pipefail
ENVF="${PI_AGENT_DIR:-$HOME/.pi/agent}/vm.env"
if [ -f "$ENVF" ]; then
  # shellcheck disable=SC1090
  . "$ENVF"
fi

HOST="${PI_VM_HOST:-}"
USER_="${PI_VM_USER:-}"
PORT="${PI_VM_PORT:-22}"

if [ -z "$HOST" ]; then
  echo "还没有记录虚拟机。两种方式：" >&2
  echo "  1) 让脚本自己发现并装：" >&2
  echo "     python scripts/setup-tools.py --target ubuntu --ssh-host <ip> --ssh-user <user>" >&2
  echo "  2) 手动指定一次：" >&2
  echo "     PI_VM_HOST=<ip> PI_VM_USER=<user> bash scripts/vt.sh id" >&2
  exit 1
fi

TARGET="$HOST"
[ -n "$USER_" ] && TARGET="$USER_@$HOST"

if [ $# -eq 0 ]; then
  exec ssh -p "$PORT" -t "$TARGET" 'bash -l'
fi
# -t 让 sudo/gdb 这类需要终端的命令正常工作
if [ -t 0 ] && [ -t 1 ]; then
  exec ssh -p "$PORT" -t "$TARGET" "$*"
else
  exec ssh -p "$PORT" "$TARGET" "$*"
fi
