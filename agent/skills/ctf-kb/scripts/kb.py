#!/usr/bin/env python3
"""ctf-kb: 在本地 CTF 知识库（skills/ctf/**/*.md）里做检索，避免整篇读大文件。

用法:
  python kb.py list                          # 域 + 文件数/体积
  python kb.py files <domain|regex>          # 列出文件
  python kb.py find "tcache|fastbin"         # 全文检索，输出 file:line + 最近标题
  python kb.py find "ssti" --domain ctf-web  # 限定域
  python kb.py headings <file>               # 列某文件的标题目录（带行号）
  python kb.py section <file> <heading-sub>  # 只打印某个小节（默认截断 6000 字符）
  python kb.py read <file> <start> <end>     # 按行号读片段（等价 read offset/limit）

约定:
  * <file> 可以是相对 pack 根的路径（如 ctf-web/sql-injection.md）或绝对路径。
  * 输出默认截断，防止一次灌爆上下文；--max-chars 可调。
  * 纯标准库，无网络。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
DEFAULT_ROOT = HERE.parents[2] / "ctf"  # skills/ctf-kb/scripts/kb.py -> skills/ctf


def root_dir(arg: str | None) -> Path:
    root = Path(arg).expanduser().resolve() if arg else DEFAULT_ROOT
    if not root.is_dir():
        sys.exit(f"[kb] pack root not found: {root}")
    return root


def iter_files(root: Path, domain: str | None = None):
    base = root
    if domain:
        cand = root / domain if (root / domain).is_dir() else None
        if cand is None:
            hits = [p for p in root.glob(f"*{domain}*") if p.is_dir()]
            if not hits:
                sys.exit(f"[kb] no domain matching {domain!r} under {root}")
            cand = hits[0]
        base = cand
    for p in sorted(base.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        yield p


def rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def heading_positions(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(index, level, text)] for real headings only (fenced code blocks skipped)."""
    out = []
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,4}) ", line)
        if m:
            out.append((i, len(m.group(1)), line.strip()))
    return out


def nearest_heading(text: str, lineno: int) -> str:
    """lineno is 0-based index into lines."""
    lines = text.splitlines()
    best = ""
    for i, _lvl, head in heading_positions(lines):
        if i <= lineno:
            best = head
        else:
            break
    return best


def cmd_list(root: Path, a) -> int:
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        files = list(iter_files(root, d.name))
        size = sum(f.stat().st_size for f in files)
        rows.append((d.name, len(files), size))
    w = max((len(r[0]) for r in rows), default=4)
    for name, n, size in rows:
        print(f"{name:<{w}}  {n:3d} files  {size/1024:8.1f} KB")
    total_f = sum(r[1] for r in rows)
    total_s = sum(r[2] for r in rows)
    print(f"{'TOTAL':<{w}}  {total_f:3d} files  {total_s/1024:8.1f} KB")
    return 0


def cmd_files(root: Path, a) -> int:
    domain = a.domain or a.pattern
    for p in iter_files(root, domain):
        print(f"{p.stat().st_size/1024:7.1f} KB  {rel(root, p)}")
    return 0


def cmd_find(root: Path, a) -> int:
    try:
        rx = re.compile(a.pattern, re.I)
    except re.error as e:
        sys.exit(f"[kb] bad regex: {e}")
    shown = 0
    hits_total = 0
    for p in iter_files(root, a.domain):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            hits_total += 1
            if shown >= a.limit:
                continue
            head = nearest_heading(text, i)
            snippet = line.strip()
            if len(snippet) > a.width:
                snippet = snippet[: a.width] + " …"
            print(f"{rel(root, p)}:{i+1}: [{head}] {snippet}")
            shown += 1
    print(f"[kb] {hits_total} hit(s), showing {shown} (use --limit/--width to widen)")
    return 0


def resolve_file(root: Path, name: str) -> Path:
    p = Path(name)
    if p.is_absolute() and p.is_file():
        return p
    cand = root / name
    if cand.is_file():
        return cand
    hits = [x for x in root.rglob(Path(name).name) if x.is_file()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"[kb] file not found: {name}")
    sys.exit("[kb] ambiguous, candidates:\n  " + "\n  ".join(rel(root, h) for h in hits[:20]))


def cmd_headings(root: Path, a) -> int:
    p = resolve_file(root, a.file)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, _lvl, head in heading_positions(lines):
        print(f"{i+1:6d}  {head}")
    return 0


def cmd_section(root: Path, a) -> int:
    p = resolve_file(root, a.file)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    pat = re.compile(re.escape(a.heading_sub), re.I)
    heads = heading_positions(lines)
    start = None
    level = None
    for i, lvl, text in heads:
        if pat.search(text):
            start, level = i, lvl
            break
    if start is None:
        sys.exit(f"[kb] no heading matching {a.heading_sub!r} in {a.file} (try `headings`)")
    end = len(lines)
    for j, lvl, _text in heads:
        if j > start and lvl <= level:
            end = j
            break
    out = "\n".join(lines[start:end])
    if len(out) > a.max_chars:
        out = out[: a.max_chars] + f"\n\n[kb] truncated at {a.max_chars} chars of {len(out)} — use `read {a.file} {start+1} <end>` for the rest"
    print(out)
    return 0


def cmd_read(root: Path, a) -> int:
    p = resolve_file(root, a.file)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    s = max(1, a.start)
    e = min(len(lines), a.end)
    for i in range(s, e + 1):
        print(f"{i:6d}  {lines[i-1]}")
    print(f"[kb] {rel(root,p)} lines {s}-{e} of {len(lines)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="kb.py", description="local CTF knowledge-base retrieval")
    ap.add_argument("--root", help="pack root (default: ../ctf)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    pf = sub.add_parser("files")
    pf.add_argument("pattern")

    pd = sub.add_parser("find")
    pd.add_argument("pattern")
    pd.add_argument("--domain")
    pd.add_argument("--limit", type=int, default=40)
    pd.add_argument("--width", type=int, default=160)

    ph = sub.add_parser("headings")
    ph.add_argument("file")

    ps = sub.add_parser("section")
    ps.add_argument("file")
    ps.add_argument("heading_sub")
    ps.add_argument("--max-chars", type=int, default=6000)

    pr = sub.add_parser("read")
    pr.add_argument("file")
    pr.add_argument("start", type=int)
    pr.add_argument("end", type=int)

    a = ap.parse_args()
    root = root_dir(a.root)
    return {
        "list": cmd_list,
        "files": cmd_files,
        "find": cmd_find,
        "headings": cmd_headings,
        "section": cmd_section,
        "read": cmd_read,
    }[a.cmd](root, a)


if __name__ == "__main__":
    sys.exit(main())
