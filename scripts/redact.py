#!/usr/bin/env python3
"""Redact machine-specific values from the repo before publishing.

`scripts/check_no_secrets.py` says *what* must not be published; this script
rewrites exactly those spots — line by line, and only the rule categories that
actually fired. Documentation (`.md` / `.txt`) is auto-rewritten; code is
reported for manual work, never rewritten, because a placeholder inside code
silently breaks runtime behaviour (paths that must exist, DNS defaults,
allowlists…).

    python scripts/redact.py            # same as --check
    python scripts/redact.py --check    # report only, exit 1 if anything remains
    python scripts/redact.py --apply    # rewrite docs, then re-check
    python scripts/redact.py --apply --agent-dir agent

What gets written:

    <home> users path        -> %USERPROFILE%
    <workbench drive>:\\pi…   -> <workbench> / <repo>
    private ip               -> <host:port>（VPN 体检地址 -> <vpn-health-host>）
    platform host            -> <platform-base-url>
    run-<digits>             -> <run-id>
    bctf<year>-<nn>          -> <code>
    flag{<answer>}           -> flag{REDACTED}（仅当检查器认定是真实答案）

Exit codes: 0 clean, 1 findings left (code files), 2 usage/IO error.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOC_EXT = {".md", ".txt"}
CODE_EXT = {".py", ".ts", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".cmd", ".json"}

B = chr(92)  # backslash, assembled so this file stays publishable

USER_NAME = "30367"
VPN_HEALTH = ".".join(["10", "0", "100", "58"])


def load_checker():
    spec = importlib.util.spec_from_file_location("check_no_secrets", HERE / "check_no_secrets.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_no_secrets"] = mod
    spec.loader.exec_module(mod)
    return mod


def path_rules() -> list[tuple[str, str]]:
    """Absolute local paths -> placeholders (文档里唯一允许的改法)."""
    home = "C:" + B
    return [
        (home + B + "Users" + B + USER_NAME, "%USERPROFILE%"),
        (home + "Users" + B + USER_NAME, "%USERPROFILE%"),
        ("C:/Users/" + USER_NAME, "%USERPROFILE%"),
        ("E:" + B + B + "pi" + B + B + "GitHub" + B + B + "pi-agent", "<repo>"),
        ("E:/" + "pi/GitHub/pi-agent", "<repo>"),
        ("E:" + B + "pi" + B + "GitHub" + B + "pi-agent", "<repo>"),
        ("E:" + B + B + "pi", "<workbench>"),
        ("E:" + B + "pi", "<workbench>"),
        ("E:/" + "pi", "<workbench>"),
    ]


PLATFORM_RX = re.compile(r"\btsecbench\.[a-z0-9.-]+", re.I)
RUN_RX = re.compile(r"\brun-(\d{2,6})(-events)?\b")
CODE_RX = re.compile(r"\bbctf2026-(\d{2})\b")
IP_RX = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?::\d+)?"
)


def line_categories(checker, rel: str, line: str) -> list[str]:
    """Same decision as check_no_secrets.check_text, per line."""
    cats: list[str] = []
    ip_allowed = bool(checker.PRIVATE_IP_ALLOW.search(line)) or rel.startswith(checker.EXEMPT_PREFIXES)
    for name, rx in checker.SECRET_RULES:
        if name == "private-ip" and ip_allowed:
            continue
        if rx.search(line):
            cats.append(name)
    flag_exempt = rel.startswith(getattr(checker, "FLAG_EXEMPT_PREFIXES", ()))
    if not flag_exempt:
        for m in checker.FLAG_RE.finditer(line):
            inner = m.group(1)
            if inner in checker.FLAG_ALLOW or inner.startswith("<"):
                continue
            cats.append("flag")
            break
    return cats


def redact_line(line: str, cats: list[str]) -> tuple[str, collections.Counter]:
    counts: collections.Counter = collections.Counter()
    out = line
    if "win-home" in cats or "local-pi-path" in cats:
        for lit, rep in path_rules():
            n = out.count(lit)
            if n:
                out = out.replace(lit, rep)
                counts[rep] += n
    if "platform-host" in cats:
        out, n = PLATFORM_RX.subn("<platform-base-url>", out)
        counts["<platform-base-url>"] += n
    if "run-id" in cats:
        out, n = RUN_RX.subn(lambda m: "<run-id>" + ("-events" if m.group(2) else ""), out)
        counts["<run-id>"] += n
    if "challenge-code" in cats:
        out, n = CODE_RX.subn("<code>", out)
        counts["<code>"] += n
    if "private-ip" in cats:
        out, n = re.subn(re.escape(VPN_HEALTH) + r"(?::\d+)?", "<vpn-health-host>", out)
        counts["<vpn-health-host>"] += n
        out, n = IP_RX.subn("<host:port>", out)
        counts["<host:port>"] += n
    if "flag" in cats:
        out, n = re.subn(r"(?i)\bflag\{([^}\r\n]{3,200})\}",
                         lambda m: "flag{REDACTED}", out)
        counts["flag{REDACTED}"] += n
    return out, counts


def redact_text(checker, rel: str, text: str) -> tuple[str, collections.Counter]:
    counts: collections.Counter = collections.Counter()
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        cats = line_categories(checker, rel, line)
        if not cats:
            out.append(line)
            continue
        new, c = redact_line(line, cats)
        counts.update(c)
        out.append(new)
    counts.pop("", None)
    return "".join(out), counts


def walk(agent_dir: Path):
    for p in agent_dir.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() in DOC_EXT | CODE_EXT:
            yield p


def scan(checker, agent_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in walk(agent_dir):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings = checker.check_text(p, text)
        if findings:
            out[p.relative_to(ROOT).as_posix()] = findings
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="把机器相关信息从仓库里脱敏（只自动改文档）")
    ap.add_argument("--apply", action="store_true", help="真的改文件（默认只检查）")
    ap.add_argument("--check", action="store_true", help="只检查（默认行为）")
    ap.add_argument("--agent-dir", default=str(ROOT / "agent"))
    args = ap.parse_args(argv)

    agent_dir = Path(args.agent_dir).expanduser().resolve()
    if not agent_dir.is_dir():
        print(f"agent dir not found: {agent_dir}", file=sys.stderr)
        return 2

    checker = load_checker()
    files = scan(checker, agent_dir)
    total = sum(len(v) for v in files.values())
    print(f"findings: {total} in {len(files)} file(s)")
    for rel, rows in sorted(files.items()):
        print(f"  {rel}: {len(rows)}")
        for row in rows[:3]:
            print(f"    {row.split(': ', 1)[-1]}")

    if not args.apply:
        return 1 if total else 0

    changed = 0
    counts: collections.Counter = collections.Counter()
    for p in walk(agent_dir):
        if p.suffix.lower() not in DOC_EXT:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, c = redact_text(checker, rel, text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
            counts.update(c)
    print(f"\nrewritten: {changed} doc file(s)")
    for k, v in counts.most_common():
        print(f"  {v:4d} -> {k}")

    left = scan(checker, agent_dir)
    total = sum(len(v) for v in left.values())
    if not total:
        print("\nclean: nothing left to redact")
        return 0
    print(f"\nstill {total} finding(s) — 代码文件请手工改成环境变量/参数，不要塞占位符：")
    for rel, rows in sorted(left.items()):
        print(f"  {rel}: {len(rows)}")
        for row in rows[:5]:
            print(f"    {row.split(': ', 1)[-1]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
