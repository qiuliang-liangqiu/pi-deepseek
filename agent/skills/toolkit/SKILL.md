---
name: toolkit
description: 检查本机缺哪些 CTF/取证/逆向工具，按用户选择安装（白名单包管理器，绝不 curl|sh），并把结果写进 ~/.pi/agent/toolkit.json 供后续调用。当出现 command not found / 工具不可用 / 想装 jq、ffuf、pwntools、exiftool 等时使用。
---

# toolkit：先看有什么，再问要不要装

## 什么时候用

- 命令报 `command not found` / `不是内部或外部命令`；
- skill 里写的工具（`ffuf`、`nuclei`、`jadx`、`exiftool`、`binwalk`…）明显不在；
- 用户说"装一下 xxx"、"我这缺什么工具"。

**不要**因为缺工具就自己找二进制、`curl | sh`、或从 GitHub releases 下 zip 解压到 PATH——
一律走 `scripts/toolkit.py`，它只执行内置目录里的包管理器命令。

## 三条命令

```bash
python scripts/toolkit.py --check                 # 表格：✓ 已装 / · 缺失
python scripts/toolkit.py --check --json          # 给 agent 读
python scripts/toolkit.py --install               # 交互选择（编号/类别/all）
python scripts/toolkit.py --install jq,ffuf       # 指定 id
python scripts/toolkit.py --install web --dry-run # 只打印命令
```

- `--install <id>` 先过滤掉已装的，再逐条确认；
- `--yes` 只对**已经点名**的 id 跳过确认，不会扩大范围；
- `--allow-sudo` 才会执行 `sudo apt ...`，否则打印后跳过；
- `--dry-run` 一定不动系统。

## 给 agent 的纪律

1. 先 `--check --json`，把"缺什么"用一句话告诉用户，**不要**自动开装。
2. 用户点头后，只装他选的那几个；装完再跑一次 `--check --json` 确认。
3. 装不上（网络/权限/没有包管理器）就停下报告，不要换来源、不要下载随机二进制。
4. 装完的版本落在 `~/.pi/agent/toolkit.json`，后续命令先查这个文件，别再凭记忆假设。

## 目录里有什么

`base`（go）、`recon`（nmap/ncat/jq/httpx）、`web`（ffuf/gobuster/nuclei/sqlmap/mitmproxy）、
`binary`（gdb/r2/upx/patchelf/pwntools）、`forensics`（exiftool/binwalk/foremost/tshark/volatility3/scapy）、
`crypto`（hashcat/pycryptodome/gmpy2/z3）、`android`（jadx/apktool/frida-tools）、`qol`（rg/fd/7z）。

要加新工具：在 `scripts/toolkit.py` 的 `CATALOG` 里加一条，**必须是包管理器命令**，
并写清 `requires`（比如 `ffuf` 需要先有 `go`）。
