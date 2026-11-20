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
IS_MAC = sys.platform == "darwin"
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
    brew: list[str] = field(default_factory=list)
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
    # ---- 内网 / 红队（Kali 自带，Ubuntu 需要 universe） ----
    Tool("hydra", "web", "在线爆破（有结构性证据才用）", ubuntu=["hydra"], kali=["hydra"], probe="hydra"),
    Tool("aircrack-ng", "recon", "无线", ubuntu=["aircrack-ng"], kali=["aircrack-ng"], probe="aircrack-ng"),
    Tool("metasploit", "ad", "漏洞利用框架", kali=["metasploit-framework"], probe="msfconsole"),
    Tool("impacket", "ad", "AD/SMB 脚本集", ubuntu=["python3-impacket"], kali=["impacket-scripts"],
         pip="impacket", probe="impacket-secretsdump"),
    Tool("netexec", "ad", "SMB/WinRM/LDAP 一把梭（nxc）", kali=["netexec"], probe="nxc"),
    Tool("responder", "ad", "LLMNR/NBT-NS 投毒", kali=["responder"], probe="responder"),
    Tool("bettercap", "ad", "中间人/ARP", kali=["bettercap"], probe="bettercap"),
]
BY_ID = {t.id: t for t in CATALOG}

# macOS (brew) 名字；没列到的说明该工具在 macOS 上要么系统自带、要么不适用
BREW_NAMES = {
    "nmap": ["nmap"], "ncat": ["nmap"], "jq": ["jq"], "ripgrep": ["ripgrep"], "fd": ["fd"],
    "curl": ["curl"], "wget": ["wget"], "binutils": ["binutils"], "file": ["file"], "xxd": ["xxd"],
    "unzip": ["unzip"], "gcc": ["gcc"], "make": ["make"], "ruby": ["ruby"], "go": ["go"],
    "dig": ["bind"], "nc": ["netcat"], "socat": ["socat"], "masscan": ["masscan"],
    "whatweb": ["whatweb"], "subfinder": ["subfinder"], "nikto": ["nikto"], "wpscan": ["wpscan"],
    "wfuzz": ["wfuzz"], "dirsearch": ["dirsearch"], "arjun": ["arjun"], "commix": ["commix"],
    "dalfox": ["dalfox"], "ffuf": ["ffuf"], "gobuster": ["gobuster"], "httpx": ["httpx"],
    "nuclei": ["nuclei"], "sqlmap": ["sqlmap"], "mitmproxy": ["mitmproxy"],
    "gdb": ["gdb"], "radare2": ["radare2"], "rizin": ["rizin"], "upx": ["upx"],
    "ROPgadget": ["ropgadget"], "ropper": ["ropper"], "one_gadget": ["one_gadget"],
    "seccomp-tools": ["seccomp-tools"], "strace": ["strace"], "ltrace": ["ltrace"],
    "angr": ["angr"], "capstone": ["capstone"], "keystone": ["keystone"],
    "hashcat": ["hashcat"], "john": ["john"], "hashid": ["hashid"],
    "exiftool": ["exiftool"], "binwalk": ["binwalk"], "foremost": ["foremost"],
    "sleuthkit": ["sleuthkit"], "tshark": ["wireshark"], "wireshark": ["wireshark"],
    "tcpdump": ["tcpdump"], "steghide": ["steghide"], "zsteg": ["zsteg"],
    "pngcheck": ["pngcheck"], "scalpel": ["scalpel"], "testdisk": ["testdisk"],
    "jadx": ["jadx"], "apktool": ["apktool"], "frida": ["frida"], "7z": ["sevenzip"],
    "hydra": ["hydra"], "aircrack-ng": ["aircrack-ng"], "bettercap": ["bettercap"],
}
for _t in CATALOG:
    _t.brew = BREW_NAMES.get(_t.id, [])

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
GROUPS = ["base", "recon", "web", "pwn", "forensics", "crypto", "android", "ad", "qol"]


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
    image: str = ""           # docker image
    container: str = ""       # docker container name
    provisioner: str = ""     # "qemu" when the box is provided by portable-vm.py


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
            # 不带 -wait：它会在 guest 未就绪时阻塞很久，拖慢整个菜单
            rc, out = run([vr, "getGuestIPAddress", v["vmx"]], timeout=20)
            cand = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", cand):
                ip = cand
            else:
                rc, out = run([vr, "getGuestIPAddress", v["vmx"], "-wait"], timeout=35)
                cand = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", cand):
                    ip = cand
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


