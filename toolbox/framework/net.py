"""HTTP 原语：会话、原始报文、并发 map。

为什么不用 requests：
  * 靶场经常需要"保留原签名的伪造 cookie / 畸形 header / 非标准 method"
  * 有些题要 http.client 级别的 keep-alive、逐字节控制
  * 只依赖标准库，任何环境都能跑
"""
from __future__ import annotations

import http.client
import re
import socket
import ssl
import urllib.parse
from concurrent.futures import ThreadPoolExecutor


class Response:
    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status, headers, body, url=""):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        import json
        return json.loads(self.body)

    def brief(self, n=300):
        """探测阶段用：只回显状态码 + 截断正文，避免大 HTML 烧上下文。"""
        return f"{self.status} {len(self.body)}B :: {self.text[:n]}"

    def __repr__(self):
        return f"<Response {self.status} {len(self.body)}B {self.url}>"


class Session:
    """极简会话：维护 cookie、默认 header、可选 http/https。"""

    def __init__(self, host, port=80, scheme="http", timeout=15, headers=None):
        self.host, self.port, self.scheme = host, port, scheme
        self.timeout = timeout
        self.cookies = {}
        self.headers = {"User-Agent": "Mozilla/5.0 (pi-framework)", **(headers or {})}
        self._conn = None

    # ---- 连接管理 ----
    def _new_conn(self):
        if self.scheme == "https":
            ctx = ssl._create_unverified_context()
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ---- 请求 ----
    def request(self, method, path, body=None, headers=None, follow=False, raw_target=None):
        hdrs = dict(self.headers)
        if self.cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None and not isinstance(body, (bytes, bytearray)):
            body = urllib.parse.urlencode(body).encode() if isinstance(body, dict) else str(body).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if headers:
            hdrs.update(headers)
        target = raw_target if raw_target is not None else path
        conn = self._new_conn()
        try:
            conn.request(method, target, body=body, headers=hdrs)
            r = conn.getresponse()
            data = r.read()
            # 注意：dict(getheaders()) 会把多条 Set-Cookie 折叠成一条，导致
            # 「session + acl」双 cookie 的站点登录状态丢失。这里保留全部。
            _hdr_list = r.getheaders()
            _hdrs = dict(_hdr_list)
            _cookies = [v for k, v in _hdr_list if k.lower() == "set-cookie"]
            if len(_cookies) > 1:
                _hdrs["set-cookie"] = _cookies
            resp = Response(r.status, _hdrs, data, target)
        finally:
            if not follow:
                conn.close()
            else:
                self._conn = conn
        _sc = resp.headers.get("set-cookie")
        if isinstance(_sc, str):
            _sc = [_sc]
        for v in (_sc or []):
            m = re.match(r"\s*([^=]+)=([^;]*)", v)
            if m:
                self.cookies[m.group(1).strip()] = m.group(2)
        return resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)


def raw_exchange(host, port, payload: bytes, timeout=15, read_timeout=5.0):
    """发送原始字节（请求走私、畸形 header 场景），返回全部响应字节。"""
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.sendall(payload)
        s.settimeout(read_timeout)
        chunks = []
        while True:
            try:
                b = s.recv(65535)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        s.close()


def pmap(fn, items, workers=12):
    """并发映射，异常吞掉返回 None —— 渗透场景里失败是常态。"""
    def safe(x):
        try:
            return fn(x)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(safe, items))
