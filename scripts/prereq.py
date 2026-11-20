#!/usr/bin/env python3
"""Preflight for a fresh machine: can this repo even run here?

    python scripts/prereq.py                 # table: python / git / node / npm / pi / disk / network
    python scripts/prereq.py --install       # ask, then install what is missing
    python scripts/prereq.py --yes           # install without asking (still only the listed items)
    python scripts/prereq.py --json
    python scripts/prereq.py --quiet         # exit code only (0 = ready)

Runs on the system python (3.8+ syntax here on purpose, so it can *report* that
python is too old instead of crashing on import).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PY = (3, 10)
MIN_NODE = 18
MIN_DISK_GB = 8
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def run(argv, timeout=120):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def version_tuple(text):
    import re
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return (0, 0, 0)
    return tuple(int(x) if x else 0 for x in m.groups())


def disk_free_gb(path: Path) -> float:
    try:
        p = path if path.exists() else path.parent
        return shutil.disk_usage(str(p)).free / 1e9
    except OSError:
        return -1.0


def http_ok(url: str) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status < 500
    except Exception:
        return False


def pkg_manager() -> str:
    if IS_WIN:
        return "winget" if shutil.which("winget") else ""
    if IS_MAC:
        return "brew" if shutil.which("brew") else ""
    for c in ("apt-get", "dnf", "pacman"):
        if shutil.which(c):
            return c
    return ""


def install_cmd(pkg_id: str, mgr: str):
    """pkg_id is a logical name: python|git|node|npm|pi"""
    if mgr == "winget":
        ids = {"python": "Python.Python.3.12", "git": "Git.Git", "node": "OpenJS.NodeJS.LTS"}
        if pkg_id in ids:
            return [["winget", "install", "-e", "--id", ids[pkg_id],
                     "--accept-package-agreements", "--accept-source-agreements"]]
        return []
    if mgr == "brew":
        formula = {"python": "python@3.12", "git": "git", "node": "node"}.get(pkg_id)
        return [["brew", "install", formula]] if formula else []
    if mgr == "apt-get":
        pkg = {"python": "python3", "git": "git", "node": "nodejs", "npm": "npm"}.get(pkg_id)
        return [["sudo", "apt-get", "install", "-y", pkg]] if pkg else []
    if mgr == "dnf":
        pkg = {"python": "python3", "git": "git", "node": "nodejs", "npm": "npm"}.get(pkg_id)
        return [["sudo", "dnf", "install", "-y", pkg]] if pkg else []
    if mgr == "pacman":
        pkg = {"python": "python", "git": "git", "node": "nodejs", "npm": "npm"}.get(pkg_id)
        return [["sudo", "pacman", "-S", "--noconfirm", pkg]] if pkg else []
    return []


def checks(root: Path) -> list[dict]:
    rows = []

    def add(name, ok, detail, required=True, fix=""):
        rows.append({"name": name, "ok": bool(ok), "detail": detail,
                     "required": required, "fix": fix})

    py = sys.version_info
    add("python", py >= MIN_PY, f"{py.major}.{py.minor}.{py.micro}",
        fix=f"需要 >= {MIN_PY[0]}.{MIN_PY[1]}; 安装后用新解释器重跑")

    git = shutil.which("git")
    add("git", bool(git), run([git, "--version"])[1].strip() if git else "not found",
        required=False, fix="git 可选，但克隆/校验会用到")

    node = shutil.which("node")
    nver = version_tuple(run([node, "-v"])[1]) if node else (0,)
    add("node", bool(node) and nver >= (MIN_NODE,), run([node, "-v"])[1].strip() if node else "not found",
        fix=f"需要 >= {MIN_NODE}（pi 的运行环境）")

    npm = shutil.which("npm")
    add("npm", bool(npm), run([npm, "-v"])[1].strip() if npm else "not found",
        fix="随 node 一起安装")

    pi = shutil.which("pi") or shutil.which("pi.cmd") or shutil.which("pi.exe")
    add("pi", bool(pi), "installed" if pi else "not found", fix="npm i -g @earendil-works/pi-coding-agent")

    agent = Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
    free = disk_free_gb(agent)
    add("disk", free < 0 or free >= MIN_DISK_GB, f"{free:.1f} GB free", fix=f"工具链约需 {MIN_DISK_GB} GB")

    net = http_ok("https://pypi.org/simple/") and http_ok("https://registry.npmjs.org/")
    add("network", net, "pypi+npm reachable" if net else "unreachable",
        required=False, fix="装 pip/npm 包需要外网；只能离线就用 --minimal")
    return rows


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    rows = checks(root)
    missing = [r for r in rows if not r["ok"] and r["required"]]

    if args.json:
        print(json.dumps({"ok": not missing, "checks": rows}, ensure_ascii=False, indent=2))
        return 0 if not missing else 1

    if not args.quiet:
        for r in rows:
            mark = "OK " if r["ok"] else ("!! " if r["required"] else "-- ")
            print(f"  {mark}{r['name']:<8} {r['detail']}")
            if not r["ok"] and r["fix"]:
                print(f"        {r['fix']}")

    if not missing:
        if not args.quiet:
            print("\n前置齐全。")
        return 0

    if not args.install:
        if not args.quiet:
            print(f"\n缺 {len(missing)} 项。加 --install 让我装（会先问一遍）。")
        return 1

    mgr = pkg_manager()
    if not mgr:
        print("找不到可用的包管理器（winget / brew / apt / dnf / pacman），只能手动装。")
        return 1

    plan = []
    for r in missing:
        if r["name"] == "pi":
            if shutil.which("npm"):
                plan.append(("pi", [["npm", "install", "-g", "@earendil-works/pi-coding-agent"]]))
            continue
        cmds = install_cmd(r["name"], mgr)
        if cmds:
            plan.append((r["name"], cmds))

    if not plan:
        print("没有可自动安装的项，按上面的提示手动处理。")
        return 1

    print("\n将执行：")
    for name, cmds in plan:
        for c in cmds:
            print(f"  $ {' '.join(c)}")
    if not args.yes:
        try:
            if input("\n确认？[y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1

    failed = []
    for name, cmds in plan:
        for c in cmds:
            print(f"  $ {' '.join(c)}")
            rc, out = run(c, timeout=1800)
            if rc != 0:
                failed.append(name)
                print(f"    ! {name} failed (exit {rc})")
                tail = [l for l in out.strip().splitlines() if l.strip()][-3:]
                for l in tail:
                    print(f"      {l[:120]}")
                break
    if failed:
        print(f"\n失败：{', '.join(sorted(set(failed)))}（手动处理后重跑 --check）")
        return 1

    print("\n装完了。**重启终端**让 PATH 生效，然后重跑：python scripts/prereq.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