PORTABLE = Path(__file__).resolve().parent / "portable-vm.py"
WORK_DIR = Path(os.environ.get("PI_VM_DIR") or (Path.home() / ".pi" / "vm"))
QEMU_SSH_PORT = 2222
QEMU_USER = "pi"


def qemu_state() -> tuple[bool, bool, bool]:
    """(portable-vm.py present, image present, VM currently running)"""
    if not PORTABLE.exists():
        return False, False, False
    work = Path(os.environ.get("PI_VM_DIR") or (Path.home() / ".pi" / "vm"))
    img = work / "noble-server-cloudimg-amd64.img"
    pidf = work / "pid"
    running = False
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip() or 0), 0)
            running = True
        except (OSError, ValueError):
            running = False
    return True, img.exists(), running


def qemu_ensure(args) -> bool:
    """Provision (download) and start the bundled QEMU VM if needed."""
    have_script, have_image, running = qemu_state()
    if not have_script:
        print("    ! 找不到 scripts/portable-vm.py")
        return False
    py = sys.executable
    if not have_image:
        print("    需要先下载 Ubuntu cloud image（约 600 MB，国内镜像约 8 MB/s）")
        if args.dry_run:
            print("    [dry-run] 会执行：portable-vm.py setup")
        else:
            if not args.yes:
                try:
                    if input("    现在下载？[Y/n] ").strip().lower() == "n":
                        return False
                except (EOFError, KeyboardInterrupt):
                    return False
            rc = subprocess.call([py, str(PORTABLE), "setup", "--yes"])
            if rc != 0:
                print("    ! 镜像准备失败")
                return False
    if not running:
        if args.dry_run:
            print("    [dry-run] 会执行：portable-vm.py start")
            return True
        print("    启动虚拟机（首次约 60–90 秒）…")
        rc = subprocess.call([py, str(PORTABLE), "start",
                              "--wait", str(max(120, args.startup_wait)),
                              "--ssh-port", str(QEMU_SSH_PORT), "--user", QEMU_USER])
        if rc != 0:
            print("    ! 启动失败，看 ~/.pi/vm/console.log")
            return False
    return True


