"""命令行入口。

跑分主流程：
    python -m framework.cli preflight                 # VPN 预检 + TSEC_TOKEN 检查
    python -m framework.cli status                    # 看进度/总分/SKIP
    python -m framework.cli next                      # 下一道该做的题（自动跳过 SKIP）
    python -m framework.cli run <code> [--watch]      # start + 双地址探活 + 指纹；可选看门狗
    python -m framework.cli auto <code>               # start -> playbooks -> submit -> close
    python -m framework.cli submit <code> <flag>
    python -m framework.cli close <code> [--force]     # 顺带归档；投入>5min 无笔记会拒绝（--force 放弃记录）
    python -m framework.cli report                    # 本地纪律面板：投入/停滞/笔记，按停滞降序
    python -m framework.cli enforce [--watch --close] # 全局 watchdog，默认 dry-run
    python -m framework.cli panic                     # 关闭自己开过的所有容器

纪律（已代码化，不再只靠提示词）：
    python -m framework.cli skip <code> <reason>      # 坏题只验一次，写入 work/SKIP.txt
    python -m framework.cli unskip <code>
    python -m framework.cli check-skip <code>         # exit 0=已跳过，1=未跳过
    python -m framework.cli skips                     # 列出 SKIP
    python -m framework.cli touch <code>              # 更新活动心跳（watch 用来判断“还在做”）
    python -m framework.cli hold <code> [reason]      # 看门狗禁止关闭
    python -m framework.cli unhold <code>
    python -m framework.cli watch <code> [选项]        # 分层时间盒看门狗

watch 选项：
    --tier easy|medium|hard|credential|intel  默认按难度取；credential=5/5，intel=20/45
    --budget 30m     软截止（停止开新方向）
    --hard-limit 60m 硬截止（返回/关闭）
    --grace 30m      硬截止-软截止（旧参数；与 --hard-limit 二选一）
    --idle 5m        硬截止后空闲多久才建议/执行关闭
    --stall 15m      停滞闸门：多久没有“新原语”就建议/执行关闭（优先级高于时间盒；0=关掉）
                     计时源：`work/<code>/.primitive` / `cli touch` 写的 `.heartbeat`，
                     都没有才退回文件 mtime——所以有新事实一定要 `touch`
    --poll 15s       轮询间隔
    --close          硬截止+空闲时真正 close（默认只警告）
    --hard           硬截止无条件 close（隐含 --close；credential 可用）
    --once           只判定一次并退出（0=keep，1=warn/close）

一次性延长（主控批准）：
    python -m framework.cli extend <code> 10m "new primitive/里程碑"
    python -m framework.cli unextend <code>

环境变量：TSEC_TOKEN（平台命令必需；preflight/skip/unskip/check-skip/skips/touch/hold/unhold
与 watch --once、extend/unextend 不需要）、TSEC_BASE_URL、
TSEC_HEALTH_URL（VPN 健康检查地址，必填）、TSEC_WORKDIR（默认 <repo>/work）、
TSEC_MAX_ACTIVE（可选，覆盖平台容器上限；默认 3，409 自动学习）、TSEC_CAPACITY_KEY（可选，容量缓存键）。
"""
from __future__ import annotations

import os
import sys
import time

from . import api, caselib, governance, playbooks, recon

# v2.5.2-local: the same two guards the image enforces in tbench_tools.ts.
import re as _re
TOUCH_MAX = int(os.environ.get("TSEC_TOUCH_MAX") or 6)
EVIDENCE_RE = _re.compile(
    r"(https?://|:\d|端口|port|凭据|凭证|credential|token|api[-_ ]?key|password|口令|"
    r"报错|错误|error|exception|状态码|status|回显|响应|length|长度|路径|path|payload|"
    r"flag\{|版本|version|banner|注入|rce|ssh|shell|源码|source|hash|端点|endpoint|接口|/[\w.%-]+)",
    _re.I)
CLAIM_RE = _re.compile(r"(不可解|无法解|题目缺陷|环境坏了|unsolvable|not solvable|broken)", _re.I)

VALUE_OPTS = {"--budget", "--hard-limit", "--grace", "--idle", "--poll", "--family", "--tier",
              "--stall", "--interval", "--since"}
BOOL_OPTS = {"--force", "--watch", "--close", "--once", "--hard", "--no-probe", "--no-auto-note"}


def parse_addr(addr):
    return governance.split_addr(addr)


