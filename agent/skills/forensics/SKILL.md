---
name: forensics
description: "Forensics and misc: binwalk/foremost/exiftool/tshark carving, layered encodings, file-header repair, metadata and packet streams. Load for disk images, pcap, stego or \"misc\" artifacts."
---

# forensics

1. `file`, `exiftool`, `binwalk -e`, `foremost`, `strings`, `xxd`.
2. Images: `zsteg`, `steghide`, `pngcheck`, LSB, palette, appended ZIP.
3. PCAP: `tshark -r`, follow TCP streams, export objects; look for HTTP creds,
   DNS exfil, ICMP payloads.
4. Archives: check nested archives, password hints, known plaintext, CRC.
5. Memory/disk: `vol.py`, `mmls`, `fls`, `icat`, carve deleted files.
6. Decode layers: base64 → gzip → XOR → protobuf; script the pipeline.