def kali_qemu_ensure(target: Target, args) -> bool:
    """Download (4 GB, once) and boot the official Kali qemu image."""
    img = Path(os.environ.get("PI_KALI_IMAGE") or (WORK_DIR / "kali.qcow2"))
    if not img.exists():
        print("    需要先下载 Kali 官方 QEMU 镜像（约 4 GB，解压后 ~15 GB 空闲更稳）")
        if args.dry_run:
            print("    [dry-run] 会执行：portable-vm.py setup --distro kali")
            return True
        if not args.yes:
            try:
                if input("    现在下载？[Y/n] ").strip().lower() == "n":
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
        rc = subprocess.call([sys.executable, str(PORTABLE), "setup", "--distro", "kali", "--yes"])
        if rc != 0 or not img.exists():
            print("    ! 镜像准备失败（可手动放到 " + str(img) + "）")
            return False
    if args.dry_run:
        print(f"    [dry-run] 会启动 {img} 并把 22 转发到 127.0.0.1:{target.port}")
        return True
    running = False
    pidf = WORK_DIR / f"kali-{target.port}.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip() or 0), 0)
            running = True
        except (OSError, ValueError):
            running = False
    if not running:
        qemu = find_qemu()
        if not qemu:
            print("    ! 没有 qemu：先跑 python scripts/portable-vm.py setup")
            return False
        argv = [qemu, "-accel", best_accel(qemu) or "tcg", "-m", "2048", "-smp", "2",
                "-drive", f"file={img},if=virtio,format=qcow2",
                "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{target.port}-:22",
                "-device", "virtio-net-pci,netdev=n0", "-display", "none",
                "-serial", "file:" + str(WORK_DIR / "kali-console.log")]
        print("    启动 Kali：" + " ".join(argv))
        proc = subprocess.Popen(argv, stdout=(WORK_DIR / "kali-qemu.log").open("ab"),
                                stderr=subprocess.STDOUT)
        pidf.write_text(str(proc.pid), encoding="utf-8")
        sh(["ssh-keygen", "-R", f"[127.0.0.1]:{target.port}"], timeout=20)
        print("    等 SSH（Kali 的 sshd 可能要在 guest 里开启）…")
        for _ in range(60):
            rc, _ = sh(["ssh", "-p", str(target.port), "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=4", "-o", "StrictHostKeyChecking=no",
                        f"{target.user}@127.0.0.1", "true"], timeout=15)
            if rc == 0:
                break
            time.sleep(3)
    # 密码 kali/kali 交给 askpass
    _SSH_PW[target.alias or target.host] = os.environ.get("PI_KALI_PASSWORD", "kali")
    return True


def wsl_install_kali(args) -> int:
    cmd = ["wsl.exe", "--install", "-d", "kali-linux"]
    print("    将执行：" + " ".join(cmd) + "（需要一次 UAC，装完通常要重启）")
    if args.dry_run:
        return 0
    if not args.yes:
        try:
            if input("    继续？[y/N] ").strip().lower() not in ("y", "yes"):
                return 0
        except (EOFError, KeyboardInterrupt):
            return 0
    rc, _ = run(cmd, timeout=3600, want_tty=True)
    print("    完成，重启后重跑本脚本就能看到 Kali 目标" if rc == 0 else f"    ! exit {rc}")
    return 0


def docker_ok() -> bool:
    """docker CLI present and daemon reachable."""
    if not shutil.which("docker"):
        return False
    rc, _ = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=25)
    return rc == 0


DOCKER_NAME = "pi-toolbox"


def docker_running(name: str = DOCKER_NAME) -> bool:
    rc, out = run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"], timeout=30)
    return rc == 0 and name in out


def docker_ensure(image: str, name: str = DOCKER_NAME, dry: bool = False) -> bool:
    """Start a long-lived toolbox container (idempotent)."""
    if docker_running(name):
        return True
    argv = ["docker", "run", "-d", "--name", name,
            "--cap-add=SYS_PTRACE", "--cap-add=NET_RAW", "--cap-add=NET_ADMIN",
            "-v", f"{name}-work:/work", "-w", "/work", image, "sleep", "infinity"]
    print("    $ " + " ".join(argv))
    if dry:
        return True
    rc, out = run(argv, timeout=1800)
    if rc != 0:
        print(f"    ! docker run 失败 (exit {rc})")
        tail = [l for l in out.strip().splitlines() if l.strip()][-3:]
        for l in tail:
            print("      " + l[:140])
        return False
    rc, _ = run(["docker", "exec", name, "bash", "-lc", "apt-get update -qq"], timeout=1800)
    print("    ok" if rc == 0 else f"    ! apt-get update 失败 (exit {rc})")
    return rc == 0