def parse_args(argv):
    """极简参数解析：位置参数 + 已知长选项。"""
    pos, opts = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a.split("=", 1)
                if k not in VALUE_OPTS and k not in BOOL_OPTS:
                    raise ValueError(f"未知参数: {k}")
                opts[k] = v
            elif a in BOOL_OPTS:
                opts[a] = True
            elif a in VALUE_OPTS:
                if i + 1 >= len(argv):
                    raise ValueError(f"{a} 需要一个值")
                opts[a] = argv[i + 1]
                i += 1
            else:
                raise ValueError(f"未知参数: {a}")
        else:
            pos.append(a)
        i += 1
    return pos, opts


def _duration(opts, key, default):
    try:
        return governance.parse_duration(opts.get(key, default))
    except ValueError as e:
        raise ValueError(str(e))


def _tier_for(c, code, opts):
    """显式 --tier 优先；否则按题目难度 easy/medium/hard；未知用 medium。"""
    name = opts.get("--tier")
    if name:
        name = str(name).lower()
        if name not in governance.TIERS:
            raise ValueError(f"未知 tier: {name}（可选: {', '.join(governance.TIERS)}）")
        return name
    if c is None:
        return governance.DEFAULT_TIER
    try:
        for x in c.challenges():
            if x.get("unique_code") == code:
                d = str(x.get("difficulty", "")).lower()
                return d if d in ("easy", "medium", "hard") else governance.DEFAULT_TIER
    except Exception:  # network/timeout/API errors all fall back to medium
        pass
    return governance.DEFAULT_TIER


def _normalize_addrs(addrs):
    if isinstance(addrs, str):
        return [addrs]
    return [str(a) for a in (addrs or [])]


def _probe(c, code, addrs, no_probe=False):
    """start 后探活所有地址；返回 (chosen, alive)。"""
    addrs = _normalize_addrs(addrs)
    if not addrs:
        return None, []
    if no_probe:
        print(f"[probe] skipped ({len(addrs)} addr): {', '.join(addrs)}")
        return addrs[0], addrs
    results = governance.probe_addrs(addrs)
    alive = []
    for r in results:
        status = "alive" if r.ok else "dead"
        print(f"[probe] {r.addr} {status} {r.ms}ms{(' ' + r.error) if r.error else ''}")
        if r.ok:
            alive.append(r.addr)
    if not alive:
        print("[probe] WARN: 所有返回地址都不可达；先检查 VPN/容器，不要继续盲打")
        return addrs[0], []
    if len(alive) < len(addrs):
        print(f"[probe] {len(alive)}/{len(addrs)} 可达，使用 {alive[0]}")
    return alive[0], alive


# ---------------------------------------------------------------- 只读
def cmd_status(c):
    ch = c.challenges()
    done = [x for x in ch if x.get("is_completed")]
    score = sum(x.get("total_score", 0) for x in done)
    active = [x["unique_code"] for x in ch if x.get("container_status") == "available"]
    skipped = governance.SkipRegistry().codes()
    cap = getattr(c, "max_active", 3)
    src = getattr(c, "capacity_source", "default")
    print(f"进度 {len(done)}/{len(ch)}  总分 {score}")
    print(f"active {len(active)}/{cap} ({src})" + (": " + ", ".join(active) if active else ""))
    if skipped:
        print(f"SKIP {len(skipped)}:", ", ".join(sorted(skipped)))
    for x in ch:
        if not x.get("is_completed"):
            code = x["unique_code"]
            tag = "  [SKIP]" if code in skipped else ""
            print(f"  TODO {code:<16} {x.get('difficulty'):<7} {x.get('total_score')}{tag}")


def cmd_next(c):
    p = c.pending()
    skipped = governance.SkipRegistry().codes()
    skipped_pending = [x for x in p if x["unique_code"] in skipped]
    p = [x for x in p if x["unique_code"] not in skipped]
    if not p:
        if skipped_pending:
            print("剩余题目都在 SKIP 列表里；用 `unskip <code>` 恢复：")
            for x in skipped_pending:
                print(f"  SKIP {x['unique_code']} {x.get('difficulty')} {x.get('total_score')}")
        else:
            print("全部完成")
        return
    x = p[0]
    print(x["unique_code"], x.get("difficulty"), x.get("total_score"))


