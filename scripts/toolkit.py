#!/usr/bin/env python3
"""Detect missing CTF tooling, let the user pick, then install only what was picked.

    python scripts/toolkit.py --check                # table: what is here / missing
    python scripts/toolkit.py --check --json         # machine readable (for an agent)
    python scripts/toolkit.py --check --missing      # only the gaps
    python scripts/toolkit.py --install             # interactive picker
    python scripts/toolkit.py --install ffuf,jq     # install by id
    python scripts/toolkit.py --install web         # install a whole category
    python scripts/toolkit.py --install all --dry-run --yes

Safety model
------------
* Only commands from the built-in catalog run. There is no "download this URL"
  path, no `curl | sh`, no arbitrary argv passthrough.
* Nothing installs without an explicit pick + confirmation; `--yes` only skips
  the confirmation prompt for the ids you already named.
* `sudo` is never implied: pass `--allow-sudo` or the command is printed and
  skipped.
* Writes `~/.pi/agent/toolkit.json` so an agent can see which tools exist
  instead of guessing at runtime.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

AGENT_DIR = Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
STATE = AGENT_DIR / "toolkit.json"


@dataclass
class Tool:
    id: str
    category: str
    why: str
    # check
    kind: str = "binary"          # binary | python | npm
    probe: str = ""               # binary name / python module / npm package
    version_args: list[str] = field(default_factory=lambda: ["--version"])
    # install: platform -> list of argv lists (each row runs in order)
    install: dict[str, list[list[str]]] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)   # other commands that must exist first


def T(id, category, why, kind="binary", probe="", version_args=None, win=None, linux=None, mac=None,
      requires=None):
    t = Tool(id, category, why, kind, probe or id, version_args or ["--version"])
    t.requires = requires or []
    if win:
        t.install["windows"] = win
    if linux:
        t.install["linux"] = linux
    if mac:
        t.install["darwin"] = mac
    return t


CATALOG: list[Tool] = [
    # ---------------- base toolchains
    T("go", "base", "装 ffuf/httpx/nuclei 这类 Go 工具的前置", probe="go", version_args=["version"],
      win=[["winget", "install", "-e", "--id", "GoLang.Go", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "golang-go"]], mac=[["brew", "install", "go"]]),
    # ---------------- recon / web
    T("nmap", "recon", "端口与服务指纹",
      win=[["winget", "install", "-e", "--id", "Insecure.Nmap", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "nmap"]], mac=[["brew", "install", "nmap"]]),
    T("ncat", "recon", "shell/中继/快速 listener", probe="ncat",
      win=[["winget", "install", "-e", "--id", "Insecure.Nmap", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "ncat"]], mac=[["brew", "install", "nmap"]]),
    T("jq", "recon", "读 JSON 响应（比 python -c 快）",
      win=[["winget", "install", "-e", "--id", "jqlang.jq", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "jq"]], mac=[["brew", "install", "jq"]]),
    T("ffuf", "web", "目录/参数 fuzz（Go 单文件）", probe="ffuf", requires=["go"],
      win=[["go", "install", "github.com/ffuf/ffuf/v2@latest"]],
      linux=[["go", "install", "github.com/ffuf/ffuf/v2@latest"]],
      mac=[["brew", "install", "ffuf"]]),
    T("gobuster", "web", "DNS/vhost 枚举", probe="gobuster", requires=["go"],
      win=[["go", "install", "github.com/OJ/gobuster/v3@latest"]],
      linux=[["go", "install", "github.com/OJ/gobuster/v3@latest"]],
      mac=[["brew", "install", "gobuster"]]),
    T("httpx", "recon", "批量探活/指纹（projectdiscovery）", probe="httpx", requires=["go"],
      win=[["go", "install", "github.com/projectdiscovery/httpx/cmd/httpx@latest"]],
      linux=[["go", "install", "github.com/projectdiscovery/httpx/cmd/httpx@latest"]],
      mac=[["brew", "install", "httpx"]]),
    T("nuclei", "web", "模板化扫漏洞", probe="nuclei", requires=["go"],
      win=[["go", "install", "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"]],
      linux=[["go", "install", "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"]],
      mac=[["brew", "install", "nuclei"]]),
    T("sqlmap", "web", "SQL 注入（先手测再上它）", kind="binary", probe="sqlmap",
      win=[["pip", "install", "--user", "sqlmap"]], linux=[["pip", "install", "--user", "sqlmap"]],
      mac=[["brew", "install", "sqlmap"]]),
    # ---------------- binary / pwn / rev
    T("gdb", "binary", "动态调试（Linux/WSL）", probe="gdb",
      linux=[["sudo", "apt", "install", "-y", "gdb"]], mac=[["brew", "install", "gdb"]]),
    T("radare2", "binary", "反汇编/补丁（r2 -A，Linux/macOS）", probe="r2",
      linux=[["sudo", "apt", "install", "-y", "radare2"]], mac=[["brew", "install", "radare2"]]),
    T("rizin", "binary", "r2 的现代分支（Windows 用这个）", probe="rizin", version_args=["-v"],
      win=[["winget", "install", "-e", "--id", "Rizin.Rizin", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "rizin"]], mac=[["brew", "install", "rizin"]]),
    T("upx", "binary", "解壳/压壳", probe="upx",
      win=[["winget", "install", "-e", "--id", "UPX.UPX", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "upx-ucl"]], mac=[["brew", "install", "upx"]]),
    T("patchelf", "binary", "改 interpreter/rpath（pwn 必备）", probe="patchelf",
      linux=[["sudo", "apt", "install", "-y", "patchelf"]]),
    T("pwntools", "binary", "exploit 框架 + checksec（一眼看 RELRO/NX/PIE）", kind="python",
      probe="pwn", version_args=[],
      win=[["pip", "install", "--user", "pwntools"]], linux=[["pip", "install", "--user", "pwntools"]],
      mac=[["pip", "install", "--user", "pwntools"]]),
    # ---------------- forensics
    T("exiftool", "forensics", "元数据/隐写线索", probe="exiftool",
      win=[["winget", "install", "-e", "--id", "OliverBetz.ExifTool", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "libimage-exiftool-perl"]],
      mac=[["brew", "install", "exiftool"]]),
    T("binwalk", "forensics", "固件/嵌套压缩包", kind="python", probe="binwalk", version_args=[],
      win=[["pip", "install", "--user", "binwalk"]], linux=[["pip", "install", "--user", "binwalk"]],
      mac=[["pip", "install", "--user", "binwalk"]]),
    T("foremost", "forensics", "文件雕刻", probe="foremost",
      linux=[["sudo", "apt", "install", "-y", "foremost"]]),
    T("tshark", "forensics", "pcap 命令行分析", probe="tshark",
      win=[["winget", "install", "-e", "--id", "WiresharkFoundation.Wireshark",
            "--accept-package-agreements", "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "tshark"]], mac=[["brew", "install", "wireshark"]]),
    T("volatility3", "forensics", "内存镜像分析", kind="python", probe="volatility3", version_args=[],
      win=[["pip", "install", "--user", "volatility3"]], linux=[["pip", "install", "--user", "volatility3"]],
      mac=[["pip", "install", "--user", "volatility3"]]),
    # ---------------- crypto
    T("hashcat", "crypto", "GPU/CPU 破哈希（有结构性证据才用；Windows 需手动下官方包）", probe="hashcat",
      linux=[["sudo", "apt", "install", "-y", "hashcat"]], mac=[["brew", "install", "hashcat"]]),
    T("pycryptodome", "crypto", "AES/RSA 快速原型", kind="python", probe="Crypto", version_args=[],
      win=[["pip", "install", "--user", "pycryptodome"]], linux=[["pip", "install", "--user", "pycryptodome"]],
      mac=[["pip", "install", "--user", "pycryptodome"]]),
    T("gmpy2", "crypto", "大整数/开方（Coppersmith 前置）", kind="python", probe="gmpy2", version_args=[],
      win=[["pip", "install", "--user", "gmpy2"]], linux=[["pip", "install", "--user", "gmpy2"]],
      mac=[["pip", "install", "--user", "gmpy2"]]),
    T("z3", "crypto", "约束求解（keygen/VM 题）", kind="python", probe="z3", version_args=[],
      win=[["pip", "install", "--user", "z3-solver"]], linux=[["pip", "install", "--user", "z3-solver"]],
      mac=[["pip", "install", "--user", "z3-solver"]]),
    T("scapy", "forensics", "构造/解析报文", kind="python", probe="scapy", version_args=[],
      win=[["pip", "install", "--user", "scapy"]], linux=[["pip", "install", "--user", "scapy"]],
      mac=[["pip", "install", "--user", "scapy"]]),
    # ---------------- android / misc
    T("jadx", "android", "APK 反编译（dex→java）", probe="jadx",
      win=[["winget", "install", "-e", "--id", "Skylot.jadx", "--accept-package-agreements",
            "--accept-source-agreements"]],
      mac=[["brew", "install", "jadx"]]),
    T("apktool", "android", "资源/清单还原", probe="apktool",
      linux=[["sudo", "apt", "install", "-y", "apktool"]], mac=[["brew", "install", "apktool"]]),
    T("frida-tools", "android", "运行时 hook", kind="python", probe="frida_tools", version_args=[],
      win=[["pip", "install", "--user", "frida-tools"]], linux=[["pip", "install", "--user", "frida-tools"]],
      mac=[["pip", "install", "--user", "frida-tools"]]),
    # ---------------- quality of life
    T("ripgrep", "qol", "全文搜索（代替 grep -r）", probe="rg",
      win=[["winget", "install", "-e", "--id", "BurntSushi.ripgrep.MSVC",
            "--accept-package-agreements", "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "ripgrep"]], mac=[["brew", "install", "ripgrep"]]),
    T("fd", "qol", "按名字找文件（代替 find）", probe="fd",
      win=[["winget", "install", "-e", "--id", "sharkdp.fd", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "fd-find"]], mac=[["brew", "install", "fd"]]),
    T("7z", "qol", "解一切压缩包", probe="7z",
      win=[["winget", "install", "-e", "--id", "7zip.7zip", "--accept-package-agreements",
            "--accept-source-agreements"]],
      linux=[["sudo", "apt", "install", "-y", "7zip"]], mac=[["brew", "install", "sevenzip"]]),
    T("mitmproxy", "web", "抓包/改包（mitmproxy / mitmdump）", kind="python", probe="mitmproxy",
      version_args=[],
      win=[["pip", "install", "--user", "mitmproxy"]], linux=[["pip", "install", "--user", "mitmproxy"]],
      mac=[["pip", "install", "--user", "mitmproxy"]]),
]

BY_ID = {t.id: t for t in CATALOG}
CATEGORIES = sorted({t.category for t in CATALOG})


# ------------------------------------------------------------------ probing
def plat() -> str:
    p = sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "darwin"
    return "linux"


def probe_binary(name: str, version_args: list[str]) -> tuple[bool, str]:
    path = shutil.which(name)
    if not path:
        return False, ""
    if not version_args:
        return True, path
    try:
        out = subprocess.run([path, *version_args], capture_output=True, text=True, timeout=8)
        first = (out.stdout or out.stderr).strip().splitlines()
        ver = first[0][:60] if first else ""
        return True, f"{path}" + (f" ({ver})" if ver else "")
    except Exception:
        return True, path


def probe(t: Tool) -> tuple[bool, str]:
    if t.kind == "python":
        spec = importlib.util.find_spec(t.probe)
        if spec is None:
            return False, ""
        ver = ""
        try:
            mod = __import__(t.probe)
            ver = str(getattr(mod, "__version__", ""))[:30]
        except Exception:
            pass
        return True, f"python:{t.probe}" + (f" {ver}" if ver else "")
    return probe_binary(t.probe, t.version_args)


def survey() -> list[dict]:
    rows = []
    for t in CATALOG:
        ok, detail = probe(t)
        rows.append({"id": t.id, "category": t.category, "why": t.why,
                     "installed": ok, "detail": detail,
                     "recipe": t.install.get(plat(), [])})
    return rows


# ------------------------------------------------------------------ installing

# ------------------------------------------------------------------ versions
VERSION_TTL = 7 * 24 * 3600
_WINGET_LOCK = __import__("threading").Lock()   # winget serialises on its source DB


def _cache() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_text(argv: list[str], timeout: float = 25) -> str:
    """Run a probe command and decode as UTF-8 (winget/apt emit UTF-8 even on GBK consoles)."""
    out = subprocess.run(argv, capture_output=True, timeout=timeout)
    return (out.stdout or out.stderr).decode("utf-8", "replace")


def _package_id(recipe: list[list[str]]) -> tuple[str, str]:
    """Return (manager, identifier) for a recipe, e.g. ('winget', 'jqlang.jq')."""
    if not recipe:
        return "", ""
    argv = recipe[0]
    mgr = argv[0]
    if mgr == "winget" and "--id" in argv:
        return "winget", argv[argv.index("--id") + 1]
    if mgr in ("apt", "apt-get", "dnf", "pacman"):
        return "apt", argv[-1]
    if mgr == "brew":
        return "brew", argv[-1]
    if mgr.startswith("pip"):
        skip = {"install", "download", "wheel"}
        names = [a for a in argv[1:] if not a.startswith("-") and a not in skip]
        return "pip", names[0] if names else ""
    if mgr == "npm":
        return "npm", argv[-1]
    if mgr == "go":
        return "go", argv[-1].split("@")[0]
    return mgr, argv[-1]


def _pypi_version(pkg: str) -> str:
    import urllib.request
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as r:
        return json.loads(r.read().decode()).get("info", {}).get("version", "?")


def resolve_version(tool: Tool) -> tuple[str, str]:
    """Return (version, note). Never raises; unknowns come back as '?'."""
    mgr, ident = _package_id(tool.install.get(plat(), []))
    try:
        if mgr == "winget":
            text = ""
            for attempt in (1, 2):
                with _WINGET_LOCK:
                    text = _run_text(["winget", "show", "--id", ident, "--exact",
                                      "--accept-source-agreements"], timeout=40)
                if "版本:" in text or "Version:" in text:
                    break
            for line in text.splitlines():
                low = line.strip()
                if low.startswith(("Version:", "版本:")):
                    return low.split(":", 1)[1].strip(), ""
            return "?", "winget show: 未解析到版本"
        if mgr == "apt":
            for line in _run_text(["apt-cache", "policy", ident], timeout=10).splitlines():
                if line.strip().startswith("Candidate:"):
                    v = line.split(":", 1)[1].strip()
                    return (v, "") if v and v != "(none)" else ("?", "apt 无候选版本")
            return "?", "apt-cache 不可用"
        if mgr == "brew":
            data = json.loads(_run_text(["brew", "info", "--json=v2", ident], timeout=20) or "{}")
            v = (data.get("formulae") or [{}])[0].get("versions", {}).get("stable")
            return (v or "?", "")
        if mgr == "pip":
            return _pypi_version(ident), "PyPI"
        if mgr == "npm":
            text = _run_text(["npm", "view", ident, "version"], timeout=20).strip()
            return (text.splitlines()[-1] if text else "?"), ""
        if mgr == "go":
            return "latest (@latest)", "go install 自行解析"
    except Exception as exc:  # network / missing manager / parse noise
        return "?", f"{type(exc).__name__}"
    return "?", "无 recipe"


def version_table(chosen: list[str], refresh: bool = False, jobs: int = 6) -> dict[str, dict]:
    """Probe versions for the chosen ids (cached, parallel). Returns id -> info."""
    cached = (_cache().get("versions") or {})
    now = int(__import__("time").time())
    todo = []
    info: dict[str, dict] = {}
    for tid in chosen:
        hit = cached.get(tid)
        if hit and not refresh and now - int(hit.get("at", 0)) < VERSION_TTL:
            info[tid] = hit
        else:
            todo.append(tid)

    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futs = {pool.submit(resolve_version, BY_ID[tid]): tid for tid in todo}
            for fut, tid in futs.items():
                version, note = fut.result()
                info[tid] = {"version": version, "note": note, "at": now}
        state = _cache()
        state.setdefault("versions", {}).update({t: info[t] for t in todo})
        try:
            AGENT_DIR.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return info


def _w(text: str) -> int:
    """Display width: CJK counts as 2 columns."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def print_plan(chosen: list[str], info: dict[str, dict]) -> None:
    print(f"\n计划安装（{plat()}）：")
    w1 = max(_w(t) for t in chosen) + 2
    rows = []
    for tid in chosen:
        t = BY_ID[tid]
        mgr, ident = _package_id(t.install.get(plat(), []))
        v = info.get(tid, {}).get("version", "?")
        src = f"{mgr}:{ident}" if mgr else "无 recipe"
        note = "需要先装: " + ",".join(t.requires) if t.requires and any(
            not shutil.which(r) for r in t.requires) else info.get(tid, {}).get("note", "")
        rows.append((tid, src, v, note))
    w2 = max(_w(r[1]) for r in rows) + 2
    w3 = max(_w(r[2]) for r in rows) + 2
    print(f"  {_pad('id', w1)}{_pad('来源', w2)}{_pad('版本', w3)} 备注")
    for tid, src, v, note in rows:
        print(f"  {_pad(tid, w1)}{_pad(src, w2)}{_pad(v, w3)}{note}")


