"""Minimal SOCKS5 (no-auth, domain CONNECT) helper for wireproxy."""
import socket, struct

PROXY = ("127.0.0.1", 25346)

def connect(host, port, proxy=PROXY, timeout=10):
    s = socket.create_connection(proxy, timeout=timeout)
    s.settimeout(timeout)
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        raise RuntimeError("socks greeting failed")
    h = host.encode()
    req = b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", port)
    s.sendall(req)
    rep = s.recv(4)
    if len(rep) < 4 or rep[1] != 0:
        raise RuntimeError(f"socks connect failed rep={rep[1] if len(rep)>1 else '?'}")
    atyp = rep[3]
    if atyp == 1: s.recv(4)
    elif atyp == 4: s.recv(16)
    else: s.recv(s.recv(1)[0])
    s.recv(2)
    return s

if __name__ == "__main__":
    s = connect("swalithlzqougtis5.internal", 80)
    s.settimeout(2)
    import sys
    buf = b""
    try:
        while True:
            d = s.recv(4096)
            if not d: break
            buf += d
    except socket.timeout:
        pass
    sys.stdout.write(buf.decode("utf-8", "replace"))
