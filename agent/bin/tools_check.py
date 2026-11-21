#!/usr/bin/env python3
"""Check which CTF / benchmark tools are missing on this machine.

Installed as `~/.pi/agent/bin/tools_check.py` so the `tool-check` extension can
ask it at session start: no repo checkout, no network, stdlib only.

    python tools_check.py check              # human table
    python tools_check.py check --json        # machine readable (the extension uses this)
    python tools_check.py count-missing       # just the number (ASCII, for shell/CI)
    python tools_check.py check --group web   # one group only
    python tools_check.py check --catalog my.json

`--catalog` takes a JSON list of {"id", "probe", "group"?, "desc"?, "platforms"?};
`platforms` defaults to all three, so entries stay visible everywhere.

Exit codes: 0 normally; `--strict` returns 1 when something is missing.
Missing tools are only *reported* — installing is the user's decision
(`scripts/setup-tools.py` / `scripts/toolkit.py`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

CATALOG_VERSION = 1
ALL_PLATFORMS = ("windows", "linux", "darwin")

# id == the id used by scripts/setup-tools.py, so the hint it prints is runnable.
CATALOG: list[dict] = [
    {"id": "jq", "group": "recon", "desc": "读 JSON", "probe": "jq"},
    {"id": "ripgrep", "group": "recon", "desc": "全文搜索", "probe": "rg"},
    {"id": "fd", "group": "recon", "desc": "按名找文件", "probe": "fd"},
    {"id": "nmap", "group": "recon", "desc": "端口/服务指纹", "probe": "nmap"},
    {"id": "ncat", "group": "recon", "desc": "listener / 中继", "probe": "ncat"},
    {"id": "ffuf", "group": "web", "desc": "目录 / 参数 fuzz", "probe": "ffuf"},
    {"id": "gobuster", "group": "web", "desc": "vhost / DNS 枚举", "probe": "gobuster"},
    {"id": "upx", "group": "pwn", "desc": "脱壳 / 压缩", "probe": "upx"},
    {"id": "binwalk", "group": "forensics", "desc": "固件 / 容器解析", "probe": "binwalk"},
    {"id": "sqlmap", "group": "crypto", "desc": "SQL 注入自动化", "probe": "sqlmap"},
    # 原生只有 Linux/macOS 有：Windows 上不是“缺”，而是要用 WSL / VM / Docker
    {"id": "gdb", "group": "pwn", "desc": "动态调试", "probe": "gdb", "platforms": ("linux", "darwin")},
    {"id": "patchelf", "group": "pwn", "desc": "改 ELF 解释器 / rpath", "probe": "patchelf",
     "platforms": ("linux", "darwin")},
]


def platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def load_catalog(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog must be a JSON list")
    out = []
    for row in data:
        if not isinstance(row, dict) or not row.get("id") or not row.get("probe"):
            raise ValueError("every catalog row needs at least id + probe")
        out.append(row)
    return out


def repo_hint() -> str:
    """Where the installer lives, if we can tell from PI_WORKBENCH."""
    workbench = (os.environ.get("PI_WORKBENCH") or "").strip()
    if workbench:
        repo = Path(workbench).resolve().parent
        script = repo / "scripts" / "setup-tools.py"
        if script.is_file():
            return f"python {script}"
        script = repo / "scripts" / "toolkit.py"
        if script.is_file():
            return f"python {script} --install"
    return "python scripts/setup-tools.py   # 在 workbench 仓库里"


def collect(catalog: list[dict], group: str | None) -> dict:
    plat = platform_key()
    ok: list[str] = []
    missing: list[str] = []
    needs_alt: list[dict] = []
    for row in catalog:
        if group and row.get("group") and row["group"] != group:
            continue
        tid = str(row["id"])
        probe = str(row.get("probe") or tid)
        platforms = row.get("platforms") or ALL_PLATFORMS
        if plat not in platforms:
            needs_alt.append({"id": tid, "why": f"原生不支持 {plat}", "use": "WSL / VM / Docker"})
            continue
        if shutil.which(probe):
            ok.append(tid)
        else:
            missing.append(tid)
    return {
        "catalog_version": CATALOG_VERSION,
        "platform": plat,
        "group": group or "",
        "checked": len(ok) + len(missing) + len(needs_alt),
        "ok": ok,
        "missing": missing,
        "needs_alt": needs_alt,
        "hint": repo_hint() if missing else "",
        "generated_at": int(time.time()),
    }


def render_human(info: dict) -> str:
    lines = [f"platform {info['platform']} · checked {info['checked']}"
             + (f" · group {info['group']}" if info["group"] else "")]
    if info["ok"]:
        lines.append("  ok       " + ", ".join(info["ok"]))
    if info["missing"]:
        lines.append("  missing  " + ", ".join(info["missing"]))
    for row in info["needs_alt"]:
        lines.append(f"  needs    {row['id']} -> {row['use']}（{row['why']}）")
    if not info["missing"] and not info["needs_alt"]:
        lines.append("  没有缺失项")
    if info["hint"]:
        lines.append("  安装： " + info["hint"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="检查本机缺哪些 CTF / 跑分工具")
    ap.add_argument("action", nargs="?", default="check",
                    choices=["check", "count-missing"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有缺失时退出码 1")
    ap.add_argument("--group", default="")
    ap.add_argument("--catalog", default="", help="自定义清单（JSON）")
    args = ap.parse_args(argv)

    try:
        catalog = load_catalog(args.catalog) if args.catalog else CATALOG
    except Exception as exc:  # noqa: BLE001
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2

    info = collect(catalog, args.group or None)

    if args.action == "count-missing":
        print(len(info["missing"]))
        return 0
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(render_human(info))
    return 1 if (args.strict and info["missing"]) else 0


if __name__ == "__main__":
    sys.exit(main())
