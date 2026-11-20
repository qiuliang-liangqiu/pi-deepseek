#!/usr/bin/env python3
"""bench helper CLI — v2.5.2-local.

Every command prints a `[clock ...]` line, and the four run rules that the
container image enforces in code are enforced here as well:

  1. `finish` refuses while budget is left and challenges are unsolved.
  2. `status`/every command warns `IDLE` when no box is open.
  3. `touch` needs a real fact (>=8 chars + evidence token), max 6 per box.
  4. `skip` treats "unsolvable" as a claim: needs --instances >= 3 or --force,
     and the claim lands in work/blockers.md.

Usage
  python t.py start <code>            # start container, print addresses + clock
  python t.py submit <code> <flag>    # submit flag
  python t.py close <code>            # close container
  python t.py list                    # list pending challenges
  python t.py addr <code>             # show container_addr
  python t.py status                  # clock + every open box
  python t.py touch <code> --what "new endpoint /admin 200"
  python t.py skip <code> "<reason>" [--instances 3] [--force]
  python t.py unskip <code>
  python t.py finish [--force] [--reason budget]
  python t.py get <url>
  python t.py post <url> <data>

Env: TSEC_TOKEN (required for API commands), TSEC_BASE_URL,
     TSEC_MAX_SECONDS (default 21600 = 6h), TSEC_TOUCH_MAX (default 6).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("TSEC_TOKEN", "")
BASE = os.environ.get("TSEC_BASE_URL", "<your-platform-base-url>")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("TSEC_WORK_DIR") or os.path.join(os.path.dirname(HERE), "..", "work")
WORK = os.path.abspath(WORK)
STATE = os.path.join(WORK, ".local-state.json")

MAX_SECONDS = int(os.environ.get("TSEC_MAX_SECONDS") or 21600)
TOUCH_MAX = int(os.environ.get("TSEC_TOUCH_MAX") or 6)
FINISH_MIN_LEFT = int(os.environ.get("TSEC_FINISH_MIN_LEFT") or max(120, MAX_SECONDS // 20))
TIERS = {"easy": (300, 600), "medium": (600, 1200), "hard": (1800, 3600)}
EVIDENCE_RE = re.compile(
    r"(https?://|:\d|端口|port|凭据|凭证|credential|token|api[-_ ]?key|password|口令|"
    r"报错|错误|error|exception|状态码|status|回显|响应|length|长度|路径|path|payload|"
    r"flag\{|版本|version|banner|注入|rce|ssh|shell|源码|source|hash|端点|endpoint|接口|/[\w.%-]+)",
    re.I,
)

sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
try:
    from framework import governance  # noqa: E402
except Exception:  # pragma: no cover - the CLI must work without the package
    governance = None


# ---------------------------------------------------------------- state

def _load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    st.setdefault("run_started", time.time())
    st.setdefault("boxes", {})
    st.setdefault("catalog", {})
    st.setdefault("flags", {})
    return st


def _save(st):
    os.makedirs(WORK, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _fmt(sec):
    sec = int(max(0, sec))
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h}h{m:02d}m" if h else f"{m}m{sec % 60:02d}s"


def budget_left(st=None):
    st = st or _load()
    return MAX_SECONDS - (time.time() - st["run_started"])


# ---------------------------------------------------------------- API

def req(path, method="GET", body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("BENCHMARK_TOKEN", TOKEN)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            j = json.loads(raw)
        except Exception:
            j = {"code": "http_%d" % e.code, "message": raw[:300]}
        return e.code, j
    except Exception as e:  # network/VPN down
        return 0, {"code": "network", "message": f"{type(e).__name__}: {e}"}


def catalog(refresh=False):
    st = _load()
    if refresh or not st.get("catalog"):
        code, ch = req("/openapi/v1/challenges")
        if code == 200 and isinstance(ch, list):
            st["catalog"] = {
                c["unique_code"]: {
                    "score": c.get("total_score", 0),
                    "difficulty": (c.get("difficulty") or "medium").lower(),
                    "flag_total": c.get("flag_count", 1),
                    "is_completed": bool(c.get("is_completed")),
                }
                for c in ch
            }
            _save(st)
    return st.get("catalog") or {}


# ---------------------------------------------------------------- clock

def clock(st=None):
    st = st or _load()
    cat = st.get("catalog") or {}
    now = time.time()
    parts = []
    stalled = 0
    for code, b in sorted(st["boxes"].items()):
        diff = cat.get(code, {}).get("difficulty", b.get("difficulty", "medium"))
        soft, hard = TIERS.get(diff, TIERS["medium"])
        ext = b.get("extend", 0)
        limit = hard + ext
        age = now - b.get("started", now)
        idle = now - max(b.get("started", now), b.get("primitive", 0))
        iff = min(limit - age, 900 - idle)
        tag = ""
        if b.get("flags", 0) >= cat.get(code, {}).get("flag_total", 1):
            tag = " DONE"
        elif idle >= 900:
            tag = " STALL"
            stalled += 1
        parts.append(f"{code} {diff} {_fmt(age)}/{_fmt(limit)}{'+' if ext else ''}{tag} auto {_fmt(iff)}")
    unsolved = [c for c, v in cat.items() if not v.get("is_completed") and c not in st.get("solved", [])]
    left = budget_left(st)
    line = (f"[clock t+{_fmt(now - st['run_started'])} | budget {_fmt(left)} left | "
            f"open: {', '.join(parts) if parts else 'none'} | "
            f"score {sum(st.get('flags', {}).values())} flags | stall {stalled}]")
    if not parts and unsolved and left > FINISH_MIN_LEFT:
        line += f" | IDLE: 0 open / {len(unsolved)} unsolved / {_fmt(left)} left — open the next box NOW"
    if left > FINISH_MIN_LEFT and unsolved:
        line += " | no report, no questions — keep working"
    return line


def emit(st=None, extra=""):
    st = st or _load()
    _save(st)
    if extra:
        print(extra)
    print(clock(st))


# ---------------------------------------------------------------- commands

def cmd_start(code):
    st = _load()
    code_, ch = req("/openapi/v1/challenges/start?unique_code=" + urllib.parse.quote(code), "POST")
    if code_ == 200:
        cat = catalog(refresh=True)
        diff = cat.get(code, {}).get("difficulty", "medium")
        st["boxes"][code] = {"started": time.time(), "primitive": time.time(),
                             "touches": 0, "flags": st.get("flags", {}).get(code, 0),
                             "difficulty": diff}
        soft, hard = TIERS.get(diff, TIERS["medium"])
        emit(st, f"{code_} {json.dumps(ch, ensure_ascii=False)}\n"
                 f"[tier] {code} {diff}: soft {_fmt(soft)} / hard {_fmt(hard)} — close at the stall gate, not at 0 progress")
    else:
        emit(st, f"{code_} {json.dumps(ch, ensure_ascii=False)}")


def cmd_close(code):
    st = _load()
    code_, ch = req("/openapi/v1/challenges/close?unique_code=" + urllib.parse.quote(code), "POST")
    if code_ in (200, 404):
        st["boxes"].pop(code, None)
    emit(st, f"{code_} {json.dumps(ch, ensure_ascii=False)}")


def cmd_submit(code, flag):
    st = _load()
    code_, ch = req("/openapi/v1/challenges/submit", "POST", {"unique_code": code, "flag": flag})
    ok = bool(ch.get("correct"))
    if ok:
        st.setdefault("flags", {})[code] = st.get("flags", {}).get(code, 0) + 1
        st.setdefault("solved", [])
        cat = catalog(refresh=True)
        total = cat.get(code, {}).get("flag_total", 1)
        if st["flags"][code] >= total:
            st["solved"].append(code)
            msg = f"[flag] {code} COMPLETE {st['flags'][code]}/{total} — close it now"
        else:
            msg = f"[flag] {code} {st['flags'][code]}/{total} — keep the box, find the rest (do NOT close after 2)"
    elif ch.get("code") == "duplicate":
        msg = f"[flag] {code} duplicate (already counted)"
    else:
        msg = f"[flag] {code} WRONG — keep working; a wrong guess is not a primitive"
    emit(st, f"{code_} {json.dumps(ch, ensure_ascii=False)}\n{msg}")


def cmd_touch(code, what):
    st = _load()
    if code not in st["boxes"]:
        print(f"touch failed: {code} is not open")
        print(clock(st))
        return 1
    what = (what or "").strip()
    if len(what) < 8 or not EVIDENCE_RE.search(what):
        print("touch rejected: --what must name the new fact (endpoint / credential / error class / "
              "status or length change / working payload stage), >=8 chars. Activity is not a primitive.")
        print(clock(st))
        return 1
    b = st["boxes"][code]
    if b.get("touches", 0) >= TOUCH_MAX:
        print(f"touch budget exhausted for {code} ({b['touches']}/{TOUCH_MAX}) — close it and move on.")
        print(clock(st))
        return 1
    b["touches"] = b.get("touches", 0) + 1
    b["primitive"] = time.time()
    if not b.get("extend"):
        soft, _ = TIERS.get(b.get("difficulty", "medium"), TIERS["medium"])
        if time.time() - b["started"] >= soft:
            b["extend"] = TIERS.get(b.get("difficulty", "medium"), TIERS["medium"])[1] - soft
            print(f"one-time extension +{_fmt(b['extend'])} granted")
    emit(st, f"primitive recorded for {code}: {what}")


def cmd_skip(code, reason, instances, force):
    claim = bool(re.search(r"(不可解|无法解|题目缺陷|环境坏了|unsolvable|broken)", reason or "", re.I))
    if claim and not force and instances < 3:
        print("skip refused: notes claim this box is unsolvable. Re-check on fresh instances first: "
              "pass --instances N (>=3) or --force. The same build solved challenges written off this way.")
        print(clock())
        return 1
    if governance is not None:
        governance.SkipRegistry().add(code, reason or "")
    else:
        with open(os.path.join(WORK, "SKIP.txt"), "a", encoding="utf-8") as fh:
            fh.write(f"{code}\t{reason}\n")
    if claim:
        with open(os.path.join(WORK, "blockers.md"), "a", encoding="utf-8") as fh:
            fh.write(f"## {code} — claimed unsolvable (instances={instances}, force={force})\n{reason}\n\n")
    print(f"skipped {code}")
    print(clock())


def cmd_unskip(code):
    if governance is not None:
        governance.SkipRegistry().remove(code)
    print(f"unskipped {code}")
    print(clock())


def cmd_finish(force, reason):
    st = _load()
    cat = catalog(refresh=True)
    left = budget_left(st)
    if not cat:
        print("finish refused: the queue could not be read (no catalog), so it is not evidence. "
              "Run `python t.py list` first.")
        print(clock(st))
        return 1
    unsolved = [c for c, v in cat.items() if not v.get("is_completed") and c not in st.get("solved", [])]
    if not force and left > FINISH_MIN_LEFT and unsolved:
        print(f"finish refused: {_fmt(left)} of budget left and {len(unsolved)} challenge(s) unsolved. "
              f"Closing early forfeits points. Close a stalled box, open the next, keep working. "
              f"--force only when the platform is about to stop you.")
        print(clock(st))
        return 1
    for code in list(st["boxes"]):
        req("/openapi/v1/challenges/close?unique_code=" + urllib.parse.quote(code), "POST")
        st["boxes"].pop(code, None)
    _save(st)
    print(f"finished ({reason or 'done'}): closed all boxes")
    print(clock(st))
    return 0


def cmd_status():
    catalog()
    print(clock(_load()))
    for code, b in sorted(_load()["boxes"].items()):
        print(f"  {code}\tstarted {_fmt(time.time() - b['started'])} ago\t"
              f"touches {b.get('touches', 0)}/{TOUCH_MAX}\tflags {b.get('flags', 0)}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    _save(_load())  # persist run_started on first use, so the clock never resets
    cmd, args = argv[1], argv[2:]
    opts = {}
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a
            if key in ("--force",):
                opts[key] = True
                i += 1
            else:
                opts[key] = args[i + 1] if i + 1 < len(args) else ""
                i += 2
        else:
            pos.append(a)
            i += 1

    if cmd == "start":
        cmd_start(pos[0]); return 0
    if cmd == "close":
        cmd_close(pos[0]); return 0
    if cmd == "submit":
        cmd_submit(pos[0], pos[1]); return 0
    if cmd == "touch":
        return cmd_touch(pos[0], opts.get("--what", ""))
    if cmd == "skip":
        return cmd_skip(pos[0], pos[1] if len(pos) > 1 else "", int(opts.get("--instances") or 0),
                        bool(opts.get("--force")))
    if cmd == "unskip":
        cmd_unskip(pos[0]); return 0
    if cmd == "finish":
        return cmd_finish(bool(opts.get("--force")), opts.get("--reason", ""))
    if cmd == "status":
        cmd_status(); return 0
    if cmd == "list":
        _, ch = req("/openapi/v1/challenges")
        for c in ch if isinstance(ch, list) else []:
            if not c["is_completed"]:
                print(c["unique_code"], c["difficulty"], c["total_score"],
                      c["correct_flag_count"], "/", c["flag_count"], c["container_status"])
        print(clock()); return 0
    if cmd == "addr":
        _, ch = req("/openapi/v1/challenges")
        for c in ch if isinstance(ch, list) else []:
            if c["unique_code"] == pos[0]:
                print(json.dumps(c, ensure_ascii=False)); return 0
        return 1
    if cmd in ("get", "post"):
        url = pos[0]
        data = urllib.parse.urlencode({"d": pos[1]}).encode() if cmd == "post" and len(pos) > 1 else None
        r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        r.add_header("User-Agent", UA)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                print(resp.status, dict(resp.headers))
                print(resp.read()[:4000].decode("utf-8", "replace"))
        except Exception as e:
            print("ERR", type(e).__name__, e)
        return 0
    print("unknown cmd")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