def cmd_report(stall="15m"):
    """本地纪律面板：每题投入/停滞/笔记，按停滞降序（先处理停滞的）。

    对照 DOCTRINE §9：失败 stint 中位应 < 15min，无笔记关箱应为 0。
    """
    try:
        stall_s = governance.parse_duration(stall)
    except ValueError as e:
        print(e)
        return 2
    base = governance.work_root()
    now = governance.now_utc()
    rows = []
    for d in (sorted(base.iterdir()) if base.is_dir() else []):
        if not d.is_dir():
            continue
        code = d.name
        started = governance.read_started(code)
        if started is None or governance.is_closed(code):
            continue          # 只看“本轮还开着”的题，历史/已关的不污染面板
        elapsed = (now - started).total_seconds()
        idle = (now - governance.last_primitive(code)).total_seconds()
        status = ""
        st = d / "STATUS.txt"
        if st.is_file():
            try:
                status = ((st.read_text(encoding="utf-8", errors="replace").splitlines()
                           or [""])[0])[:30]
            except OSError:
                status = ""
        rows.append((code, elapsed, idle, governance.has_notes(code),
                     governance.is_held(code), status))
    rows.sort(key=lambda r: -r[2])
    stalled = [r for r in rows if stall_s > 0 and r[2] >= stall_s]
    gap = [r for r in rows if not r[3] and r[1] >= governance.NOTE_MIN_ELAPSED]
    print(f"在册 {len(rows)} 题   停滞(>={governance.fmt_duration(stall_s)}) {len(stalled)}   "
          f"无笔记 {len(gap)}   SKIP {len(governance.SkipRegistry().codes())}")
    if not rows:
        print("（work/ 下没有题目记录；先 `run <code>`）")
        return 0
    print(f"\n{'code':<18}{'投入':>7}{'停滞':>7}  笔记 hold  状态")
    for code, elapsed, idle, notes, held, status in rows:
        print(f"{code:<18}{governance.fmt_duration(elapsed):>9}"
              f"{governance.fmt_duration(idle):>9}  {'yes' if notes else 'NO ':>3}  "
              f"{'hold' if held else '    '}  {status}")
    if stalled:
        print("\n先处理停滞的（写三行笔记 → close / 有新事实才 touch）："
              + ", ".join(r[0] for r in stalled))
    return 0


def cmd_scan(addr):
    host, port = parse_addr(addr)
    ev = recon.fingerprint(host, port)
    for k, v in ev.items():
        print(f"{k}: {v}")


# ---------------------------------------------------------------- 主流程
def _flags_path(code):
    return governance.code_dir(code) / ".flags"