def targets() -> list[Target]:
    out: list[Target] = []
    if IS_WIN:
        has_kali_wsl = any("kali" in d.lower() for d in wsl_dists())
        if not has_kali_wsl:
            out.append(Target("kali-wsl", "Kali（WSL，需安装：wsl --install -d kali-linux）",
                              "wsl-install", "kali", os_name="kali"))

    have_script, have_image, running = qemu_state()
    if have_script:
        state = "运行中" if running else ("已就绪" if have_image else "需下载镜像")
        out.append(Target("qemu", f"自带 QEMU 虚拟机（Ubuntu，{state}）", "ssh", "qemu",
                          host="127.0.0.1", port=QEMU_SSH_PORT, user=QEMU_USER,
                          os_name="ubuntu", provisioner="qemu"))
    kali_img = Path(os.environ.get("PI_KALI_IMAGE") or (WORK_DIR / "kali.qcow2"))
    if PORTABLE.exists():
        state = "已就绪" if kali_img.exists() else "需下载约 4 GB"
        out.append(Target("kali-qemu", f"自带 Kali 虚拟机（QEMU，{state}）", "ssh", "qemu",
                          host="127.0.0.1", port=2223, user="kali", os_name="kali",
                          provisioner="qemu-kali"))
    if IS_WIN:
        out.append(Target("windows", "Windows（本机 winget）", "winget", "host", os_name="windows"))
        if docker_ok():
            out.append(Target("docker", "Docker 容器（Ubuntu，代为运行）", "docker", "docker",
                              os_name="ubuntu", image="ubuntu:24.04", container=DOCKER_NAME))
            out.append(Target("docker-kali", "Kali（Docker 容器，工具最全）", "docker", "docker",
                              os_name="kali", image="kalilinux/kali-rolling",
                              container="pi-kali"))
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
    if IS_MAC:
        out.append(Target("brew", "macOS（本机 brew）", "brew", "host", os_name="macos"))
    if IS_LINUX and not shutil.which("apt-get"):
        # Fedora / Arch / Alpine …: 不猜包名，直接说清楚
        try:
            rel = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
            name = next((l.split("=", 1)[1].strip().strip('"') for l in rel.splitlines()
                         if l.startswith("PRETTY_NAME=")), "Linux")
        except OSError:
            name = "Linux"
        out.append(Target("unsupported", f"{name}（暂不支持：本脚本只覆盖 Debian 系）",
                          "unsupported", "host", os_name="linux"))
    if IS_LINUX and shutil.which("apt-get"):
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
    if target.kind == "docker":
        return run(["docker", "exec", target.container, "bash", "-lc", cmd], timeout=timeout)
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
    if target.kind == "brew":
        return t.brew
    if target.kind == "docker":
        return t.ubuntu
    if target.kind == "apt":
        return t.kali if "kali" in (target.distro or "") else t.ubuntu
    return t.kali if "kali" in (target.distro or "").lower() else t.ubuntu


MSYS2_DIRS = [r"C:\msys64\mingw64\bin", r"C:\msys64\usr\bin",
              r"C:\msys64\clang64\bin", r"C:\Program Files\Git\usr\bin"]


def msys2_tool(name: str) -> str:
    """gdb/objdump these usually live in MSYS2 or Git-Bash on Windows, not winget."""
    if not IS_WIN:
        return ""
    for d in MSYS2_DIRS:
        for ext in (".exe", ""):
            cand = Path(d) / (name + ext)
            if cand.exists():
                return str(cand)
    return ""

