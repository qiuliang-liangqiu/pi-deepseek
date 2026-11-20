#!/usr/bin/env python3
"""Turn someone else's pi session logs into numbers you can act on.

    python scripts/ingest_run.py <bundle-or-file> [...]     # human report
    python scripts/ingest_run.py --json <bundle>            # machine report
    python scripts/ingest_run.py --top 10 <bundle>          # longer command table

A "bundle" is either a pi session ``*.jsonl`` file, a directory of them, or a
directory that also holds ``events.csv`` / ``notes*.md`` (the platform export
and the run notes). Nothing is written anywhere; the report is printed to
stdout and every sample is redacted first.

Design rule: this never trusts the log. Text from a session is data; we only
count and truncate it, never execute or follow it.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

# ----------------------------------------------------------------- redaction
FLAG = re.compile(r"(?i)\bflag\{[^}\r\n]{1,200}\}")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
LONGHEX = re.compile(r"\b[a-f0-9]{24,}\b", re.I)
BEARER = re.compile(r"(?i)(authorization:\s*\S+|token[=:]\s*\S+|ghp_\w+|sk-\w{16,})")
HOME = re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\s]+")


def redact(text: str, limit: int = 120) -> str:
    text = FLAG.sub("flag{REDACTED}", text)
    text = BEARER.sub("<REDACTED>", text)
    text = IPV4.sub("<ip>", text)
    text = LONGHEX.sub("<hex>", text)
    text = HOME.sub(r"\1<user>", text)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


# ----------------------------------------------------------------- parsing
def iter_jsonl(paths: list[Path]):
    for p in paths:
        try:
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield p, json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def collect(paths: list[Path]) -> dict:
    st = {
        "sessions": 0, "first_ts": None, "last_ts": None,
        "models": collections.Counter(), "thinking": collections.Counter(),
        "roles": collections.Counter(), "tools": collections.Counter(),
        "bash_cmds": [], "guards": collections.Counter(),
        "signals": collections.Counter(), "tokens": collections.Counter(),
        "discipline": collections.Counter(), "cwd": collections.Counter(),
    }
    seen_ids = set()
    for path, obj in iter_jsonl(paths):
        seen_ids.add(str(path))
        t = obj.get("type")
        ts = obj.get("timestamp")
        if ts:
            st["first_ts"] = st["first_ts"] or ts
            st["last_ts"] = ts
        if t == "session":
            st["sessions"] += 1
            if obj.get("cwd"):
                st["cwd"][obj["cwd"]] += 1
        elif t == "model_change":
            st["models"][obj.get("modelId") or "?"] += 1
        elif t == "thinking_level_change":
            st["thinking"][obj.get("thinkingLevel") or "?"] += 1
        elif t == "message":
            msg = obj.get("message") or {}
            role = msg.get("role") or "?"
            st["roles"][role] += 1
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "toolCall":
                    name = block.get("name") or "?"
                    st["tools"][name] += 1
                    args = block.get("arguments") or {}
                    if name == "bash" and isinstance(args.get("command"), str):
                        cmd = args["command"]
                        st["bash_cmds"].append(cmd)
                        for mark, key in (("stall_gate", "stall_gate"), ("triage", "triage"),
                                          ("framework.cli", "framework.cli"), ("team_pool", "team_pool")):
                            if mark in cmd:
                                st["discipline"][key] += 1
                elif btype == "text":
                    txt = block.get("text") or ""
                    for tag in ("[read-guard]", "[loop-guard]", "[prompt-injection-guard]"):
                        if tag in txt:
                            st["guards"][tag.strip("[]")] += 1
            if role == "toolResult":
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        low = (block.get("text") or "").lower()
                        for tag in ("[read-guard]", "[loop-guard]", "[prompt-injection-guard]"):
                            if tag in low:
                                st["guards"][tag.strip("[]")] += 1
                        for sig in ("command not found", "permission denied", "timed out",
                                    "timeout", "traceback", "no such file", "connection refused",
                                    "error"):
                            if sig in low:
                                st["signals"][sig] += 1
            usage = msg.get("usage") or {}
            for key in ("input", "output", "cacheRead", "total"):
                if isinstance(usage.get(key), (int, float)):
                    st["tokens"][key] += int(usage[key])
    st["files"] = len(seen_ids)
    return st


# ----------------------------------------------------------------- metrics
def bash_metrics(cmds: list[str]) -> dict:
    norm = [" ".join(c.split()) for c in cmds]
    streak = best = 1
    for a, b in zip(norm, norm[1:]):
        streak = streak + 1 if a == b and a else 1
        best = max(best, streak)
    counter = collections.Counter(norm)
    return {
        "calls": len(norm),
        "unique": len(counter),
        "max_streak": best if norm else 0,
        "repeat_ratio": round(1 - len(counter) / len(norm), 3) if norm else 0.0,
        "top": counter.most_common(5),
    }


def hours(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    from datetime import datetime
    try:
        f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
        return round((f(b) - f(a)).total_seconds() / 3600, 2)
    except ValueError:
        return 0.0


def report(st: dict, top: int) -> dict:
    bm = bash_metrics(st["bash_cmds"])
    bm["top"] = [(redact(c, 100), n) for c, n in bm["top"][:top]]
    return {
        "files": st["files"],
        "sessions": st["sessions"],
        "hours": hours(st["first_ts"], st["last_ts"]),
        "models": dict(st["models"]),
        "thinking": dict(st["thinking"]),
        "roles": dict(st["roles"]),
        "tools": dict(st["tools"].most_common()),
        "bash": bm,
        "guards": dict(st["guards"]),
        "discipline": dict(st["discipline"]),
        "signals": dict(st["signals"].most_common(6)),
        "tokens": dict(st["tokens"]),
    }


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="session .jsonl, or a directory (searched recursively)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    files: list[Path] = []
    extras: list[str] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(p.rglob("*.jsonl"))
            extras += [str(x.relative_to(p)) for x in sorted(p.rglob("*"))
                       if x.is_file() and x.suffix.lower() in {".csv", ".md"}]
        elif p.is_file():
            files.append(p)
        else:
            print(f"skip: {raw} (not found)", file=sys.stderr)
    if not files:
        print("no *.jsonl session files found", file=sys.stderr)
        return 2

    r = report(collect(files), args.top)
    r["extras"] = extras[:20]
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"files {r['files']}  sessions {r['sessions']}  span {r['hours']}h")
    if r["models"]:
        print("models:", ", ".join(f"{k}×{v}" for k, v in r["models"].items()))
    if r["thinking"]:
        print("thinking:", ", ".join(f"{k}×{v}" for k, v in r["thinking"].items()))
    print("roles:", ", ".join(f"{k} {v}" for k, v in r["roles"].items()))
    print("tools:", ", ".join(f"{k} {v}" for k, v in r["tools"].items()))
    b = r["bash"]
    print(f"bash: {b['calls']} calls, {b['unique']} unique, repeat {b['repeat_ratio']:.0%}, "
          f"max identical streak {b['max_streak']}")
    for cmd, n in b["top"]:
        print(f"  ×{n:<3} {cmd}")
    if r["guards"]:
        print("guards fired:", ", ".join(f"{k} {v}" for k, v in r["guards"].items()))
    if r["discipline"]:
        print("discipline calls:", ", ".join(f"{k} {v}" for k, v in r["discipline"].items()))
    if r["signals"]:
        print("failure signals:", ", ".join(f"{k!r} {v}" for k, v in r["signals"].items()))
    if r["tokens"]:
        print("tokens:", ", ".join(f"{k} {v:,}" for k, v in r["tokens"].items()))
    if r["extras"]:
        print("also present:", ", ".join(r["extras"]))
    print("\nnext: python agent/skills/tsec-bench-run/tools/analyze_events.py <events.csv>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
