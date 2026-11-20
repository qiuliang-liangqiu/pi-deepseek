#!/usr/bin/env python3
"""Pick a target OS and install the CTF toolchain there.

Targets discovered automatically
--------------------------------
* **Windows** (winget)                      — native host
* **WSL** distros (Ubuntu/Kali)             — `wsl.exe -d <distro> -u root -- ...`
* **VMware / VirtualBox VMs** (via SSH)     — `vmrun`/`VBoxManage` for IP, then `ssh`
* **current Debian-family host** (apt)      — when already running on Linux

    python scripts/setup-tools.py                      # menu, then a mapping table
    python scripts/setup-tools.py --list               # mappings only
    python scripts/setup-tools.py --target vm --ssh-user <you> --only jq --dry-run
    python scripts/setup-tools.py --target wsl:Ubuntu --yes
    python scripts/setup-tools.py --target windows --group web,recon --yes

Safety
------
* Nothing installs without an explicit pick (or `--yes` on a named target).
* Packages are validated first (`winget show`, `apt-cache policy` over the same
  channel that will install them); unavailable tools are marked, not skipped.
* `sudo` on Linux needs `--allow-sudo`; over SSH it is passed to `ssh -t` so the
  remote sudo prompt works.
* stdlib only; no `curl | sh`, no downloads outside the package managers.
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
from dataclasses import dataclass, field
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
SSH_CONFIG = Path.home() / ".ssh" / "config"
_SSH_PW: dict[str, str] = {}
SSH_PW_FILE = ""


# --------------------------------------------------------------------- catalog
@dataclass
class Tool:
    id: str
    group: str
    why: str
    win: str = ""
    ubuntu: list[str] = field(default_factory=list)
    kali: list[str] = field(default_factory=list)
    pip: str = ""
    go: str = ""
    gem: str = ""
    probe: str = ""
    note: str = ""


CATALOG: list[Tool] = [
    Tool("go", "base", "装 Go 工具的前置", win="GoLang.Go", ubuntu=["golang-go"], kali=["golang"], probe="go"),
    Tool("nmap", "recon", "端口/服务指纹", win="Insecure.Nmap", ubuntu=["nmap"], kali=["nmap"], probe="nmap"),
    Tool("ncat", "recon", "listener/中继", win="Insecure.Nmap", ubuntu=["ncat"], kali=["ncat"], probe="ncat"),
    Tool("jq", "recon", "读 JSON", win="jqlang.jq", ubuntu=["jq"], kali=["jq"], probe="jq"),
    Tool("ripgrep", "recon", "全文搜索", win="BurntSushi.ripgrep.MSVC", ubuntu=["ripgrep"], kali=["ripgrep"], probe="rg"),
    Tool("fd", "recon", "按名找文件", win="sharkdp.fd", ubuntu=["fd-find"], kali=["fd-find"], probe="fd"),
    Tool("ffuf", "web", "目录/参数 fuzz", ubuntu=["ffuf"], kali=["ffuf"], go="github.com/ffuf/ffuf/v2@latest", probe="ffuf"),
    Tool("gobuster", "web", "vhost/DNS 枚举", ubuntu=["gobuster"], kali=["gobuster"], go="github.com/OJ/gobuster/v3@latest", probe="gobuster"),
    Tool("httpx", "web", "批量探活", ubuntu=["httpx-toolkit"], kali=["httpx-toolkit"], go="github.com/projectdiscovery/httpx/cmd/httpx@latest", probe="httpx",
         note="Debian 系的 httpx 是 Python 版，pd 版叫 httpx-toolkit"),
    Tool("nuclei", "web", "模板扫漏洞", ubuntu=["nuclei"], kali=["nuclei"], go="github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest", probe="nuclei"),
    Tool("sqlmap", "web", "SQL 注入", ubuntu=["sqlmap"], kali=["sqlmap"], pip="sqlmap", probe="sqlmap"),
    Tool("mitmproxy", "web", "抓包改包", ubuntu=["mitmproxy"], kali=["mitmproxy"], pip="mitmproxy", probe="mitmdump"),
    Tool("gdb", "pwn", "动态调试", ubuntu=["gdb"], kali=["gdb"], probe="gdb"),
    Tool("radare2", "pwn", "反汇编", ubuntu=["radare2"], kali=["radare2"], probe="r2"),
    Tool("rizin", "pwn", "r2 现代分支", ubuntu=["rizin"], kali=["rizin"], probe="rizin"),
    Tool("upx", "pwn", "解壳", win="UPX.UPX", ubuntu=["upx-ucl"], kali=["upx-ucl"], probe="upx"),
    Tool("patchelf", "pwn", "改 rpath/interp", ubuntu=["patchelf"], kali=["patchelf"], probe="patchelf"),
    Tool("pwntools", "pwn", "exploit 框架 + checksec", pip="pwntools", probe="pwn"),
    Tool("exiftool", "forensics", "元数据", win="OliverBetz.ExifTool", ubuntu=["libimage-exiftool-perl"],
         kali=["libimage-exiftool-perl"], probe="exiftool"),
    Tool("binwalk", "forensics", "固件/嵌套包", ubuntu=["binwalk"], kali=["binwalk"], pip="binwalk", probe="binwalk"),
    Tool("foremost", "forensics", "文件雕刻", ubuntu=["foremost"], kali=["foremost"], probe="foremost"),
    Tool("sleuthkit", "forensics", "文件系统取证", ubuntu=["sleuthkit"], kali=["sleuthkit"], probe="fls"),
    Tool("tshark", "forensics", "pcap 分析", win="WiresharkFoundation.Wireshark", ubuntu=["tshark"], kali=["tshark"], probe="tshark"),
    Tool("volatility3", "forensics", "内存镜像", pip="volatility3", probe="vol"),
    Tool("scapy", "forensics", "构造报文", pip="scapy", probe="scapy"),
    Tool("hashcat", "crypto", "破哈希", ubuntu=["hashcat"], kali=["hashcat"], probe="hashcat"),
    Tool("john", "crypto", "john the ripper", ubuntu=["john"], kali=["john"], probe="john"),
    Tool("pycryptodome", "crypto", "AES/RSA 原型", pip="pycryptodome", probe="Crypto"),
    Tool("gmpy2", "crypto", "大整数", pip="gmpy2", probe="gmpy2"),
    Tool("z3", "crypto", "约束求解", pip="z3-solver", probe="z3"),
    Tool("jadx", "android", "APK 反编译", win="Skylot.jadx", ubuntu=["jadx"], kali=["jadx"], probe="jadx"),
    Tool("apktool", "android", "资源还原", ubuntu=["apktool"], kali=["apktool"], probe="apktool"),
    Tool("frida", "android", "运行时 hook", pip="frida-tools", probe="frida"),
    Tool("7z", "qol", "解压缩", win="7zip.7zip", ubuntu=["p7zip-full"], kali=["p7zip-full"], probe="7z"),

    # ---- second batch: referenced by skills/playbooks, packageable ----
    Tool("curl", "base", "HTTP 请求（几乎所有 payload 都用它）", win="cURL.cURL", ubuntu=["curl"], kali=["curl"], probe="curl"),
    Tool("wget", "base", "下载", ubuntu=["wget"], kali=["wget"], probe="wget"),
    Tool("binutils", "base", "objdump/readelf/nm/strings", ubuntu=["binutils"], kali=["binutils"], probe="objdump"),
    Tool("file", "base", "文件类型识别", ubuntu=["file"], kali=["file"], probe="file"),
    Tool("xxd", "base", "十六进制查看", ubuntu=["xxd"], kali=["xxd"], probe="xxd"),
    Tool("unzip", "base", "解 zip", ubuntu=["unzip"], kali=["unzip"], probe="unzip"),
    Tool("gcc", "base", "本地复刻/编译 exp", ubuntu=["gcc"], kali=["gcc"], probe="gcc"),
    Tool("make", "base", "编译", ubuntu=["make"], kali=["make"], probe="make"),
    Tool("ruby", "base", "gem 工具的前置", ubuntu=["ruby", "ruby-dev"], kali=["ruby", "ruby-dev"], probe="ruby"),
    Tool("dig", "recon", "DNS 查询", ubuntu=["dnsutils"], kali=["dnsutils"], probe="dig"),
    Tool("nc", "recon", "netcat", ubuntu=["netcat-openbsd"], kali=["netcat-openbsd"], probe="nc"),
    Tool("socat", "recon", "端口转发/中继", ubuntu=["socat"], kali=["socat"], probe="socat"),
    Tool("masscan", "recon", "全端口快速扫描", ubuntu=["masscan"], kali=["masscan"], probe="masscan"),
    Tool("whatweb", "recon", "指纹识别", ubuntu=["whatweb"], kali=["whatweb"], probe="whatweb"),
    Tool("subfinder", "recon", "子域枚举", go="github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
         kali=["subfinder"], probe="subfinder"),
    Tool("nikto", "web", "老式 web 扫描", ubuntu=["nikto"], kali=["nikto"], probe="nikto"),
    Tool("wpscan", "web", "WordPress 扫描", kali=["wpscan"], gem="wpscan", probe="wpscan"),
    Tool("wfuzz", "web", "fuzz（老牌）", pip="wfuzz", probe="wfuzz"),
    Tool("dirsearch", "web", "目录爆破", pip="dirsearch", probe="dirsearch"),
    Tool("arjun", "web", "参数发现", pip="arjun", probe="arjun"),
    Tool("commix", "web", "命令注入", pip="commix", probe="commix"),
    Tool("dalfox", "web", "XSS 扫描", go="github.com/hahwul/dalfox/v2@latest", probe="dalfox"),
    Tool("ROPgadget", "pwn", "找 gadget", pip="ROPgadget", probe="ROPgadget"),
    Tool("ropper", "pwn", "gadget 搜索/ROP 构建", pip="ropper", probe="ropper"),
    Tool("one_gadget", "pwn", "libc one-gadget（ruby）", gem="one_gadget", probe="one_gadget"),
    Tool("seccomp-tools", "pwn", "看 seccomp 规则（ruby）", gem="seccomp-tools", probe="seccomp-tools"),
    Tool("gdb-multiarch", "pwn", "跨架构调试", ubuntu=["gdb-multiarch"], kali=["gdb-multiarch"], probe="gdb-multiarch"),
    Tool("qemu-user", "pwn", "跑异构架构二进制", ubuntu=["qemu-user"], kali=["qemu-user"], probe="qemu-x86_64"),
    Tool("strace", "pwn", "系统调用跟踪", ubuntu=["strace"], kali=["strace"], probe="strace"),
    Tool("ltrace", "pwn", "库调用跟踪", ubuntu=["ltrace"], kali=["ltrace"], probe="ltrace"),
    Tool("angr", "rev", "符号执行（重）", pip="angr", probe="angr"),
    Tool("unicorn", "rev", "CPU 模拟（emu 题必备）", pip="unicorn", probe="unicorn"),
    Tool("capstone", "rev", "反汇编引擎", pip="capstone", probe="capstone"),
    Tool("keystone", "rev", "汇编引擎", pip="keystone-engine", probe="keystone"),
    Tool("sage", "crypto", "数学/格（体积大，通常自带）", probe="sage", note="不在包管理器里，用官方安装器或镜像自带的"),
    Tool("hashid", "crypto", "哈希类型识别", pip="hashid", probe="hashid"),
    Tool("steghide", "forensics", "隐写（jpeg/wav）", ubuntu=["steghide"], kali=["steghide"], probe="steghide"),
    Tool("zsteg", "forensics", "PNG/BMP 隐写（ruby）", gem="zsteg", probe="zsteg"),
    Tool("pngcheck", "forensics", "PNG 结构校验", ubuntu=["pngcheck"], kali=["pngcheck"], probe="pngcheck"),
    Tool("scalpel", "forensics", "文件雕刻", ubuntu=["scalpel"], kali=["scalpel"], probe="scalpel"),
    Tool("testdisk", "forensics", "分区/文件恢复（含 photorec）", ubuntu=["testdisk"], kali=["testdisk"], probe="testdisk"),
    Tool("bulk-extractor", "forensics", "批量特征提取", ubuntu=["bulk-extractor"], kali=["bulk-extractor"], probe="bulk_extractor"),
    Tool("tcpdump", "forensics", "抓包", ubuntu=["tcpdump"], kali=["tcpdump"], probe="tcpdump"),
    Tool("wireshark", "forensics", "GUI 抓包（含 tshark）", win="WiresharkFoundation.Wireshark",
         ubuntu=["wireshark"], kali=["wireshark"], probe="wireshark"),
    Tool("gau", "recon", "取历史 URL", go="github.com/lc/gau/v2/cmd/gau@latest", probe="gau"),
    Tool("katana", "recon", "爬虫取端点", go="github.com/projectdiscovery/katana/cmd/katana@latest", probe="katana"),
]
BY_ID = {t.id: t for t in CATALOG}

# pip 包名 → import 名（探测用，别拿命令行名当证据）
PIP_MODULE = {
    "pwntools": "pwn", "pycryptodome": "Crypto", "z3-solver": "z3",
    "frida-tools": "frida", "volatility3": "volatility3", "scapy": "scapy",
    "mitmproxy": "mitmproxy", "binwalk": "binwalk", "sqlmap": "sqlmap",
}


_PEP668: dict[str, bool] = {}


def pep668(target: Target) -> bool:
    """Ubuntu 23.04+/Debian 12+ refuse `pip install` outside a venv."""
    key = target.key
    if key not in _PEP668:
        if target.kind == "apt":
            _PEP668[key] = bool(list(Path("/usr/lib").glob("python3*/EXTERNALLY-MANAGED")))
        else:
            _PEP668[key] = exec_probe(target, "ls /usr/lib/python3*/EXTERNALLY-MANAGED >/dev/null 2>&1")
    return _PEP668[key]


def pip_install_cmd(target: Target, pkg: str) -> str:
    extra = " --break-system-packages" if pep668(target) else ""
    return f"pip3 install --user{extra} {pkg}"


def pip_probe_cmd(mod: str) -> str:
    return f'python3 -c "import {mod}" >/dev/null 2>&1' 
GROUPS = ["base", "recon", "web", "pwn", "forensics", "crypto", "android", "qol"]


# --------------------------------------------------------------------- targets
@dataclass
class Target:
    key: str                 # windows | apt | wsl:<distro> | vm:<name>
    label: str
    kind: str                # winget | apt | wsl | ssh
    where: str = ""
    distro: str = ""         # wsl distro name or ubuntu/kali
    host: str = ""           # ssh host (ip or alias)
    user: str = ""
    port: int = 22
    alias: str = ""          # ssh config alias, if one matches the IP
    vmx: str = ""
    os_name: str = ""        # ubuntu | kali | debian | windows | linux


def run(argv: list[str], timeout: int = 120, want_tty: bool = False,
        env: dict | None = None, stdin_text: str | None = None) -> tuple[int, str]:
    try:
        if want_tty:
            p = subprocess.run(argv, timeout=timeout, env=env)
            return p.returncode, ""
        p = subprocess.run(argv, capture_output=True, timeout=timeout, env=env,
                           input=stdin_text.encode() if stdin_text is not None else None)
        out = (p.stdout or b"") + (p.stderr or b"")
        return p.returncode, out.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def decode_maybe_utf16(b: bytes) -> str:
    if b.count(b"\x00") > len(b) // 4:
        try:
            return b.decode("utf-16-le", "replace")
        except Exception:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def wsl_available() -> bool:
    rc, out = run(["wsl.exe", "-l", "-q"], timeout=30)
    if rc != 0:
        return False
    text = decode_maybe_utf16(out.encode("utf-8", "replace")) if isinstance(out, str) else out
    return "未安装" not in text and "not installed" not in text.lower()


def wsl_dists() -> list[str]:
    if not IS_WIN or not shutil.which("wsl.exe"):
        return []
    try:
        p = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=30)
    except Exception:
        return []
    text = decode_maybe_utf16(p.stdout)
    if "未安装" in text or "not installed" in text.lower():
        return []
    return [ln.strip().strip("\x00") for ln in text.splitlines() if ln.strip().strip("\x00")]


def ssh_config_hosts() -> list[dict]:
    """[{alias, host, user, port, identity}] from ~/.ssh/config"""
    out: list[dict] = []
    if not SSH_CONFIG.exists():
        return out
    cur: dict = {}
    for line in SSH_CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if re.match(r"^Host\s+", s, re.I):
            if cur.get("alias") and cur.get("host"):
                out.append(cur)
            cur = {"alias": s.split(None, 1)[1].split()[0]}
        elif s and not s.startswith("#") and cur:
            m = re.match(r"^(HostName|User|Port|IdentityFile)\s+(.+)$", s, re.I)
            if m:
                key = m.group(1).lower()
                key = {"hostname": "host", "identityfile": "identity"}.get(key, key)
                cur[key] = m.group(2).strip()
    if cur.get("alias") and cur.get("host"):
        out.append(cur)
    return out


def vmrun() -> str:
    for cand in (r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe",
                 r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"):
        if Path(cand).exists():
            return cand
    return shutil.which("vmrun") or ""


def vm_candidates() -> list[dict]:
    """Find VMs: running ones via vmrun, plus .vmx files under usual dirs."""
    vms: dict[str, dict] = {}
    vr = vmrun()
    if vr:
        rc, out = run([vr, "list"], timeout=60)
        for line in out.splitlines()[1:]:
            line = line.strip()
            if line.lower().endswith(".vmx"):
                vms[line] = {"vmx": line, "running": True}
    # only standard locations and whatever the user points at with VM_DIRS:
    #   VM_DIRS="D:\vms;E:\lab"  (Windows uses ';', Linux ':')
    roots: list[Path] = []
    if IS_WIN:
        roots += [Path.home() / "Documents" / "Virtual Machines",
                  Path.home() / "Virtual Machines"]
    for chunk in os.environ.get("VM_DIRS", "").split(os.pathsep):
        if chunk.strip():
            roots.append(Path(chunk.strip()))
    for root in roots:
        if not root or not root.is_dir():
            continue
        for vmx in list(root.rglob("*.vmx"))[:40]:
            key = str(vmx)
            vms.setdefault(key, {"vmx": key, "running": False})
    for v in vms.values():
        v["name"] = Path(v["vmx"]).stem
        if v["running"] and vr:
            ip = ""
            for extra in (["-wait"], []):
                rc, out = run([vr, "getGuestIPAddress", v["vmx"], *extra], timeout=90)
                cand = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", cand):
                    ip = cand
                    break
            v["ip"] = ip
    return list(vms.values())


def guest_os(target: Target) -> str:
    if target.os_name:
        return target.os_name
    if target.kind == "ssh" and target.host:
        host = target.alias or target.host
        rc, out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
                       "-p", str(target.port), host,
                       "grep -E '^(ID|VERSION_ID)=' /etc/os-release"], timeout=30)
        if rc == 0:
            low = out.lower()
            if "kali" in low:
                return "kali"
            if "ubuntu" in low:
                return "ubuntu"
            if "debian" in low:
                return "debian"
    name = (target.distro or Path(target.vmx).stem or target.label).lower()
    for osname in ("kali", "ubuntu", "debian"):
        if osname in name:
            return osname
    return ""


def os_title(os_name: str) -> str:
    return {"ubuntu": "Ubuntu", "kali": "Kali", "debian": "Debian", "windows": "Windows"}.get(
        os_name, os_name or "Linux")


def targets() -> list[Target]:
    out: list[Target] = []
    if IS_WIN:
        out.append(Target("windows", "Windows（本机 winget）", "winget", "host", os_name="windows"))
    for d in wsl_dists():
        dl = d.lower()
        key = "wsl:" + d
        label_kind = "Kali" if "kali" in dl else ("Ubuntu" if "ubuntu" in dl else "Linux")
        osname = "kali" if "kali" in dl else ("ubuntu" if "ubuntu" in dl else
                                              ("debian" if "debian" in dl else ""))
        out.append(Target(key, f"{label_kind}（WSL: {d}）", "wsl", d,
                          distro=d, host="localhost", os_name=osname))
    cfg = ssh_config_hosts()
    for vm in vm_candidates():
        alias = ""
        for h in cfg:
            if vm.get("ip") and h.get("host") == vm["ip"]:
                alias = h["alias"]
                break
        state = f"运行中 {vm.get('ip') or '(IP 未知)'}" if vm["running"] else "未开机"
        t = Target("vm:" + vm["name"], "", "ssh",
                   vm["vmx"], host=vm.get("ip") or "", alias=alias, vmx=vm["vmx"])
        t.os_name = guest_os(t)          # 未开机时退回按 VM 名字猜
        t.label = f"{os_title(t.os_name)}（虚拟机: {vm['name']} · {state}）"
        out.append(t)
    if IS_LINUX:
        d = ""
        try:
            rel = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
            d = "kali" if "kali" in rel else ("ubuntu" if "ubuntu" in rel else "debian")
        except OSError:
            d = "linux"
        out.append(Target("apt", f"{os_title(d)}（本机 apt）", "apt", "host",
                          distro=d, os_name=d))
    return out


# --------------------------------------------------------------------- exec
_ASKPASS: dict[str, str] = {}


def askpass_env(password: str) -> dict:
    """SSH_ASKPASS helper so a password can be used without a TTY (no sshpass needed)."""
    key = "askpass"
    helper = _ASKPASS.get(key)
    if not helper:
        import tempfile
        fd, path = tempfile.mkstemp(prefix="pi-askpass-", suffix=".sh")
        os.write(fd, b'#!/bin/sh\nprintf "%s\n" "$PI_SSH_PASS"\n')
        os.close(fd)
        os.chmod(path, 0o700)
        _ASKPASS[key] = helper = path
    env = dict(os.environ)
    env.update({"PI_SSH_PASS": password, "SSH_ASKPASS": helper,
                "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": env.get("DISPLAY", ":0")})
    return env


def password_for(target: Target) -> str:
    """Prompt once per host, honour --ssh-password-file."""
    key = target.alias or target.host
    if key in _SSH_PW:
        return _SSH_PW[key]
    pw = ""
    if SSH_PW_FILE:
        try:
            pw = Path(SSH_PW_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            pw = ""
    if not pw:
        user = target.user or target.alias or target.host
        prompt = f"{user}@{target.host or target.alias} 的 SSH 密码（不回显）: "
        try:
            if sys.stdin.isatty():
                import getpass
                pw = getpass.getpass(prompt)
            else:                       # 无终端（CI/管道）：从 stdin 读一行
                print(prompt, end="", file=sys.stderr, flush=True)
                pw = sys.stdin.readline().rstrip(chr(13) + chr(10))

        except (EOFError, KeyboardInterrupt):
            pw = ""
    _SSH_PW[key] = pw
    return pw


def ssh_argv(target: Target, cmd: str, password: str = "", tty: bool = False,
             key_only: bool = False) -> list[str]:
    host = target.alias or (f"{target.user}@{target.host}" if target.user else target.host)
    argv = ["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(target.port)]
    if key_only:
        argv += ["-o", "BatchMode=yes"]
    elif password:
        argv += ["-o", "PreferredAuthentications=password,keyboard-interactive",
                 "-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1"]
    if tty:
        argv.append("-t")
    return argv + [host, cmd]


def exec_in(target: Target, cmd: str, timeout: int = 120, tty: bool = False,
            password: str = "", stdin_text: str | None = None) -> tuple[int, str]:
    if target.kind == "wsl":
        argv = ["wsl.exe", "-d", target.distro, "-u", "root", "--", "bash", "-lc", cmd]
        return run(argv, timeout=timeout)
    if target.kind == "ssh":
        if not (target.host or target.alias):
            return 2, "no ssh host"
        pw = password or _SSH_PW.get(target.alias or target.host, "")
        argv = ssh_argv(target, cmd, password=pw, tty=tty)
        env = askpass_env(pw) if pw else None
        return run(argv, timeout=timeout, want_tty=tty and not pw, env=env,
                   stdin_text=stdin_text)
    return run(["bash", "-lc", cmd], timeout=timeout)


def exec_probe(target: Target, cmd: str) -> bool:
    rc, _ = exec_in(target, cmd, timeout=30)
    return rc == 0


def ssh_reachable(target: Target, interactive: bool = True) -> bool:
    """Key auth first; if that fails, ask for a password and try again."""
    if not (target.host or target.alias):
        return False
    rc, _ = run(ssh_argv(target, "true", key_only=True), timeout=30)
    if rc == 0:
        return True
    if not interactive:
        return False
    pw = password_for(target)
    if not pw:
        return False
    rc, _ = run(ssh_argv(target, "true", password=pw), timeout=30, env=askpass_env(pw))
    return rc == 0


# --------------------------------------------------------------------- resolve
def tool_pkgs(t: Tool, target: Target) -> list[str]:
    if target.kind == "winget":
        return [t.win] if t.win else []
    if target.kind == "apt":
        return t.kali if "kali" in (target.distro or "") else t.ubuntu
    return t.kali if "kali" in (target.distro or "").lower() else t.ubuntu


def installed_in(target: Target, t: Tool) -> bool:
    probe = t.probe or t.id
    if target.kind == "winget":
        return shutil.which(probe) is not None or shutil.which(t.id) is not None
    if target.kind == "apt":
        if shutil.which(probe) is not None:
            return True
    else:
        if exec_probe(target, f"command -v {probe} >/dev/null 2>&1"):
            return True
    if t.pip:                              # 也可以只按 import 名存在来判定
        mod = PIP_MODULE.get(t.pip, t.pip.replace("-", "_"))
        return exec_probe(target, pip_probe_cmd(mod))
    return False


def apt_has(target: Target, pkgs: list[str]) -> dict[str, bool]:
    if not pkgs:
        return {}
    joined = " ".join(pkgs)
    rc, out = exec_in(target, f"LC_ALL=C apt-cache policy {joined} 2>/dev/null", timeout=60)
    res = {p: False for p in pkgs}
    cur = None
    for line in out.splitlines():
        m = re.match(r"^(\S+):\s*$", line)
        if m and m.group(1) in res:
            cur = m.group(1)
            continue
        if cur and "Candidate:" in line:
            res[cur] = "(none)" not in line
    return res


def winget_has(wid: str) -> bool:
    if not wid or not shutil.which("winget"):
        return False
    rc, out = run(["winget", "show", "--id", wid, "--exact", "--accept-source-agreements"], timeout=45)
    return rc == 0 and "找不到" not in out and "No package found" not in out


def make_plan(target: Target, ids: list[str] | None, groups: list[str] | None) -> list[dict]:
    rows = []
    for t in CATALOG:
        if ids and t.id not in ids:
            continue
        if groups and t.group not in groups:
            continue
        pkgs = tool_pkgs(t, target)
        already = installed_in(target, t)
        rows.append({"tool": t, "pkgs": pkgs, "installed": already})
    # validate packages in one shot
    all_pkgs = sorted({p for r in rows if r["pkgs"] and not r["installed"] for p in r["pkgs"]})
    if target.kind == "winget":
        ok = {p: winget_has(p) for p in all_pkgs}
    else:
        ok = apt_has(target, all_pkgs) if all_pkgs else {}
    for r in rows:
        if r["installed"]:
            r["available"], r["fallback"] = True, ""
        elif r["pkgs"]:
            r["available"] = all(ok.get(p, False) for p in r["pkgs"])
            t = r["tool"]
            r["fallback"] = "" if r["available"] else (
                f"pip:{t.pip}" if t.pip else (f"gem:{t.gem}" if t.gem else
                                             ("go install" if t.go else "")))
        else:
            r["available"] = False
            t = r["tool"]
            r["fallback"] = (f"pip:{t.pip}" if t.pip else (f"gem:{t.gem}" if t.gem else
                            ("go install" if t.go else "")))
    return rows


def show_plan(rows: list[dict], target: Target) -> None:
    print(f"\n目标：{target.label}")
    print(f"  {'id':<12} {'组':<10} {'包名':<34} 状态")
    for r in rows:
        t = r["tool"]
        state = "已装" if r["installed"] else ("可装" if r["available"] else
                                              (f"兜底 {r['fallback']}" if r["fallback"] else "不可用"))
        pkg = ", ".join(r["pkgs"]) if r["pkgs"] else "-"
        note = f"  ← {t.note}" if t.note else ""
        print(f"  {t.id:<12} {t.group:<10} {pkg:<34} {state}{note}")


# --------------------------------------------------------------------- install
def install(target: Target, rows: list[dict], allow_sudo: bool, dry: bool) -> list[str]:
    todo = [r for r in rows if not r["installed"]]
    if not todo:
        print("  全部已安装")
        return []

    apt_pkgs = [p for r in todo if r["available"] for p in r["pkgs"]]
    failed: list[str] = []

    if target.kind == "winget":
        for r in todo:
            t = r["tool"]
            if not r["available"] or not t.win:
                print(f"    ! {t.id}: 该平台无包" + ("（可用 go/pip 兜底）" if r["fallback"] else ""))
                failed.append(t.id)
                continue
            argv = ["winget", "install", "-e", "--id", t.win, "--accept-package-agreements",
                    "--accept-source-agreements"]
            print(f"    $ {' '.join(argv)}")
            if dry:
                continue
            rc, _ = run(argv, timeout=900)
            if rc != 0:
                failed.append(t.id)
                print(f"    ! {t.id} failed (exit {rc})")
        return failed

    if apt_pkgs:
        cmd = "LC_ALL=C apt-get install -y " + " ".join(sorted(set(apt_pkgs)))
        if target.kind == "apt" and not allow_sudo and os.geteuid() != 0:
            print(f"    ! 需要 sudo：加 --allow-sudo 才执行\n    $ sudo {cmd}")
            return [r["tool"].id for r in todo if r["available"]]
        if target.kind == "apt":
            cmd = "sudo " + cmd
            print(f"    $ {cmd}")
            if not dry:
                rc, _ = run(["bash", "-lc", cmd], timeout=1800)
                if rc != 0:
                    print(f"    ! apt failed (exit {rc})")
        else:
            print(f"    $ [{target.label}] {cmd}")
            if not dry:
                if target.kind == "ssh":
                    pw = _SSH_PW.get(target.alias or target.host or "", "")
                    if pw and "sudo " in cmd:            # 无 TTY，用 sudo -S 喂密码
                        cmd2 = cmd.replace("sudo ", "sudo -S -p '' ", 1)
                        print("      （sudo 用同一密码，经 stdin 传入）")
                        rc, _ = exec_in(target, cmd2, timeout=1800, stdin_text=pw + chr(10))
                    else:
                        rc, _ = exec_in(target, cmd, timeout=1800, tty=True)
                else:
                    rc, _ = exec_in(target, cmd, timeout=1800)
                if rc != 0:
                    print(f"    ! apt failed (exit {rc})")

    for r in todo:
        t = r["tool"]
        if r["available"]:
            continue
        if not r["fallback"]:
            print(f"    ! {t.id}: 无可用安装方式")
            failed.append(t.id)
            continue
        if t.pip:
            cmd = pip_install_cmd(target, t.pip)
        elif t.gem:
            cmd = f"gem install --user-install {t.gem}"
        elif t.go:
            cmd = f"go install {t.go}"
        else:
            failed.append(t.id)
            continue
        print(f"    $ [{target.label}] {cmd}")
        if dry:
            continue
        rc, _ = exec_in(target, cmd, timeout=1200, tty=(target.kind == "ssh"))
        if rc != 0:
            failed.append(t.id)
            print(f"    ! {t.id} fallback failed (exit {rc})")
    return failed


# --------------------------------------------------------------------- menu
def menu(ts: list[Target]) -> Target | None:
    print("\033[1;36m选择把工具装到哪：\033[0m")
    for i, t in enumerate(ts, 1):
        print(f"  {i}. {t.label}")
    print("  0. 跳过")
    if IS_WIN and not any(t.kind == "wsl" for t in ts):
        print("\033[33m  （没装 WSL：管理员 PowerShell 跑 wsl --install -d Ubuntu 后再来）\033[0m")
    for t in ts:
        if t.kind == "ssh" and not t.host:
            print(f"\033[33m  （{t.label} 启动后再选，或用 --ssh-host 指定地址）\033[0m")
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw in ("0", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(ts):
            return ts[int(raw) - 1]
        print("  ?")


# --------------------------------------------------------------------- main
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="", help="windows | wsl:<distro> | vm:<name> | apt | auto")
    ap.add_argument("--group", default="", help="逗号分隔：" + ",".join(GROUPS))
    ap.add_argument("--only", default="", help="只处理这些 id（逗号分隔）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--allow-sudo", action="store_true")
    ap.add_argument("--ssh-host", default="", help="VM/远程机 IP 或 ssh 别名")
    ap.add_argument("--ssh-user", default="")
    ap.add_argument("--ssh-port", type=int, default=22)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ssh-password-file", default="", help="从文件读 SSH 密码（不落 shell 历史）")
    ap.add_argument("--start-vm", action="store_true", help="VM 没开机时用 vmrun start 启动并等 IP")
    ap.add_argument("--install-wsl", action="store_true", help="未装 WSL 时执行 wsl --install -d Ubuntu（需要 UAC）")
    args = ap.parse_args()

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
            if t.gem:
                fb.append(f"gem:`{t.gem}`")
            print(f"| `{t.id}` | {t.group} | {win} | {ub} | {ka} | {', '.join(fb) or '—'} |")
        return 0

    if args.install_wsl:
        if wsl_dists():
            print("WSL 已安装：" + ", ".join(wsl_dists()))
            return 0
        cmd = ["wsl.exe", "--install", "-d", "Ubuntu"]
        print("将执行：" + " ".join(cmd) + "（会弹 UAC；装完通常需要重启一次）")
        if args.dry_run:
            return 0
        if not args.yes:
            try:
                if input("继续？[y/N] ").strip().lower() not in ("y", "yes"):
                    return 0
            except (EOFError, KeyboardInterrupt):
                return 0
        rc, _ = run(cmd, timeout=1800, want_tty=True)
        print(f"exit={rc}" + ("；重启后重跑本脚本即可看到 WSL 目标" if rc == 0 else ""))
        return 0

    global SSH_PW_FILE
    SSH_PW_FILE = args.ssh_password_file

    ids = [x.strip() for x in args.only.split(",") if x.strip()] or None
    groups = [x.strip() for x in args.group.split(",") if x.strip()] or None

    ts = targets()
    if args.ssh_host:                       # ad-hoc remote
        ts.append(Target("ssh:" + args.ssh_host, f"SSH: {args.ssh_host}", "ssh",
                         args.ssh_host, host=args.ssh_host, user=args.ssh_user, port=args.ssh_port))

    if args.list:
        pool = ts
        if args.target and args.target != "auto":
            pool = [t for t in ts if t.key == args.target or t.key.startswith(args.target)]
            if not pool and args.target in ("ubuntu", "kali", "debian"):
                pool = [t for t in ts if t.os_name == args.target]
            if not pool:
                print(f"target {args.target!r} not found; have: {[t.key for t in ts]}")
                return 2
        for t in pool:
            if t.kind == "ssh" and not t.host:
                print(f"\n目标：{t.label}  （未开机/未发现 IP，跳过检测）")
                continue
            show_plan(make_plan(t, ids, groups), t)
        return 0

    if args.target and args.target != "auto":
        pool = [t for t in ts if t.key == args.target or t.key.startswith(args.target)]
        if not pool and args.target in ("ubuntu", "kali", "debian"):
            pool = [t for t in ts if t.os_name == args.target]
            pool.sort(key=lambda t: (not bool(t.host), t.kind != "wsl"))   # 能连的优先
        if not pool:
            print(f"target {args.target!r} not found; have: {[t.key for t in ts]}")
            return 2
        chosen = pool[:1]
    elif args.yes:
        chosen = [t for t in ts if t.kind == "winget"] or ts[:1]
    else:
        picked = menu(ts)
        chosen = [picked] if picked else []

    if not chosen:
        print("skipped")
        return 0

    target = chosen[0]
    if target.kind == "ssh" and not target.host and target.vmx:
        go = args.start_vm
        if not go and not args.yes and not args.dry_run:
            try:
                go = input(f"{target.label} 没开机，用 vmrun 启动它？[Y/n] ").strip().lower() != "n"
            except (EOFError, KeyboardInterrupt):
                go = False
        if go:
            vr = vmrun()
            if vr:
                print(f"    $ {vr} start \"{target.vmx}\" nogui")
                run([vr, "start", target.vmx, "nogui"], timeout=180)
                import time
                for _ in range(30):          # 最多等 150s 拿 IP
                    rc, out = run([vr, "getGuestIPAddress", target.vmx, "-wait"], timeout=90)
                    ip = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                        target.host = ip
                        break
                    time.sleep(5)
        if not target.host:
            print("    ! 启动后仍拿不到 IP：等 VMware Tools 就绪，或加 --ssh-host <ip>")
    if target.kind == "ssh" and not target.host:
        if not args.ssh_host:
            print(f"{target.label} 还没开机或拿不到 IP。启动它（vmrun start \"{target.vmx}\"）"
                  f"后重跑，或加 --ssh-host <ip>。")
            return 2
    if target.kind == "ssh" and not ssh_reachable(target):
        hint = target.alias or f"{target.user + '@' if target.user else ''}{target.host}"
        print(f"SSH 连不上 {hint}。检查：虚拟机已开机、sshd 在跑、防火墙放行，或先配好免密：")
        print(f"  ssh-copy-id {hint}")
        return 2

    rows = make_plan(target, ids, groups)
    show_plan(rows, target)
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "tool"} | {"id": r["tool"].id}
                          for r in rows], ensure_ascii=False, indent=2))
        return 0

    todo = [r for r in rows if not r["installed"]]
    if not todo:
        return 0
    if not args.yes and not args.dry_run:
        try:
            ans = input(f"\n安装以上 {len(todo)} 项？[Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans == "n":
            print("skipped")
            return 0

    failed = install(target, rows, args.allow_sudo, args.dry_run)
    if failed:
        print(f"\n失败/未装：{', '.join(sorted(set(failed)))}")
        print("可重试：python scripts/setup-tools.py --target "
              f"{target.key} --only {','.join(sorted(set(failed)))}")
        return 1
    if not args.dry_run:
        print("\n完成。验证：python scripts/setup-tools.py --target "
              f"{target.key} --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
