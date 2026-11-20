---
name: pwn-misc
description: "Non-mainstream pwn targets: MIPS/ARM/RISC-V, seccomp-only (ORW), static musl binaries and unusual ABIs. Load when a normal x86-64 glibc path does not apply."
---

# pwn-misc

1. `seccomp-tools dump ./bin` (if available) or inspect `prctl`/BPF; list allowed
   syscalls. If `execve` is blocked, use ORW: `open` → `read` → `write`.
2. No libc/system: use `syscall; ret` and raw syscall numbers; for static ELF
   use `ROPgadget`/`ropper` and the binary's own `syscall` gadgets.
3. Static link = fixed addresses (ET_EXEC) but no libc offsets; find `syscall`
   and `pop` gadgets in the binary.
4. Unusual arch (MIPS/ARM): respect endianness and delay slots; QEMU user mode
   can run it locally; GOT may be writable outside GNU_RELRO.
5. TLS/OpenSSL challenges: callbacks (info/psk/servername), HRR/session reuse,
   length-prefixed extensions, canary brute force; static OpenSSL gives gadgets.
6. For `fork` servers, canary/ASLR may be shared across children; brute force is
   viable. For `exec` per connection, re-leak each time.
7. Read the flag with `cat /flag*` or ORW; do not assume `system` exists.
