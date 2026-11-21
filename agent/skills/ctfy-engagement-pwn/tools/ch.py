"""Interactive client for the clean-room service (via SOCKS tunnel)."""
import socket, time, sys
from socks import connect

HOST, PORT = "swalithlzqougtis5.internal", 80

class Chal:
    def __init__(self, host=HOST, port=PORT, timeout=8.0):
        self.s = connect(host, port, timeout=timeout)
        self.buf = b""
        self.s.settimeout(0.5)
        self.read_until(b"> ")          # banner + first menu

    # --- low level -------------------------------------------------
    def _fill(self, deadline):
        while time.time() < deadline:
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                continue
            if not d:
                self.eof = True
                return
            self.buf += d

    def read_until(self, tok, timeout=6.0):
        end = time.time() + timeout
        while tok not in self.buf and time.time() < end:
            self._fill(time.time() + 0.3)
        i = self.buf.find(tok)
        if i < 0:
            out, self.buf = self.buf, b""
            return out
        out = self.buf[: i + len(tok)]
        self.buf = self.buf[i + len(tok):]
        return out

    def read_idle(self, idle=0.5, total=6.0):
        end = time.time() + total
        last = time.time()
        while time.time() < end:
            n = len(self.buf)
            self._fill(time.time() + idle)
            if len(self.buf) == n:
                break
            end = time.time() + total
        out, self.buf = self.buf, b""
        return out

    def send(self, data, nl=True):
        if isinstance(data, str): data = data.encode()
        self.s.sendall(data + (b"\n" if nl else b""))

    def cmd(self, c, tok=b"> "):
        self.send(c)
        return self.read_until(tok)

    # --- protocol --------------------------------------------------
    def menu(self):
        return self.read_until(b"> ")

    def create_text(self, n):
        self.send("1")
        self.read_until(b"length: ")
        self.send(str(n))
        return self.read_until(b"created text at")

    def create_buf(self):
        return self.cmd("2", b"created buffer at")

    def select(self, i, tok=b"selected"):
        self.send("3")
        self.read_until(b"slot: ")
        self.send(str(i))
        return self.read_until(tok)

    def delete(self, i):
        self.send("5")
        self.read_until(b"slot: ")
        self.send(str(i))
        return self.read_idle(0.4, 3)

    def edit_text(self, newlen, data):
        self.send("4")
        head = self.read_until(b"length: ")      # "text[old]: ...\nlength: "
        self.send(str(newlen))
        self.read_until(b"data: ")
        self.send(data, nl=False)
        tail = self.read_until(b"> ")
        return head, tail

    def close(self):
        try: self.s.close()
        except Exception: pass

if __name__ == "__main__":
    c = Chal()
    print(repr(c.buf))