def run_rows(rows: list[list[str]], dry: bool, allow_sudo: bool) -> bool:
    for argv in rows:
        needs_sudo = argv[:1] == ["sudo"]
        if needs_sudo and not allow_sudo:
            print(f"    skip (needs sudo, pass --allow-sudo): {' '.join(argv)}")
            continue
        if not shutil.which(argv[0]) and argv[0] != "sudo":
            print(f"    skip (missing {argv[0]}): {' '.join(argv)}")
            return False
        print(f"    $ {' '.join(argv)}")
        if dry:
            continue
        try:
            rc = subprocess.run(argv).returncode
        except KeyboardInterrupt:
            print("    interrupted")
            return False
        if rc != 0:
            print(f"    failed (exit {rc})")
            return False
    return True


def write_state() -> None:
    try:
        _write_state()
    except OSError as exc:
        print(f"  (state not written: {exc})")


def _write_state() -> None:
    rows = survey()
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(
        {"platform": plat(), "tools": {r["id"]: {"installed": r["installed"],
                                                 "detail": r["detail"],
                                                 "category": r["category"]}
                                       for r in rows}},
        ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ cli
def print_table(rows: list[dict], only_missing: bool) -> None:
    width = max(len(r["id"]) for r in rows) + 2
    order = {c: i for i, c in enumerate(CATEGORIES)}
    shown = sorted((r for r in rows if not (only_missing and r["installed"])),
                   key=lambda r: (order.get(r["category"], 99), r["id"]))
    cat = ""
    for r in shown:
        if r["category"] != cat:
            cat = r["category"]
            print(f"\n[{cat}]")
        mark = "✓" if r["installed"] else "·"
        detail = r["detail"] if r["installed"] else r["why"]
        print(f"  {mark} {r['id']:<{width}} {detail}")
    miss = sum(1 for r in rows if not r["installed"])
    print(f"\n{len(rows) - miss}/{len(rows)} installed, {miss} missing")


def pick(rows: list[dict]) -> list[str]:
    missing = [r for r in rows if not r["installed"]]
    if not missing:
        print("nothing to install")
        return []
    for i, r in enumerate(missing, 1):
        print(f"  {i:>2}. {r['id']:<12} [{r['category']}] {r['why']}")
    print(f"\n  选择：编号(1,3,5 或 2-4)、类别({','.join(CATEGORIES)})、all、回车取消")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  (no interactive input; use --install <ids> or --install all --yes)")
        return []
    if not raw:
        return []
    return resolve(raw, missing)


def resolve(raw: str, pool: list[dict]) -> list[str]:
    ids = {r["id"] for r in pool}
    cats = {r["category"] for r in pool}
    if raw == "all":
        return sorted(ids)
    out: set[str] = set()
    for part in raw.replace(" ", ",").split(","):
        if not part:
            continue
        if part in ids:
            out.add(part)
        elif part in cats:
            out |= {r["id"] for r in pool if r["category"] == part}
        elif "-" in part and part.replace("-", "").isdigit():
            a, b = part.split("-", 1)
            for i in range(int(a), int(b) + 1):
                if 1 <= i <= len(pool):
                    out.add(pool[i - 1]["id"])
        elif part.isdigit() and 1 <= int(part) <= len(pool):
            out.add(pool[int(part) - 1]["id"])
        else:
            print(f"  unknown: {part}")
    return [r["id"] for r in pool if r["id"] in out]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="show the table and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--missing", action="store_true", help="only gaps")
    ap.add_argument("--install", nargs="?", const="__picker__", metavar="IDS",
                    help="ids / category / all; omit value for the interactive picker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--allow-sudo", action="store_true")
    ap.add_argument("--no-versions", action="store_true", help="skip version probing (offline)")
    ap.add_argument("--refresh", action="store_true", help="ignore the 7-day version cache")
    ap.add_argument("--with-versions", action="store_true", help="with --check: also resolve latest")
    args = ap.parse_args()

    rows = survey()

    if args.json:
        print(json.dumps({"platform": plat(), "tools": rows}, ensure_ascii=False, indent=2))
        return 0
    if args.check or not args.install:
        print_table(rows, args.missing)
        if args.with_versions:
            missing_ids = [r["id"] for r in rows if not r["installed"]]
            if missing_ids:
                print(f"\nresolving latest versions for {len(missing_ids)} missing tools…")
                info = version_table(missing_ids, refresh=args.refresh)
                for tid in missing_ids:
                    print(f"  {tid:<12} {info[tid]['version']:<14} {info[tid].get('note','')}")
        if not args.install:
            print("\ninstall: python scripts/toolkit.py --install   (或 --install jq,ffuf)")
            write_state()
        return 0

    if args.install == "__picker__":
        chosen = pick(rows)
    else:
        chosen = resolve(args.install, rows)
        chosen = [c for c in chosen if not next(r["installed"] for r in rows if r["id"] == c)] \
            if chosen else []

    if not chosen:
        print("nothing selected")
        return 0

    if args.no_versions:
        print(f"\nwill install ({plat()}): {', '.join(chosen)}")
    else:
        info = version_table(chosen, refresh=args.refresh)
        print_plan(chosen, info)
    ok_all = True
    for tid in chosen:
        t = BY_ID[tid]
        recipe = t.install.get(plat(), [])
        print(f"\n== {t.id}  ({t.why})")
        missing_req = [r for r in t.requires if not shutil.which(r)]
        if missing_req:
            print(f"    need first: {', '.join(missing_req)}  "
                  f"(python scripts/toolkit.py --install {','.join(missing_req)})")
            ok_all = False
            continue
        if not recipe:
            print(f"    no packaged recipe for {plat()}; see the project's release page")
            ok_all = False
            continue
        if not args.yes and not args.dry_run:
            if input("    install? [Y/n] ").strip().lower() == "n":
                print("    skipped")
                continue
        ok_all &= run_rows(recipe, args.dry_run, args.allow_sudo)

    if not args.dry_run:
        write_state()
        print(f"\nstate written: {STATE}")
        after = survey()
        got = [r["id"] for r in after if r["installed"]]
        print(f"now installed: {len(got)}/{len(after)}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