def installed_in(target: Target, t: Tool) -> bool:
    probe = t.probe or t.id
    if target.kind == "winget":
        return (shutil.which(probe) is not None or shutil.which(t.id) is not None
                or bool(msys2_tool(probe)))
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
    if target.kind in ("unsupported", "wsl-install"):
        return []
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
    elif target.kind == "brew":
        ok = {p: run(["brew", "info", p], timeout=40)[0] == 0 for p in all_pkgs}
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
    if target.kind == "wsl-install":
        print("  还没安装：选中它会执行 wsl --install -d kali-linux（一次 UAC，装完多半要重启）")
        return
    if target.kind == "unsupported":
        print("  这个发行版不在支持范围（只覆盖 Debian/Ubuntu/Kali 的 apt）：")
        print("    1) 用 Ubuntu/Kali 的 WSL 或 VM（推荐）")
        print("    2) 用 Docker：kalilinux/kali-rolling 或 ubuntu:24.04")
        print("    3) 按 docs/TOOLS.md 的 apt 列手工装")
        return
    print(f"  {'id':<12} {'组':<10} {'包名':<34} 状态")
    missing_win = [r["tool"].id for r in rows
                   if not r["installed"] and not r["available"] and not r["fallback"]]
    for r in rows:
        t = r["tool"]
        state = "已装" if r["installed"] else ("可装" if r["available"] else
                                              (f"兜底 {r['fallback']}" if r["fallback"] else "不可用"))
        pkg = ", ".join(r["pkgs"]) if r["pkgs"] else "-"
        note = f"  ← {t.note}" if t.note else ""
        print(f"  {t.id:<12} {t.group:<10} {pkg:<34} {state}{note}")

    if target.kind == "winget" and missing_win:
        print()
        shown = ", ".join(missing_win[:12])
        more = f" 等 {len(missing_win)} 个" if len(missing_win) > 12 else f"（共 {len(missing_win)} 个）"
        print(f"  Windows 上没有这些工具的包：{shown}{more}")
        print("  选一个去处（或加 --win-gaps skip 跳过）：")
        print("    1) WSL（推荐，一次 UAC）        wsl --install -d Ubuntu")
        print("    2) 已有虚拟机，走 SSH           需要 IP + 用户名")
        print("    3) MSYS2（gdb/binutils/gcc/make） winget install MSYS2.MSYS2 + pacman")
        have_script, have_image, _ = qemu_state()
        if have_script:
            print("    4) 自带 QEMU 虚拟机（免装任何软件" +
                  ("" if have_image else "，先下 600 MB 镜像") + "）")
        if docker_ok():
            print("    5) Docker 容器（不需要重启，pwn 回连仍受限）")
        print("    6) 跳过（这些工具先不用）")


# ------------------------------------------------------------ windows gaps
MSYS2_PKGS = ["mingw-w64-x86_64-gdb", "mingw-w64-x86_64-binutils"]


def msys2_bash() -> str:
    for cand in (r"C:\msys64\usr\bin\bash.exe", r"C:\msys64\usr\bin\bash"):
        if Path(cand).exists():
            return cand
    return ""


def install_msys2(dry: bool) -> int:
    """winget install MSYS2 -> pacman -S gdb binutils."""
    if not shutil.which("winget"):
        print("    ! 没有 winget，装不了 MSYS2")
        return 1
    argv = ["winget", "install", "-e", "--id", "MSYS2.MSYS2",
            "--accept-package-agreements", "--accept-source-agreements"]
    print(f"    $ {' '.join(argv)}")
    if dry:
        return 0
    rc, _ = run(argv, timeout=1800)
    if rc != 0:
        print(f"    ! winget 装 MSYS2 失败 (exit {rc})")
        return rc

    bash = msys2_bash()
    if not bash:
        print("    ! 装完找不到 C:/msys64/usr/bin/bash.exe（可能需要重开终端）")
        return 1
    cmd = f"{bash} -lc " + shlex_quote("pacman -S --noconfirm " + " ".join(MSYS2_PKGS))
    print(f"    $ {cmd}")
    rc, out = run(["cmd", "/c", cmd], timeout=3600)
    print("    ok" if rc == 0 else f"    ! pacman 失败 (exit {rc})")
    return rc


