#!/usr/bin/env python3
"""stall_gate.py —— 止损闸门（AGENTS.md「止损闸门与速率纪律」的可执行落点）

用途：给「14 分钟无新原语」这条硬规则一个机械判据，避免靠自觉。

用法（每个目标一个 notes.md，放在本轮报告同目录）：

  python stall_gate.py open  <dir> <target>       # 开箱：建 notes.md，记录 T0
  python stall_gate.py beat  <dir> "新事实..."     # 有新原语
  python stall_gate.py beat  <dir> -               # 无新原语（刷新动作时间，但不算进展）
  python stall_gate.py check <dir> [--minutes 14] # 闸门判定
  python stall_gate.py show  <dir>

check 退出码：
  0 = 可以继续（距最后一次 [P] 在窗口内）
  1 = 已停滞，必须先写三行（当前假设/已排除/下一招）或换目标
  2 = 找不到 notes.md

注意：
- `-`（无新原语）只刷新“最后一次动作时间”，**不会**解除停滞；
  判据永远是「距最后一次 [P] 的分钟数」。
- [P] 行只有 HH:MM，没有日期。解析时以 T0 的日期为基准，并处理跨午夜
  （时间倒退 > 12h 视为进入第二天；未来 > 2h 视为前一天）。
  2026-11-20 修复：旧实现用 `date.today()` 套所有 [P]，跨午夜时 23:50 会被
  当成“今天 23:50”（未来），elapsed 为负，闸门被静默放行。
- 没有 T0/[P] 但 notes.md 有内容时，退回文件 mtime 判定（旧 notes 也能用），
  并在输出里明确写 `fallback=file-mtime`。
"""
import argparse
import datetime as dt
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NOTES = "notes.md"
T0_RE = re.compile(r"^T0\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
LINE_RE = re.compile(r"^-\s+(\d{2}:\d{2})\s+\[([P\-])\]\s*(.*)$")
TIME_RE = re.compile(r"^-\s+(\d{2}:\d{2})\s+")


def _path(d):
    return os.path.join(d, NOTES)


def _stamp(p):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(p))
    except OSError:
        return dt.datetime.now()


def _parse_entries(p):
    """Return (t0, last_p, last_action, n_entries, fallback_used)."""
    now = dt.datetime.now()
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, None, None, 0, False

    t0 = None
    for line in text.splitlines():
        m = T0_RE.match(line.strip())
        if m:
            t0 = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            break

    base_day = (t0 or _stamp(p)).date()
    now = dt.datetime.now()
    try:
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(p))
    except OSError:
        mtime = now
    # Use mtime when the file was touched in the last day; it is the best
    # evidence for which calendar day the last HH:MM belongs to.
    ref = mtime if (now - mtime) < dt.timedelta(days=1) else now
    last_p = None
    last_action = None
    n = 0
    prev = None
    for line in text.splitlines():
        s = line.strip()
        m = LINE_RE.match(s)
        if not m:
            # 只认台账行；自由格式的 notes 不计入 [P]，但 mtime 会兜底。
            continue
        n += 1
        try:
            t = dt.datetime.strptime(m.group(1), "%H:%M").time()
        except ValueError:
            continue
        # Try yesterday / today / tomorrow and pick the date closest to the
        # file's last-write time. This survives a run crossing midnight.
        cands = [dt.datetime.combine(base_day + dt.timedelta(days=d), t) for d in (-1, 0, 1)]
        cand = min(cands, key=lambda c: abs((c - ref).total_seconds()))
        if prev and cand < prev - dt.timedelta(hours=12):
            cand += dt.timedelta(days=1)
        if cand > now + dt.timedelta(hours=2):
            cand -= dt.timedelta(days=1)
        base_day = cand.date()
        prev = cand
        if last_action is None or cand > last_action:
            last_action = cand
        if m.group(2) == "P" and (last_p is None or cand > last_p):
            last_p = cand

    fallback = False
    if last_p is None and t0 is None and n == 0:
        # 旧式自由 notes：没有 T0 / [P]，用文件 mtime；没有内容才报错。
        if text.strip():
            fallback = True
    return t0, last_p, last_action, n, fallback


