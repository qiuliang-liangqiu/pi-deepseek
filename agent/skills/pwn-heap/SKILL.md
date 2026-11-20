---
name: pwn-heap
description: "glibc heap exploitation: tcache/unsorted-bin primitives, UAF, double free, FSOP and consolidation tricks. Load when the pwn target has a heap allocator surface (add/edit/free menus, note managers)."
---

# pwn-heap

1. Identify allocator/version: `strings libc.so.6 | grep 'GNU C Library'`,
   `ldd`, `malloc_trim`/`__libc_malloc` symbols, tcache presence (>=2.26).
2. Bug classes: UAF, double free, overflow into next chunk size, off-by-one/null,
   unsafe unlink, tcache poisoning, largebin/unsorted bin attack, FSOP.
3. Leak order: unsorted bin `fd/bk` → libc; heap pointer from smallbin/tcache;
   PIE from vtable/IO/stack. Count `view`/`show` budget.
4. glibc >=2.32: safe-linking on tcache/fastbin (`next = ptr ^ (chunk>>12)`);
   keep a heap leak before poisoning.
5. FSOP: fake `_IO_FILE` + `_wide_data` + vtable, `_IO_wfile_jumps`,
   `setcontext` pivot, `_IO_list_all` on exit/flush. Check `_flags`, `_mode`,
   `_wide_vtable->__doallocate`.
6. House of Force/Spirit/Botcake/Orange: check top chunk size and alignment.
7. For one-byte size corruption, preserve `PREV_INUSE` and low nibble; compute
   the exact resulting size.
8. After a heap primitive, prefer `environ`/stack ROP or the version's
   free-hook equivalent; test the exact build.