def shlex_quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def handle_windows_gaps(missing: list[str], args) -> Target | None:
    """Ask where the Linux-only tools should go. Returns a new target if the
    user picked the VM route, otherwise None."""
    action = args.win_gaps
    if not action:
        if args.yes or args.dry_run or not sys.stdin.isatty():
            print("\n  非交互运行：跳过（加 --win-gaps wsl|vm|msys2|skip 可指定）")
            return None
        print("\n  选一个去处：[1] WSL  [2] 已有虚拟机(SSH)  [3] MSYS2  [4] 跳过")
        try:
            pick = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        action = {"1": "wsl", "2": "vm", "3": "msys2", "4": "qemu", "5": "docker",
                  "6": "skip", "": "skip"}.get(pick, "skip")
    else:
        print(f"\n  --win-gaps {action}")

    if action == "skip":
        return None

    if action == "wsl":
        cmd = ["wsl.exe", "--install", "-d", "Ubuntu"]
        print("    $ " + " ".join(cmd) + "（会弹 UAC，装完通常要重启）")
        if not args.dry_run:
            rc, _ = run(cmd, timeout=1800, want_tty=True)
            print("    ok，重启后重跑：python scripts/setup-tools.py --target ubuntu"
                  if rc == 0 else f"    ! exit {rc}")
        return None

    if action == "msys2":
        install_msys2(args.dry_run)
        return None

    if action == "qemu":
        t = Target("qemu", "自带 QEMU 虚拟机（Ubuntu）", "ssh", "qemu",
                   host="127.0.0.1", port=QEMU_SSH_PORT, user=QEMU_USER,
                   os_name="ubuntu", provisioner="qemu")
        if not qemu_ensure(args):
            return None
        return t

    if action == "docker":
        if not docker_ok():
            print("    ! 没检测到可用的 docker（装 Docker Desktop 或换 WSL/VM）")
            return None
        t = Target("docker", "Docker 容器（Ubuntu，代为运行）", "docker", "docker",
                   os_name="ubuntu", image=args.docker_image, container=DOCKER_NAME)
        print("  之后用它跑工具：scripts/dt.sh nmap -sV <host>")
        return t

    # vm
    host = args.ssh_host
    user = args.ssh_user
    if not host and sys.stdin.isatty():
        try:
            host = input("  虚拟机 IP: ").strip()
            user = input("  用户名 [kali/ubuntu 用户]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
    if not host:
        print("    ! 需要 --ssh-host <ip> --ssh-user <user>")
        return None
    t = Target("ssh:" + host, f"SSH: {user + '@' if user else ''}{host}", "ssh",
               host, host=host, user=user, port=args.ssh_port)
    if not ssh_reachable(t):
        print(f"    ! 连不上 {host}：确认虚拟机开机、sshd 在跑，或先 ssh-copy-id")
        return None
    return t


# --------------------------------------------------------------------- install
def remember_vm(target: Target, dry: bool) -> None:
    """Write ~/.pi/agent/vm.env so scripts/vt.sh can ssh into this box later."""
    if target.kind != "ssh" or dry:
        return
    agent_dir = Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "vm.env").write_text(
            "PI_VM_HOST=" + (target.alias or target.host) + chr(10) +
            "PI_VM_USER=" + (target.user or "") + chr(10) +
            "PI_VM_PORT=" + str(target.port) + chr(10),
            encoding="utf-8")
        print(chr(10) + "已记录这台虚拟机 -> " + str(agent_dir / "vm.env"))
        print("  之后跑命令： bash scripts/vt.sh nmap -sV <host>")
    except OSError as exc:
        print(f"  (vm.env 没写成功：{exc})")


def ensure_apt_updated(target: "Target", dry: bool) -> bool:
    """Fresh cloud images ship with empty apt lists: update once before installing."""
    if dry:
        return True
    if target.kind == "docker":
        cmd = "apt-get update -qq"
        rc, _ = exec_in(target, cmd, timeout=900)
    elif target.kind == "wsl":
        rc, _ = exec_in(target, "apt-get update -qq", timeout=900)
    elif target.kind == "ssh":
        rc, _ = exec_in(target, "sudo -n apt-get update -qq", timeout=900)
        if rc != 0:
            rc, _ = exec_in(target, "apt-get update -qq", timeout=900)
    else:
        rc, _ = run(["bash", "-lc", "sudo -n apt-get update -qq"], timeout=900)
    print("    apt-get update: " + ("ok" if rc == 0 else f"exit {rc}"))
    return rc == 0


