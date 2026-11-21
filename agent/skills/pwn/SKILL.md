---
name: pwn
description: "Pwn triage and exploit build order (bug → leak → arbitrary read → arbitrary write → control flow), with checksec/objdump/gdb/pwntools usage. Load when the challenge ships an ELF, service or binary and needs memory-corruption exploitation."
---

# pwn

1. If the image ships it, run `python3 /opt/tools/pwn_triage.py <file>` (locally this tool is absent — fall back to `checksec`/`readelf -d`/`file`); read protections, interpreter,
   NEEDED libs, libc hints and nearby loader/libc.
1b. If the image ships it, retrieve a similar known case before writing an exploit:
   `python3 /opt/cases/retrieve.py --text "heap menu view 2.43"` (or `--query` JSON);
   locally use `%USERPROFILE%/.pi/agent/skills/ctf-kb` (`kb.py`) instead.
   Use it as a plan/template, not as an answer: verify every offset against the
   current binary and libc.
2. Classify the target before choosing a technique:
   - normal ELF (heap/stack/format/FSOP) → pwn-heap.md
   - custom VM/bytecode/JS engine → pwn-vm.md
   - QEMU device / kernel / guest → pwn-kernel.md
   - browser renderer (V8/Firefox) → pwn-browser.md
   - Windows driver (IOCTL/EPROCESS) → pwn-windows.md
   - static/MIPS/seccomp/ORW → pwn-misc.md
3. Reproduce locally with the **provided** loader/libc:
   `patchelf --set-interpreter ./ld-linux... --set-rpath . ./bin` or
   `./ld-linux... --library-path . ./bin`.
4. Build the chain in this order: bug → leak (PIE/libc/heap/stack) →
   arbitrary read → arbitrary write → control flow → flag read.
   Do not jump to ROP before you have the leak/primitive.
5. Match the exact libc/loader version from the files; offsets from a different
   build are the most common failure.
6. Remote may differ from local: keep offsets in variables, retry the connection,
   and use bounded brute force (canary/KASLR/ASLR) with a checkpoint.
7. Flag locations to try after a shell: `/flag`, `/flag*`, `/home/*/flag*`,
   `/root/flag*`, `C:\flag.txt`, `/cf/host/flag`, `/tmp/flag*`.
8. Save the exploit and the exact command/output in `/ctfy/work/`; note the
   offsets and the libc version in `notes.md`.
