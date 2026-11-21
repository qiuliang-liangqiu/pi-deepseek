#!/usr/bin/env python3
"""Scan this repo for leaked secrets, real flags and absolute Windows home paths.

Exit code 0 = clean, 1 = findings. Intended as a pre-commit / CI check.

    python scripts/check_no_secrets.py
    python scripts/check_no_secrets.py --quiet
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}

SECRET_RULES = [
    ("api-key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("ctf2-token", re.compile(r"ctf2_[0-9a-f]{32,}")),
    ("hbc-token", re.compile("41137e" + "453ded44d2a9402acf37aeb060")),
    ("benchmark-token", re.compile(r"BENCHMARK_TOKEN\s*=\s*[A-Za-z0-9._-]{16,}")),
    ("agent-token", re.compile(r"Agent-Token:\s*[a-f0-9]{20,}")),
    ("win-home", re.compile(r"C:[\\/]Users[\\/](?!<u>|suspect)[A-Za-z0-9._-]+")),
    ("local-pi-path", re.compile(r"\bE:[\\/]pi\b")),
    # ---- platform identity leaks (this repo must stay platform-agnostic) ----
    ("platform-host", re.compile(r"\btsecbench\.[a-z0-9.-]+\b", re.I)),
    ("run-id", re.compile(r"\brun-\d{4,6}(-events)?\b")),
    ("challenge-code", re.compile(r"\b(?:tch|xb|bctf\d{4})[-_]?\d{2}\b")),
    ("private-ip", re.compile(r"\b(?:10\.(?:\d{1,3})\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
]

# 允许出现的占位/文档示例（私有地址只有在这些行里才算合法）
PRIVATE_IP_ALLOW = re.compile(
    r"<host:port>|<your-|10\.0\.0\.0/8|10\.13\.37|10\.0\.0\.1\b|192\.168\.1\.(?:1|2|100)|"
    r"10\.0\.0\.\$i|172\.16\.0\.0/12|172\.1[789]\.|172\.2[0-9]\.|10\.0\.2\.[0-9]"
)
# 第三方 skill 快照（ctf-skills）里的私有地址都是文档示例，不做平台泄漏判定
EXEMPT_PREFIXES = ("agent/skills/ctf/",)
# 同一快照里的 flag 都是公开 writeup 的答案（pwnable.tw 等），不是本平台泄漏
FLAG_EXEMPT_PREFIXES = ("agent/skills/ctf/",)
FLAG_RE = re.compile(r"(?i)\bflag\{([^}\r\n]{3,200})\}")
FLAG_ALLOW = {"REDACTED", "...", "word1.word2.word3",
              # 占位符与自测夹具
              "uuid", "deadbeef", "feedface"}


def iter_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def check_text(path: Path, text: str) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        allowed = bool(PRIVATE_IP_ALLOW.search(line)) or rel.startswith(EXEMPT_PREFIXES)
        for name, rx in SECRET_RULES:
            if name == "private-ip" and allowed:
                continue
            if rx.search(line):
                findings.append(f"{rel}:{i}: {name}")
        for m in FLAG_RE.finditer(line):
            inner = m.group(1)
            if rel.startswith(FLAG_EXEMPT_PREFIXES):
                continue
            if inner in FLAG_ALLOW or inner.startswith("<"):
                continue
            findings.append(f"{rel}:{i}: flag literal {m.group(0)[:60]}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    findings: list[str] = []
    for path in iter_files():
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(check_text(path, text))

    if findings:
        print(f"FAIL: {len(findings)} finding(s)")
        for f in findings:
            print("  " + f)
        return 1
    if not args.quiet:
        print("OK: no secrets, real flags or absolute Windows home paths found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
