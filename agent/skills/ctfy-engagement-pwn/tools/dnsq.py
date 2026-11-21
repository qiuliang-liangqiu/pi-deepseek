"""Resolve A records via DNS-over-TCP to the tunnel resolver (through SOCKS)."""
import os, struct, socket, sys
from socks import connect

def q(name, server=os.environ.get("CTFY_DNS_SERVER", "<dns-server>"), port=53, timeout=6):
    tid = 0x1234
    hdr = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qn = b"".join(bytes([len(p)])+p.encode() for p in name.split(".")) + b"\x00"
    pkt = hdr + qn + struct.pack(">HH", 1, 1)
    s = connect(server, port, timeout=timeout)
    s.sendall(struct.pack(">H", len(pkt)) + pkt)
    ln = s.recv(2); n = struct.unpack(">H", ln)[0]
    data = b""
    while len(data) < n:
        d = s.recv(n-len(data))
        if not d: break
        data += d
    s.close()
    # parse answers
    ancount = struct.unpack(">H", data[6:8])[0]
    off = 12
    while data[off]: off += data[off]+1
    off += 5
    out = []
    for _ in range(ancount):
        if data[off] & 0xc0 == 0xc0: off += 2
        else:
            while data[off]: off += data[off]+1
            off += 1
        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", data[off:off+10]); off += 10
        rd = data[off:off+rdlen]; off += rdlen
        if rtype == 1: out.append(socket.inet_ntoa(rd))
        elif rtype == 5: out.append("CNAME")
    return out

if __name__ == "__main__":
    for name in sys.argv[1:]:
        try: print(name, "->", q(name))
        except Exception as e: print(name, "ERR", e)
