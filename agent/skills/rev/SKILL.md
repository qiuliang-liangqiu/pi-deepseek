---
name: rev
description: "Reverse engineering workflow: strings/objdump/gdb/ltrace first, then reimplement the check in python or solve constraints with z3. Load for crackme, keygen, packed or obfuscated binaries."
---

# rev

1. `file`, `strings -a`, `readelf -S`, `objdump -d`, `ltrace/strace`.
2. Look for the check function: `strcmp`, `memcmp`, table lookups, XOR loops.
3. Reimplement the check in Python; brute-force small spaces, use z3 for
   constraint systems.
4. Check for packing (`upx -d`), self-modifying code, anti-debug, VM bytecode.
5. For .NET/Java/Python, inspect metadata first (`strings`, `unzip -l`).
6. Verify the recovered input by running the binary with it before submitting.
