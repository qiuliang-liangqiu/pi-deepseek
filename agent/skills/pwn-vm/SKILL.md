---
name: pwn-vm
description: "Custom VM and JS-engine exploitation: bytecode review, addrof/fakeobj, backing-store corruption. Load when the binary is a bespoke interpreter or script engine rather than a native buffer overflow."
---

# pwn-vm

1. Identify the runtime: file, strings, symbols, Dockerfile, run.sh, patches.
2. Map the dispatch loop: opcode table, operand encoding, value representation
   (tagged ints/pointers), stack/register file, heap object headers.
3. Look for missing bounds checks: immediate operands used as indexes, signed/
   unsigned length, object type confusion, GC roots and stale slots.
4. Build a bytecode assembler/disassembler before exploiting; send one chunk per
   read if the VM protocol has length-prefixed chunks.
5. Primitive order: type confusion → leak (PIE/heap) → arbitrary read/write →
   control flow (vtable/function pointer/ROP).
6. JS engines: addrof/fakeobj via JIT type confusion, `ArrayBuffer` backing store,
   wasm RWX, sandbox cage; keep the exploit deterministic and retry on failure.
7. QEMU devices: map BAR/MMIO, DMA descriptors, timers; UAF in a device object can
   be turned into a host callback (`obj->cb(obj+8)`) or `system` in the QEMU
   process. Check the device realize order and vCPU-thread allocation arena.
8. Verify a local flag path and run the same binary/container locally before
   sending to remote.
