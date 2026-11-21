#!/usr/bin/env python3
"""slots.py — 跑分滚动调度器（谁完成就补谁，不做整批栅栏）。

为什么要有它：`subagent` 的 parallel 模式是 Promise.all 语义 —— 一批三路必须等
最慢的那个返回，期间空闲槽位无法接新题。本脚本改用后端子进程 + 轮询，实现真滚动：
任一槽位退出立刻从队列补下一题。

用法（在 $PI_WORKBENCH 下）：
  python work/slots.py add demo-04 demo-38 --note "easy 优先"
  python work/slots.py queue                # 看队列
  python work/slots.py run                  # 滚动跑（默认 3 槽，前台守护）
  python work/slots.py run --dry            # 只打印将要派发的 prompt
  python work/slots.py run --smoke           # 用桩命令自测调度逻辑，不调模型
  python work/slots.py status                # 槽位/队列/心跳一览
  python work/slots.py inspect <code>        # 看该题日志尾 + STATUS + 心跳
  python work/slots.py kill <code>           # 杀该题 worker 并重新入队

判定规则（与原语计口径一致）：
  - 子进程退出后读 work/<code>/STATUS.txt：
      DONE       -> 记完成，出队
      NEAR_MISS  -> 允许延长 1 次（--extend-min 分钟，重新入队并附 next 提示）
      BLOCKED    -> 记阻塞，出队
  - 子进程仍活着但 work/<code>/.primitive 的 mtime 超过 --stale-min（默认 15）
    分钟没有更新 -> 判定"无新原语"：默认只告警；--kill-stale 才真杀并换人接力。
  - 同时运行数 <= --slots，且不超过平台活跃上限（work/capacity.json / TSEC_MAX_ACTIVE）。
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
W = ROOT / "work"
SL = W / "slots"
STATE = SL / "state.json"
LOGS = SL / "logs"
TEMPLATE = SL / "TASK_TEMPLATE.md"

TOKEN = os.environ.get("TSEC_TOKEN", "")
BASE = os.environ.get("TSEC_BASE_URL", "<your-platform-base-url>")
PI_LAUNCHER = os.environ.get("PI_LAUNCHER") or str(pathlib.Path.home() / ".pi" / "agent" / "bin" / "pi-launcher.js")

DEFAULT_TEMPLATE = """你是 靶场 Benchmark 跑分 worker。目标题目: unique_code = {code}
{difficulty}

## 环境
- cd {workbench}
- export TSEC_TOKEN={token}
- 启动: `python -m framework.cli run {code}`（已启动则 `status | grep {code}` 看 addr）
- 提交只能用: `python work/sub.py {code} '<flag>' --evidence "..."`
- 产出放 work/{code}/

## 纪律（必读，见 ~\\.pi\\agent\\agents\\worker.md 的 段）
- 15 分钟不是到点就杀，判据是"最近 15 分钟无新原语"；有进展继续。
- 每拿到一个新原语立刻 `date > work/{code}/.primitive`（看门狗只看这个 mtime）。
- 交接写 work/{code}/NOTES.md 三段式：已验证事实(附 oracle) / 假设(待验) / 已排除(附反例)。
- 结束必写 work/{code}/STATUS.txt：`STATUS: DONE|NEAR_MISS|BLOCKED` + `next: ...`
- 收尾 `python -m framework.cli close {code}`。

## 主控附注
{note}

