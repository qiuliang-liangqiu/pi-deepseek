---
name: pwn-browser
description: "Browser renderer exploitation: JIT bugs, wasm, type confusion, and sandbox-escape ordering. Load when the target is a browser or JavaScript engine challenge."
---

# pwn-browser

1. Fingerprint browser build: `firefox --version`, `d8 --version`, `strings`,
   patch series, `js/src` revision. Offsets are build-specific.
2. Find a reachable bug: JIT type confusion, UAF in DOM/Image/Blob, iframe
   lifecycle, SVG `<use>`, parser, Wasm.
3. Primitives: `addrof` / `fakeobj`, arbitrary read/write over a `Uint8Array`
   backing store, then leak `libxul`/`libc` and a stack pivot.
4. Renderer exploit: JIT warmup, deterministic heap spray, `setcontext`/`execve`
   or ROP; the sandbox may still block file reads, so target `/readflag`.
5. Run the exact headless build from the attachment; reproduce locally before
   remote. Browser crashes are noisy and slow: checkpoint before each attempt.
6. Keep retries bounded; one bad heap layout should reconnect, not hang.
