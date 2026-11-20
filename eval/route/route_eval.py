#!/usr/bin/env python3
"""Skill 路由评测：给一段题目描述，看 agent 是否会加载正确的 ctf-* skill。

用法:
  python route_eval.py                 # 全部用例
  python route_eval.py --tier smoke    # 只跑 smoke
  python route_eval.py --id web-jwt-upload
  python route_eval.py --dry-run       # 只打印用例

判据:
  expect   —— 该 skill 的 SKILL.md 必须被 read 到（命中即 pass）
  forbid   —— 这些 skill 不允许被 read（命中即 fail）
  direct   —— expect 是否第一个被读（仅记录，不算 fail）
结果写到 out/route-<时间>.json，并打印 markdown 表。

成本：每个用例一次 `pi -p`（deepseek-flash 约 7k tokens）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"
OUT = HERE / "out"

PROMPT = """这是技能路由测试，不是真的解题。题目：

{task}

请只做一件事：用 read 打开你实际会用的 1-2 个 SKILL.md（在 ~/.pi/agent/skills/ 下），
然后一行说明你的题型判断。不要运行其它工具，不要真的开始解题。"""

SKILL_RE = re.compile(r"([a-z0-9-]+)/SKILL\.md", re.I)


def run_case(case: dict, timeout: int) -> dict:
    prompt = PROMPT.format(task=case["task"])
    t0 = time.time()
    exe = shutil.which("pi") or "pi"
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = [f'"{exe}"', "-p", "--mode", "json", "--no-session"]
        use_shell = True
    else:
        cmd, use_shell = [exe, "-p", "--mode", "json", "--no-session"], False
    proc = subprocess.run(
        cmd if not use_shell else " ".join(cmd),
        input=prompt, shell=use_shell, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        cwd=str(Path.home()),
    )
    dur = time.time() - t0
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    (OUT / "raw" / f"{case['id']}.jsonl").write_text(proc.stdout or "", encoding="utf-8", errors="replace")
    read_skills: list[str] = []
    cost = 0.0
    err = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = ev.get("type")
        if et == "tool_execution_start":
            args = ev.get("args") or {}
            blob = json.dumps(args, ensure_ascii=False).replace("\\\\", "/")
            m = SKILL_RE.search(blob)
            if m:
                name = m.group(1)
                if name not in read_skills:
                    read_skills.append(name)
        elif et == "message_end":
            msg = ev.get("message") or {}
            usage = msg.get("usage") or {}
            c = (usage.get("cost") or {}).get("total")
            if isinstance(c, (int, float)):
                cost += c
    if proc.returncode != 0:
        err = (proc.stderr or "")[-500:]
    expect = case["expect"]
    forbid = case.get("forbid") or []
    wrong = [s for s in read_skills if s in forbid]
    return {
        "id": case["id"],
        "tier": case.get("tier", "full"),
        "expect": expect,
        "read": read_skills,
        "pass": expect in read_skills,
        "clean": not wrong,
        "wrong": wrong,
        "direct": bool(read_skills) and read_skills[0] == expect,
        "dispatcher_first": bool(read_skills) and read_skills[0] == "solve-challenge",
        "cost_usd": round(cost, 6),
        "seconds": round(dur, 1),
        "error": err,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier")
    ap.add_argument("--id", action="append")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict", action="store_true", help="forbid 命中也算失败")
    ap.add_argument("--json-out", action="store_true")
    a = ap.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if a.tier:
        cases = [c for c in cases if c.get("tier") == a.tier]
    if a.id:
        cases = [c for c in cases if c["id"] in a.id]
    if not cases:
        sys.exit("no cases matched")

    if a.dry_run:
        for c in cases:
            print(f"{c['id']:24s} tier={c.get('tier'):5s} expect={c['expect']:14s} forbid={','.join(c.get('forbid') or [])}")
        return 0

    OUT.mkdir(exist_ok=True)
    rows = []
    for i, c in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {c['id']} ...", flush=True)
        try:
            r = run_case(c, a.timeout)
        except subprocess.TimeoutExpired:
            r = {"id": c["id"], "tier": c.get("tier"), "expect": c["expect"], "read": [], "pass": False,
                 "wrong": [], "direct": False, "dispatcher_first": False, "cost_usd": 0, "seconds": a.timeout,
                 "error": "timeout"}
        rows.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        print(f"    {status} read={r['read']} wrong={r['wrong']} {r['seconds']}s ${r['cost_usd']}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = OUT / f"route-{stamp}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.strict:
        for r in rows:
            r["pass"] = r["pass"] and r["clean"]
    npass = sum(1 for r in rows if r["pass"])
    print("\n| case | expect | read | pass | direct | s | $ |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['id']} | {r['expect']} | {', '.join(r['read']) or '-'} | "
              f"{'Y' if r['pass'] else 'N'} | {'Y' if r.get('clean', True) else 'extra:' + ','.join(r['wrong'])} | "
              f"{'Y' if r['direct'] else ('disp' if r['dispatcher_first'] else '-')} | "
              f"{r['seconds']} | {r['cost_usd']} |")
    print(f"\n{npass}/{len(rows)} passed; total ${round(sum(r['cost_usd'] for r in rows), 5)}; -> {out_path}")
    return 0 if npass == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