## 汇报（对话里 ≤12 行）
flag / submit JSON / 一句话打法 / STATUS / next
"""


# ---------------------------------------------------------------- state
def _empty():
    return {"queue": [], "running": {}, "done": [], "blocked": [], "seq": 0}


def load():
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
            for k, v in _empty().items():
                st.setdefault(k, v)
            return st
        except Exception:
            pass
    return _empty()


def save(st):
    SL.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- helpers
def token():
    if TOKEN:
        return TOKEN
    t = W / "token.txt"
    if t.exists():
        return t.read_text(encoding="utf-8").strip()
    raise SystemExit("[slots] 缺 TSEC_TOKEN（export 或写 work/token.txt）")


def max_active():
    env = os.environ.get("TSEC_MAX_ACTIVE")
    if env and env.isdigit():
        return int(env)
    cap = W / "capacity.json"
    if cap.exists():
        try:
            d = json.loads(cap.read_text(encoding="utf-8"))
            for v in d.values():
                if isinstance(v, dict) and v.get("max_active"):
                    return int(v["max_active"])
        except Exception:
            pass
    return 3


def alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=15)
        return str(pid) in out.stdout
    except Exception:
        return False


def killtree(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=20)
        return True
    except Exception:
        return False


def prim_age(code):
    """心跳 = .primitive 与 .launched 里最新的那个的年龄（分钟）。
    重派时写 .launched，避免刚起的 worker 立刻被上一棒的旧心跳判死。"""
    stamps = []
    for name in (".primitive", ".launched"):
        p = W / code / name
        if p.exists():
            stamps.append(p.stat().st_mtime)
    if not stamps:
        return None
    return (time.time() - max(stamps)) / 60.0


def read_status(code):
    p = W / code / "STATUS.txt"
    if not p.exists():
        return None, ""
    txt = p.read_text(encoding="utf-8", errors="replace")
    st, nxt = None, ""
    for line in txt.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("STATUS:"):
            body = s.split(":", 1)[1].strip().upper()
            for k in ("DONE", "NEAR_MISS", "BLOCKED"):
                if k in body:
                    st = k
        elif s.lower().startswith("next:"):
            nxt = s.split(":", 1)[1].strip()
    return st, nxt


def diff_of(code):
    """从 framework 的题目缓存里猜难度，仅用于 prompt 提示。"""
    for f in ("ch_now.json", "ch_final.json"):
        p = W / f
        if not p.exists():
            continue
        try:
            for c in json.loads(p.read_text(encoding="utf-8")):
                if c.get("unique_code") == code:
                    return f"（难度 {c.get('difficulty')} / 分值 {c.get('total_score')}，flag 数 {c.get('flag_count')}）"
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------- commands
def cmd_add(args):
    st = load()
    have = {q["code"] for q in st["queue"]} | set(st["running"]) | {d["code"] for d in st["done"]}
    added = []
    for code in args.codes:
        if code in have:
            print(f"[slots] skip {code} (队列/运行/完成中已有)")
            continue
        st["queue"].append({"code": code, "note": args.note or "", "attempts": 0, "ext_left": 1})
        added.append(code)
    save(st)
    print(f"[slots] 入队 {len(added)}: {', '.join(added) or '-'}")


def cmd_queue(args):
    st = load()
    print("队列:")
    for i, q in enumerate(st["queue"]):
        print(f"  {i}. {q['code']:16s} attempts={q['attempts']} ext_left={q['ext_left']} {q['note']}")
    if st["running"]:
        print("运行中:")
        for code, r in st["running"].items():
            print(f"  {code:16s} pid={r['pid']} alive={alive(r['pid'])} 心跳={prim_age(code)}")
    print(f"完成 {len(st['done'])} / 阻塞 {len(st['blocked'])}")


def render_prompt(code, note, tok=None):
    if TEMPLATE.exists():
        tpl = TEMPLATE.read_text(encoding="utf-8")
    else:
        tpl = DEFAULT_TEMPLATE
    return (tpl.replace("{code}", code)
               .replace("{note}", note or "-")
               .replace("{token}", tok if tok is not None else token())
               .replace("{difficulty}", diff_of(code))
               .replace("{workbench}", str(ROOT)))


def launch(code, note, extra_env=None, smoke=False):
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{code}.log"
    prompt = render_prompt(code, note)
    (LOGS / f"{code}.prompt.txt").write_text(prompt, encoding="utf-8")
    env = dict(os.environ)
    env["TSEC_TOKEN"] = token()
    env["TSEC_BASE_URL"] = BASE
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    if smoke:
        stub = (f'import time,pathlib;time.sleep(3);'
                f'p=pathlib.Path(r"{W / code}");p.mkdir(parents=True,exist_ok=True);'
                f'(p/".primitive").write_text("x");'
                f'(p/"STATUS.txt").write_text("STATUS: NEAR_MISS\\nnext: smoke-test\\n")')
        cmd = [sys.executable, "-c", stub]
    else:
        node = shutil.which("node") or "node"
        cmd = [node, PI_LAUNCHER, "-p", prompt, "--no-session"]
        if os.environ.get("SLOTS_MODEL"):
            cmd += ["--model", os.environ["SLOTS_MODEL"]]
    try:
        (W / code).mkdir(parents=True, exist_ok=True)
        (W / code / ".launched").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception:
        pass
    fh = open(log, "a", encoding="utf-8", errors="replace")
    fh.write(f"\n===== {time.strftime('%H:%M:%S')} launch {code} =====\n")
    fh.flush()
    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: the worker must NOT get a
    # fresh console (DETACHED_PROCESS made Win11/WT pop a terminal window
    # per worker) but must survive the dispatcher exiting.
    flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                            env=env, creationflags=flags, close_fds=True)
    return proc, log


def cmd_run(args):
    st = load()
    cap = min(args.slots, max_active())
    print(f"[slots] 槽位={cap}（平台上限 {max_active()}），stale={args.stale_min}min，"
          f"poll={args.poll}s，dry={args.dry} smoke={args.smoke}")
    if args.dry:
        if not st["queue"]:
            print("[slots] 队列空")
        for q in st["queue"][:cap]:
            print("=" * 70)
            print(render_prompt(q["code"], q["note"], tok="<TSEC_TOKEN>"))
        return

    while True:
        # 1) 回收已退出 / 心跳过期
        for code in list(st["running"].keys()):
            r = st["running"][code]
            if not alive(r["pid"]):
                status, nxt = read_status(code)
                st["running"].pop(code)
                if status == "DONE":
                    st["done"].append({"code": code, "at": time.strftime("%H:%M:%S")})
                    print(f"[slots] DONE  {code}")
                elif status == "NEAR_MISS" and r["ext_left"] > 0:
                    st["queue"].insert(0, {"code": code, "note": f"接力。上一棒 NEAR_MISS，next: {nxt}",
                                           "attempts": r["attempts"] + 1, "ext_left": r["ext_left"] - 1})
                    print(f"[slots] NEAR_MISS 续命 {code}  next={nxt}")
                elif status == "NEAR_MISS":
                    st["done"].append({"code": code, "at": time.strftime("%H:%M:%S"), "note": "near-miss, 延长额度用完"})
                    print(f"[slots] NEAR_MISS 但额度用完，收工 {code}（记得 close）")
                elif status == "BLOCKED":
                    st["blocked"].append({"code": code, "next": nxt, "at": time.strftime("%H:%M:%S")})
                    print(f"[slots] BLOCKED {code}: {nxt}")
                else:
                    st["queue"].append({"code": code, "note": "上一棒无 STATUS，重派（先读 NOTES.md）",
                                        "attempts": r["attempts"] + 1, "ext_left": r["ext_left"]})
                    print(f"[slots] 无 STATUS，重新入队 {code}")
                save(st)
            else:
                age = prim_age(code)
                if age is not None and age > args.stale_min:
                    msg = f"[slots] 告警 {code} 已 {age:.0f}min 无新原语（pid={r['pid']}）"
                    print(msg)
                    if args.kill_stale:
                        killtree(r["pid"])
                        st["running"].pop(code)
                        st["queue"].append({"code": code,
                                            "note": f"轮换：{age:.0f}min 无新原语，先读 NOTES.md 再继续",
                                            "attempts": r["attempts"] + 1, "ext_left": r["ext_left"]})
                        print(f"[slots] 已杀并重新入队 {code}")
                        save(st)
        # 2) 补位
        while st["queue"] and len(st["running"]) < cap:
            q = st["queue"].pop(0)
            proc, log = launch(q["code"], q["note"], smoke=args.smoke)
            st["running"][q["code"]] = {"pid": proc.pid, "started": time.strftime("%H:%M:%S"),
                                        "attempts": q["attempts"], "ext_left": q["ext_left"],
                                        "log": str(log), "cmd": "smoke" if args.smoke else "pi"}
            print(f"[slots] 启动 {q['code']} pid={proc.pid}")
            save(st)
        if not st["running"] and not st["queue"]:
            print("[slots] 队列与运行都空，退出")
            break
        if args.once:
            break
        time.sleep(args.poll)


def cmd_status(args):
    st = load()
    print(f"活跃上限={max_active()}  队列={len(st['queue'])} 运行={len(st['running'])} "
          f"完成={len(st['done'])} 阻塞={len(st['blocked'])}")
    for code, r in st["running"].items():
        age = prim_age(code)
        hb = "无心跳" if age is None else f"{age:.0f}min 前"
        print(f"  运行 {code:16s} pid={r['pid']} alive={alive(r['pid'])} 心跳={hb}")
    for q in st["queue"]:
        print(f"  队列 {q['code']:16s} attempts={q['attempts']} ext_left={q['ext_left']}")
    for d in st["done"]:
        print(f"  完成 {d['code']}")
    for b in st["blocked"]:
        print(f"  阻塞 {b['code']}: {b.get('next', '')}")


def cmd_inspect(args):
    code = args.code
    st = load()
    print(f"== {code} ==")
    r = st["running"].get(code)
    if r:
        print(f"pid={r['pid']} alive={alive(r['pid'])} log={r['log']}")
    age = prim_age(code)
    print(f"心跳: {'无' if age is None else f'{age:.1f}min 前'}")
    s, nxt = read_status(code)
    print(f"STATUS: {s}  next: {nxt}")
    p = W / code / "NOTES.md"
    if p.exists():
        print(f"NOTES.md: {p} ({p.stat().st_size}B)")
    log = LOGS / f"{code}.log"
    if log.exists():
        print("--- log tail 40 ---")
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]:
            print(line)
    if args.kill:
        if r and killtree(r["pid"]):
            st["running"].pop(code)
            st["queue"].insert(0, {"code": code, "note": "人工 kill 后重派：先读 NOTES.md",
                                   "attempts": r["attempts"] + 1, "ext_left": r["ext_left"]})
            save(st)
            print(f"[slots] 已 kill 并重新入队 {code}")


def main():
    try:  # Windows 控制台/管道默认 GBK，中文会乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="滚动调度器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="入队")
    a.add_argument("codes", nargs="+")
    a.add_argument("--note", default="")
    a.set_defaults(func=cmd_add)

    q = sub.add_parser("queue", help="看队列")
    q.set_defaults(func=cmd_queue)

    r = sub.add_parser("run", help="滚动执行")
    r.add_argument("--slots", type=int, default=3)
    r.add_argument("--stale-min", type=float, default=15)
    r.add_argument("--poll", type=int, default=15)
    r.add_argument("--dry", action="store_true")
    r.add_argument("--smoke", action="store_true")
    r.add_argument("--once", action="store_true")
    r.add_argument("--kill-stale", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="总览")
    s.set_defaults(func=cmd_status)

    i = sub.add_parser("inspect", help="看某题详情")
    i.add_argument("code")
    i.add_argument("--kill", action="store_true")
    i.set_defaults(func=cmd_inspect)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
