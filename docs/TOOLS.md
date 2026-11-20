# 工具对照表（Windows / Ubuntu / Kali）

> 由 `python scripts/setup-tools.py --markdown` 生成；改 `scripts/setup-tools.py` 的目录后重新生成。
> `apt` 列给的是**包名**，`winget` 列是包 ID，`兜底` 是没有原生包时的最小替代。
> 装之前脚本会用 `apt-cache policy` / `winget show` 复核可用性，不可用的会标 `不可用`。
> 同一张表也用于 WSL 发行版和 **VMware 虚拟机（走 SSH）**：目标系统不同，走的包管理器就不同。

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
