#!/usr/bin/env python3
"""Check which CTF / benchmark tools are missing on this machine.

Installed as `~/.pi/agent/bin/tools_check.py` so the `tool-check` extension can
ask it at session start: no repo checkout, no network, stdlib only.

    python tools_check.py check              # human table
    python tools_check.py check --json        # machine readable (the extension uses this)
    python tools_check.py env                 # where am I + install targets
    python tools_check.py count-missing       # just the number (ASCII, for shell/CI)
    python tools_check.py check --group web   # one group only
    python tools_check.py check --catalog my.json

`--json` also carries `env` (OS / WSL state / SSH hosts), `targets` (install
destinations) and `probes` (id + executable pairs the extension reuses to check
a WSL/SSH host without loading this file there).

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
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CATALOG_VERSION = 2
ALL_PLATFORMS = ("windows", "linux", "darwin")

# id == the id used by scripts/setup-tools.py, so the hint it prints is runnable.
CATALOG: list[dict] = [
    {"id": "go", "group": "base", "desc": "装 Go 工具的前置", "probe": "go"},
    {"id": "nmap", "group": "recon", "desc": "端口/服务指纹", "probe": "nmap"},
    {"id": "ncat", "group": "recon", "desc": "listener/中继", "probe": "ncat"},
    {"id": "jq", "group": "recon", "desc": "读 JSON", "probe": "jq"},
    {"id": "ripgrep", "group": "recon", "desc": "全文搜索", "probe": "rg"},
    {"id": "fd", "group": "recon", "desc": "按名找文件", "probe": "fd"},
    {"id": "ffuf", "group": "web", "desc": "目录/参数 fuzz", "probe": "ffuf"},
    {"id": "gobuster", "group": "web", "desc": "vhost/DNS 枚举", "probe": "gobuster"},
    {"id": "httpx", "group": "web", "desc": "批量探活", "probe": "httpx"},
    {"id": "nuclei", "group": "web", "desc": "模板扫漏洞", "probe": "nuclei"},
    {"id": "sqlmap", "group": "web", "desc": "SQL 注入", "probe": "sqlmap", "module": "sqlmap"},
    {"id": "mitmproxy", "group": "web", "desc": "抓包改包", "probe": "mitmdump", "module": "mitmproxy"},
    {"id": "gdb", "group": "pwn", "desc": "动态调试", "probe": "gdb", "platforms": ("linux", "darwin")},
    {"id": "radare2", "group": "pwn", "desc": "反汇编", "probe": "r2", "platforms": ("linux", "darwin")},
    {"id": "rizin", "group": "pwn", "desc": "r2 现代分支", "probe": "rizin", "platforms": ("linux", "darwin"), "alt": ["radare2"]},
    {"id": "upx", "group": "pwn", "desc": "解壳", "probe": "upx"},
    {"id": "patchelf", "group": "pwn", "desc": "改 rpath/interp", "probe": "patchelf", "platforms": ("linux", "darwin")},
    {"id": "pwntools", "group": "pwn", "desc": "exploit 框架 + checksec", "probe": "pwn", "module": "pwn"},
    {"id": "exiftool", "group": "forensics", "desc": "元数据", "probe": "exiftool"},
    {"id": "binwalk", "group": "forensics", "desc": "固件/嵌套包", "probe": "binwalk", "module": "binwalk"},
    {"id": "foremost", "group": "forensics", "desc": "文件雕刻", "probe": "foremost", "platforms": ("linux", "darwin")},
    {"id": "sleuthkit", "group": "forensics", "desc": "文件系统取证", "probe": "fls", "platforms": ("linux", "darwin")},
    {"id": "tshark", "group": "forensics", "desc": "pcap 分析", "probe": "tshark"},
    {"id": "volatility3", "group": "forensics", "desc": "内存镜像", "probe": "vol", "module": "volatility3"},
    {"id": "scapy", "group": "forensics", "desc": "构造报文", "probe": "scapy", "module": "scapy"},
    {"id": "hashcat", "group": "crypto", "desc": "破哈希", "probe": "hashcat", "platforms": ("linux", "darwin")},
    {"id": "john", "group": "crypto", "desc": "john the ripper", "probe": "john", "platforms": ("linux", "darwin")},
    {"id": "pycryptodome", "group": "crypto", "desc": "AES/RSA 原型", "probe": "Crypto", "module": "Crypto"},
    {"id": "gmpy2", "group": "crypto", "desc": "大整数", "probe": "gmpy2", "module": "gmpy2"},
    {"id": "z3", "group": "crypto", "desc": "约束求解", "probe": "z3", "module": "z3"},
    {"id": "jadx", "group": "android", "desc": "APK 反编译", "probe": "jadx"},
    {"id": "apktool", "group": "android", "desc": "资源还原", "probe": "apktool", "platforms": ("linux", "darwin")},
    {"id": "frida", "group": "android", "desc": "运行时 hook", "probe": "frida", "module": "frida"},
    {"id": "7z", "group": "qol", "desc": "解压缩", "probe": "7z"},
    {"id": "curl", "group": "base", "desc": "HTTP 请求（几乎所有 payload 都用它）", "probe": "curl"},
    {"id": "wget", "group": "base", "desc": "下载", "probe": "wget", "platforms": ("linux", "darwin")},
    {"id": "binutils", "group": "base", "desc": "objdump/readelf/nm/strings", "probe": "objdump", "platforms": ("linux", "darwin")},
    {"id": "file", "group": "base", "desc": "文件类型识别", "probe": "file", "platforms": ("linux", "darwin")},
    {"id": "xxd", "group": "base", "desc": "十六进制查看", "probe": "xxd", "platforms": ("linux", "darwin")},
    {"id": "unzip", "group": "base", "desc": "解 zip", "probe": "unzip", "platforms": ("linux", "darwin")},
    {"id": "gcc", "group": "base", "desc": "本地复刻/编译 exp", "probe": "gcc", "platforms": ("linux", "darwin")},
    {"id": "make", "group": "base", "desc": "编译", "probe": "make", "platforms": ("linux", "darwin")},
    {"id": "ruby", "group": "base", "desc": "gem 工具的前置", "probe": "ruby", "platforms": ("linux", "darwin")},
    {"id": "dig", "group": "recon", "desc": "DNS 查询", "probe": "dig", "platforms": ("linux", "darwin")},
    {"id": "nc", "group": "recon", "desc": "netcat", "probe": "nc", "platforms": ("linux", "darwin")},
    {"id": "socat", "group": "recon", "desc": "端口转发/中继", "probe": "socat", "platforms": ("linux", "darwin")},
    {"id": "masscan", "group": "recon", "desc": "全端口快速扫描", "probe": "masscan", "platforms": ("linux", "darwin")},
    {"id": "whatweb", "group": "recon", "desc": "指纹识别", "probe": "whatweb", "platforms": ("linux", "darwin")},
    {"id": "subfinder", "group": "recon", "desc": "子域枚举", "probe": "subfinder"},
    {"id": "nikto", "group": "web", "desc": "老式 web 扫描", "probe": "nikto", "platforms": ("linux", "darwin")},
    {"id": "wpscan", "group": "web", "desc": "WordPress 扫描", "probe": "wpscan"},
    {"id": "wfuzz", "group": "web", "desc": "fuzz（老牌）", "probe": "wfuzz", "module": "wfuzz"},
    {"id": "dirsearch", "group": "web", "desc": "目录爆破", "probe": "dirsearch", "module": "dirsearch"},
    {"id": "arjun", "group": "web", "desc": "参数发现", "probe": "arjun", "module": "arjun"},
    {"id": "commix", "group": "web", "desc": "命令注入", "probe": "commix", "module": "commix"},
    {"id": "dalfox", "group": "web", "desc": "XSS 扫描", "probe": "dalfox"},
    {"id": "ROPgadget", "group": "pwn", "desc": "找 gadget", "probe": "ROPgadget", "module": "ROPgadget"},
    {"id": "ropper", "group": "pwn", "desc": "gadget 搜索/ROP 构建", "probe": "ropper", "module": "ropper"},
    {"id": "one_gadget", "group": "pwn", "desc": "libc one-gadget（ruby）", "probe": "one_gadget"},
    {"id": "seccomp-tools", "group": "pwn", "desc": "看 seccomp 规则（ruby）", "probe": "seccomp-tools"},
    {"id": "gdb-multiarch", "group": "pwn", "desc": "跨架构调试", "probe": "gdb-multiarch", "platforms": ("linux", "darwin")},
    {"id": "qemu-user", "group": "pwn", "desc": "跑异构架构二进制", "probe": "qemu-x86_64", "platforms": ("linux", "darwin")},
    {"id": "strace", "group": "pwn", "desc": "系统调用跟踪", "probe": "strace", "platforms": ("linux", "darwin")},
    {"id": "ltrace", "group": "pwn", "desc": "库调用跟踪", "probe": "ltrace", "platforms": ("linux", "darwin")},
    {"id": "angr", "group": "rev", "desc": "符号执行（重）", "probe": "angr", "module": "angr"},
    {"id": "unicorn", "group": "rev", "desc": "CPU 模拟（emu 题必备）", "probe": "unicorn", "module": "unicorn"},
    {"id": "capstone", "group": "rev", "desc": "反汇编引擎", "probe": "capstone", "module": "capstone"},
    {"id": "keystone", "group": "rev", "desc": "汇编引擎", "probe": "keystone", "module": "keystone"},
    {"id": "sage", "group": "crypto", "desc": "数学/格（体积大，通常自带）", "probe": "sage", "platforms": ("linux", "darwin")},
    {"id": "hashid", "group": "crypto", "desc": "哈希类型识别", "probe": "hashid", "module": "hashid"},
    {"id": "steghide", "group": "forensics", "desc": "隐写（jpeg/wav）", "probe": "steghide", "platforms": ("linux", "darwin")},
    {"id": "zsteg", "group": "forensics", "desc": "PNG/BMP 隐写（ruby）", "probe": "zsteg"},
    {"id": "pngcheck", "group": "forensics", "desc": "PNG 结构校验", "probe": "pngcheck", "platforms": ("linux", "darwin")},
    {"id": "scalpel", "group": "forensics", "desc": "文件雕刻", "probe": "scalpel", "platforms": ("linux", "darwin")},
    {"id": "testdisk", "group": "forensics", "desc": "分区/文件恢复（含 photorec）", "probe": "testdisk", "platforms": ("linux", "darwin")},
    {"id": "bulk-extractor", "group": "forensics", "desc": "批量特征提取", "probe": "bulk_extractor", "platforms": ("linux", "darwin"), "alt": ["scalpel", "foremost", "sleuthkit"]},
    {"id": "tcpdump", "group": "forensics", "desc": "抓包", "probe": "tcpdump", "platforms": ("linux", "darwin")},
    {"id": "wireshark", "group": "forensics", "desc": "GUI 抓包（含 tshark）", "probe": "wireshark"},
    {"id": "gau", "group": "recon", "desc": "取历史 URL", "probe": "gau"},
    {"id": "katana", "group": "recon", "desc": "爬虫取端点", "probe": "katana"},
    {"id": "hydra", "group": "web", "desc": "在线爆破（有结构性证据才用）", "probe": "hydra", "platforms": ("linux", "darwin")},
    {"id": "aircrack-ng", "group": "recon", "desc": "无线", "probe": "aircrack-ng", "platforms": ("linux", "darwin")},
    {"id": "metasploit", "group": "ad", "desc": "漏洞利用框架", "probe": "msfconsole", "platforms": ("linux", "darwin"), "alt": ["impacket", "nuclei"]},
    {"id": "impacket", "group": "ad", "desc": "AD/SMB 脚本集", "probe": "impacket-secretsdump", "module": "impacket"},
    {"id": "netexec", "group": "ad", "desc": "SMB/WinRM/LDAP 一把梭（nxc）", "probe": "nxc", "platforms": ("linux", "darwin"), "alt": ["impacket"]},
    {"id": "responder", "group": "ad", "desc": "LLMNR/NBT-NS 投毒", "probe": "responder", "platforms": ("linux", "darwin"), "alt": ["bettercap"]},
    {"id": "bettercap", "group": "ad", "desc": "中间人/ARP", "probe": "bettercap", "platforms": ("linux", "darwin")},
]


def platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _os_release() -> dict[str, str]:
    """Parse /etc/os-release (Linux only; empty elsewhere)."""
    out: dict[str, str] = {}
    for p in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
        if out:
            break
    return out


def _wsl_distros() -> list[str]:
    """Names from `wsl.exe -l -q` (UTF-16 output on Windows)."""
    if os.name != "nt" or not shutil.which("wsl"):
        return []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True,
                           timeout=15, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = p.stdout or b""
    if raw.count(b"\x00") > len(raw) // 6:
        text = raw.decode("utf-16-le", "replace")
    else:
        text = raw.decode("utf-8", "replace")
    out: list[str] = []
    for line in text.splitlines():
        name = line.replace("\x00", "").strip().lstrip("*").strip()
        if name and name.lower() != "windows subsystem for linux distributions:":
            out.append(name)
    return out


def _wsl_detail() -> list[dict]:
    """`wsl -l -v`: name / state (Running|Stopped) / version, plus default flag."""
    if os.name != "nt" or not shutil.which("wsl"):
        return []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run(["wsl.exe", "-l", "-v"], capture_output=True,
                           timeout=15, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = p.stdout or b""
    if raw.count(b"\x00") > len(raw) // 6:
        text = raw.decode("utf-16-le", "replace")
    else:
        text = raw.decode("utf-8", "replace")
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.replace("\x00", "").rstrip()
        if not line.strip() or line.lstrip().startswith("NAME"):
            continue
        default = line.lstrip().startswith("*")
        m = re.match(r"\*?\s*(.+?)\s{2,}(\w+)\s+(\d+)\s*$", line.strip())
        if not m:
            continue
        rows.append({"name": m.group(1).strip(), "state": m.group(2),
                     "version": int(m.group(3)), "default": default})
    if rows:
        return rows
    # 老版本 WSL 的 -l -v 可能不可用，退回只给名字
    return [{"name": n, "state": "Unknown", "version": 0, "default": n.startswith("*")}
            for n in _wsl_distros()]


def _ssh_hosts(limit: int = 8) -> list[str]:
    """Concrete `Host` aliases from ~/.ssh/config (wildcards skipped)."""
    try:
        text = (Path.home() / ".ssh" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hosts: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*Host\s+(.+)$", line, re.IGNORECASE)
        if not m:
            continue
        for name in m.group(1).split():
            if any(ch in name for ch in "*?!"):
                continue
            if name not in hosts:
                hosts.append(name)
    return hosts[:limit]


def _in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        return b"container=" in Path("/proc/1/environ").read_bytes()
    except OSError:
        return False


def _sudo_nopass() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    try:
        p = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_env() -> dict:
    """Where is the agent itself running? Windows / WSL / Ubuntu / Kali / macOS."""
    plat = platform_key()
    rel = _os_release() if plat == "linux" else {}
    distro = (rel.get("ID") or "").lower()
    if not distro:
        like = (rel.get("ID_LIKE") or "").lower()
        distro = like.split()[0] if like else ""
    wsl_distro = (os.environ.get("WSL_DISTRO_NAME") or "").strip()
    wsl = bool(wsl_distro)
    if plat == "linux" and not wsl:
        try:
            wsl = "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            wsl = False
    if plat == "windows":
        pretty = f"Windows {platform.release()}".strip()
    elif plat == "darwin":
        pretty = f"macOS {platform.mac_ver()[0]}".strip()
    else:
        pretty = rel.get("PRETTY_NAME") or (distro.title() or "Linux")
    tools = [x for x in ("winget", "apt-get", "dnf", "pacman", "brew", "docker", "wsl")
             if shutil.which(x)]
    admin = False
    if plat == "windows":
        try:
            import ctypes
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            admin = False
    else:
        try:
            admin = os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            admin = False
    wsl_rows = _wsl_detail()
    wsl_distros = [r["name"] for r in wsl_rows]
    return {
        "os": plat,
        "pretty": pretty,
        "distro": distro,
        "wsl": wsl,
        "wsl_distro": wsl_distro,
        "container": _in_container(),
        "arch": platform.machine(),
        "python": sys.executable,
        "admin": admin,
        "sudo": bool(shutil.which("sudo")) if plat == "linux" else False,
        "sudo_nopass": _sudo_nopass() if plat == "linux" else False,
        "pkg": tools,
        "wsl_distros": wsl_distros,
        "wsl_detail": wsl_rows,
        "ssh_hosts": _ssh_hosts(),
    }


def install_targets(env: dict) -> list[dict]:
    """Candidate destinations, mirroring `scripts/setup-tools.py` keys."""
    out: list[dict] = []
    plat = env.get("os")
    if plat == "windows":
        if "winget" in env.get("pkg", []):
            out.append({"key": "windows", "label": "Windows 本机（winget）", "kind": "winget"})
        for d in env.get("wsl_distros", []):
            dl = d.lower()
            tag = "Kali" if "kali" in dl else ("Ubuntu" if "ubuntu" in dl else "Linux")
            out.append({"key": f"wsl:{d}", "label": f"{tag}（WSL: {d}）", "kind": "wsl"})
        for h in env.get("ssh_hosts", []):
            out.append({"key": f"ssh:{h}", "label": f"SSH: {h}", "kind": "ssh"})
        out.append({"key": "ssh", "label": "SSH 连接 Ubuntu/Kali（输入 user@host）", "kind": "ssh-new"})
        if not env.get("wsl_distros"):
            out.append({"key": "wsl-ubuntu",
                        "label": "Ubuntu（WSL，还没装：wsl --install -d Ubuntu）",
                        "kind": "wsl-install"})
        if not any("kali" in d.lower() for d in env.get("wsl_distros", [])):
            out.append({"key": "kali-wsl",
                        "label": "Kali（WSL，还没装：wsl --install -d kali-linux）",
                        "kind": "wsl-install"})
    elif plat == "darwin":
        if "brew" in env.get("pkg", []):
            out.append({"key": "brew", "label": "macOS 本机（brew）", "kind": "brew"})
    else:
        if "apt-get" in env.get("pkg", []):
            out.append({"key": "apt", "label": f"{env.get('pretty', 'Linux')}（本机 apt）", "kind": "apt"})
        else:
            out.append({"key": "unsupported",
                        "label": f"{env.get('pretty', 'Linux')}（非 Debian 系，暂不支持自动安装）",
                        "kind": "unsupported"})
    return out


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


def setup_script() -> str:
    """Absolute path of `scripts/setup-tools.py`, if we can find it."""
    explicit = (os.environ.get("PI_SETUP_TOOLS") or "").strip()
    if explicit and Path(explicit).is_file():
        return str(Path(explicit).resolve())
    # 随 agent 一起安装的副本：干净机器没有 repo checkout 也能一键安装
    me = Path(__file__).resolve()
    for cand in (me.parent.parent / "scripts" / "setup-tools.py",
                 me.parent / "setup-tools.py"):
        if cand.is_file():
            return str(cand)
    workbench = (os.environ.get("PI_WORKBENCH") or "").strip()
    if workbench:
        script = Path(workbench).resolve().parent / "scripts" / "setup-tools.py"
        if script.is_file():
            return str(script)
    here = Path.cwd()
    for base in (here, *list(here.parents)[:4]):
        script = base / "scripts" / "setup-tools.py"
        if script.is_file():
            return str(script)
    return ""


def repo_hint(env: dict, targets: list[dict]) -> str:
    """Command that matches this host: menu on Windows, local apt elsewhere."""
    script = setup_script()
    if not script:
        return ""
    if env.get("os") == "windows":
        return f"python {script}   # 先选目标：Windows 本机 / WSL / SSH / Kali"
    local = next((t["key"] for t in targets if t.get("kind") in ("apt", "brew")), "")
    if local:
        return f"python {script} --target {local}   # 本机（只检查加 --list）"
    return f"python {script} --list"


def probe_modules(rows: list[dict], plat: str) -> set[str]:
    """Pip-installed tools: check importability with find_spec (no import side effects)."""
    mods = [(str(r["id"]), str(r["module"])) for r in rows
            if r.get("module") and plat in (r.get("platforms") or ALL_PLATFORMS)]
    if not mods:
        return set()
    nl = chr(10)
    code = nl.join([
        "import importlib.util as u",
        "miss = []",
        "for i, m in " + repr(mods) + ":",
        "    try:",
        "        ok = u.find_spec(m) is not None",
        "    except Exception:",
        "        ok = False",
        "    if not ok:",
        "        miss.append(i)",
        "print(' '.join(miss))",
    ])
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    gone = set(out.split()) if out else set()
    return {i for i, _ in mods} - gone


def collect(catalog: list[dict], group: str | None) -> dict:
    plat = platform_key()
    ok: list[str] = []
    missing: list[str] = []
    needs_alt: list[dict] = []
    rows = [r for r in catalog
            if not (group and r.get("group") and r["group"] != group)]
    mod_ok = probe_modules(rows, plat)
    for row in rows:
        tid = str(row["id"])
        probe = str(row.get("probe") or tid)
        platforms = row.get("platforms") or ALL_PLATFORMS
        # 先看 PATH（MSYS2/Git-Bash 里的 gdb、objdump 也算已装），再看平台
        if shutil.which(probe) or tid in mod_ok:
            ok.append(tid)
        elif plat not in platforms:
            needs_alt.append({"id": tid, "why": f"原生不支持 {plat}", "use": "WSL / VM / Docker"})
        else:
            missing.append(tid)
    env = detect_env()
    # 等效替代：Kali 专属工具在 Ubuntu/Windows 上没有包时，用它代替就不算缺
    alt_map = {str(r["id"]): [str(a) for a in (r.get("alt") or [])] for r in catalog}
    ok_set = set(ok)
    covered: list[dict] = []
    kept_missing: list[str] = []
    for tid in missing:
        by = next((a for a in alt_map.get(tid, []) if a in ok_set), "")
        if by:
            covered.append({"id": tid, "by": by})
        else:
            kept_missing.append(tid)
    missing = kept_missing
    kept_alt: list[dict] = []
    for row in needs_alt:
        by = next((a for a in alt_map.get(row["id"], []) if a in ok_set), "")
        if by:
            covered.append({"id": row["id"], "by": by})
        else:
            kept_alt.append(row)
    needs_alt = kept_alt
    targets = install_targets(env)
    probes = [{"id": str(r["id"]), "probe": str(r.get("probe") or r["id"]),
               "module": str(r.get("module") or ""),
               "alt": [str(a) for a in (r.get("alt") or [])],
               "platforms": list(r.get("platforms") or ALL_PLATFORMS)} for r in catalog]
    groups = {str(r["id"]): str(r.get("group") or "") for r in catalog}
    return {
        "catalog_version": CATALOG_VERSION,
        "platform": plat,
        "group": group or "",
        "checked": len(ok) + len(missing) + len(needs_alt),
        "ok": ok,
        "missing": missing,
        "needs_alt": needs_alt,
        "covered": covered,
        "env": env,
        "targets": targets,
        "probes": probes,
        "groups": groups,
        "script": setup_script(),
        "hint": repo_hint(env, targets) if (missing or needs_alt) else "",
        "generated_at": int(time.time()),
    }


def env_line(info: dict) -> str:
    env = info.get("env") or {}
    bits = [env.get("pretty") or info.get("platform", "?")]
    if env.get("wsl"):
        bits.append("WSL" + (f": {env['wsl_distro']}" if env.get("wsl_distro") else ""))
    if env.get("container"):
        bits.append("容器")
    if env.get("admin"):
        bits.append("root")
    return " · ".join(bits)


def _group_line(ids: list[str], groups: dict, limit: int = 10) -> str:
    from collections import Counter
    counts = Counter(groups.get(i, "other") for i in ids)
    by = " · ".join(f"{g} {n}" for g, n in counts.most_common())
    shown = ", ".join(ids[:limit]) + (" …" if len(ids) > limit else "")
    return f"{by}  ({shown})" if len(ids) > limit else ", ".join(ids)


def render_human(info: dict) -> str:
    groups = info.get("groups") or {}
    lines = [f"本机 {env_line(info)} · platform {info['platform']} · checked {info['checked']}"
             + (f" · group {info['group']}" if info["group"] else "")]
    if info["ok"]:
        lines.append("  ok       " + _group_line(info["ok"], groups, limit=12))
    if info["missing"]:
        lines.append("  missing  " + _group_line(info["missing"], groups, limit=10))
    if info["needs_alt"]:
        alt = [r["id"] for r in info["needs_alt"]]
        lines.append("  needs    " + _group_line(alt, groups, limit=8) + "  -> WSL / VM / Docker")
    if info.get("covered"):
        pairs = ", ".join(f"{c['id']}→{c['by']}" for c in info["covered"][:8])
        lines.append("  covered  " + pairs)
    if not info["missing"] and not info["needs_alt"]:
        lines.append("  没有缺失项")
    targets = info.get("targets") or []
    if targets and (info["missing"] or info["needs_alt"]):
        labels = " / ".join(t["label"] for t in targets[:4])
        lines.append("  可选目标： " + labels)
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
                    choices=["check", "count-missing", "env"])
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
    if args.action == "env":
        payload = {"env": info["env"], "targets": info["targets"], "script": info["script"]}
        print(json.dumps(payload, ensure_ascii=False) if args.json else
              env_line(info) + "\n" + "\n".join("  - " + t["label"] for t in info["targets"]))
        return 0
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(render_human(info))
    return 1 if (args.strict and info["missing"]) else 0


if __name__ == "__main__":
    sys.exit(main())