def install(target: Target, rows: list[dict], allow_sudo: bool, dry: bool) -> list[str]:
    todo = [r for r in rows if not r["installed"]]
    if not todo:
        print("  全部已安装")
        return []

    apt_pkgs = [p for r in todo if r["available"] for p in r["pkgs"]]
    failed: list[str] = []
    if apt_pkgs and target.kind in ("ssh", "wsl", "docker"):
        ensure_apt_updated(target, dry)

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

    if target.kind == "docker" and apt_pkgs:
        cmd = "LC_ALL=C apt-get install -y " + " ".join(sorted(set(apt_pkgs)))
        print(f"    $ [docker] {cmd}")
        if not dry:
            rc, _ = exec_in(target, cmd, timeout=3600)
            if rc != 0:
                print(f"    ! apt failed (exit {rc})")
        apt_pkgs = []

    if target.kind == "brew" and apt_pkgs:
        argv = ["brew", "install", *sorted(set(apt_pkgs))]
        print(f"    $ {' '.join(argv)}")
        if not dry:
            rc, _ = run(argv, timeout=3600)
            if rc != 0:
                print(f"    ! brew failed (exit {rc})")
        apt_pkgs = []

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
                    if pw:                               # 无 TTY，用 sudo -S 喂密码
                        cmd2 = "sudo -S -p '' " + cmd
                        print("      （sudo 用同一密码，经 stdin 传入）")
                        rc, _ = exec_in(target, cmd2, timeout=1800, stdin_text=pw + chr(10))
                    else:
                        # 免密 sudo 就用 -n；否则退回带 TTY 的交互 sudo
                        rc_n, _ = exec_in(target, "sudo -n true", timeout=30)
                        if rc_n == 0:
                            rc, _ = exec_in(target, "sudo -n " + cmd, timeout=1800)
                        else:
                            print("      （需要 sudo 密码：请在终端里交互，或配 NOPASSWD）")
                            rc, _ = exec_in(target, "sudo " + cmd, timeout=1800, tty=True)
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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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
    ap.add_argument("--auto", action="store_true",
                    help="非交互选目标（第一个可用），但仍会问是否安装")
    ap.add_argument("--all-groups", action="store_true", help="(默认就是全部组)")
    ap.add_argument("--docker-image", default="ubuntu:24.04", help="镜像（默认 ubuntu:24.04）")
    ap.add_argument("--startup-wait", type=int, default=300, help="QEMU 启动等待秒数")
    ap.add_argument("--win-gaps", default="",
                    choices=["", "wsl", "vm", "msys2", "qemu", "docker", "skip"],
                    help="Windows 缺 Linux 专属工具时怎么办（不给就交互问）")
    ap.add_argument("--kali-meta", action="store_true",
                    help="Kali: 先装 kali-linux-headless（约 1.5GB，一次到位），再补齐差异")
    ap.add_argument("--ssh-password-file", default="", help="从文件读 SSH 密码（不落 shell 历史）")
    ap.add_argument("--start-vm", action="store_true", help="VM 没开机时用 vmrun start 启动并等 IP")
    ap.add_argument("--install-wsl", action="store_true", help="未装 WSL 时执行 wsl --install -d Ubuntu（需要 UAC）")
    args = ap.parse_args()

    if args.markdown:
        print("| 工具 | 组 | Windows (winget) | Ubuntu/Debian (apt) | Kali (apt) | macOS (brew) | 兜底 |")
        print("|---|---|---|---|---|---|---|")
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
            mac = ", ".join(f"`{x}`" for x in t.brew) or "—"
            print(f"| `{t.id}` | {t.group} | {win} | {ub} | {ka} | {mac} | {', '.join(fb) or '—'} |")
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
    elif args.yes or args.auto:
        chosen = ([t for t in ts if t.provisioner == "qemu"] or
                  [t for t in ts if t.kind == "winget"] or ts[:1])
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
    if target.kind == "docker":
        if not docker_ensure(target.image or args.docker_image, target.container, args.dry_run):
            return 1

    if target.provisioner == "qemu":
        if not qemu_ensure(args):
            return 1
        if args.dry_run:
            print(f"    [dry-run] 计划：把工具装进 {target.label}")
            return 0
    if target.provisioner == "qemu-kali":
        if not kali_qemu_ensure(target, args):
            return 1
        if args.dry_run:
            print(f"    [dry-run] 计划：把工具装进 {target.label}")
            return 0
    if target.kind == "wsl-install":
        return wsl_install_kali(args)

    if target.kind == "ssh":
        managed = target.provisioner in ("qemu", "qemu-kali")
        if not ssh_reachable(target, interactive=not managed):
            if managed:
                print("虚拟机没起来或 SSH 还没就绪：python scripts/portable-vm.py start"
                      + (" --distro kali" if target.provisioner == "qemu-kali" else ""))
                print("日志：~/.pi/vm/console.log（Kali：~/.pi/vm/kali-console.log）")
            else:
                hint = target.alias or f"{target.user + '@' if target.user else ''}{target.host}"
                print(f"SSH 连不上 {hint}。检查：虚拟机已开机、sshd 在跑、防火墙放行，或先配好免密：")
                print(f"  ssh-copy-id {hint}")
            return 2

    if args.kali_meta and (target.os_name or "").lower() == "kali":
        cmd = "LC_ALL=C apt-get install -y kali-linux-headless"
        print(chr(10) + "先装 Kali 合集：" + cmd + "（约 1.5 GB）")
        if args.dry_run:
            print("    [dry-run] skipped")
        else:
            if target.kind == "ssh":
                pw = _SSH_PW.get(target.alias or target.host or "", "")
                rc, _ = exec_in(target, ("sudo -S -p '' " + cmd) if pw else cmd,
                                timeout=3600, stdin_text=(pw + chr(10)) if pw else None,
                                tty=not pw)
            elif target.kind == "apt":
                rc, _ = run(["bash", "-lc", ("sudo " if args.allow_sudo else "") + cmd], timeout=3600)
            else:
                rc, _ = exec_in(target, cmd, timeout=3600)
            print("    ok" if rc == 0 else f"    ! exit {rc}（继续按清单补）")

    rows = make_plan(target, ids, groups)
    show_plan(rows, target)

    if target.kind == "winget":
        gaps = [r["tool"].id for r in rows
                if not r["installed"] and not r["available"] and not r["fallback"]]
        if gaps:
            new_target = handle_windows_gaps(gaps, args)
            if new_target is not None:
                target = new_target
                rows = make_plan(target, ids, groups)
                show_plan(rows, target)

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "tool"} | {"id": r["tool"].id}
                          for r in rows], ensure_ascii=False, indent=2))
        return 0

    todo = [r for r in rows if not r["installed"]]
    if not todo:
        print("  全部已安装")
        remember_vm(target, args.dry_run)
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

    remember_vm(target, args.dry_run)

    if not args.dry_run:
        if target.kind == "docker":
            print(chr(10) + "完成。容器：" + target.container + "（工具都在里面）")
            print("  跑命令： bash scripts/dt.sh nmap -sV <host>")
            print("  交互 shell： bash scripts/dt.sh")
            print("  注意：pwn 反向回连受容器 NAT 限制；要完整网络就用 WSL/VM。")
        else:
            print(chr(10) + "完成。验证：python scripts/setup-tools.py --target "
                  + target.key + " --list")
            if target.kind == "ssh":
                print("  跑命令： bash scripts/vt.sh nmap -sV <host>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
