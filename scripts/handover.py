#!/usr/bin/env python3
"""Build a hand-over bundle: config + installers + a tailored INSTALL.md.

    python scripts/handover.py                 # -> dist/pi-handover-<date>.tar.gz
    python scripts/handover.py --out /tmp/x    # custom output dir
    python scripts/handover.py --no-tar        # just write the directory

What goes in: every tracked file of this repo (git ls-files, so .gitignore rules
apply) plus a generated INSTALL.md and SHA256SUMS.

What never goes in: auth.json, settings.json with secrets, mcp.json, sessions/,
teams/, toolkit.json, __pycache__, anything that fails check_no_secrets.

Why not a VM image: a working Linux VM is tens of GB; the bundle ships the
*recipe* (setup-tools.py) so the recipient rebuilds the same tool set locally.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "sessions", "teams", "backups"}
EXCLUDE_NAMES = {"auth.json", "toolkit.json", "mcp.json", "triage_state.json", ".env"}


def sh(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")


def tracked_files() -> list[Path]:
    rc, out = sh(["git", "ls-files"])
    if rc != 0:
        print("warning: git ls-files failed; falling back to a plain walk", file=sys.stderr)
        return [p for p in ROOT.rglob("*") if p.is_file()]
    files = []
    for rel in out.splitlines():
        p = ROOT / rel
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p.name in EXCLUDE_NAMES or p.name.endswith(".bak"):
            continue
        files.append(p)
    return sorted(files)


INSTALL_MD = """# 交接包：pi-agent workbench（{date}）

这不是虚拟机镜像，也不是装好的环境。里面是 **配置 + 安装器**：配置层 5.6 MB 原样带走，
Linux 工具层用同一份清单在目标机器上重建。

| 层 | 能否原样带走 | 说明 |
|---|---|---|
| 配置（agent/、skills、扩展、toolbox） | ✅ 100% | 就是本包里这些文件 |
| Linux 工具（80 个） | ⚠️ 重建 | `scripts/setup-tools.py`，10–20 分钟、1.5–3 GB |
| 环境（VPN、API key、平台账号、本机路径、GPU） | ❌ 带不走 | 见文末清单，需要自己配 |

## 三条路，按推荐顺序

### A. Windows + WSL2（最接近作者本机，推荐）

```powershell
# 1) 管理员 PowerShell，一次性
wsl --install -d Ubuntu

# 2) 重启后进入 Ubuntu
cd /mnt/c/Users/<你>/Downloads
tar -xzf {tarball}
cd {dirname}
bash scripts/bootstrap.sh --api          # 装配置 + 问 key + 端到端验证
python3 scripts/setup-tools.py           # 选目标：WSL: Ubuntu → 装 80 个工具
```

### B. 已有 Linux 机器 / 虚拟机（走 SSH 也行）

```bash
tar -xzf {tarball}; cd {dirname}
bash scripts/bootstrap.sh --api
python3 scripts/setup-tools.py --target ubuntu --yes --allow-sudo
# 从 Windows 远程装到 VM 上：
python scripts/setup-tools.py --target ubuntu --ssh-host <ip> --ssh-user <user>
```

### B2. Kali（工具最全，省事）

Kali 自带绝大多数工具，加 `--kali-meta` 一次装完（约 1.5 GB）：

```bash
# WSL（官方发行版）
wsl --install -d kali-linux
wsl -d kali-linux -- bash -lc "cd /mnt/c/... && python3 scripts/setup-tools.py --target kali --kali-meta --yes"

# 或官方 Kali VM（预装工具，导入后走 SSH）
python scripts/setup-tools.py --target kali --ssh-host <kali-ip> --ssh-user kali --kali-meta
```

Kali 与 Ubuntu 的包名差异（`httpx-toolkit`、`impacket-scripts`、`metasploit-framework`、
`netexec`）已在 `docs/TOOLS.md` 的 Kali 列标出。

### C. Docker（只想要工具，不想动系统）

```bash
docker run --rm -it --cap-add=SYS_PTRACE --cap-add=NET_RAW --cap-add=NET_ADMIN \\
  -v work:/work -v "$PWD:/repo" ubuntu:24.04 bash
cd /repo && python3 scripts/setup-tools.py --target ubuntu --yes --allow-sudo
```

pwn 回连 / VPN 在容器里会受限，见 README 的说明。

## 验证（三步，缺一不可）

```bash
python scripts/doctor.py                 # 配置层：agent dir / 扩展 / toolkit
python scripts/setup-tools.py --list     # 工具层：哪些已装、哪些不可用
pi -p "reply with the single word: pong" # 模型层：能出 token 才算通
```

## 带不走的东西（必须自己解决）

| 项 | 为什么 | 怎么办 |
|---|---|---|
| 平台 token / 账号 | 属于个人凭据 | 用你自己的 |
| 模型 API key | 同上（本包不含 auth.json） | `pi auth login deepseek --api-key` |
| VPN 配置 | 与网络环境绑定 | 用你自己的 VPN；容器内联网需额外路由 |
| 作者的虚拟机 | 那个 VM 目录 **117 GB**，不适合传输 | 用 `setup-tools.py` 重建同样的工具集 |
| Windows 专有工具 | gdb/patchelf 等在 winget 里没有 | 走 WSL / VM |
| Ghidra / Sage / Burp / IDA 等 9 个 | 不在包管理器里 | 见 `docs/TOOLS.md` 末尾的手动表 |
| 本地路径 / 盘符 | 每人不同 | `PI_WORKBENCH`、`VM_DIRS` 自己设 |

## 自检：包是否干净

```bash
python scripts/check_no_secrets.py       # 0 = 无 flag/token/内网 IP
sha256sum -c SHA256SUMS
```
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--name", default="")
    ap.add_argument("--no-tar", action="store_true")
    args = ap.parse_args()

    rc, out = sh([sys.executable, "scripts/check_no_secrets.py", "--quiet"])
    if rc != 0:
        print("refusing to build: check_no_secrets failed\n" + out, file=sys.stderr)
        return 1

    stamp = date.today().isoformat()
    name = args.name or f"pi-handover-{stamp}"
    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="handover-")) / name
    stage.mkdir(parents=True)

    files = tracked_files()
    for src in files:
        rel = src.relative_to(ROOT)
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (stage / "INSTALL.md").write_text(
        INSTALL_MD.format(date=stamp, tarball=f"{name}.tar.gz", dirname=name), encoding="utf-8")

    # checksums over what we ship
    lines = []
    for p in sorted(stage.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{h}  {p.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    size_mb = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) / 1e6
    print(f"staged {len(files)} files + INSTALL.md + SHA256SUMS  ({size_mb:.1f} MB raw)")

    if args.no_tar:
        final = out_dir / name
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(stage), final)
        print(f"directory: {final}")
        return 0

    tarball = out_dir / f"{name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(stage, arcname=name)
    shutil.rmtree(stage.parent, ignore_errors=True)
    print(f"tarball: {tarball}  ({tarball.stat().st_size/1e6:.1f} MB)")
    print("\n收件人执行：\n  tar -xzf %s && cd %s && bash scripts/bootstrap.sh --api" % (tarball.name, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
