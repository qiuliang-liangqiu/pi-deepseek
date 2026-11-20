# 工具对照表（Windows / Ubuntu / Kali / macOS）

> 由 `python scripts/setup-tools.py --markdown` 生成；改 `scripts/setup-tools.py` 的目录后重新生成。
> `apt` 列是**包名**，`winget` 列是包 ID，`brew` 列是 formula，`兜底` 是没有原生包时的最小替代（pip / gem / go）。
> 装之前脚本会用 `apt-cache policy` / `winget show` / `brew info` 复核可用性，不可用的标 `不可用`。
> 同一张表用于 WSL 发行版和 **VMware 虚拟机（走 SSH）**：目标不同，走的包管理器就不同。

## Kali 的特别处理

Kali 自带绝大多数工具，逐条装反而慢。加 `--kali-meta` 会先装 `kali-linux-headless`
（约 1.5 GB，一次到位），再按本表补齐差异：

```bash
python scripts/setup-tools.py --target kali --kali-meta --yes
```

Kali 与 Ubuntu 的差异：`httpx` → `httpx-toolkit`、`impacket` → `impacket-scripts`，
另加 `metasploit-framework`、`netexec`、`responder`、`bettercap`。

## 不支持的发行版

脚本只覆盖 Debian 系（Ubuntu / Kali / Debian）的 apt。Fedora / Arch / Alpine 会明确提示
"暂不支持"，并给三条出路：用 WSL/VM、用 Docker、或按本表手工装。

