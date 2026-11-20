# 工具对照表（Windows / Ubuntu / Kali）

> 由 `python scripts/setup-tools.py --markdown` 生成；改 `scripts/setup-tools.py` 的目录后重新生成。
> `apt` 列给的是**包名**，`winget` 列是包 ID，`兜底` 是没有原生包时的最小替代（pip / gem / go）。
> 装之前脚本会用 `apt-cache policy` / `winget show` 复核可用性，不可用的会标 `不可用`。
> 同一张表也用于 WSL 发行版和 **VMware 虚拟机（走 SSH）**：目标系统不同，走的包管理器就不同。

## Kali 的特别处理

Kali 自带绝大多数工具，逐条装反而慢。在 Kali 目标上加 `--kali-meta` 会先装
`kali-linux-headless`（约 1.5 GB，一次到位），再按本表补齐差异：

```bash
python scripts/setup-tools.py --target kali --kali-meta --yes
```

Kali 与 Ubuntu 的差异（本表已有 `kali` 列）：`httpx` → `httpx-toolkit`、
`impacket` → `impacket-scripts`、另加 `metasploit-framework`、`netexec`、`responder`、
`bettercap`。反向：Ubuntu 没有 `metasploit`/`netexec`，会标 `不可用`。

| 工具 | 组 | Windows (winget) | Ubuntu/Debian (apt) | Kali (apt) | 兜底 |
|---|---|---|---|---|---|
| `go` | base | `GoLang.Go` | `golang-go` | `golang` | — |
| `nmap` | recon | `Insecure.Nmap` | `nmap` | `nmap` | — |
| `ncat` | recon | `Insecure.Nmap` | `ncat` | `ncat` | — |
| `jq` | recon | `jqlang.jq` | `jq` | `jq` | — |
| `ripgrep` | recon | `BurntSushi.ripgrep.MSVC` | `ripgrep` | `ripgrep` | — |
| `fd` | recon | `sharkdp.fd` | `fd-find` | `fd-find` | — |
| `ffuf` | web | — | `ffuf` | `ffuf` | go install |
| `gobuster` | web | — | `gobuster` | `gobuster` | go install |
| `httpx` | web | — | `httpx-toolkit` | `httpx-toolkit` | go install |
| `nuclei` | web | — | `nuclei` | `nuclei` | go install |
| `sqlmap` | web | — | `sqlmap` | `sqlmap` | pip:`sqlmap` |
| `mitmproxy` | web | — | `mitmproxy` | `mitmproxy` | pip:`mitmproxy` |
| `gdb` | pwn | — | `gdb` | `gdb` | — |
| `radare2` | pwn | — | `radare2` | `radare2` | — |
| `rizin` | pwn | — | `rizin` | `rizin` | — |
| `upx` | pwn | `UPX.UPX` | `upx-ucl` | `upx-ucl` | — |
| `patchelf` | pwn | — | `patchelf` | `patchelf` | — |
| `pwntools` | pwn | — | — | — | pip:`pwntools` |
| `exiftool` | forensics | `OliverBetz.ExifTool` | `libimage-exiftool-perl` | `libimage-exiftool-perl` | — |
| `binwalk` | forensics | — | `binwalk` | `binwalk` | pip:`binwalk` |
| `foremost` | forensics | — | `foremost` | `foremost` | — |
| `sleuthkit` | forensics | — | `sleuthkit` | `sleuthkit` | — |
| `tshark` | forensics | `WiresharkFoundation.Wireshark` | `tshark` | `tshark` | — |
| `volatility3` | forensics | — | — | — | pip:`volatility3` |
| `scapy` | forensics | — | — | — | pip:`scapy` |
| `hashcat` | crypto | — | `hashcat` | `hashcat` | — |
| `john` | crypto | — | `john` | `john` | — |
| `pycryptodome` | crypto | — | — | — | pip:`pycryptodome` |
| `gmpy2` | crypto | — | — | — | pip:`gmpy2` |
| `z3` | crypto | — | — | — | pip:`z3-solver` |
| `jadx` | android | `Skylot.jadx` | `jadx` | `jadx` | — |
| `apktool` | android | — | `apktool` | `apktool` | — |
| `frida` | android | — | — | — | pip:`frida-tools` |
| `7z` | qol | `7zip.7zip` | `p7zip-full` | `p7zip-full` | — |
| `curl` | base | `cURL.cURL` | `curl` | `curl` | — |
| `wget` | base | — | `wget` | `wget` | — |
| `binutils` | base | — | `binutils` | `binutils` | — |
| `file` | base | — | `file` | `file` | — |
| `xxd` | base | — | `xxd` | `xxd` | — |
| `unzip` | base | — | `unzip` | `unzip` | — |
| `gcc` | base | — | `gcc` | `gcc` | — |
| `make` | base | — | `make` | `make` | — |
| `ruby` | base | — | `ruby`, `ruby-dev` | `ruby`, `ruby-dev` | — |
| `dig` | recon | — | `dnsutils` | `dnsutils` | — |
| `nc` | recon | — | `netcat-openbsd` | `netcat-openbsd` | — |
| `socat` | recon | — | `socat` | `socat` | — |
| `masscan` | recon | — | `masscan` | `masscan` | — |
| `whatweb` | recon | — | `whatweb` | `whatweb` | — |
| `subfinder` | recon | — | — | `subfinder` | go install |
| `nikto` | web | — | `nikto` | `nikto` | — |
| `wpscan` | web | — | — | `wpscan` | gem:`wpscan` |
| `wfuzz` | web | — | — | — | pip:`wfuzz` |
| `dirsearch` | web | — | — | — | pip:`dirsearch` |
| `arjun` | web | — | — | — | pip:`arjun` |
| `commix` | web | — | — | — | pip:`commix` |
| `dalfox` | web | — | — | — | go install |
| `ROPgadget` | pwn | — | — | — | pip:`ROPgadget` |
| `ropper` | pwn | — | — | — | pip:`ropper` |
| `one_gadget` | pwn | — | — | — | gem:`one_gadget` |
| `seccomp-tools` | pwn | — | — | — | gem:`seccomp-tools` |
| `gdb-multiarch` | pwn | — | `gdb-multiarch` | `gdb-multiarch` | — |
| `qemu-user` | pwn | — | `qemu-user` | `qemu-user` | — |
| `strace` | pwn | — | `strace` | `strace` | — |
| `ltrace` | pwn | — | `ltrace` | `ltrace` | — |
| `angr` | rev | — | — | — | pip:`angr` |
| `unicorn` | rev | — | — | — | pip:`unicorn` |
| `capstone` | rev | — | — | — | pip:`capstone` |
| `keystone` | rev | — | — | — | pip:`keystone-engine` |
| `sage` | crypto | — | — | — | — |
| `hashid` | crypto | — | — | — | pip:`hashid` |
| `steghide` | forensics | — | `steghide` | `steghide` | — |
| `zsteg` | forensics | — | — | — | gem:`zsteg` |
| `pngcheck` | forensics | — | `pngcheck` | `pngcheck` | — |
| `scalpel` | forensics | — | `scalpel` | `scalpel` | — |
| `testdisk` | forensics | — | `testdisk` | `testdisk` | — |
| `bulk-extractor` | forensics | — | `bulk-extractor` | `bulk-extractor` | — |
| `tcpdump` | forensics | — | `tcpdump` | `tcpdump` | — |
| `wireshark` | forensics | `WiresharkFoundation.Wireshark` | `wireshark` | `wireshark` | — |
| `gau` | recon | — | — | — | go install |
| `katana` | recon | — | — | — | go install |
| `hydra` | web | — | `hydra` | `hydra` | — |
| `aircrack-ng` | recon | — | `aircrack-ng` | `aircrack-ng` | — |
| `metasploit` | ad | — | — | `metasploit-framework` | — |
| `impacket` | ad | — | `python3-impacket` | `impacket-scripts` | pip:`impacket` |
| `netexec` | ad | — | — | `netexec` | — |
| `responder` | ad | — | — | `responder` | — |
| `bettercap` | ad | — | — | `bettercap` | — |

## 这些不在包管理器里（需要手动装）

| 工具 | 为什么 | 怎么装 |
|---|---|---|
| Ghidra | 需要 JDK + 官方 zip，几百 MB | 官方 release 解压，`ghidraRun` |
| SageMath | 体积数 GB，发行版包版本旧 | 官方安装器 / conda / 官方 docker 镜像 |
| Burp Suite / ZAP | GUI 代理 | 官网安装；ZAP 在 Kali 里自带 |
| IDA / Binary Ninja | 商业授权 | 不自动化 |
| Cutter | Ghidra/rizin 的 GUI | 官方 release |
| Autopsy | 取证 GUI + Java | 官方 installer |
| pwndbg / GEF / PEDA | 是 gdb 的插件，靠 `git clone` + `.gdbinit` | 各自 README 的一行 source 命令 |
| MobSF | 一整套 web 服务 | 官方 docker 镜像 |
| WinDbg / x64dbg / IDA-Win | Windows 二进制专用 | 官方安装包 |

需要这些时脚本会显示 `不可用`（而不是假装装好了）。
