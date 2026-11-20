#!/usr/bin/env python3
"""安全提交器（2026-11-15 复盘版）。

设计目标：**让"错误提交"这件事在工具层就不可能批量失控**。

用法：
  python work/sub.py <code> <flag> [--evidence "为什么相信它是对的"] [--force] [--keep] [--dry]

硬门（任一不过就不发请求）：
  1. 该题已通关            -> 拒绝（duplicate 不加分，纯浪费）
  2. 同一 flag 提交过失败过 -> 拒绝（重复无效提交）
  3. 该题累计错 >= 2       -> 拒绝，除非 --force，并打印历史
  4. 容器 stopped          -> 自动 start（否则平台静默返回 correct=False 假阴性）
  5. 无 --evidence 时给出提示（不阻断，但写入日志，便于复盘）

所有提交写 work/submits.jsonl；每题的错次/已试 hash 写 work/attempts.json。
"""
import os, sys, pathlib, json, time, hashlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from framework.api import BenchmarkClient
from framework import governance

W = pathlib.Path(__file__).resolve().parent
SUBMITS = W / "submits.jsonl"
ATTEMPTS = W / "attempts.json"
MAX_WRONG_PER_CODE = 2


def sha8(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def load_attempts():
    if ATTEMPTS.exists():
        try:
            return json.loads(ATTEMPTS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_attempts(a):
    ATTEMPTS.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv):
    try:  # Windows 控制台/管道默认 GBK，中文会乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.environ.get("TEAM_WORKER") == "1":
        print("[worker] sub.py is main-agent only", file=sys.stderr)
        return 1
    pos = [a for a in argv if not a.startswith("--")]
    opts = {}
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            k = argv[i]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--") and k in ("--evidence",):
                opts[k] = argv[i + 1]
                i += 2
                continue
            opts[k] = True
        i += 1
    if len(pos) < 2:
        print(__doc__)
        return 2
    code, flag = pos[0], pos[1]
    force = "--force" in opts
    keep = "--keep" in opts
    dry = "--dry" in opts
    evidence = opts.get("--evidence", "")

    att = load_attempts()
    rec = att.setdefault(code, {"wrong": 0, "tried": [], "correct": False})

    # ---- 门 1/2/3（纯本地，不发请求）----
    if rec.get("correct"):
        print(f"[gate] {code} 已通关，拒绝重复提交")
        return 3
    h = sha8(flag)
    if h in rec["tried"]:
        print(f"[gate] {code} 该 flag（sha8={h}）已经提交并失败过，拒绝重复")
        return 3
    if rec["wrong"] >= MAX_WRONG_PER_CODE and not force:
        print(f"[gate] {code} 已错 {rec['wrong']} 次（上限 {MAX_WRONG_PER_CODE}），拒绝提交。")
        print(f"       历史: {rec['tried']}")
        print("       先确认是不是假阴性（容器 stopped / 格式前缀），确需再试加 --force")
        return 3
    if dry:
        print(f"[dry] {code} 通过本地门：wrong={rec['wrong']} 已试={rec['tried']} sha8={h}")
        return 0

    c = BenchmarkClient()
    status = None
    try:
        me = next((x for x in c.challenges() if x["unique_code"] == code), None)
        status = (me or {}).get("container_status")
    except Exception as e:
        print(f"[warn] challenges() 失败（{e}），继续尝试 start")

    started = False
    if status != "available":
        try:
            addrs = c.start(code)
            started = True
            governance.mark_started(code)
            print(f"[start] {code} -> {addrs}")
        except Exception as e:
            print(f"[start] 失败: {e}")

    r = c.submit(code, flag)
    ok = bool(r.get("correct"))

    rec["tried"].append(h)
    if ok:
        rec["correct"] = True
        # Persist the local flag count so a later `close` (new process) still
        # sees the multi-flag gate. The in-memory c.flags is empty there.
        try:
            d = W / code
            d.mkdir(parents=True, exist_ok=True)
            (d / ".flags").write_text(str(int(c.flags.get(code, 0) or 1)), encoding="utf-8")
        except Exception:
            pass
    else:
        rec["wrong"] += 1
    save_attempts(att)

    SUBMITS.parent.mkdir(exist_ok=True)
    with SUBMITS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "code": code, "sha8": h,
            "len": len(flag), "container_before": status, "started_here": started,
            "evidence": evidence, "result": r,
        }, ensure_ascii=False) + "\n")

    print(r)
    if not ok:
        print(f"[gate] 这是 {code} 的第 {rec['wrong']} 次失败（上限 {MAX_WRONG_PER_CODE}）"
              f"{'；已到上限，下次必须 --force' if rec['wrong'] >= MAX_WRONG_PER_CODE else ''}")
        print("       先排除假阴性：container_status 是否 available、前缀/包裹是否符合题面、是否该题动态 flag")

    if started and not keep:
        try:
            c.close(code)
            print(f"[close] {code}")
        except Exception as e:
            print(f"[close] 失败: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
