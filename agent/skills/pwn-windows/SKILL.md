---
name: pwn-windows
description: "Windows kernel/user exploitation: driver IOCTLs, pool grooming, EPROCESS/Token swapping. Load when the challenge ships a .sys driver or a Windows binary target."
---

# pwn-windows

1. Identify the device and IOCTL surface: `DriverEntry`, IRP dispatch, device
   name, SDDL, `METHOD_BUFFERED`, `FILE_ANY_ACCESS`.
2. Recover structures from the driver: object headers, rings/descriptors,
   transaction records, cache entries, MAC/SipHash keys, canaries.
3. Look for stale caches/refcount bugs: free without invalidating a cache,
   resize with a different slot id, export/import of the same object.
4. Build kernel read/write: forge the cache entry/transaction, use the driver's
   own MAC oracle to sign it, then read/write arbitrary kernel addresses.
5. Leak: driver base via a kernel pointer leak; `ntoskrnl` via IAT/PE scan;
   `PsInitialSystemProcess` → `EPROCESS` → `ActiveProcessLinks` → own process.
6. Token: find `Token` offset by comparing System/own EPROCESS, write the System
   token, verify `GetTokenInformation(TokenUser)` is `S-1-5-18`.
7. Read the flag (`C:\flag.txt`) and avoid returning through corrupted driver
   state; sleep/hang instead of bugchecking if needed.
