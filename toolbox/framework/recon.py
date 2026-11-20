"""指纹识别：把"首页长什么样"映射到"该上哪套打法"。

原则：
  1. 只用 3-5 个请求完成判别（首页 / Server 头 / openapi / 常见 404 特征）
  2. 判别结果必须可解释（返回 evidence），便于踩坑复盘
  3. 判错比判慢更糟：不确定时返回 'unknown'，交给通用 playbook 轮询
"""
from __future__ import annotations

import re

from . import net

# 打法族（与 playbooks 里的函数名对应）
FAMILIES = (
    "dalgona_xss",      # 标题 Dalgona / alert 检测回显
    "django_ssti",      # /greet/?name= + DEBUG 页
    "fastapi_jobs",     # uvicorn + /jobs 或 /company/{id}/jobs
    "ssrf_internal",    # 页面出现 internal-service
    "php_static_flag",  # Apache + /static/ 目录列表
    "php_login",        # PHP 登录表单（含 admin.php?）
    "ping_tool",        # 命令注入常见载体
    "graphql",          # /graphql 或 {"data":{...}}
    "wordpress",        # wp-content / wp-json
    "upload",           # 存在 file input
    "unknown",
)


def _title(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return m.group(1).strip() if m else ""


def fingerprint(host, port=80, scheme="http", timeout=10):
    s = net.Session(host, port, scheme, timeout=timeout)
    ev = {"host": f"{host}:{port}", "server": "", "title": "", "notes": []}
    fam = set()
    try:
        r = s.get("/")
        ev["server"] = r.headers.get("Server", "")
        ev["powered"] = r.headers.get("X-Powered-By", "")
        html = r.text
        ev["title"] = _title(html)
        low = html.lower()

        if "dalgona" in low:
            fam.add("dalgona_xss")
        if "internal-service" in low or "internal network" in low:
            fam.add("ssrf_internal")
        if 'type="file"' in low or 'enctype="multipart/form-data"' in low:
            fam.add("upload")
        if re.search(r'name=["\'](host|ip|target|url|domain)["\']', low) and "ping" in low:
            fam.add("ping_tool")
        if "<form" in low and 'type="password"' in low:
            fam.add("php_login" if ("php" in ev["powered"].lower() or "php" in ev["server"].lower()) else "web_login")
        if "demo" in low and "password" in low:
            ev["notes"].append("demo/demo 提示")

        # FastAPI: /openapi.json 存在且含 /jobs 之类
        try:
            o = s.get("/openapi.json")
            if o.status == 200 and b'"openapi"' in o.body:
                paths = list(o.json().get("paths", {}).keys())
                ev["paths"] = paths
                if any("jobs" in p for p in paths):
                    fam.add("fastapi_jobs")
                if any("graphql" in p for p in paths):
                    fam.add("graphql")
                ev["notes"].append("fastapi openapi")
        except Exception:
            pass

        # Django: 400 DisallowedHost / DEBUG 页
        if "DisallowedHost" in html or "django" in low:
            fam.add("django_ssti")
            ev["notes"].append("django")

        # WordPress
        for p in ("/wp-json/", "/wp-login.php"):
            try:
                if s.get(p).status in (200, 301, 302):
                    fam.add("wordpress")
                    break
            except Exception:
                pass

        # Apache /static/ 目录列表（403 的 flag 文件也可从这里发现）
        try:
            st = s.get("/static/")
            if st.status == 200 and "Index of" in st.text:
                ev["notes"].append("static dir listing")
                fam.add("php_static_flag")
        except Exception:
            pass

    except Exception as e:
        ev["notes"].append(f"probe_error: {e}")
    finally:
        s.close()

    if not fam:
        fam.add("unknown")
    ev["families"] = sorted(fam)
    return ev


def guess_flag_in(text):
    m = re.search(r"flag\{[^}]{1,200}\}", text or "")
    return m.group(0) if m else None


def score_text(text):
    """判断"这个响应是不是已经把 flag 吐出来了"，返回 (hit, flag)。"""
    if not text:
        return False, None
    f = guess_flag_in(text)
    return (f is not None), f
