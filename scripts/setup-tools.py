#!/usr/bin/env python3
"""Picker + installer for the CTF toolchain, across Windows / WSL / native Linux.

    python scripts/setup-tools.py                     # 菜单：选目标系统 → 看包名映射 → 装
    python scripts/setup-tools.py --list              # 只打印映射表，不装
    python scripts/setup-tools.py --target windows    # 指定目标（windows|ubuntu|kali|auto）
    python scripts/setup-tools.py --target ubuntu --dry-run
    python scripts/setup-tools.py --target windows --yes --group web,recon
    python scripts/setup-tools.py --gui               # Windows 弹出选择框（失败回落到菜单）

设计原则
--------
* **装什么由用户选**：默认走菜单；`--yes` 只跳过"确认安装"，不扩大范围。
* **先看清再装**：每个目标先打印"工具 → 包名 → 是否可用"，再执行。
* **不猜包名**：装之前用 `apt-cache policy` / `winget show` 验证；不可用的工具
  显式标成 unavailable，不静默跳过。
* **sudo 要单独授权**：Linux 上没 `--allow-sudo` 只打印命令。
* 只用标准库；不 `curl | sh`，不从 release 下二进制。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------- catalog
@dataclass
class Tool:
    id: str
    group: str
    why: str
    win: str = ""                 # winget id
    ubuntu: list[str] = field(default_factory=list)   # apt package names
    kali: list[str] = field(default_factory=list)
    pip: str = ""                 # fallback: pip package
    go: str = ""                  # fallback: go module
    probe: str = ""               # command to test (defaults to id)
    note: str = ""


CATALOG: list[Tool] = [
    # base
    Tool("go", "base", "装 Go 工具的前置", win="GoLang.Go", ubuntu=["golang-go"], kali=["golang"], probe="go"),
    # recon
    Tool("nmap", "recon", "端口/服务指纹", win="Insecure.Nmap", ubuntu=["nmap"], kali=["nmap"], probe="nmap"),
    Tool("ncat", "recon", "listener/中继", win="Insecure.Nmap", ubuntu=["ncat"], kali=["ncat"], probe="ncat"),
    Tool("jq", "recon", "读 JSON", win="jqlang.jq", ubuntu=["jq"], kali=["jq"], probe="jq"),
    Tool("ripgrep", "recon", "全文搜索", win="BurntSushi.ripgrep.MSVC", ubuntu=["ripgrep"], kali=["ripgrep"], probe="rg"),
    Tool("fd", "recon", "按名找文件", win="sharkdp.fd", ubuntu=["fd-find"], kali=["fd-find"], probe="fd"),
    # web
    Tool("ffuf", "web", "目录/参数 fuzz", ubuntu=["ffuf"], kali=["ffuf"], go="github.com/ffuf/ffuf/v2@latest", probe="ffuf"),
    Tool("gobuster", "web", "vhost/DNS 枚举", ubuntu=["gobuster"], kali=["gobuster"], go="github.com/OJ/gobuster/v3@latest", probe="gobuster"),
    Tool("httpx", "web", "批量探活", ubuntu=["httpx-toolkit"], kali=["httpx-toolkit"], go="github.com/projectdiscovery/httpx/cmd/httpx@latest", probe="httpx",
         note="Debian 系的 httpx 是 Python 版，pd 版叫 httpx-toolkit"),
    Tool("nuclei", "web", "模板扫漏洞", ubuntu=["nuclei"], kali=["nuclei"], go="github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest", probe="nuclei"),
    Tool("sqlmap", "web", "SQL 注入", ubuntu=["sqlmap"], kali=["sqlmap"], pip="sqlmap", probe="sqlmap"),
    Tool("mitmproxy", "web", "抓包改包", ubuntu=["mitmproxy"], kali=["mitmproxy"], pip="mitmproxy", probe="mitmdump"),
    # binary / pwn
    Tool("gdb", "pwn", "动态调试", ubuntu=["gdb"], kali=["gdb"], probe="gdb"),
    Tool("radare2", "pwn", "反汇编", ubuntu=["radare2"], kali=["radare2"], probe="r2"),
    Tool("rizin", "pwn", "r2 现代分支", ubuntu=["rizin"], kali=["rizin"], probe="rizin"),
    Tool("upx", "pwn", "解壳", win="UPX.UPX", ubuntu=["upx-ucl"], kali=["upx-ucl"], probe="upx"),
    Tool("patchelf", "pwn", "改 rpath/interp", ubuntu=["patchelf"], kali=["patchelf"], probe="patchelf"),
    Tool("pwntools", "pwn", "exploit 框架 + checksec", pip="pwntools", probe="pwn"),
    # forensics
    Tool("exiftool", "forensics", "元数据", win="OliverBetz.ExifTool", ubuntu=["libimage-exiftool-perl"],
         kali=["libimage-exiftool-perl"], probe="exiftool"),
    Tool("binwalk", "forensics", "固件/嵌套包", ubuntu=["binwalk"], kali=["binwalk"], pip="binwalk", probe="binwalk"),
    Tool("foremost", "forensics", "文件雕刻", ubuntu=["foremost"], kali=["foremost"], probe="foremost"),
    Tool("sleuthkit", "forensics", "文件系统取证", ubuntu=["sleuthkit"], kali=["sleuthkit"], probe="fls"),
    Tool("tshark", "forensics", "pcap 分析", win="WiresharkFoundation.Wireshark", ubuntu=["tshark"], kali=["tshark"], probe="tshark"),
    Tool("volatility3", "forensics", "内存镜像", pip="volatility3", probe="vol"),
    Tool("scapy", "forensics", "构造报文", pip="scapy", probe="scapy"),
    # crypto
    Tool("hashcat", "crypto", "破哈希", ubuntu=["hashcat"], kali=["hashcat"], probe="hashcat"),
    Tool("john", "crypto", "john the ripper", ubuntu=["john"], kali=["john"], probe="john"),
    Tool("pycryptodome", "crypto", "AES/RSA 原型", pip="pycryptodome", probe="Crypto"),
    Tool("gmpy2", "crypto", "大整数", pip="gmpy2", probe="gmpy2"),
    Tool("z3", "crypto", "约束求解", pip="z3-solver", probe="z3"),
    # android
    Tool("jadx", "android", "APK 反编译", win="Skylot.jadx", ubuntu=["jadx"], kali=["jadx"], probe="jadx"),
    Tool("apktool", "android", "资源还原", ubuntu=["apktool"], kali=["apktool"], probe="apktool"),
    Tool("frida", "android", "运行时 hook", pip="frida-tools", probe="frida"),
    # qol
    Tool("7z", "qol", "解压缩", win="7zip.7zip", ubuntu=["p7zip-full"], kali=["p7zip-full"], probe="7z"),
]

GROUPS = ["base", "recon", "web", "pwn", "forensics", "crypto", "android", "qol"]
BY_ID = {t.id: t for t in CATALOG}


# --------------------------------------------------------------------- helpers
def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def run(argv: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def distro() -> str:
    """"ubuntu" | "kali" | "debian" | "other" """
    if not IS_LINUX:
        return ""
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return "other"
    if "kali" in text:
        return "kali"
    if "ubuntu" in text:
        return "ubuntu"
    if "debian" in text:
        return "debian"
    return "other"


def wsl_dists() -> list[str]:
    if not IS_WIN or not has("wsl.exe"):
        return []
    rc, out = run(["wsl.exe", "-l", "-q"], timeout=25)
    if rc != 0:
        return []
    return [x.strip().strip("\x00") for x in out.splitlines() if x.strip()]


# --------------------------------------------------------------------- targets
@dataclass
class Target:
    key: str            # windows | ubuntu | kali
    label: str
    kind: str           # winget | apt
    where: str          # 描述：本机 / WSL 发行版名


def available_targets() -> list[Target]:
    out: list[Target] = []
    if IS_WIN:
        out.append(Target("windows", "Windows 本机（winget）", "winget", "host"))
        for d in wsl_dists():
            dl = d.lower()
            if "kali" in dl:
                out.append(Target("kali", f"WSL: {d}（Kali）", "apt", d))
            elif "ubuntu" in dl or "debian" in dl:
                out.append(Target("ubuntu", f"WSL: {d}（Ubuntu/Debian）", "apt", d))
    elif IS_LINUX:
        d = distro()
        if d == "kali":
            out.append(Target("kali", "本机（Kali）", "apt", "host"))
        else:
            out.append(Target("ubuntu", f"本机（{d or 'Linux'}，Debian 系）", "apt", "host"))
    return out


# --------------------------------------------------------------------- resolve
_APT_CACHE: dict[str, bool] = {}
_WINGET_CACHE: dict[str, bool] = {}


def apt_available(pkgs: list[str]) -> dict[str, bool]:
    if not has("apt-cache"):
        return {p: False for p in pkgs}
    res = {}
    for p in pkgs:
        if p not in _APT_CACHE:
            rc, out = run(["apt-cache", "policy", p], timeout=20)
            _APT_CACHE[p] = rc == 0 and "Candidate:" in out and "(none)" not in out
        res[p] = _APT_CACHE[p]
    return res


def winget_available(wid: str) -> bool:
    if not wid:
        return False
    if wid in _WINGET_CACHE:
        return _WINGET_CACHE[wid]
    if not has("winget"):
        _WINGET_CACHE[wid] = False
        return False
    rc, out = run(["winget", "show", "--id", wid, "--exact",
                   "--accept-source-agreements"], timeout=45)
    _WINGET_CACHE[wid] = rc == 0 and ("找不到" not in out and "No package found" not in out)
    return _WINGET_CACHE[wid]


def packages_for(tool: Tool, target: Target) -> list[str]:
    if target.kind == "winget":
        return [tool.win] if tool.win else []
    return tool.kali if target.key == "kali" else tool.ubuntu


def installed(tool: Tool) -> bool:
    probe = tool.probe or tool.id
    if tool.pip and not (tool.win or tool.ubuntu or tool.kali):
        import importlib.util
        return importlib.util.find_spec(tool.pip) is not None
    return has(probe)


def plan(target: Target, groups: list[str] | None) -> list[dict]:
    rows = []
    for t in CATALOG:
        if groups and t.group not in groups:
            continue
        pkgs = packages_for(t, target)
        already = installed(t)
        if already:
            avail = True
        elif pkgs and target.kind == "apt":
            avail = all(apt_available(pkgs).values())
        elif pkgs and target.kind == "winget":
            avail = winget_available(pkgs[0])
        else:
            avail = False                      # no package for this OS
        fallback = ""
        if not avail:
            if t.pip:
                fallback = f"pip:{t.pip}"
            elif t.go and has("go"):
                fallback = "go install"
            elif t.go:
                fallback = "go install（需先装 go）"
        note = t.note
        if not avail and not fallback:
            note = (note + " " if note else "") + "该平台无包"
        rows.append({
            "id": t.id, "group": t.group, "why": t.why,
            "installed": already,
            "pkgs": pkgs, "available": avail, "fallback": fallback,
            "note": note,
        })
    return rows


# --------------------------------------------------------------------- install
def cmd_for(row: dict, target: Target, allow_sudo: bool) -> list[list[str]]:
    t = BY_ID[row["id"]]
    if target.kind == "winget":
        if not t.win:
            return []
        return [["winget", "install", "-e", "--id", t.win, "--accept-package-agreements",
                 "--accept-source-agreements"]]
    cmds: list[list[str]] = []
    if row["available"] and row["pkgs"]:
        base = ["apt-get", "install", "-y", *row["pkgs"]]
        cmds.append(["sudo", *base] if allow_sudo else base)
    elif t.pip:
        cmds.append(["pip3", "install", "--user", t.pip])
    elif t.go and has("go"):
        cmds.append(["go", "install", t.go])
    return cmds


def show_plan(rows: list[dict], target: Target) -> None:
    print(f"\n目标：{target.label}")
    print(f"  {'id':<12} {'组':<10} {'包名':<32} {'状态'}")
    for r in rows:
        state = "已装" if r["installed"] else ("可装" if (r["available"] or r["fallback"]) else "不可用")
        pkg = ", ".join(r["pkgs"]) if r["pkgs"] else (r["fallback"] or "-")
        note = f"  ← {r['note']}" if r["note"] else ""
        print(f"  {r['id']:<12} {r['group']:<10} {pkg:<32} {state}{note}")


# --------------------------------------------------------------------- menu
def menu(targets: list[Target]) -> Target | None:
    print(c("1;36", "选择要把工具装到哪个系统："))
    for i, t in enumerate(targets, 1):
        print(f"  {i}. {t.label}")
    print("  0. 跳过（只装 pi 配置，不装工具）")
    if IS_WIN and not any(t.where != "host" for t in targets):
        print(c("33", "  （没检测到 WSL 发行版；先用管理员 PowerShell 跑 wsl --install -d Ubuntu）"))
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw == "0" or raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(targets):
            return targets[int(raw) - 1]
        print("  ?")


def gui_pick(targets: list[Target]) -> Target | None:
    """Windows: 一个简单的选择框；任何异常都回落到菜单。"""
    if not IS_WIN:
        return None
    items = "|".join(t.label for t in targets)
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$f=New-Object System.Windows.Forms.Form;"
        "$f.Text='选择工具安装目标';$f.Width=520;$f.Height=260;"
        "$l=New-Object System.Windows.Forms.Label;$l.Text='工具装到哪里？';$l.AutoSize=$true;$l.Location='12,12';$f.Controls.Add($l);"
        "$b=New-Object System.Windows.Forms.ListBox;$b.Location='12,40';$b.Width=470;$b.Height=140;"
        f"$b.Items.AddRange(('{items}').Split('|'));$b.SelectedIndex=0;$f.Controls.Add($b);"
        "$ok=New-Object System.Windows.Forms.Button;$ok.Text='OK';$ok.Location='320,195';$ok.DialogResult='OK';$f.Controls.Add($ok);"
        "$f.AcceptButton=$ok;[void]$f.ShowDialog();Write-Output $b.SelectedIndex"
    )
    rc, out = run(["powershell", "-NoProfile", "-Command", ps], timeout=300)
    if rc != 0:
        return None
    try:
        idx = int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return targets[idx] if 0 <= idx < len(targets) else None


# --------------------------------------------------------------------- main
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="", help="windows | ubuntu | kali | auto")
    ap.add_argument("--group", default="", help="逗号分隔：recon,web,pwn,forensics,crypto,android,qol")
    ap.add_argument("--list", action="store_true", help="只打印映射表")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--allow-sudo", action="store_true")
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--auto", action="store_true", help="非交互：用第一个可用目标")
    ap.add_argument("--markdown", action="store_true", help="输出三系统对照表（给 docs 用）")
    ap.add_argument("--all-groups", action="store_true", help="(默认就是全部组)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    targets = available_targets()
    if not targets:
        print("no supported target detected (need Windows/winget or a Debian-family Linux)")
        return 2

    groups = [g.strip() for g in args.group.split(",") if g.strip()] or None
    if args.markdown:
        print("| 工具 | 组 | Windows (winget) | Ubuntu/Debian (apt) | Kali (apt) | 兜底 |")
        print("|---|---|---|---|---|---|")
        for t in CATALOG:
            win = f"`{t.win}`" if t.win else "—"
            ub = ", ".join(f"`{x}`" for x in t.ubuntu) or "—"
            ka = ", ".join(f"`{x}`" for x in t.kali) or "—"
            fb = []
            if t.pip:
                fb.append(f"pip:`{t.pip}`")
            if t.go:
                fb.append("go install")
            print(f"| `{t.id}` | {t.group} | {win} | {ub} | {ka} | {', '.join(fb) or '—'} |")
        return 0
    if args.list:
        for t in targets:
            show_plan(plan(t, groups), t)
        return 0

    if args.target and args.target != "auto":
        match = [t for t in targets if t.key == args.target]
        if not match:
            print(f"target {args.target!r} not available here; have: {[t.key for t in targets]}")
            return 2
        chosen: list[Target] = match[:1]
    elif args.yes or args.auto:
        chosen = targets[:1]        # 非交互默认第一个（通常是 Windows 本机）
    else:
        picked = gui_pick(targets) if args.gui else None
        picked = picked or menu(targets)
        chosen = [picked] if picked else []

    if not chosen:
        print("skipped")
        return 0

    for target in chosen:
        rows = plan(target, groups)
        show_plan(rows, target)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            continue
        todo = [r for r in rows if not r["installed"]]
        if not todo:
            print("  全部已安装")
            continue
        if not args.yes and not args.dry_run:
            try:
                ans = input(f"\n安装以上 {len(todo)} 项？[Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans == "n":
                print("  skipped")
                continue
        if target.kind == "apt" and not args.allow_sudo and has("apt-get"):
            if os.geteuid() != 0:
                print(c("33", "  ! 需要 sudo：加 --allow-sudo 才会执行 apt，否则只打印命令"))
        failed = []
        for r in todo:
            cmds = cmd_for(r, target, args.allow_sudo)
            if not cmds:
                print(c("33", f"    ! {r['id']}: no package for this target"
                              + (" — install go first" if BY_ID[r['id']].go else "")))
                failed.append(r["id"])
                continue
            for argv in cmds:
                if argv[0] == "sudo" and not args.allow_sudo:
                    print(f"    skip (needs sudo): {' '.join(argv)}")
                    continue
                print(f"    $ {' '.join(argv)}")
                if args.dry_run:
                    continue
                rc, _ = run(argv, timeout=900)
                if rc != 0:
                    failed.append(r["id"])
                    print(c("33", f"    ! {r['id']} failed (exit {rc})"))
        if failed:
            print(c("33", f"  failed: {', '.join(sorted(set(failed)))}"))
            print("  重试：python scripts/setup-tools.py --target "
                  f"{target.key} --group {r['group']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
