---
name: pwn-kernel
description: "Kernel and QEMU escape playbooks: device UAF/DMA/timer bugs, commit_creds and modprobe_path routes, reading oopses and finding the shipped vmlinux. Load for kernel-module or QEMU device challenges."
---

# pwn-kernel

1. Fingerprint: `/proc/version`, `/proc/config.gz`, `/proc/kallsyms`,
   `/proc/devices`, `dmesg_restrict`, `kptr_restrict`, SMEP/SMAP/KASLR, userfaultfd,
   io_uring, unprivileged BPF.
2. Find the bug class: UAF in a driver/ioctl, missing size/bounds check, race,
   refcount, timer/RCU, object cache reuse. Read the source/patch if provided.
3. Build: leak (kernel base/heap) → arbitrary read → arbitrary write →
   `commit_creds(prepare_kernel_cred(0))` or `modprobe_path`/`core_pattern`/
   `poweroff_cmd`, then `geteuid()==0` check.
4. QEMU escape: enumerate PCI BARs, mmap `resource0`, enable MMIO/bus master,
   use a DMA device to read/write guest memory, turn a device UAF into a host
   function pointer call. The QEMU process usually runs as a low-priv user;
   `system` in the host process is enough to read the flag.
5. Guest kernel: KASLR slide via an oracle/MAC, `swapgs`/`iretq` return to user,
   keep a userland `getuid` check.
6. Windows kernel: IOCTL/METHOD_BUFFERED, object refcount/UAF, leak
   `PsInitialSystemProcess`, walk `ActiveProcessLinks`, steal `Token`; verify
   with the token SID (`S-1-5-18`).
7. Do not crash the target before the final write; one-shot if `panic=-1`.
