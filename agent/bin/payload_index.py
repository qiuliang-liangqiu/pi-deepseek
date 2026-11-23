#!/usr/bin/env python3
"""payload_index.py — 小而准、可验证的 Web payload 索引。

设计目标（与“大而全的 payload 库”相反）：
  - 每条只回答四件事：触发信号 / 最小载荷 / 期望的 oracle / 失败后换什么
  - 每条都有 control（负对照），避免把“页面本来就这样”当成命中
  - 每条都标 stack、placement、encoding、risk，防止“语法对、环境错”
  - 源文件是 JSONL；Markdown 由本脚本生成，不手改

用法：
  python payload_index.py list [--class sqli] [--risk read]
  python payload_index.py show sqli-auth-01
  python payload_index.py search "注释"
  python payload_index.py curl sqli-auth-01 --target http://127.0.0.1:8080/login
  python payload_index.py render [--out PATH] [--check]
  python payload_index.py validate
  python payload_index.py selftest
  python payload_index.py stats

默认索引：~/.pi/agent/skills/ctf/ctf-web/payload-index.jsonl
对应文档：~/.pi/agent/skills/ctf/ctf-web/payload-index.md
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import sys
import urllib.parse
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AGENT = Path(__file__).resolve().parent.parent
DEFAULT_JSONL = AGENT / "skills" / "ctf" / "ctf-web" / "payload-index.jsonl"
DEFAULT_MD = DEFAULT_JSONL.with_suffix(".md")

CLASS_ORDER = [
    "sqli", "ssti", "xxe", "lfi", "deser",
    "ssrf", "upload", "jwt", "cmdi",
]
RISKS = {"read", "auth", "exec", "callback", "upload"}
ENCODINGS = {"raw", "url", "double-url", "utf-16le-bom", "base64", "json"}
KINDS = {"payload", "proc"}
VERIFIED = {"run", "lab", "std"}

REQUIRED = [
    "id", "class", "title", "signal", "stack", "placement",
    "payload", "oracle", "control", "pivot", "verify", "risk",
    "kind", "verified",
]

# 只拦“破坏性/持久化/反弹 shell”，不拦 CTF 里正常的读文件与命令执行探针。
DANGEROUS = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r"mkfs(?:\.\w+)?\s",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\b(?:bash|sh)\s+-i\b",
    r"\b(?:nc|ncat)\s+(?:.*\s)?-e\s",
    r"(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b",
    r"chmod\s+777\s+/(?:\s|$)",
    r"\buseradd\b",
    r"\bcrontab\s+-",
    r"\bshutdown\b|\breboot\b",
    r">\s*/dev/sd[a-z]",
    r"nc\s+-l\s+-p\s+\d+\s+-e",
]


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(AGENT))
    except ValueError:
        return str(path)


def load(path: Path):
    entries, errors = [], []
    if not path.exists():
        return [], [f"not found: {path}"]
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            e = json.loads(s)
        except json.JSONDecodeError as ex:
            errors.append(f"{path.name}:{n}: JSON error: {ex}")
            continue
        if not isinstance(e, dict):
            errors.append(f"{path.name}:{n}: not an object")
            continue
        e["_line"] = n
        entries.append(e)
    return entries, errors


def validate(entries, errors):
    seen = set()
    for e in entries:
        loc = f"line {e.get('_line', '?')} id={e.get('id', '?')}"
        for k in REQUIRED:
            if k not in e or not str(e.get(k, "")).strip():
                errors.append(f"{loc}: missing/empty field: {k}")
        eid = str(e.get("id", ""))
        cls = str(e.get("class", ""))
        if eid in seen:
            errors.append(f"{loc}: duplicate id")
        seen.add(eid)
        if cls and not eid.startswith(cls + "-"):
            errors.append(f"{loc}: id should start with '{cls}-'")
        if cls and cls not in CLASS_ORDER:
            errors.append(f"{loc}: unknown class '{cls}'")
        if e.get("risk") not in RISKS:
            errors.append(f"{loc}: risk must be one of {sorted(RISKS)}")
        if e.get("encoding", "raw") not in ENCODINGS:
            errors.append(f"{loc}: encoding must be one of {sorted(ENCODINGS)}")
        if e.get("kind") not in KINDS:
            errors.append(f"{loc}: kind must be one of {sorted(KINDS)}")
        if e.get("verified") not in VERIFIED:
            errors.append(f"{loc}: verified must be one of {sorted(VERIFIED)}")
        if e.get("verified") in ("run", "lab") and not str(e.get("evidence", "")).strip():
            errors.append(f"{loc}: verified={e.get('verified')} requires evidence")
        payload = str(e.get("payload", ""))
        if len(payload) > 2000:
            errors.append(f"{loc}: payload > 2000 chars")
        low = payload.lower()
        for pat in DANGEROUS:
            if re.search(pat, low, re.I):
                errors.append(f"{loc}: destructive pattern blocked: {pat}")
        if "<CALLBACK>" in payload and "CALLBACK" not in str(e.get("oracle", "")).upper():
            errors.append(f"{loc}: payload uses <CALLBACK> but oracle does not mention it")
    return errors


def die(errors):
    for x in errors:
        print("ERROR", x, file=sys.stderr)
    print(f"\n{len(errors)} error(s)", file=sys.stderr)
    return 1


def find(entries, eid):
    for e in entries:
        if e.get("id") == eid:
            return e
    return None


def cmd_list(entries, a):
    rows = [e for e in entries if (not a.cls or e.get("class") == a.cls)
            and (not a.risk or e.get("risk") == a.risk)
            and (not a.verified or e.get("verified") == a.verified)]
    for e in rows:
        tag = e.get("verified") or "std"
        if e.get("kind") == "proc":
            tag += "/proc"
        print(f"{e['id']:22} {e.get('risk', '?'):8} {tag:9} {e.get('title', '')}")
    print(f"\n{len(rows)} entries")


def cmd_show(entries, a):
    e = find(entries, a.id)
    if not e:
        print(f"not found: {a.id}", file=sys.stderr)
        return 2
    print(f"id        : {e['id']}")
    print(f"class     : {e['class']}    risk: {e.get('risk')}    encoding: {e.get('encoding', 'raw')}")
    print(f"kind      : {e.get('kind')}    verified: {e.get('verified')}")
    print(f"title     : {e['title']}")
    print(f"signal    : {e['signal']}")
    print(f"stack     : {e['stack']}")
    print(f"placement : {e['placement']}")
    print("payload   :")
    for line in str(e["payload"]).splitlines() or [""]:
        print("  | " + line)
    print(f"oracle    : {e['oracle']}")
    if e.get("oracle_value"):
        print(f"oracle_val: {e['oracle_value']}")
    print(f"control   : {e['control']}")
    print(f"pivot     : {e['pivot']}")
    print(f"verify    : {e['verify']}")
    if e.get("ref"):
        print(f"ref       : {e['ref']}")
    if e.get("evidence"):
        print(f"evidence  : {e['evidence']}")
    if e.get("notes"):
        print(f"notes     : {e['notes']}")
    return 0


def _encoded_payload(e):
    p = str(e["payload"])
    enc = e.get("encoding", "raw")
    if enc == "url":
        return urllib.parse.quote(p, safe="")
    if enc == "double-url":
        return urllib.parse.quote(urllib.parse.quote(p, safe=""), safe="")
    if enc == "base64":
        return base64.b64encode(p.encode("utf-8")).decode()
    return p


def cmd_curl(entries, a):
    e = find(entries, a.id)
    if not e:
        print(f"not found: {a.id}", file=sys.stderr)
        return 2
    p = _encoded_payload(e)
    target = a.target
    pl = str(e.get("placement", "body"))
    if e.get("class") == "jwt":
        print("# JWT：payload 是 header/claim 的修改点，不是完整的 Authorization 值")
        print("# 先生成/重签 token，再发：curl -sS <target> -H 'Authorization: Bearer <token>'")
        print("# 修改点：", p)
        print()
        print("# oracle :", e["oracle"])
        print("# control:", e["control"])
        print("# verify :", e["verify"])
        return 0
    if e.get("encoding") == "utf-16le-bom":
        print("# utf-16le-bom：先编码再发原始字节；不要用 curl 的字符串参数")
        print("#   python -c \"import sys; sys.stdout.buffer.write(open('body.xml','rb').read().encode('utf-16'))\" > body.bin")
        print(f"#   curl -sS -X POST {shlex.quote(target)} --data-binary @body.bin")
    elif pl.startswith("param:"):
        name = pl.split(":", 1)[1]
        sep = "&" if "?" in target else "?"
        url = target + sep + urllib.parse.quote(name) + "=" + urllib.parse.quote(p, safe="")
        print(f"curl -sS --path-as-is {shlex.quote(url)}")
    elif pl.startswith("body:"):
        name = pl.split(":", 1)[1]
        print(f"curl -sS -X POST {shlex.quote(target)} --data-urlencode {shlex.quote(name + '=' + p)}")
    elif pl.startswith("json:"):
        print(f"curl -sS -X POST {shlex.quote(target)} -H 'Content-Type: application/json' --data-binary {shlex.quote(p)}")
    elif pl.startswith("header:"):
        name = pl.split(":", 1)[1]
        print(f"curl -sS {shlex.quote(target)} -H {shlex.quote(name + ': ' + p)}")
    elif pl.startswith("cookie:"):
        name = pl.split(":", 1)[1]
        print(f"curl -sS {shlex.quote(target)} -H {shlex.quote('Cookie: ' + name + '=' + p)}")
    elif pl.startswith("filename"):
        print("# multipart 上传；文件内容按 notes 准备，filename 用下面的值")
        print(f"curl -sS -X POST {shlex.quote(target)} -F {shlex.quote('file=@<local-file>;filename=' + p)}")
    else:
        print(f"curl -sS -X POST {shlex.quote(target)} --data-binary {shlex.quote(p)}")
    print()
    print("# oracle :", e["oracle"])
    print("# control:", e["control"])
    print("# verify :", e["verify"])
    return 0


def cmd_search(entries, a):
    q = a.term.lower()
    hits = 0
    for e in entries:
        fields = []
        for k, v in e.items():
            if k.startswith("_"):
                continue
            if q in str(v).lower():
                fields.append(k)
        if fields:
            hits += 1
            print(f"{e['id']:22} {e.get('title', '')}  [{', '.join(fields)}]")
    print(f"\n{hits} hit(s) for {a.term!r}")
    return 0 if hits else 1


def cmd_stats(entries, a):
    from collections import Counter
    c = Counter(e.get("class") for e in entries)
    r = Counter(e.get("risk") for e in entries)
    v = Counter(e.get("verified") for e in entries)
    k = Counter(e.get("kind") for e in entries)
    print(f"entries: {len(entries)}")
    print("by class:", ", ".join(f"{k0}={c.get(k0, 0)}" for k0 in CLASS_ORDER))
    print("by risk :", ", ".join(f"{k0}={r.get(k0, 0)}" for k0 in sorted(RISKS)))
    print("by verified:", ", ".join(f"{k0}={v.get(k0, 0)}" for k0 in sorted(VERIFIED)))
    print("by kind    :", ", ".join(f"{k0}={k.get(k0, 0)}" for k0 in sorted(KINDS)))


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", "<br>").replace("\r", "")


def short(s, n=72):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def code(s):
    """Render a table-safe code span; keep newlines visible as \\n."""
    s = str(s).replace("\r", "").replace("\n", "\\n").replace("|", "\\|")
    if "`" in s:
        return "`` " + s + " ``"
    return "`" + s + "`"


def render_md(entries):
    from collections import Counter
    c = Counter(e.get("class") for e in entries)
    out = []
    out.append("# Web Payload Index（小而准 / 可验证）")
    out.append("")
    out.append("> 由 `bin/payload_index.py render` 从 `payload-index.jsonl` 生成，**不要手改本文件**。")
    out.append("> 这不是 PayloadsAllTheThings 的替代品；它只保留每条都能回答“怎么验”的最小条目。")
    out.append("")
    out.append("## 使用协议（4 步，缺一不可）")
    out.append("")
    out.append("1. **先抓基线**：正常请求的状态码、长度、跳转、关键字。没有基线就没有 oracle。")
    out.append("2. **只发最小载荷**：一次只改一个变量；先证明“模板被求值/文件被读到/命令被执行”，再谈利用链。")
    out.append("3. **看 oracle**：必须是可观测差异（49、`root:x:0:0`、`uid=`、+3s、回调、状态码）。")
    out.append("4. **做 control**：负对照必须不命中。control 也命中的差异，是页面噪声，不是漏洞。")
    out.append("")
    out.append("失败后的顺序：换编码 → 换分隔符/注释 → 换位置（参数/头/Cookie/文件名）→ 换协议层。")
    out.append("同层连续 3 次否定就下沉，不要在同一层继续加字典。")
    out.append("")
    out.append("条目后缀：**run** = 本轮真实题上验证过；**lab** = 本地 loopback lab 上 oracle+control 通过；**std** = 标准语法、未在本轮复测；**proc** = 需要生成 token/文件或多步 setup。")
    out.append("")
    out.append("## 统计")
    out.append("")
    out.append("| 类 | 条数 | 说明 |")
    out.append("|---|---:|---|")
    desc = {
        "sqli": "SQL 注入：认证绕过、布尔/报错/时间/UNION、NoSQL",
        "ssti": "模板注入：Jinja2/Twig/Freemarker/Velocity/ERB/Smarty/Thymeleaf",
        "xxe": "XML 外部实体：文件读取、UTF-16 绕 WAF、OOB、SVG/SOAP",
        "lfi": "本地文件包含：遍历、strip/编码、filter、日志/data/phar",
        "deser": "反序列化：PHP/Java/Python/Node/YAML/.NET 的检测与最小验证",
        "ssrf": "SSRF：IP 变形、元数据、gopher、allowlist 绕过",
        "upload": "文件上传：后缀/内容类型/魔术字节/.htaccess/路径穿越",
        "jwt": "JWT：none、弱密钥、算法混淆、jku/x5u、kid、claim",
        "cmdi": "命令注入：分隔符、换行、子 shell、空格绕过、编码",
    }
    for cls in CLASS_ORDER:
        if c.get(cls):
            out.append(f"| `{cls}` | {c[cls]} | {desc.get(cls, '')} |")
    out.append("")
    for cls in CLASS_ORDER:
        rows = [e for e in entries if e.get("class") == cls]
        if not rows:
            continue
        out.append(f"## {cls}")
        out.append("")
        out.append("| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |")
        out.append("|---|---|---|---|---|---|")
        for e in rows:
            out.append(
                "| `{}` | {} | {} | {} | {} | {} |".format(
                    esc(e["id"]), esc(e["title"]) +
                    (" · **run**" if e.get("verified") == "run" else (" · **lab**" if e.get("verified") == "lab" else " · std")) +
                    (" · proc" if e.get("kind") == "proc" else ""),
                    esc(e["signal"]),
                    code(short(e["payload"], 88)), esc(e["oracle"]),
                    esc(e["control"]) + "<br>→ " + esc(e["pivot"]),
                )
            )
        out.append("")
    out.append("## 详细字段")
    out.append("")
    out.append("```bash")
    out.append("python ~/.pi/agent/bin/payload_index.py show <id>")
    out.append("python ~/.pi/agent/bin/payload_index.py curl <id> --target <URL>  # 只打印请求，不发送")
    out.append("python ~/.pi/agent/bin/payload_index.py search <关键词>")
    out.append("python ~/.pi/agent/bin/payload_index.py validate   # 结构 + 破坏性模式")
    out.append("python ~/.pi/agent/bin/payload_index.py selftest   # 编码/JSON/oracle_value 自检")
    out.append("```")
    out.append("")
    out.append("每条详情都包含 `stack`（版本/框架假设）、`placement`（放哪）、`encoding`（怎么编）、")
    out.append("`verify`（怎么确认不是噪声）与 `ref`（本机对应笔记）。")
    out.append("")
    return "\n".join(out)


def cmd_render(entries, a):
    out = Path(a.out) if a.out else DEFAULT_MD
    text = render_md(entries)
    if a.check:
        old = out.read_text(encoding="utf-8") if out.exists() else ""
        if old != text:
            print(f"DIFF {out}", file=sys.stderr)
            return 1
        print(f"ok {out} ({len(entries)} entries)")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(entries)} entries)")
    return 0


def cmd_validate(entries, errors, a):
    errors = validate(entries, errors)
    if errors:
        return die(errors)
    print(f"ok {len(entries)} entries; schema, ids, risk, encoding, dangerous-pattern scan passed")
    return 0


def md_table_errors(md: str):
    """Catch broken tables (a payload pipe that was not escaped)."""
    errors, block = [], []
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            counts = []
            for row in block:
                cells = re.split(r"(?<!\\)\|", row)[1:-1]
                counts.append(len(cells))
            if len(set(counts)) > 1:
                errors.append(f"markdown table columns differ {counts}: {block[0][:70]}")
            block = []
    return errors


def cmd_selftest(entries, errors, a):
    errors = validate(entries, errors)
    rendered = render_md(entries)
    errors.extend(md_table_errors(rendered))
    for e in entries:
        eid = e.get("id", "?")
        enc = e.get("encoding", "raw")
        payload = str(e.get("payload", ""))
        if enc == "base64" and "<" not in payload:
            try:
                base64.b64decode(payload, validate=False)
            except Exception as ex:
                errors.append(f"{eid}: base64 decode failed: {ex}")
        if enc == "utf-16le-bom":
            try:
                payload.encode("utf-16")
            except Exception as ex:
                errors.append(f"{eid}: utf-16 encode failed: {ex}")
        if enc == "json" or str(e.get("placement", "")).startswith("json:"):
            body = payload
            if body.startswith("{") or body.startswith("["):
                try:
                    json.loads(body)
                except Exception as ex:
                    errors.append(f"{eid}: json placement but payload is not valid JSON: {ex}")
        ov = str(e.get("oracle_value", "")).strip()
        if ov and ov.lower() not in str(e.get("oracle", "")).lower():
            errors.append(f"{eid}: oracle_value {ov!r} not present in oracle text")
        if esc(eid) not in rendered:
            errors.append(f"{eid}: missing from rendered markdown")
    if errors:
        return die(errors)
    print(f"ok {len(entries)} entries; selftest passed (encoding/json/oracle_value/render/table columns)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="curated, verifiable web payload index")
    ap.add_argument("--file", default=str(DEFAULT_JSONL))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("--class", dest="cls"); p.add_argument("--risk"); p.add_argument("--verified")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("search"); p.add_argument("term")
    p = sub.add_parser("curl"); p.add_argument("id"); p.add_argument("--target", required=True)
    p = sub.add_parser("render"); p.add_argument("--out"); p.add_argument("--check", action="store_true")
    sub.add_parser("validate")
    sub.add_parser("selftest")
    sub.add_parser("stats")

    a = ap.parse_args()
    entries, errors = load(Path(a.file))
    if a.cmd == "validate":
        return cmd_validate(entries, errors, a)
    if a.cmd == "selftest":
        return cmd_selftest(entries, errors, a)
    if errors:
        return die(errors)
    if not entries:
        return die([f"no entries in {a.file}"])
    if a.cmd == "list":
        return cmd_list(entries, a)
    if a.cmd == "show":
        return cmd_show(entries, a)
    if a.cmd == "search":
        return cmd_search(entries, a)
    if a.cmd == "curl":
        return cmd_curl(entries, a)
    if a.cmd == "stats":
        return cmd_stats(entries, a)
    if a.cmd == "render":
        return cmd_render(entries, a)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