| 工具 | 组 | Windows (winget) | Ubuntu/Debian (apt) | Kali (apt) | macOS (brew) | 兜底 |
|---|---|---|---|---|---|---|
| `go` | base | `GoLang.Go` | `golang-go` | `golang` | `go` | — |
| `nmap` | recon | `Insecure.Nmap` | `nmap` | `nmap` | `nmap` | — |
| `ncat` | recon | `Insecure.Nmap` | `ncat` | `ncat` | `nmap` | — |
| `jq` | recon | `jqlang.jq` | `jq` | `jq` | `jq` | — |
| `ripgrep` | recon | `BurntSushi.ripgrep.MSVC` | `ripgrep` | `ripgrep` | `ripgrep` | — |
| `fd` | recon | `sharkdp.fd` | `fd-find` | `fd-find` | `fd` | — |
| `ffuf` | web | — | `ffuf` | `ffuf` | `ffuf` | go install |
| `gobuster` | web | — | `gobuster` | `gobuster` | `gobuster` | go install |
| `httpx` | web | — | `httpx-toolkit` | `httpx-toolkit` | `httpx` | go install |
| `nuclei` | web | — | `nuclei` | `nuclei` | `nuclei` | go install |
| `sqlmap` | web | — | `sqlmap` | `sqlmap` | `sqlmap` | pip:`sqlmap` |
| `mitmproxy` | web | — | `mitmproxy` | `mitmproxy` | `mitmproxy` | pip:`mitmproxy` |
| `gdb` | pwn | — | `gdb` | `gdb` | `gdb` | — |
| `radare2` | pwn | — | `radare2` | `radare2` | `radare2` | — |
| `rizin` | pwn | — | `rizin` | `rizin` | `rizin` | — |
| `upx` | pwn | `UPX.UPX` | `upx-ucl` | `upx-ucl` | `upx` | — |
| `patchelf` | pwn | — | `patchelf` | `patchelf` | — | — |
| `pwntools` | pwn | — | — | — | — | pip:`pwntools` |
| `exiftool` | forensics | `OliverBetz.ExifTool` | `libimage-exiftool-perl` | `libimage-exiftool-perl` | `exiftool` | — |
| `binwalk` | forensics | — | `binwalk` | `binwalk` | `binwalk` | pip:`binwalk` |
| `foremost` | forensics | — | `foremost` | `foremost` | `foremost` | — |
| `sleuthkit` | forensics | — | `sleuthkit` | `sleuthkit` | `sleuthkit` | — |
| `tshark` | forensics | `WiresharkFoundation.Wireshark` | `tshark` | `tshark` | `wireshark` | — |
| `volatility3` | forensics | — | — | — | — | pip:`volatility3` |
| `scapy` | forensics | — | — | — | — | pip:`scapy` |
| `hashcat` | crypto | — | `hashcat` | `hashcat` | `hashcat` | — |
| `john` | crypto | — | `john` | `john` | `john` | — |
| `pycryptodome` | crypto | — | — | — | — | pip:`pycryptodome` |
| `gmpy2` | crypto | — | — | — | — | pip:`gmpy2` |
| `z3` | crypto | — | — | — | — | pip:`z3-solver` |
| `jadx` | android | `Skylot.jadx` | `jadx` | `jadx` | `jadx` | — |
| `apktool` | android | — | `apktool` | `apktool` | `apktool` | — |
| `frida` | android | — | — | — | `frida` | pip:`frida-tools` |
| `7z` | qol | `7zip.7zip` | `p7zip-full` | `p7zip-full` | `sevenzip` | — |
| `curl` | base | `cURL.cURL` | `curl` | `curl` | `curl` | — |
| `wget` | base | — | `wget` | `wget` | `wget` | — |
| `binutils` | base | — | `binutils` | `binutils` | `binutils` | — |
| `file` | base | — | `file` | `file` | `file` | — |
| `xxd` | base | — | `xxd` | `xxd` | `xxd` | — |
| `unzip` | base | — | `unzip` | `unzip` | `unzip` | — |
| `gcc` | base | — | `gcc` | `gcc` | `gcc` | — |
| `make` | base | — | `make` | `make` | `make` | — |
| `ruby` | base | — | `ruby`, `ruby-dev` | `ruby`, `ruby-dev` | `ruby` | — |
| `dig` | recon | — | `dnsutils` | `dnsutils` | `bind` | — |
| `nc` | recon | — | `netcat-openbsd` | `netcat-openbsd` | `netcat` | — |
| `socat` | recon | — | `socat` | `socat` | `socat` | — |
| `masscan` | recon | — | `masscan` | `masscan` | `masscan` | — |
| `whatweb` | recon | — | `whatweb` | `whatweb` | `whatweb` | — |
| `subfinder` | recon | — | — | `subfinder` | `subfinder` | go install |
| `nikto` | web | — | `nikto` | `nikto` | `nikto` | — |
| `wpscan` | web | — | — | `wpscan` | `wpscan` | gem:`wpscan` |
| `wfuzz` | web | — | — | — | `wfuzz` | pip:`wfuzz` |
| `dirsearch` | web | — | — | — | `dirsearch` | pip:`dirsearch` |
| `arjun` | web | — | — | — | `arjun` | pip:`arjun` |
| `commix` | web | — | — | — | `commix` | pip:`commix` |
| `dalfox` | web | — | — | — | `dalfox` | go install |
| `ROPgadget` | pwn | — | — | — | `ropgadget` | pip:`ROPgadget` |
| `ropper` | pwn | — | — | — | `ropper` | pip:`ropper` |
| `one_gadget` | pwn | — | — | — | `one_gadget` | gem:`one_gadget` |
| `seccomp-tools` | pwn | — | — | — | `seccomp-tools` | gem:`seccomp-tools` |
| `gdb-multiarch` | pwn | — | `gdb-multiarch` | `gdb-multiarch` | — | — |
| `qemu-user` | pwn | — | `qemu-user` | `qemu-user` | — | — |
| `strace` | pwn | — | `strace` | `strace` | `strace` | — |
| `ltrace` | pwn | — | `ltrace` | `ltrace` | `ltrace` | — |
| `angr` | rev | — | — | — | `angr` | pip:`angr` |
| `unicorn` | rev | — | — | — | — | pip:`unicorn` |
| `capstone` | rev | — | — | — | `capstone` | pip:`capstone` |
| `keystone` | rev | — | — | — | `keystone` | pip:`keystone-engine` |
| `sage` | crypto | — | — | — | — | — |
| `hashid` | crypto | — | — | — | `hashid` | pip:`hashid` |
| `steghide` | forensics | — | `steghide` | `steghide` | `steghide` | — |
| `zsteg` | forensics | — | — | — | `zsteg` | gem:`zsteg` |
| `pngcheck` | forensics | — | `pngcheck` | `pngcheck` | `pngcheck` | — |
| `scalpel` | forensics | — | `scalpel` | `scalpel` | `scalpel` | — |
| `testdisk` | forensics | — | `testdisk` | `testdisk` | `testdisk` | — |
| `bulk-extractor` | forensics | — | `bulk-extractor` | `bulk-extractor` | — | — |
| `tcpdump` | forensics | — | `tcpdump` | `tcpdump` | `tcpdump` | — |
| `wireshark` | forensics | `WiresharkFoundation.Wireshark` | `wireshark` | `wireshark` | `wireshark` | — |
| `gau` | recon | — | — | — | — | go install |
| `katana` | recon | — | — | — | — | go install |
| `hydra` | web | — | `hydra` | `hydra` | `hydra` | — |
| `aircrack-ng` | recon | — | `aircrack-ng` | `aircrack-ng` | `aircrack-ng` | — |
| `metasploit` | ad | — | — | `metasploit-framework` | — | — |
| `impacket` | ad | — | `python3-impacket` | `impacket-scripts` | — | pip:`impacket` |
| `netexec` | ad | — | — | `netexec` | — | — |
| `responder` | ad | — | — | `responder` | — | — |
| `bettercap` | ad | — | — | `bettercap` | `bettercap` | — |

## 这些不在包管理器里（需要手动装）

| 工具 | 为什么 | 怎么装 |
|---|---|---|
| Ghidra | 需要 JDK + 官方 zip，几百 MB | 官方 release 解压，`ghidraRun` |
| SageMath | 体积数 GB，发行版包版本旧 | 官方安装器 / conda / 官方 docker 镜像 |
| Burp Suite / ZAP | GUI 代理 | 官网安装；ZAP 在 Kali 里自带 |
| IDA / Binary Ninja | 商业授权 | 不自动化 |
| Cutter | Ghidra/rizin 的 GUI | 官方 release |
| Autopsy | 取证 GUI + Java | 官方 installer |
| pwndbg / GEF / PEDA | gdb 插件，靠 `git clone` + `.gdbinit` | 各自 README 的一行 source 命令 |
| MobSF | 一整套 web 服务 | 官方 docker 镜像 |
| WinDbg / x64dbg / IDA-Win | Windows 二进制专用 | 官方安装包 |

需要这些时脚本显示 `不可用`，不假装装好了。