def cmd_open(d, target):
    os.makedirs(d, exist_ok=True)
    p = _path(d)
    now = dt.datetime.now()
    if os.path.exists(p):
        print(f"[=] 已存在 {p}（不覆盖）")
        return 0
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# {target} —— 止损台账\n\n")
        f.write(f"T0 {now:%Y-%m-%d %H:%M}  目标目录 {os.path.abspath(d)}\n\n")
        f.write("格式：`- HH:MM [P] 新原语` / `- HH:MM [-] 无新原语`\n")
        f.write("[P] = 新事实（新端口/参数/报错差异/凭据/路径/响应变化），"
                "写脚本与跑爆破不算。\n\n")
    print(f"[+] 已建 {p}")
    return 0


def cmd_beat(d, text):
    p = _path(d)
    if not os.path.exists(p):
        print(f"[!] 没有 {p}，先 open")
        return 2
    now = dt.datetime.now().strftime("%H:%M")
    mark = "-" if text.strip() in ("-", "") else "P"
    body = "无新原语" if mark == "-" else text.strip()
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"- {now} [{mark}] {body}\n")
    print(f"[+] {now} [{mark}] {body}")
    return 0


def cmd_check(d, minutes):
    p = _path(d)
    if not os.path.exists(p):
        print(f"[!] 没有 {p}")
        return 2
    t0, last_p, last_action, n, fallback = _parse_entries(p)
    mtime = _stamp(p)
    if last_p is not None:
        ref, src = last_p, "最后一次新原语"
    elif t0 is not None:
        ref, src = t0, "开箱时间 T0"
    elif fallback:
        ref, src = mtime, "文件 mtime（无 T0/[P] 的旧 notes）"
    else:
        print("[!] 台账里既没有 T0 也没有 [P] 行，且 notes 为空")
        return 2
    elapsed = (dt.datetime.now() - ref).total_seconds() / 60.0
    action_txt = ""
    if last_action is not None:
        action_txt = f" | 最后动作 {last_action:%m-%d %H:%M}"
    print(f"  台账 {n} 行 | 参考点：{src} {ref:%m-%d %H:%M}"
          f"{action_txt} | 已过 {elapsed:.1f} 分钟 / 窗口 {minutes} 分钟")
    if elapsed > minutes:
        print("  [!] 闸门触发：该假设已停滞。")
        print("      立刻写三行：当前假设 / 已排除 / 下一招，然后换目标或给出一条新原语。")
        print("      写不出新原语 → 不许继续同一个假设（包括再跑一组字典）。")
        return 1
    print("  [ok] 窗口内，可以继续。")
    return 0


def cmd_show(d):
    p = _path(d)
    if not os.path.exists(p):
        print(f"[!] 没有 {p}")
        return 2
    with open(p, encoding="utf-8", errors="replace") as f:
        sys.stdout.write(f.read())
    return 0


def main():
    ap = argparse.ArgumentParser(description="止损闸门")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("open"); a.add_argument("dir"); a.add_argument("target")
    b = sub.add_parser("beat"); b.add_argument("dir"); b.add_argument("text")
    c = sub.add_parser("check"); c.add_argument("dir"); c.add_argument("--minutes", type=int, default=14)
    d = sub.add_parser("show"); d.add_argument("dir")
    ns = ap.parse_args()
    if ns.cmd == "open":
        return cmd_open(ns.dir, ns.target)
    if ns.cmd == "beat":
        return cmd_beat(ns.dir, ns.text)
    if ns.cmd == "check":
        return cmd_check(ns.dir, ns.minutes)
    return cmd_show(ns.dir)


if __name__ == "__main__":
    sys.exit(main())