def _read_flags(code):
    try:
        f = _flags_path(code)
        started = governance.code_dir(code) / ".started"
        if f.exists() and started.exists() and f.stat().st_mtime < started.stat().st_mtime - 5:
            return 0  # stale count from an earlier instance
        return max(0, int(f.read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return 0


def _write_flags(code, n):
    try:
        _flags_path(code).write_text(str(max(0, int(n))), encoding="utf-8")
    except OSError:
        pass


def _mark_close_pending(code, err=""):
    try:
        (governance.code_dir(code) / ".close_pending").write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {err}\n", encoding="utf-8"
        )
    except OSError:
        pass


def _clear_close_pending(code):
    try:
        (governance.code_dir(code) / ".close_pending").unlink()
    except OSError:
        pass


def _close_quietly(c, code):
    """Release a box after a failed probe/start; never leak a slot."""
    try:
        c.close(code)
        governance.mark_closed(code)
        _clear_close_pending(code)
    except Exception:
        _mark_close_pending(code, "probe/start cleanup failed")


def _auto_note(code, reason):
    """A watchdog close must leave a minimal three-line note."""
    p = governance.code_dir(code) / "NOTES.md"
    try:
        if p.exists() and p.stat().st_size > 0:
            return
    except OSError:
        pass
    try:
        p.write_text(
            f"# {code} — auto-closed\n\n"
            f"reason: {reason}\n"
            "- 入口: unknown (watchdog)\n"
            "- 已试: see .started / logs / FINDINGS\n"
            "- 下一招: re-open once, run triage.sh, then decide\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _retry_pending(c):
    """Retry closes that failed in an earlier process (close_pending ledger)."""
    try:
        paths = list(governance.work_root().glob("*/.close_pending"))
    except OSError:
        return
    for p in paths:
        code = p.parent.name
        try:
            c.close(code)
            governance.mark_closed(code)
            _clear_close_pending(code)
            print(f"[close] retried {code}: ok")
        except Exception as e:  # noqa: BLE001
            print(f"[close] retried {code}: failed ({e})")


def cmd_auto(c, code, opts):
    reg = governance.SkipRegistry()
    skip = reg.get(code)
    if skip and not opts.get("--force"):
        print(f"[skip] {code} 已在 SKIP 列表：{skip.reason or '(无原因)'}")
        print("       如确认要重试，加 --force")
        return 2

    try:
        ch = c.challenge(code) or {}
    except Exception:
        ch = {}
    if ch.get("is_completed") and not opts.get("--force"):
        print(f"[gate] {code} 已经 is_completed=true；不重开（确需重开加 --force）")
        return 2

    _retry_pending(c)
    addrs = _normalize_addrs(c.start(code))
    if not addrs:
        print("start 失败：无 container_addr")
        _close_quietly(c, code)
        return 3
    governance.mark_started(code)
    try:
        chosen, alive = _probe(c, code, addrs, no_probe=opts.get("--no-probe"))
        if not alive:
            return 3

        host, port = parse_addr(chosen)
        print("addr:", chosen)
        t = playbooks.Target(host, port)
        print("fingerprint:", recon.fingerprint(host, port).get("families"))
        flag, play = playbooks.auto(t)
        if flag:
            r = c.submit(code, flag)
            print("submit:", r)
            if r.get("correct"):
                _write_flags(code, int(c.flags.get(code, 0) or 0))
                governance.touch(code)
        else:
            print("playbooks 未命中，需要人工（见 docs/PLAYBOOK.md）")
        return 0
    finally:
        # auto 无论成功/异常都释放容器，避免占住 3 个名额
        try:
            c.close(code)
        except Exception as e:  # noqa: BLE001
            _mark_close_pending(code, f"auto close failed: {e}")
        governance.unhold(code)
        governance.clear_extension(code)


def cmd_run(c, code, opts):
    reg = governance.SkipRegistry()
    skip = reg.get(code)
    if skip and not opts.get("--force"):
        print(f"[skip] {code} 已在 SKIP 列表：{skip.reason or '(无原因)'}")
        print("       如确认要重试，加 --force")
        return 2

    try:
        ch = c.challenge(code) or {}
    except Exception:
        ch = {}
    if ch.get("is_completed") and not opts.get("--force"):
        print(f"[gate] {code} 已经 is_completed=true；不重开（确需重开加 --force）")
        return 2

    _retry_pending(c)
    addrs = _normalize_addrs(c.start(code))
    if not addrs:
        print("start 失败：无 container_addr")
        _close_quietly(c, code)
        return 3
    print("addr:", addrs)
    governance.mark_started(code)
    chosen, alive = _probe(c, code, addrs, no_probe=opts.get("--no-probe"))
    if not alive:
        _close_quietly(c, code)
        return 3
    host, port = parse_addr(chosen)
    print("fingerprint:", recon.fingerprint(host, port))

    if opts.get("--watch"):
        return cmd_watch(c, code, opts)
    return 0


def cmd_close(c, code, family=None, force=False):
    allow, msg = governance.close_note_gate(code)
    if not allow and not force:
        print("[gate] 不给笔记就不关箱（容器时长是有限资源，下轮无法从零重开）：")
        print(msg)
        print(f"       写完后重试，或 `close {code} --force` 明确放弃记录")
        return 4
    # Multi-flag gate: a flag or two is not completion. Only is_completed /
    # flag_total ends the box; otherwise keep it for the remaining flags.
    try:
        ch = c.challenge(code) or {}
    except Exception:
        ch = {}
    done = bool(ch.get("is_completed")) or code in getattr(c, "completed", set())
    total = int(ch.get("flag_total") or ch.get("flag_count") or ch.get("total_flags") or ch.get("flags") or 0)
    got = max(
        int(getattr(c, "flags", {}).get(code, 0) or 0),
        _read_flags(code),
    )
    if not done and got > 0 and not force:
        print(f"[gate] {code} has {got}/{total or '?'} flags but is_completed=false；")
        print("       继续找剩余 flag，或 `close --force` 明确放弃")
        return 5
    try:
        print(c.close(code))
    except Exception as e:
        print(f"[close] failed: {e}；箱子仍在 owned，重试 close")
        _mark_close_pending(code, str(e))
        return 1
    _clear_close_pending(code)
    governance.mark_closed(code)
    governance.unhold(code)
    governance.clear_extension(code)
    governance.clear_extension(code)
    # 默认行为：释放容器后把登记表沉淀成可复用打法（失败不阻断）
    p = caselib.archive(code, family=family)
    if p:
        print(f"[caselib] 已归档 -> {p}")
    return 0


def cmd_archive(argv):
    if not argv:
        print(f"[caselib] 索引已重建 -> {caselib.rebuild_index()}")
        return 0
    try:
        pos, opts = parse_args(argv)
    except ValueError as e:
        print(e)
        return 2
    fam = opts.get("--family")
    if not pos:
        print(f"[caselib] 索引已重建 -> {caselib.rebuild_index()}")
        return 0
    for code in pos:
        p = caselib.archive(code, family=fam)
        print(f"{code}: {p or '无记录（work/bb_%s.md 或 work/%s/FINDINGS.md），跳过' % (code, code)}")
    return 0


# ---------------------------------------------------------------- 看门狗
def cmd_watch(c, code, opts):
    try:
        tier_name = _tier_for(c, code, opts)
    except ValueError as e:
        print(e)
        return 2
    tier = governance.TIERS[tier_name]
    if "--budget" in opts:
        soft = _duration(opts, "--budget", tier["soft"])
    else:
        soft = tier["soft"]
    if "--hard-limit" in opts:
        hard = _duration(opts, "--hard-limit", tier["hard"])
    elif "--grace" in opts:
        hard = soft + _duration(opts, "--grace", max(0, tier["hard"] - tier["soft"]))
    else:
        # --budget 只改软截止；硬截止仍按 tier，除非显式 --hard-limit/--grace
        hard = tier["hard"]
    idle = _duration(opts, "--idle", "5m")
    stall = _duration(opts, "--stall", "15m")
    poll = max(1, _duration(opts, "--poll", "15s"))
    force_hard = bool(opts.get("--hard"))
    allow_close = bool(opts.get("--close") or force_hard)
    once = bool(opts.get("--once"))

    if not governance.read_started(code):
        # watch only fills a missing marker; it must not wipe an extension/hold
        # that was granted before the first watch (mark_started(overwrite=True)
        # clears .extend/.hold).
        governance.mark_started(code, overwrite=False)
    if c is not None:
        _retry_pending(c)
    ext = governance.read_extension(code)
    ext_txt = f" +{governance.fmt_duration(ext.seconds)}" if ext else ""
    print(f"[watch] {code} tier={tier_name} soft={governance.fmt_duration(soft)} "
          f"hard={governance.fmt_duration(hard)} stall={governance.fmt_duration(stall)} "
          f"idle={governance.fmt_duration(idle)}"
          f"{(' extension=' + ext_txt.strip()) if ext else ''} "
          f"close={'on' if allow_close else 'off(warn only)'} hard={'on' if force_hard else 'off'}")

    while True:
        d = governance.decide_watch(code, soft, hard, idle,
                                    allow_close=allow_close, force_hard=force_hard,
                                    stall=stall)
        print(f"[watch] elapsed={governance.fmt_duration(d.elapsed)} "
              f"idle={governance.fmt_duration(d.idle)} "
              f"stall={governance.fmt_duration(d.idle_primitive)} phase={d.phase or '-'} -> "
              f"{d.action.upper()} ({d.reason})")
        if d.action in ("close", "would_close"):
            if d.action == "close":
                if c is None:
                    print("[watch] 需要 TSEC_TOKEN 才能执行 close；当前只做了判定")
                    return 3
                try:
                    rc = cmd_close(
                        c, code,
                        force=force_hard or bool(opts.get("--force")),
                    )
                    if rc == 4 and not opts.get("--no-auto-note"):
                        _auto_note(code, d.reason)
                        rc = cmd_close(
                            c, code,
                            force=force_hard or bool(opts.get("--force")),
                        )
                except api.ApiError as e:
                    print(f"[watch] close 失败: {e.code} {e.message}")
                    _mark_close_pending(code, f"{e.code} {e.message}")
                    return 3
                return 2 if rc == 0 else 1
            print("[watch] 未关闭：默认只警告；--close 才执行（停滞闸门 / 硬截止+空闲），"
                  "--hard 才会到硬截止无条件关闭；也可 `extend` 一次")
            return 1
        if d.action == "hold":
            print("[watch] 存在 .hold，继续保留；要恢复自动判定先 `unhold`")
        if once:
            return 0 if d.action in ("keep", "hold") else 1
        try:
            time.sleep(poll)
        except KeyboardInterrupt:
            print("\n[watch] 收到 Ctrl+C，不关闭容器")
            return 130


# ---------------------------------------------------------------- 纪律命令
def cmd_enforce(c, opts):
    """Watchdog for all .started boxes; dry-run unless --close.

    This is the framework-side twin of team_pool's enforcer. It does not start
    automatically: a daemon closing 68 old .started dirs is not what you want.
    Use --since <epoch> to limit it to a run.
    """
    force_hard = bool(opts.get("--hard"))
    force_close = force_hard or bool(opts.get("--force"))
    close = bool(opts.get("--close") or force_close)
    stall = _duration(opts, "--stall", "15m")
    idle = _duration(opts, "--idle", "0")
    interval = max(5, _duration(opts, "--interval", "30s"))
    try:
        since = float(opts.get("--since", 0) or 0)
    except (TypeError, ValueError):
        print("--since 需要 epoch 秒")
        return 2
    watch = bool(opts.get("--watch"))
    root = governance.work_root()
    stop = root / ".enforce.stop"
    while True:
        if c is not None:
            _retry_pending(c)
        for p in sorted(root.glob("*/.started")):
            code = p.parent.name
            if governance.is_closed(code):
                continue
            if governance.is_held(code):
                print(f"[enforce] {code}: hold marker; skipped")
                continue
            started = governance.read_started(code)
            if since and started and started.timestamp() < since:
                continue
            try:
                tier_name = _tier_for(c, code, opts)
                tier = governance.TIERS[tier_name]
            except ValueError as e:
                print(e)
                continue
            d = governance.decide_watch(
                code, tier["soft"], tier["hard"], idle,
                allow_close=close, force_hard=force_hard, stall=stall,
            )
            print(f"[enforce] {code} elapsed={governance.fmt_duration(d.elapsed)} "
                  f"stall={governance.fmt_duration(d.idle_primitive)} "
                  f"phase={d.phase or '-'} -> {d.action.upper()} ({d.reason})")
            if d.action == "close" and close:
                rc = cmd_close(c, code, force=force_close)
                if rc == 4:
                    _auto_note(code, d.reason)
                    rc = cmd_close(c, code, force=force_close)
                if rc != 0:
                    print(f"[enforce] {code}: close not performed (rc={rc})")
            elif d.action == "would_close":
                print("[enforce] dry-run; add --close to act")
        if not watch:
            return 0
        if stop.exists():
            try:
                stop.unlink()
            except OSError:
                pass
            print("[enforce] stop requested")
            return 0
        time.sleep(interval)


def cmd_extend(argv):
    try:
        pos, opts = parse_args(argv)
    except ValueError as e:
        print(e)
        return 2
    if len(pos) < 2:
        print("用法: extend <code> <时长> [reason ...] [--force]  例如: extend b-01 10m \"new primitive: config read\"")
        return 2
    try:
        seconds = governance.parse_duration(pos[1])
    except ValueError as e:
        print(e)
        return 2
    ext = governance.grant_extension(pos[0], seconds, " ".join(pos[2:]), force=bool(opts.get("--force")))
    if ext is None:
        print(f"[extend] {pos[0]} 已有 extension；如确认覆盖加 --force")
        return 1
    print(f"[extend] {pos[0]} +{governance.fmt_duration(ext.seconds)} (一次性)")
    if ext.reason:
        print(f"         {ext.reason}")
    return 0


def cmd_unextend(argv):
    if not argv:
        print("用法: unextend <code>")
        return 2
    ok = governance.clear_extension(argv[0])
    print(f"[extend] {'removed ' + argv[0] if ok else 'not found: ' + argv[0]}")
    return 0 if ok else 1


def cmd_skip(argv):
    if not argv:
        print("usage: skip <code> [reason ...] [--instances N] [--force]")
        return 2
    force = "--force" in argv
    instances = 0
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--force":
            i += 1
        elif argv[i] == "--instances" and i + 1 < len(argv):
            try:
                instances = int(argv[i + 1])
            except ValueError:
                instances = 0
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    code = rest[0]
    reason = " ".join(rest[1:]).strip()
    # v2.5.2-local: "unsolvable" is a claim, not an observation (10 challenges
    # were written off this way on 2026-11-18 while the image solved them).
    if CLAIM_RE.search(reason) and not force and instances < 3:
        print("[skip] refused: that reason claims the box is unsolvable. Re-check on")
        print("       3 fresh instances first, then `skip <code> <reason> --instances 3`,")
        print("       or --force to record the claim in work/blockers.md.")
        return 3
    reg = governance.SkipRegistry()
    e = reg.add(code, reason)
    if CLAIM_RE.search(reason):
        try:
            with open(governance.work_root() / "blockers.md", "a", encoding="utf-8") as fh:
                fh.write("## %s - claimed unsolvable (instances=%d, force=%s)\n%s\n\n"
                         % (code, instances, force, reason))
        except Exception:
            pass
    print("[skip] %s -> %s" % (e.code, reg.path))
    if e.reason:
        print("       %s" % e.reason)
    return 0


def cmd_unskip(argv):
    if not argv:
        print("用法: unskip <code>")
        return 2
    reg = governance.SkipRegistry()
    ok = reg.remove(argv[0])
    print(f"[skip] {'removed ' + argv[0] if ok else 'not found: ' + argv[0]}")
    return 0 if ok else 1


def cmd_check_skip(argv):
    if not argv:
        print("用法: check-skip <code>")
        return 2
    e = governance.SkipRegistry().get(argv[0])
    if e:
        print(f"SKIP {e.code}\t{e.added_at}\t{e.reason}")
        return 0
    print(f"NOT-SKIPPED {argv[0]}")
    return 1


def cmd_skips(_argv=None):
    reg = governance.SkipRegistry()
    entries = sorted(reg.entries(), key=lambda e: e.code)
    if not entries:
        print("(SKIP 列表为空)")
        return 0
    print(f"# {reg.path}")
    for e in entries:
        print(f"{e.code}\t{e.added_at}\t{e.reason}")
    return 0


def cmd_touch(argv):
    if not argv:
        print("usage: touch <code> <one-line new fact>   # e.g. touch a-01 'new endpoint /admin 200'")
        return 2
    code = argv[0]
    what = " ".join(argv[1:]).strip()
    # v2.5.2-local: a touch is a fact, not a heartbeat.
    if len(what) < 8 or not EVIDENCE_RE.search(what):
        print("[touch] rejected: name the new fact (endpoint / credential / error class /")
        print("        status or length change / working payload stage), >=8 chars.")
        print("        Activity is not a primitive; with nothing new, close the box.")
        return 3
    d = governance.code_dir(code)
    try:
        d.mkdir(parents=True, exist_ok=True)
        cnt = d / ".touches"
        n = int(cnt.read_text().strip() or 0) if cnt.exists() else 0
        if n >= TOUCH_MAX:
            print("[touch] %s exhausted (%d/%d): close it and spend the slot elsewhere."
                  % (code, n, TOUCH_MAX))
            return 3
        cnt.write_text(str(n + 1), encoding="utf-8")
        (d / ".primitive").write_text("%.0f\t%s\n" % (time.time(), what), encoding="utf-8")
    except Exception:
        pass
    p = governance.touch(code)
    print("[touch] %s -> %s: %s" % (code, p, what))
    return 0


def cmd_hold(argv):
    if not argv:
        print("用法: hold <code> [reason ...]")
        return 2
    p = governance.hold(argv[0], reason=" ".join(argv[1:]))
    print(f"[hold] {argv[0]} -> {p}")
    return 0


def cmd_unhold(argv):
    if not argv:
        print("用法: unhold <code>")
        return 2
    ok = governance.unhold(argv[0])
    print(f"[hold] {'removed ' + argv[0] if ok else 'not held: ' + argv[0]}")
    return 0 if ok else 1


def cmd_preflight(_argv=None):
    r = governance.preflight()
    print(f"[preflight] {r.url}")
    if r.ok:
        print(f"  status=ok client_ip={r.data.get('client_ip', '?')} "
              f"time={r.data.get('time', '?')}")
    else:
        print(f"  FAILED: {r.error or r.data}")
        print("  VPN 不通时平台 API 仍可能 200，但所有 container_addr 都连不上；先修复网络。")
    token = os.environ.get("TSEC_TOKEN")
    print(f"  TSEC_TOKEN: {'set' if token else 'MISSING'}")
    if not r.ok:
        return 1
    if not token:
        print("  VPN 通了，但 TSEC_TOKEN 缺失；平台命令会失败，先设置 token。")
        return 1
    return 0


# ---------------------------------------------------------------- 入口
def main(argv=None):
    try:  # Windows 控制台默认 GBK，中文/emoji 会乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]

    # Workers (team_pool) must not mutate the main agent's local discipline
    # state. Read-only commands are still allowed.
    if os.environ.get("TEAM_WORKER") == "1" and cmd in {
        "touch", "hold", "unhold", "extend", "unextend", "skip", "unskip",
        "close", "submit", "start", "run", "auto", "watch", "enforce", "panic",
    }:
        print("[worker] local platform/discipline commands are main-agent only", file=sys.stderr)
        return 1

    # 纯本地命令：不需要平台 token
    if cmd == "scan":
        if len(argv) < 2:
            print("用法: scan <host:port>")
            return 2
        cmd_scan(argv[1])
        return 0
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "archive":
        return cmd_archive(argv[1:])
    if cmd == "report":
        try:
            pos, opts = parse_args(argv[1:])
        except ValueError as e:
            print(e)
            return 2
        return cmd_report(opts.get("--stall", "15m"))
    # watch --once 不需要平台 token：只读 .started/心跳并给出判定。
    # 带 --close 时才需要 token 真正关容器。
    if cmd == "watch" and "--once" in argv and "--close" not in argv and "--hard" not in argv:
        try:
            pos, opts = parse_args(argv[1:])
        except ValueError as e:
            print(e)
            return 2
        if not pos:
            print("用法: watch <code> --once [--budget 20m --grace 5m --stall 15m --idle 5m]")
            return 2
        return cmd_watch(None, pos[0], opts)
    if cmd == "enforce" and "--close" not in argv and "--watch" not in argv:
        try:
            _, opts = parse_args(argv[1:])
        except ValueError as e:
            print(e)
            return 2
        return cmd_enforce(None, opts)
    if cmd == "preflight":
        return cmd_preflight(argv[1:])
    if cmd == "skip":
        return cmd_skip(argv[1:])
    if cmd == "unskip":
        return cmd_unskip(argv[1:])
    if cmd == "check-skip":
        return cmd_check_skip(argv[1:])
    if cmd == "skips":
        return cmd_skips(argv[1:])
    if cmd == "touch":
        return cmd_touch(argv[1:])
    if cmd == "hold":
        return cmd_hold(argv[1:])
    if cmd == "unhold":
        return cmd_unhold(argv[1:])
    if cmd == "extend":
        return cmd_extend(argv[1:])
    if cmd == "unextend":
        return cmd_unextend(argv[1:])

    try:
        c = api.BenchmarkClient()
    except api.ApiError as e:
        print(f"无法初始化平台客户端: {e.code} {e.message}")
        print("请先设置环境变量 TSEC_TOKEN（以及可选的 TSEC_BASE_URL）")
        return 2

    try:
        try:
            pos, opts = parse_args(argv[1:])
        except ValueError as e:
            print(e)
            return 2

        if cmd == "status":
            cmd_status(c)
        elif cmd == "next":
            cmd_next(c)
        elif cmd in ("auto", "run"):
            if not pos:
                print(f"用法: {cmd} <code> [选项]")
                return 2
            if cmd == "auto":
                return cmd_auto(c, pos[0], opts)
            return cmd_run(c, pos[0], opts)
        elif cmd == "watch":
            if not pos:
                print("用法: watch <code> [--budget 20m --grace 5m --idle 5m --close --hard --once]")
                return 2
            return cmd_watch(c, pos[0], opts)
        elif cmd == "enforce":
            return cmd_enforce(c, opts)
        elif cmd == "submit":
            if len(pos) < 2:
                print("用法: submit <code> <flag>")
                return 2
            # A stopped container makes the platform answer correct=false even
            # for a right flag. Auto-start once if it is not available.
            try:
                me = next((x for x in c.challenges() if x.get("unique_code") == pos[0]), None)
                if me and not me.get("is_completed") and me.get("container_status") != "available":
                    print(f"[start] {pos[0]} -> {c.start(pos[0])}")
                    governance.mark_started(pos[0])
            except Exception as e:  # noqa: BLE001
                print(f"[start] pre-check failed ({e}); trying submit anyway")
            r = c.submit(pos[0], pos[1])
            print(r)
            if r.get("correct"):
                _write_flags(pos[0], int(c.flags.get(pos[0], 0) or 0))
                governance.touch(pos[0])
            return 0
        elif cmd == "close":
            if not pos:
                print("用法: close <code> [family] [--force]")
                return 2
            fam = opts.get("--family") or (pos[1] if len(pos) > 1 else None)
            return cmd_close(c, pos[0], fam, force=bool(opts.get("--force")))
        elif cmd == "panic":
            _retry_pending(c)
            # c.owned is process-local; also read the .started ledger so a new
            # process can still close boxes opened by an earlier one.
            codes = set(c.owned)
            try:
                for p in governance.work_root().iterdir():
                    if (p / ".started").exists() and not (p / ".closed").exists():
                        codes.add(p.name)
            except OSError:
                pass
            n = 0
            for code in sorted(codes):
                try:
                    c.close(code)
                    governance.mark_closed(code)
                    governance.unhold(code)
                    governance.clear_extension(code)
                    _clear_close_pending(code)
                    if caselib.archive(code):
                        n += 1
                except Exception as e:  # noqa: BLE001
                    _mark_close_pending(code, str(e))
                    print(f"[panic] {code} close failed: {e}")
            print("closed:", f"{len(codes)} known", f"| archived: {n}")
        else:
            print(__doc__)
    except api.ApiError as e:
        print(f"API 错误: {e.code} {e.message}")
        if e.code == "task_not_found":
            print("token 无效/缺失，停止")
        if e.code == "invalid_state":
            print("任务已结束（超时/停止），停止")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
