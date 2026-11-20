"""题目复盘库（CASELIB）：把每题的记录沉淀成可检索的打法库。

默认行为
--------
`python -m framework.cli close <code>` 会在释放容器后**自动**调用 `archive(code)`：
  * 有记录且已解出 → `docs/CASELIB/<family>/<code>.md`
  * 有记录但没解出 → `docs/CASELIB/unsolved/<code>.md`（环境缺陷、死路同样是情报）
  * 没有任何记录 → 静默跳过，不报错、不阻断 close

布局
----
    docs/CASELIB/INDEX.md              自动生成的总索引（按 family 分组）
    docs/CASELIB/<family>/<code>.md    规范化打法笔记（带 front matter，可被索引重建）
    docs/CASELIB/_raw/<code>.md        原始记录留档（黑板 / FINDINGS.md），证据链

记录来源（按优先级）
--------------------
    1. work/bb_<code>.md         双工黑板（prompts/BLACKBOARD.template.md 格式）
    2. work/<code>/FINDINGS.md   手工复盘
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
WORK = ROOT / "work"
LIB = ROOT / "docs" / "CASELIB"

_FAMILY_BAD = re.compile(r"[^a-z0-9_.-]+")
_SEC = re.compile(r"^##\s+([A-Z_]+)", re.M)


# ---------------------------------------------------------------- 解析
def parse_sections(text: str) -> dict[str, str]:
    """把 markdown 按 `## NAME` 切段，返回 {NAME: body}。"""
    text = text or ""
    marks = list(_SEC.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end():end].strip()
    return out


def parse_kv(body: str) -> dict[str, str]:
    """抓 `- key: value` 形式的行，返回小写 key 的字典。"""
    kv: dict[str, str] = {}
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("-*").strip()
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().lower()
            if k and len(k) < 24:
                kv[k] = v.strip()
    return kv


def _slug(s: str) -> str:
    s = _FAMILY_BAD.sub("-", (s or "").strip().lower()).strip("-.")
    return s[:48] or "unclassified"


# 漏洞族关键词：只在结构化 INTEL/RESULT 里匹配（全文匹配会把“不是 SSTI”这类否定句误判）
_VULN_HINTS = (
    ("dalgona", "dalgona-xss"), ("ssti", "django-ssti"), ("django", "django-ssti"),
    ("ssrf", "ssrf"), ("fastapi", "fastapi-jobs"), ("ping", "ping-injection"),
    ("lfi", "lfi"), ("jwt", "jwt-none"), ("static", "static-paths"),
)

# 技术栈关键词：可用于全文回退猜测（命中的是“用了什么”，不受结论正负影响）
_STACK_HINTS = (
    ("bottle", "bottle"), ("flask", "flask"), ("werkzeug", "flask"),
    ("next.js", "nextjs-rsc"), ("nextjs", "nextjs-rsc"), ("react server", "nextjs-rsc"),
    ("smartbi", "smartbi"), ("spring", "spring"), ("tomcat", "tomcat"),
    ("thinkphp", "thinkphp"), ("laravel", "laravel"), ("fastapi", "fastapi"),
    ("apache", "apache"), ("nginx", "nginx"), ("express", "express"), ("gin", "gin"),
)


def _family(intel: dict, res: dict, override: str | None, raw: str = "") -> str:
    if override:
        return _slug(override)
    for src in (intel, res):
        for k in ("疑似漏洞族", "指纹族", "family", "漏洞族"):
            if src.get(k):
                # 允许 "dalgona-xss（依据: ...）"，只取括号前
                return _slug(re.split(r"[（(]", src[k])[0])
    fp = (intel.get("指纹", "") + " " + res.get("family", "") + " "
          + intel.get("已确认事实", "")).lower()
    for kw, name in _VULN_HINTS:
        if kw in fp:
            return name
    for kw, name in _STACK_HINTS:
        if kw in fp:
            return name
    # 无结构化段（如手工 FINDINGS.md）→ 扫全文猜技术栈（不用漏洞族，避免被否定句带偏）
    low = (raw or "").lower()
    for kw, name in _STACK_HINTS:
        if kw in low:
            return name
    return "unclassified"


def find_source(code: str, workdir: Path | None = None) -> Path | None:
    workdir = Path(workdir or WORK)
    for cand in (workdir / f"bb_{code}.md", workdir / code / "FINDINGS.md"):
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    return None


# ---------------------------------------------------------------- 归档
def archive(code: str, workdir: Path | None = None, libdir: Path | None = None,
            family: str | None = None, quiet: bool = False) -> Path | None:
    """把一题的记录沉淀进 CASELIB。返回写入的笔记路径；无记录返回 None。

    永不抛异常：归档失败不能影响 close 释放容器。
    """
    try:
        return _archive(code, workdir, libdir, family)
    except Exception as e:  # noqa: BLE001 - 故意吞掉，只告警
        if not quiet:
            print(f"[caselib] 归档失败（不影响 close）: {type(e).__name__}: {e}")
        return None


def _archive(code, workdir, libdir, family) -> Path | None:
    workdir = Path(workdir or WORK)
    libdir = Path(libdir or LIB)
    src = find_source(code, workdir)
    if not src:
        return None

    raw = src.read_text(encoding="utf-8", errors="replace")
    secs = parse_sections(raw)
    kv = parse_kv(secs.get("META", ""))
    intel = parse_kv(secs.get("INTEL", ""))
    res = parse_kv(secs.get("RESULT", ""))
    fam = _family(intel, res, family, raw)

    status = (res.get("status") or kv.get("status") or "").strip().lower()
    if not status:
        status = "solved" if res.get("flag") else "recorded"
    bucket = fam if status == "solved" else ("unsolved" if status == "recorded" else status)

    top = _repo_root(src)
    addr = (kv.get("addr") or kv.get("host") or intel.get("端口", "")
            or _guess_addr(raw))
    structured = bool(secs.get("INTEL") or secs.get("TRIED") or secs.get("RESULT"))
    note_path = libdir / bucket / f"{code}.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    (libdir / "_raw").mkdir(parents=True, exist_ok=True)

    raw_copy = libdir / "_raw" / f"{code}.md"
    shutil.copyfile(src, raw_copy)

    res_flag = (res.get("flag") or "").strip()
    raw_flag = res_flag if res_flag.upper() != "UNKNOWN" else ""
    masked = (res_flag[:5] + "***" + res_flag[-1]) if len(res_flag) > 6 else "***"
    note_text = _redact(
        _render_note(code, fam, status, kv, res, secs, addr, src, raw_copy, top,
                     structured=structured, raw=raw),
        raw_flag, masked,
    )
    note_path.write_text(note_text, encoding="utf-8")

    # 黑板是临时文件，归档后删掉；work/<code>/FINDINGS.md 保留（同目录还有 exp 脚本）
    if src.name.startswith("bb_"):
        try:
            src.unlink()
        except OSError:
            pass
    try:
        rebuild_index(libdir)
    except Exception:  # noqa: BLE001
        pass
    return note_path


def _repo_root(src: Path) -> Path:
    """找到 work 的上级（$PI_WORKBENCH），用于展示相对路径。"""
    for p in [src, *src.parents]:
        if p.name == "work":
            return p.parent
    return src.parent


def _guess_addr(text: str) -> str:
    m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})\b", text or "")
    return m.group(1) if m else ""


def _bullets(body: str, keep: int = 40) -> str:
    lines = [ln.rstrip() for ln in (body or "").splitlines()
             if ln.strip().startswith(("-", "*"))]
    return "\n".join(lines[:keep]) or "_(空)_"


def _render_note(code, fam, status, kv, res, secs, addr, src, raw_copy, top,
                 structured: bool = True, raw: str = "") -> str:
    tried = secs.get("TRIED", "")
    n_tried = len([ln for ln in tried.splitlines() if ln.strip().startswith("-")])
    flag = (res.get("flag") or "").strip()
    # 库要能被复用/分享：flag 原值只留在 _raw，笔记里（含 RESULT 段落）全部脱敏
    raw_flag = flag if flag.upper() != "UNKNOWN" else ""
    masked = (flag[:5] + "***" + flag[-1]) if len(flag) > 6 else "***"
    if raw_flag:
        flag = masked
    try:
        rel_raw = raw_copy.relative_to(top).as_posix()
    except ValueError:
        rel_raw = raw_copy.as_posix()

    fm = [
        "---",
        f"code: {code}",
        f"family: {fam}",
        f"status: {status}",
        f"difficulty: {kv.get('difficulty', '?')}",
        f"score: {kv.get('score', '?')}",
        f"addr: {addr or '?'}",
        f"date: {_dt.date.today().isoformat()}",
        f"tried: {n_tried}",
        f"source: {src.name}",
        "---",
    ]
    if structured:
        core = [
            "## 指纹与入口", "", _bullets(secs.get("INTEL", "")),
            "", "## 打法链（按时间序，可直接照抄）", "", _bullets(tried),
            "", "## 结论与复盘", "",
            _bullets(secs.get("RESULT", "")) if secs.get("RESULT") else "_(无 RESULT 段)_",
            "", "## 待补侦察", "",
            _bullets(secs.get("NEED_RECON", "")) if secs.get("NEED_RECON") else "_(无)_",
        ]
    else:
        # 手工 FINDINGS.md 这类没有约定段名的记录：正文直接整段保留，别丢情报
        excerpt = raw.strip()
        if len(excerpt) > 6000:
            excerpt = excerpt[:6000] + "\n\n…（超长，全文见 _raw）"
        core = ["## 原始复盘（未结构化记录，正文即打法）", "", excerpt or "_(空)_"]

    body = [
        f"# {code} · {fam}",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 结果 | **{status}** |",
        f"| 难度/分值 | {kv.get('difficulty', '?')} / {kv.get('score', '?')} |",
        f"| 目标 | {addr or '?'} |",
        f"| 尝试次数 | {n_tried} |",
        f"| flag | `{flag or '—'}`（已脱敏，原值见 `_raw`） |",
        "",
        *core,
        "",
        "## 原始记录（含原始报错证据）",
        "",
        f"`{rel_raw}`",
        "",
    ]
    return "\n".join(fm) + "\n" + "\n".join(body)


def _redact(text: str, flag: str, masked: str) -> str:
    """把正文里出现的真实 flag 换成脱敏值（RESULT 段落也会带一份原值）。"""
    if flag and masked and flag in text:
        return text.replace(flag, masked)
    return text


# ---------------------------------------------------------------- 索引
def _read_fm(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    fm, _, _ = text[3:].partition("---")
    out = {}
    for line in fm.splitlines():
        k, _, v = line.partition(":")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def rebuild_index(libdir: Path | None = None) -> Path:
    libdir = Path(libdir or LIB)
    rows: list[tuple[str, dict, str]] = []
    for p in sorted(libdir.glob("*/*.md")):
        if p.parent.name == "_raw":
            continue
        fm = _read_fm(p)
        if fm:
            rows.append((p.parent.name, fm, p.relative_to(libdir).as_posix()))

    by_fam: dict[str, list[tuple[dict, str]]] = {}
    for fam, fm, rel in rows:
        by_fam.setdefault(fam, []).append((fm, rel))

    flat = [fm for v in by_fam.values() for fm, _ in v]
    solved = sum(1 for f in flat if f.get("status") == "solved")

    out = [
        "# CASELIB · 打法库索引",
        "",
        "> 自动生成，勿手改（`python -m framework.cli archive <code>` 重建）。",
        "> 每题沉淀：指纹、打法链、原始报错；`_raw/` 是未加工证据。",
        "",
        f"共 **{len(rows)}** 条记录（解出 **{solved}** 条），覆盖 **{len(by_fam)}** 个族。",
        "",
    ]
    for fam in sorted(by_fam):
        out += [f"## {fam}（{len(by_fam[fam])}）", "",
                "| code | 结果 | 难度 | 目标 | 日期 | 笔记 |",
                "|---|---|---|---|---|---|"]
        for fm, rel in sorted(by_fam[fam], key=lambda x: x[0].get("date", ""), reverse=True):
            out.append(
                f"| `{fm.get('code', '?')}` | {fm.get('status', '?')} | {fm.get('difficulty', '?')} "
                f"| {(fm.get('addr') or '?')[:28]} | {fm.get('date', '?')} | [{rel}]({rel}) |"
            )
        out.append("")
    libdir.mkdir(parents=True, exist_ok=True)
    idx = libdir / "INDEX.md"
    idx.write_text("\n".join(out), encoding="utf-8")
    return idx


if __name__ == "__main__":
    import sys
    for c in sys.argv[1:]:
        p = archive(c)
        print(f"{c}: {p or '无记录，跳过'}")
