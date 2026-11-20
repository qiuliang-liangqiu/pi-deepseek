---
name: crypto
description: "Crypto challenge workflow: identify the construction first (encoding, XOR, block mode, RSA parameters), then attack with python (Crypto, gmpy2, sympy, z3). Load for cipher, RSA, ECC, PRNG or padding-oracle tasks."
---

# crypto

1. Identify: encoding (base64/hex/base32), XOR, block cipher mode, RSA params.
2. RSA: `n,e,c`; check small `e`, common modulus, Wiener, Fermat, shared prime,
   related-message, partial key, `gcd(c, n)`.
3. Block ciphers: ECB cut-and-paste, CBC bit-flip, padding oracle, IV reuse,
   nonce reuse (GCM/CTR), known-plaintext.
4. PRNG: recover state from outputs; check timestamp seeds.
5. Use `python3` with `Crypto`, `gmpy2`, `sympy`, `z3`; write a script, do not
   do long arithmetic by hand.
6. Verify the flag format before submitting; a decoded string is not a flag
   until it matches the challenge's expected prefix.
