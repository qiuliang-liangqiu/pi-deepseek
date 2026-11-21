#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""闭卷/靶场 run-*-events.csv 复盘：时间去向、stint 质量、重开率、尾声行为。
用法: python analyze_events.py <events.csv>
"""
import csv, io, sys, re, datetime, collections, statistics

CLOSE_EV = ("关闭靶场",)
SEC = 60.0


def load(path):
    rows = list(csv.reader(io.open(path, encoding="utf-8-sig")))
    data = [r for r in rows[1:] if r and r[0].strip()]
    ev = [(datetime.datetime.strptime(r[0].strip(), "%Y/%m/%d %H:%M:%S"),
           r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()) for r in data]
    return ev


def main(path):
    ev = load(path)
    start, end = ev[0][0], ev[-1][0]
    wall = (end - start).total_seconds()

    open_, seg = {}, []
    for ts, k, nm, code, info in ev:
        if k == "启动靶场":
            open_[code] = (ts, nm)
        elif k == "关闭靶场" and code in open_:
            a, n = open_.pop(code)
            seg.append((code, n, a, ts))
    for c, (ts, n) in open_.items():
        seg.append((c, n, ts, end))          # 结束时仍开着的按到结束算

    scores = []
    for ts, k, nm, code, info in ev:
        if k == "得分成功":
            m = re.search(r"\+(\d+)", info)
            scores.append((ts, code, nm, int(m.group(1)) if m else 0))
    by = collections.defaultdict(list)
    for ts, code, nm, p in scores:
        by[code].append((ts, p))
    solves = {
        c: {"first": v[0][0], "last": v[-1][0], "flags": len(v), "points": sum(p for _, p in v)}
        for c, v in by.items()
    }
    names = {c: n for c, n, _, _ in seg}
    codes = sorted(names)

    print("=" * 78)
    print(f"场次 {start} → {end}  ({wall/3600:.2f} h)")
    print(f"题目 {len(codes)} 道启动过 / 解出 {len(solves)} 道 / "
          f"flags {len(scores)} / 得分 {sum(x[3] for x in scores)}")
    tot = sum((b - a).total_seconds() for _, _, a, b in seg)
    st = sum((b - a).total_seconds() for c, _, a, b in seg if c in solves)
    print(f"容器时长合计 {tot/3600:.2f} h  平均并发 {tot/wall:.2f}"
          f"  （解题容器 {st/3600:.2f} h = {st/tot*100:.0f}%，"
          f"未解题 {100-st/tot*100:.0f}%）")

    # --- stint 质量：成败对比 ---
    ok, bad = [], []
    for c, n, a, b in seg:
        s = solves.get(c)
        (ok if (s and a <= s["first"] <= b) else bad).append(
            (c, n, (b - a).total_seconds() / 60))
    def stat(v, label):
        if not v: return
        xs = [x[2] for x in v]
        print(f"  {label:10s} n={len(xs):2d}  中位 {statistics.median(xs):5.1f} min"
              f"  合计 {sum(xs)/60:5.2f} h   <3min {sum(1 for x in xs if x<3):2d}"
              f"  >30min {sum(1 for x in xs if x>30):2d}")
    print("\n--- stint（一次开→关）质量 ---")
    stat(ok, "做出题的")
    stat(bad, "没做题的")
    print(f"  开箱频率 {len(seg)/ (wall/3600):.1f} 次/小时")

    # --- 重开率 ---
    cnt = collections.Counter(c for c, _, _, _ in seg)
    re_t = {c: v for c, v in cnt.items() if v > 1}
    extra = len(seg) - len(codes)
    print(f"\n--- 开箱 --- {len(codes)} 个目标，{len(seg)} 次开箱，"
          f"首开 {len(codes)}，额外 {extra}（triage 首开后的重开/重试/多段）")
    print(f"--- 重开 --- {len(re_t)} 个目标被开 ≥2 次，"
          f"这些目标共 {sum(re_t.values())} 次开箱")
    for c, v in sorted(re_t.items(), key=lambda x: -x[1])[:8]:
        mins = [round((b - a).total_seconds() / 60) for cc, _, a, b in seg if cc == c]
        print(f"  {names[c][:24]:24s} {c} {v} 次 {mins} "
              f"{'SOLVED' if c in solves else 'MISS'}")

    # --- 时间线：每小时产出 ---
    print("\n--- 每小时产出 ---")
    cum = 0
    for h in range(0, int(wall // 3600) + 1):
        lo = start + datetime.timedelta(hours=h)
        hi = lo + datetime.timedelta(hours=1)
        xs = [x for x in scores if lo <= x[0] < hi]
        p = sum(x[3] for x in xs); cum += p
        print(f"  {lo:%H:%M}-{hi:%H:%M}  {len(xs):2d} flags +{p:4d}  累计 {cum}")

    # --- 最贵的失败 ---
    print("\n--- 未解题按耗时排序 ---")
    agg = collections.defaultdict(list)
    for c, n, a, b in seg:
        if c not in solves:
            agg[c].append((b - a).total_seconds() / 60)
    for c, v in sorted(agg.items(), key=lambda x: -sum(x[1])):
        print(f"  {names[c][:24]:24s} {c} {len(v)} 次 {sum(v):6.1f} min "
              f"{[round(x) for x in v]}")

    # --- 尾声 / 解题后行为 ---
    tails = []
    for c, s in solves.items():
        last = s["last"]
        for cc, nn, a, b in seg:
            if cc == c and a <= last <= b:
                tails.append((b - last).total_seconds())
                break
    if tails:
        tails.sort()
        print(f"\n解题→关闭：中位 {tails[len(tails)//2]:.0f}s  "
              f"30s 内关闭 {sum(1 for x in tails if x <= 30)}/{len(tails)}")
    last = [e for e in ev if e[1] == "启动靶场"][-10:]
    if last:
        w = (end - last[0][0]).total_seconds() / 60
        print(f"最后 {w:.0f} min 内开箱 {len(last)} 次，0 解")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run-48753-events.csv")
