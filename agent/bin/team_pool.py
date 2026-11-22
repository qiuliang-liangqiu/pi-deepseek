#!/usr/bin/env python3
"""Local team pool for pi/T-Sec: dispatch, poll, cancel, jobs.json, clock.

This is the local-runtime twin of the tbench image's team_dispatch/poll/cancel:
- one detached `pi` child per agent, no batch barrier;
- jobs persisted in ~/.pi/agent/teams/jobs.json;
- adopt a started box: triage => close after 120s, otherwise fan out 3 agents;
- clock line `team Nr/Md | CANDIDATE <code>`.

Platform start/close need BENCHMARK_BASE_URL + BENCHMARK_TOKEN (TSEC_TOKEN is
accepted as a local fallback). If they are absent, adopt still records the box
and dispatches, but cannot auto-close.

2026-11-20 fixes:
- every jobs.json write is read-modify-write under a lock (no stale snapshot);
- load failure never returns an empty table (no accidental wipe);
- unique temp file per write;
- worker result is persisted to jobs.json *and* teams/<code>/result-<job>.md;
- FLAG_CANDIDATE/flag{...} is written to teams/<code>/candidate-<job>.txt;
- poll recovers text/candidates from old logs (including pre-fix jobs);
- worker gets prompt-injection-guard, AGENTS context, no TSEC_TOKEN, a default
  timeout, and a compact hard-rules prompt;
- orphaned jobs' live children are cancellable;
- triage reaper has an instance token so a re-open cannot be closed by an old
  timer.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path.home() / ".pi" / "agent"
TEAMS = ROOT / "teams"
JOBS = TEAMS / "jobs.json"
JOBS_LOCK = TEAMS / "jobs.lock"
TRIAGE = ROOT / "triage_state.json"
TRIAGE_GATE = ROOT / "bin" / "triage_gate.py"
SKILLS = ROOT / "skills"
GUARD = ROOT / "extensions" / "prompt-injection-guard" / "index.ts"
WORKER_GUARD = ROOT / "worker-extensions" / "worker-guard" / "index.ts"
PY = sys.executable
PI = os.environ.get("TEAM_PI_BIN", "pi")
PI_NODE = os.environ.get("TEAM_PI_NODE") or __import__("shutil").which("node") or "node"
PI_LAUNCHER = ROOT / "bin" / "pi-launcher.js"
# Windows: a console child of a console-less parent makes Windows allocate a NEW
# console, which Win11 hands to Windows Terminal => a terminal window pops up and
# stays for the worker's lifetime. Every process we spawn must opt out.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TRIAGE_PROBE = float(os.environ.get("TBENCH_TRIAGE_PROBE_S", "120"))
TRIAGE_RESTART_AFTER = float(os.environ.get("TEAM_TRIAGE_RESTART_AFTER", "21600"))
MAX_PER_BOX = int(os.environ.get("TEAM_MAX_PER_BOX", "4"))
MAX_TOTAL = int(os.environ.get("TEAM_MAX_TOTAL", "0") or 0)  # 0 = 自动（按平台容量 × 每箱上限）


def _capacity_candidates():
    out = []
    wd = os.environ.get("TSEC_WORKDIR")
    if wd:
        out.append(Path(wd) / "capacity.json")
    wb = os.environ.get("PI_WORKBENCH")
    if wb:
        out.append(Path(wb) / "work" / "capacity.json")
    extra = os.environ.get("TEAM_CAPACITY_FILE")
    if extra:
        out.append(Path(extra))
    return out


def platform_capacity():
    """返回 (可同时开启的容器数, 来源)。

    优先级：显式 env > 平台 409 学到的 capacity.json > 平台默认 3。
    没有平台凭据 = 单目标模式（用户只给一个题目+端口），返回 1。
    """
    env = (os.environ.get("TSEC_MAX_ACTIVE") or os.environ.get("BENCHMARK_MAX_ACTIVE") or "").strip()
    if env:
        try:
            n = int(env)
            if n > 0:
                return n, "env"
        except ValueError:
            pass
    if not (os.environ.get("BENCHMARK_BASE_URL") or os.environ.get("TSEC_BASE_URL")):
        return 1, "single-target"
    best = 0
    for p in _capacity_candidates():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for v in data.values():
            n = v if isinstance(v, int) else (int(v.get("max_active") or 0) if isinstance(v, dict) else 0)
            best = max(best, int(n or 0))
    if best > 0:
        return best, "cache"
    return 3, "default"


def max_total():
    """总 worker 上限：显式 TEAM_MAX_TOTAL 优先，否则 平台容量 × 每箱上限。"""
    if MAX_TOTAL > 0:
        return MAX_TOTAL
    boxes, _ = platform_capacity()
    return max(boxes, 1) * MAX_PER_BOX
DEFAULT_JOB_TIMEOUT = float(os.environ.get("TEAM_JOB_TIMEOUT", "1800"))
ORPHAN_KILL_S = float(os.environ.get("TEAM_ORPHAN_KILL_S", "300"))
ORPHAN_KILL_MAX_S = float(os.environ.get("TEAM_ORPHAN_KILL_MAX_S", "3600"))
BACKFILL_MAX_AGE = float(os.environ.get("TEAM_BACKFILL_MAX_AGE", "21600"))
AUTO_RECON = os.environ.get("TEAM_AUTO_RECON", "1") != "0"
RECON_WINDOW_S = float(os.environ.get("TEAM_RECON_WINDOW_S", "1800"))
CANDIDATE_MAX_AGE = float(os.environ.get("TEAM_CANDIDATE_MAX_AGE", "21600"))
STALL_CLOSE_S = float(os.environ.get("TEAM_STALL_CLOSE_S", os.environ.get("TBENCH_STALL_CLOSE_S", "900")))
SOLVED_HOLD_S = float(os.environ.get("TEAM_SOLVED_HOLD_S", "600"))
COMPLETED_GRACE_S = float(os.environ.get("TEAM_COMPLETED_GRACE_S", "10"))
CLOSE_RETRY_S = float(os.environ.get("TEAM_CLOSE_RETRY_S", "60"))
ENFORCE_EVERY = float(os.environ.get("TEAM_ENFORCE_EVERY", "15"))
ENFORCE_MAX_S = float(os.environ.get("TEAM_ENFORCE_MAX_S", "28800"))
TIER_HARD = {
    "easy": 10 * 60,
    "medium": 20 * 60,
    "hard": 60 * 60,
    "credential": 5 * 60,
    "intel": 45 * 60,
}

AGENTS = ("lead", "recon", "exploit", "verifier")
# 每个角色的工具集：lead 只做管理（派活/读黑板/汇总），不给 edit，避免它自己改产物。
TOOLS_BY_AGENT = {
    "lead": "read,write,bash",
    # recon/verifier 写自己的 worker-<job>.md 需要 write，但不该改已有文件 -> 不给 edit
    "recon": "read,bash,write",
    "exploit": "read,bash,write,edit",
    "verifier": "read,bash,write",
}
def _lead_prompt() -> str:
    """队长的系统提示以 agents/lead.md 为唯一来源；读不到时用兜底文本。"""
    try:
        p = Path(__file__).resolve().parent.parent / "agents" / "lead.md"
        t = p.read_text(encoding="utf-8")
        if t.startswith("---"):
            t = t.split("---", 2)[2]
        t = t.strip()
        if t:
            return t
    except Exception:  # noqa: BLE001
        pass
    return (
        "You are the LEAD for one T-Sec box: manage, never attack. Read the box files, "
        "dispatch 2-3 workers with distinct mechanisms via team_pool.py dispatch, poll, "
        "aggregate into lead-plan.md, and report only on flag candidate / abandon request / budget out."
    )

ROLE = {
    "lead": _lead_prompt(),
    "recon": (
        "You are the recon worker for one T-Sec box. Stay shallow: identify "
        "service/version/entry, run the triage steps, write <box>/triage.txt. "
        "Leave exploitation to another worker; never call platform APIs, submit "
        "flags, or close boxes."
    ),
    "exploit": (
        "You are the exploit worker for one T-Sec box. Read <box>/triage.txt "
        "first. Drive one concrete exploit chain; write candidate flags and "
        "evidence under <box>/. Never call platform APIs, submit flags, or close "
        "boxes."
    ),
    "verifier": (
        "You are the independent verifier for one T-Sec box. Re-run the claimed "
        "trigger in a fresh shell, check the candidate against the real response, "
        "and write <box>/verified.md with exact commands and output. Never call "
        "platform APIs, submit flags, or close boxes."
    ),
}
WORKER_RULES = """
Hard rules for this worker:
- After any shell/foothold, run `ls -l /challenge 2>/dev/null; cat /challenge/flag* 2>/dev/null`
  and record `FLAG_CANDIDATE <flag> | path | confidence` in the Result file.
- Rate: <=20 req/s and <=10 concurrent; use `curl -m 5` / `timeout 15`; never
  run an unbounded ffuf/hydra against the target.
- 14 minutes without a new fact: stop, write three lines (hypothesis / excluded /
  next move), and return `STALLED`.
- Two failures on the same chain: switch payload/context (A path shift / B chain
  switch) or write `NEED_RECON <what to enumerate>` instead of retrying.
- Before ending, write the `Result file:` named in the task (worker-*.md) with FACTS / CANDIDATE / NEXT / CONFIDENCE.
- Only the main agent may start/close boxes or submit flags. Never do those.
"""

CANDIDATE_RE = re.compile(r"FLAG_CANDIDATE\s*[:\-]?\s*[`'\"]?([A-Za-z0-9_{}.\-]{4,})", re.I)
FLAG_RE = re.compile(r"flag\{[^}\s]{4,}\}", re.I)
FACT_RE = re.compile(
    r"flag\{|FLAG_CANDIDATE|solved|chain|endpoint|http|端口|路径|版本|banner|"
    r"error|exception|status|credential|token|password|源码|注入|rce",
    re.I,
)


def _now() -> float:
    return time.time()


def _write_owner(tdir: Path) -> None:
    """Record which session opened this box, so run_gate only gates its own run.

    Written only when the session id is known; without it the box stays
    "unowned" and every session keeps counting it (old behaviour).
    """
    sid = (os.environ.get("RUN_GATE_SESSION") or os.environ.get("PI_SESSION_ID") or "").strip()
    if not sid or sid == "unknown":
        return
    try:
        (tdir / ".owner").write_text(sid, encoding="utf-8")
    except OSError:
        pass


def _hhmm() -> str:
    return time.strftime("%H:%M")


def _valid_code(code) -> str:
    """Reject box codes that would escape TEAMS/ or hit a Windows device."""
    c = str(code or "").strip()
    upper = c.upper()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    bad = (not c or c in (".", "..") or upper.split(".", 1)[0] in reserved
           or c.endswith(".")
           or any(ch in c for ch in ("/", "\\", ":", "\x00"))
           or any(ord(ch) < 32 for ch in c))
    if bad:
        raise SystemExit(f"[team] invalid box code: {code!r}")
    return c


def _atomic(path: Path, data) -> None:
    """Write JSON through a unique temp file, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
        try:
            # Keep a same-content backup. If a later write/another process
            # corrupts jobs.json, load_jobs() can fall back to this file.
            bak = path.with_name(path.name + ".bak")
            bak.write_bytes(path.read_bytes())
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


@contextlib.contextmanager
def jobs_lock(timeout: float = 10.0):
    """Best-effort cross-process lock for jobs.json (Windows + POSIX)."""
    TEAMS.mkdir(parents=True, exist_ok=True)
    end = _now() + max(1.0, timeout)
    owner = f"{os.getpid()}-{random.randint(100000, 999999)}"
    fd = None
    while True:
        try:
            fd = os.open(str(JOBS_LOCK), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, owner.encode("ascii", "replace"))
            break
        except FileExistsError:
            pass  # ordinary contention; wait and retry below
        except OSError as exc:
            # Windows can return EACCES instead of EEXIST while the previous
            # holder is unlinking the lock (delete-pending) or an AV scanner
            # has it open. Those are transient; anything else is fatal.
            if exc.errno not in (errno.EACCES, errno.EPERM, errno.EBUSY):
                raise
        try:
            if _now() - JOBS_LOCK.stat().st_mtime > 30:
                # Stale only if the recorded owner is gone. On POSIX an unlink
                # succeeds while the holder still has the fd open, so a live
                # owner must not lose the lock just because it is slow.
                stale = JOBS_LOCK.read_text(encoding="utf-8", errors="replace").strip()
                pid = int(stale.split("-", 1)[0]) if stale else 0
                if not pid or not alive(pid):
                    JOBS_LOCK.unlink()
                    continue
        except (OSError, ValueError):
            pass
        if _now() > end:
            raise TimeoutError("jobs.lock timeout")
        time.sleep(0.02)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                # Only unlink our own lock: another process may have created a
                # new one while we were finishing (the old code could delete it).
                cur = JOBS_LOCK.read_text(encoding="utf-8", errors="replace").strip()
                if cur == owner:
                    JOBS_LOCK.unlink()
            except OSError:
                pass


def load_jobs() -> dict:
    """Read jobs.json. A missing file is empty; a corrupt/locked file is an
    error (never silently return an empty table, which used to wipe history)."""
    last: Exception | None = None
    for _ in range(5):
        for candidate in (JOBS, JOBS.with_name(JOBS.name + ".bak")):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
                    return data
                last = ValueError(f"{candidate.name} has an unexpected shape")
            except FileNotFoundError:
                if candidate == JOBS:
                    return {"jobs": {}}
            except Exception as exc:  # noqa: BLE001 - retry any read/parse error
                last = exc
        time.sleep(0.05)
    raise RuntimeError(f"jobs.json unreadable: {last}")


def save_jobs(d: dict) -> None:
    _atomic(JOBS, d)


def update_job(job_id: str, **fields):
    """Read-modify-write one job under the lock; never write an old snapshot."""
    with jobs_lock():
        d = load_jobs()
        j = d["jobs"].get(job_id)
        if not j:
            return None
        j.update(fields)
        save_jobs(d)
        return j


def add_job(job: dict) -> None:
    with jobs_lock():
        d = load_jobs()
        d["jobs"][job["id"]] = job
        save_jobs(d)


def triage_state() -> dict:
    try:
        return json.loads(TRIAGE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def triage_active() -> bool:
    s = triage_state()
    return bool(s) and not s.get("done") and _now() < float(s.get("phase_end") or 0)


def _new_run() -> bool:
    """True for the first box of a new pi session (or a stale anonymous run).

    A completed triage phase must not restart for every A->B box in the same
    run, but a new run must start a new 30-minute phase. PI_SESSION_ID is the
    run boundary when available; without it fall back to a 6h marker.
    """
    sid = os.environ.get("PI_SESSION_ID", "").strip()
    if sid:
        safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in sid)[:64]
        marker = TEAMS / f".run-{safe}"
        if marker.exists():
            return False
        try:
            marker.write_text(str(_now()), encoding="utf-8")
        except OSError:
            pass
        return True
    marker = TEAMS / ".run-anon"
    try:
        fresh = _now() - marker.stat().st_mtime < TRIAGE_RESTART_AFTER
    except OSError:
        fresh = False
    if fresh:
        return False
    try:
        marker.write_text(str(_now()), encoding="utf-8")
    except OSError:
        pass
    return True


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        # OpenProcess/GetExitCodeProcess is ~100x faster than spawning
        # `tasklist`; poll() calls this for every running job.
        try:
            import ctypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5  # ACCESS_DENIED => exists
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True
                return code.value == 259  # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:
            pass
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=NO_WINDOW,
            ).stdout
            return str(pid) in out
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def kill_tree(pid) -> None:
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                           timeout=10, creationflags=NO_WINDOW)
        else:
            os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
    except Exception:
        pass


class Api:
    def __init__(self):
        self.base = (os.environ.get("BENCHMARK_BASE_URL")
                     or os.environ.get("TSEC_BASE_URL") or "").rstrip("/")
        self.token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN") or ""

    def ok(self) -> bool:
        return bool(self.base and self.token)

    def req(self, path, method="GET", body=None):
        if not self.ok():
            raise RuntimeError("BENCHMARK_BASE_URL/BENCHMARK_TOKEN (or TSEC_TOKEN) not set")
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("BENCHMARK_TOKEN", self.token)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")

    def start(self, code):
        return self.req(
            f"/openapi/v1/challenges/start?unique_code={urllib.parse.quote(code)}", "POST"
        )

    def close(self, code):
        try:
            return self.req(
                f"/openapi/v1/challenges/close?unique_code={urllib.parse.quote(code)}", "POST"
            )
        except urllib.error.HTTPError as exc:
            # Already gone is success for the watchdog; do not retry forever.
            if exc.code == 404:
                return {"closed": False, "already_gone": True}
            raise

    def challenge(self, code):
        for c in self.challenges():
            if isinstance(c, dict) and str(c.get("unique_code")) == code:
                return c
        return None

    def challenges(self):
        data = self.req("/openapi/v1/challenges")
        if isinstance(data, dict):
            rows = data.get("challenges") or data.get("items") or []
        else:
            rows = data if isinstance(data, list) else []
        return rows if isinstance(rows, list) else []


def score_text(text: str) -> str:
    """Last non-empty assistant text from `pi --mode json` lines."""
    out = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "message_end":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        parts = []
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text") or "")
        if parts:
            joined = "".join(parts).strip()
            if joined:
                out.append(joined)
    return out[-1] if out else ""


def _dedup(found: list[str]) -> list[str]:
    seen = set()
    out = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
        if len(out) >= 10:
            break
    return out


def candidate_flags(text: str) -> list[str]:
    """Flags a worker explicitly marked FLAG_CANDIDATE (preferred)."""
    return _dedup([m.group(1) for m in CANDIDATE_RE.finditer(text)])


def raw_flags(text: str) -> list[str]:
    """Any flag-like string (may include decoys/examples)."""
    return _dedup([m.group(0) for m in FLAG_RE.finditer(text)])


def flags_in(text: str) -> list[str]:
    # Used for local safety (stall/solved-hold): be conservative and count any
    # flag-like string, including a decoy.
    return _dedup(candidate_flags(text) + raw_flags(text))


def append_ledger(code: str, agent: str, status: str, summary: str, flags: list[str]) -> None:
    """Append a machine-readable line to notes.md so stall_gate.py can judge it."""
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    p = tdir / "notes.md"
    if not p.exists():
        p.write_text(
            f"# {code} — 止损台账\n\nT0 {time.strftime('%Y-%m-%d %H:%M')}  (auto-created by team_pool)\n\n"
            "格式：`- HH:MM [P] 新原语` / `- HH:MM [-] 无新原语`\n\n",
            encoding="utf-8",
        )
    mark = "P" if (flags or FACT_RE.search(summary or "")) else "-"
    if flags:
        body = f"{agent} found candidate(s); see candidate-<job>.txt (flag redacted)"
    elif summary:
        body = re.sub(r"\s+", " ", summary)[:240]
    else:
        body = f"{agent} job {status}; no assistant summary (see log)"
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {_hhmm()} [{mark}] {body}\n")
    except OSError:
        pass


def worker_artifacts(tdir: Path, since: float = 0.0, jid: str = "") -> str:
    """Read the files the worker was told to write.

    Per-job files first (3 workers share a box, so a shared result.md is racy);
    shared files are only a fallback for old jobs, and stale mtimes are ignored.
    """
    def read(names):
        out = []
        for name in names:
            p = tdir / name
            try:
                if p.is_file() and (not since or p.stat().st_mtime >= since - 5):
                    out.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        return out

    if jid:
        per_job = read((f"worker-{jid}.md", f"candidates-{jid}.md"))
        if per_job:
            return "\n".join(per_job)
    # Fallback only for old/legacy prompts; never mix another concurrent
    # worker's shared result.md into this job's summary.
    return "\n".join(read(("result.md", "candidates.md")))


def write_artifacts(job: dict, log_text: str, status: str) -> str:
    """Persist a human summary and candidate flags; return the summary."""
    code = str(job.get("code") or "")
    jid = str(job.get("id") or "")
    agent = str(job.get("agent") or "")
    if not code or not jid:
        return ""
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    since = float(job.get("started") or job.get("created") or 0)
    extra = worker_artifacts(tdir, since, jid)
    # Prefer the worker-written result.md over the log: the last assistant
    # text is often a preamble when the final turns are tool calls only.
    summary = extra.strip() or score_text(log_text)
    cands = (candidate_flags(summary) or candidate_flags(log_text)
             or candidate_flags(extra))
    raw = (raw_flags(summary) or raw_flags(log_text) or raw_flags(extra))
    flags = cands or raw
    if summary:
        try:
            (tdir / f"result-{jid}.md").write_text(
                f"# {jid} {status}\n\n{summary}\n", encoding="utf-8"
            )
        except OSError:
            pass
    if cands:
        try:
            (tdir / f"candidate-{jid}.txt").write_text(
                "\n".join(cands) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
    elif raw:
        # Raw flag-like strings are kept separate: logs often contain examples
        # and decoys, so the main agent must second-path check them.
        try:
            (tdir / f"flaglike-{jid}.txt").write_text(
                "\n".join(raw) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
    append_ledger(code, agent, status, summary, flags)
    if not summary and flags:
        summary = "CANDIDATE " + flags[0]
    # Keep jobs.json small; the full text is in result-<job>.md.
    return summary[:4000]


def recover_job(j: dict) -> bool:
    """Recover text/candidates for a finished job that has no summary yet."""
    if j.get("recovered"):
        return False
    code = str(j.get("code") or "")
    jid = str(j.get("id") or "")
    tdir = TEAMS / code
    log = tdir / f"{jid}.log"
    raw = ""
    try:
        if log.exists():
            raw = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    since = float(j.get("started") or j.get("created") or 0)
    extra = worker_artifacts(tdir, since, jid)
    summary = extra.strip() or score_text(raw)
    flags = (candidate_flags(summary) or raw_flags(summary)
             or candidate_flags(raw) or raw_flags(raw)
             or candidate_flags(extra) or raw_flags(extra))
    if summary or flags:
        try:
            write_artifacts(j, raw, str(j.get("status")))
        except OSError:
            pass
    j["recovered"] = True
    if summary:
        j["text"] = summary[:4000]
    elif flags:
        j["text"] = ("CANDIDATE " + flags[0])[:4000]
    return True


def _maybe_auto_recon(j: dict) -> None:
    """C-mode self-heal: an exploit worker that asks for recon gets one recon job.

    A/B (path/chain switch) remain model decisions; this only covers the
    explicit `NEED_RECON` / `STALLED` signal, with a per-job marker to avoid
    loops.
    """
    if not AUTO_RECON or triage_active():
        return
    if str(j.get("agent") or "") != "exploit":
        return
    if j.get("status") not in ("done", "error", "orphaned"):
        return
    stamp = float(j.get("ended") or j.get("created") or 0)
    if _now() - stamp > RECON_WINDOW_S:
        return
    text = str(j.get("text") or "")
    if "NEED_RECON" not in text and "STALLED" not in text:
        return
    code = str(j.get("code") or "")
    jid = str(j.get("id") or "")
    if not code or not jid:
        return
    tdir = TEAMS / code
    if any((tdir / n).exists() for n in ("closed", "reaped.json", ".hold")):
        return
    marker = tdir / f"recon-{jid}.done"
    if marker.exists():
        return
    try:
        addr = (tdir / "addr.txt").read_text(encoding="utf-8").strip()
    except OSError:
        addr = ""
    if not addr:
        return
    try:
        d = load_jobs()
        running = [x for x in d.get("jobs", {}).values()
                   if x.get("status") in ("running", "queued")]
        if len(running) >= max_total():
            return  # retry after a slot frees; do not burn the marker
        if len([x for x in running if x.get("code") == code]) >= MAX_PER_BOX:
            return
        if any(
            x.get("code") == code and x.get("agent") == "recon"
            and x.get("status") in ("running", "queued")
            for x in running
        ):
            marker.write_text("already running", encoding="utf-8")
            return
    except Exception:
        return
    task = (
        f"Box {code} at {addr}. Exploit worker {jid} returned NEED_RECON/STALLED. "
        f"Read {tdir}/result-{jid}.md and {tdir}/notes.md; enumerate the requested "
        f"surface; write {tdir}/facts.md. Do not repeat the failed chain."
    )
    try:
        marker.write_text(str(_now()), encoding="utf-8")
    except OSError:
        pass
    dispatch(code, "recon", task)


def _run(job_id: str) -> int:
    if os.environ.get("TEAM_WORKER") == "1":
        return 1
    try:
        with jobs_lock():
            d = load_jobs()
            j = d["jobs"].get(job_id)
    except Exception as exc:
        print(f"[team] _run: {exc}", file=sys.stderr)
        return 1
    if not j:
        return 1
    if str(j.get("status") or "") != "queued":
        # cancel()/reap() may have marked this job before the supervisor
        # started; never resurrect a cancelled worker.
        return 0
    code, agent, task = str(j["code"]), str(j["agent"]), str(j["task"])
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    prompt = tdir / f".prompt-{job_id}.md"
    try:
        prompt.write_text(
            ROLE.get(agent, "") + "\n" + WORKER_RULES
            + f"\n\nBox directory: {tdir}\n"
            + f"Result file: {tdir}/worker-{job_id}.md\n"
            + f"Candidates file: {tdir}/candidates-{job_id}.md\n"
            + "\nTask: " + task,
            encoding="utf-8",
        )
    except OSError as exc:
        update_job(job_id, status="error", error=f"prompt write failed: {exc}", ended=_now())
        return 1
    cmd = [PI] if os.environ.get("TEAM_PI_BIN") else (
        [PI_NODE, str(PI_LAUNCHER)] if PI_LAUNCHER.exists() else [PI]
    )
    cmd += ["-p", "--mode", "json", "--no-session", "--no-mcp", "-ne"]
    if GUARD.exists():
        cmd += ["-e", str(GUARD)]
    if WORKER_GUARD.exists():
        cmd += ["-e", str(WORKER_GUARD)]
    cmd += [
        "--tools", TOOLS_BY_AGENT.get(agent, "read,bash,write,edit"),
        "--thinking", os.environ.get("TEAM_THINKING", "high"),
        "--skill", str(SKILLS),
        "--append-system-prompt", str(prompt),
        "Task: " + task,
    ]
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("CTFY_") or k in (
            "BENCHMARK_TOKEN", "BENCHMARK_BASE_URL", "BENCHMARK_TASK_ID",
            "TSEC_TOKEN", "TSEC_BASE_URL",
        ):
            env.pop(k, None)
    env["TEAM_WORKER"] = "1"
    env["TEAM_WORKER_ROLE"] = agent
    env["TEAM_WORKER_JOB"] = job_id
    env["TEAM_BOX_DIR"] = str(tdir)
    timeout = float(j.get("timeout") or DEFAULT_JOB_TIMEOUT)
    log = tdir / f"{job_id}.log"
    started_at = _now()
    j["started"] = started_at
    update_job(job_id, status="running", started=started_at, child_pid=None, pid=os.getpid())
    try:
        with log.open("ab") as f:
            p = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(tdir),
                start_new_session=True,
                creationflags=NO_WINDOW,
            )
            update_job(job_id, child_pid=p.pid)
            try:
                rc = p.wait(timeout=max(30.0, timeout))
            except subprocess.TimeoutExpired:
                kill_tree(p.pid)
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
                rc = 124
        raw = log.read_text(encoding="utf-8", errors="replace")
        status = "done" if rc == 0 else "error"
        summary = write_artifacts(j, raw, status)
        update_job(
            job_id,
            status=status,
            returncode=rc,
            ended=_now(),
            text=summary,
            recovered=True,
        )
        return 0 if rc == 0 else 1
    except Exception as exc:  # noqa: BLE001
        update_job(job_id, status="error", error=str(exc), ended=_now())
        return 1
    finally:
        try:
            prompt.unlink()
        except OSError:
            pass


def dispatch(code: str, agent: str, task: str, timeout: float = 0) -> int:
    code = _valid_code(code)
    # worker 默认不能派生 worker（防止扇出失控）。例外：
    #   TEAM_WORKER_ROLE=lead  —— 队长就是干这个的（默认模式）
    #   TEAM_ALLOW_WORKER_DISPATCH=1 —— 显式放开（调试/特殊场景）
    if (os.environ.get("TEAM_WORKER") == "1"
            and os.environ.get("TEAM_WORKER_ROLE") != "lead"
            and os.environ.get("TEAM_ALLOW_WORKER_DISPATCH") != "1"):
        print("[team] workers may not dispatch more workers", file=sys.stderr)
        return 0
    if agent not in AGENTS:
        raise SystemExit(f"agent must be one of {AGENTS}")
    if not code or not task:
        raise SystemExit("code and task required")
    timeout = max(60.0, float(timeout or DEFAULT_JOB_TIMEOUT))
    jid = f"{code}-{agent}-{int(_now())}-{random.randint(100, 999)}"
    with jobs_lock():
        d = load_jobs()
        while jid in d["jobs"]:
            jid = f"{code}-{agent}-{int(_now())}-{random.randint(100, 999)}"
        if not os.environ.get("TEAM_ALLOW_DUP"):
            all_running = [
                j for j in d["jobs"].values()
                if j.get("status") in ("running", "queued")
            ]
            if len(all_running) >= max_total():
                print(f"[team] total workers {len(all_running)} >= cap {max_total()}; not dispatching")
                return 0
            running = [
                j
                for j in all_running
                if j.get("code") == code
            ]
            if triage_active() and len(running) >= 1 and os.environ.get("TEAM_ALLOW_TRIAGE_DISPATCH") != "1":
                print(f"[team] {code}: triage active; 1 worker/box cap")
                return 0
            if len(running) >= MAX_PER_BOX:
                print(f"[team] {code} already has {len(running)} running (cap {MAX_PER_BOX})")
                return 0
            for j in running:
                if j.get("agent") == agent:
                    print(f"[team] already running {j['id']}")
                    return 0
        d["jobs"][jid] = {
            "id": jid,
            "code": code,
            "agent": agent,
            "task": task,
            "status": "queued",
            "created": _now(),
            "timeout": timeout,
        }
        save_jobs(d)
    try:
        # _run 是"监管进程"，它自己要能跑；当派活者本身是 worker（lead）时，
        # 不能把 TEAM_WORKER=1 传下去，否则 _run 第一行就拒绝执行。
        sup_env = os.environ.copy()
        sup_env.pop("TEAM_WORKER", None)
        sup_env.pop("TEAM_WORKER_ROLE", None)
        sup = subprocess.Popen(
            [PY, str(Path(__file__).resolve()), "_run", jid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=sup_env,
            start_new_session=True,
            creationflags=NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        update_job(jid, status="error", error=f"spawn failed: {exc}", ended=_now())
        print(f"[team] spawn failed: {exc}", file=sys.stderr)
        return 1
    update_job(jid, pid=sup.pid)
    print(jid)
    return 0


def poll(wait: float = 0) -> int:
    end = _now() + max(0.0, float(wait or 0))
    while True:
        changed = False
        try:
            with jobs_lock():
                d = load_jobs()
                for j in d["jobs"].values():
                    st = j.get("status")
                    # If the box was started without adopt (e.g. platform tool
                    # result shape changed), backfill opened_at from the job so
                    # the enforcer can still manage it.
                    if st in ("running", "queued"):
                        code = str(j.get("code") or "")
                        created = float(j.get("created") or 0)
                        if code and created and _now() - created <= BACKFILL_MAX_AGE:
                            tdir = TEAMS / code
                            try:
                                op = tdir / "opened_at"
                                if not op.exists():
                                    closed_ts = 0.0
                                    for marker in ("closed", "reaped.json"):
                                        try:
                                            closed_ts = max(closed_ts, (tdir / marker).stat().st_mtime)
                                        except OSError:
                                            pass
                                    # Only resurrect when the job started after
                                    # the last close; a stale job from an older
                                    # run must not reopen a finished box.
                                    if created > closed_ts + 5:
                                        tdir.mkdir(parents=True, exist_ok=True)
                                        op.write_text(str(created), encoding="utf-8")
                                        for stale in ("closed", "reaped.json"):
                                            try:
                                                (tdir / stale).unlink()
                                            except OSError:
                                                pass
                            except OSError:
                                pass
                    if st == "running":
                        if not alive(j.get("pid")):
                            # supervisor gone: the child may still be alive, but
                            # the slot must not stay blocked. cancel() can reap it.
                            j["status"] = "orphaned"
                            j["ended"] = _now()
                            changed = True
                    elif st == "queued" and _now() - float(j.get("created") or 0) > 120:
                        if not alive(j.get("pid")):
                            j["status"] = "orphaned"
                            j["ended"] = _now()
                            changed = True
                    if j.get("status") in ("orphaned", "error") and not j.get("orphan_killed"):
                        child = j.get("child_pid")
                        age = _now() - float(j.get("ended") or _now())
                        if age > ORPHAN_KILL_MAX_S:
                            # Old enough that the pid may belong to another process.
                            j["orphan_killed"] = _now()
                            changed = True
                        elif child and age > ORPHAN_KILL_S and alive(child):
                            kill_tree(child)
                            j["orphan_killed"] = _now()
                            changed = True
                if changed:
                    save_jobs(d)
        except Exception as exc:  # noqa: BLE001
            print(f"[team] poll: {exc}", file=sys.stderr)
            return 1
        # Start the watchdog for existing opened boxes too. Without this, boxes
        # created before the enforcer existed never get a close timer. Set
        # TEAM_ENFORCE_POLL=0 to keep poll read-only.
        if not triage_active() and os.environ.get("TEAM_ENFORCE_POLL", "1") != "0":
            try:
                open_ts = []
                for tdir in TEAMS.iterdir():
                    if not tdir.is_dir() or not (tdir / "opened_at").exists():
                        continue
                    if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
                        continue
                    open_ts.append(_read_float(tdir / "opened_at", 0.0))
                if open_ts and Api().ok():
                    _ensure_enforcer(min(open_ts))
            except OSError:
                pass

        # Recover text/candidates outside the lock (logs can be multi-MB).
        try:
            d = load_jobs()
            for j in list(d["jobs"].values()):
                if j.get("status") in ("done", "error", "orphaned", "cancelled") and not j.get("recovered"):
                    # Supervisor died but the child may still be writing its log.
                    # Do not mark it recovered with an empty summary yet.
                    if j.get("status") in ("orphaned", "error") and alive(j.get("child_pid")):
                        continue
                    if recover_job(j):
                        update_job(
                            str(j.get("id")),
                            recovered=True,
                            text=j.get("text") or "",
                        )
                    if j.get("text"):
                        _maybe_auto_recon(j)
        except Exception as exc:  # noqa: BLE001
            print(f"[team] poll/recover: {exc}", file=sys.stderr)

        try:
            d = load_jobs()
        except Exception as exc:  # noqa: BLE001
            print(f"[team] poll: {exc}", file=sys.stderr)
            return 1
        jobs = sorted(d.get("jobs", {}).values(), key=lambda x: x.get("created", 0))
        if not jobs:
            print("[team] no jobs")
        else:
            for j in jobs:
                line = f"{j['id']} | {j.get('status')} | {j.get('code')} {j.get('agent')}"
                if j.get("text"):
                    line += " | " + re.sub(r"\s+", " ", str(j["text"]))[:180]
                tdir = TEAMS / str(j.get("code") or "")
                if tdir.is_dir() and any(tdir.glob(f"candidate-{j['id']}*")):
                    line += " | CANDIDATE"
                elif tdir.is_dir() and any(tdir.glob(f"flaglike-{j['id']}*")):
                    line += " | FLAGLIKE"
                print(line)
        if _now() >= end:
            return 0
        time.sleep(2)


def cancel(target: str | None = None) -> int:
    if os.environ.get("TEAM_WORKER") == "1" and os.environ.get("TEAM_ALLOW_WORKER_CANCEL") != "1":
        print("[team] workers may not cancel jobs", file=sys.stderr)
        return 1
    targets = []
    try:
        with jobs_lock():
            d = load_jobs()
            for j in d["jobs"].values():
                if j.get("status") not in ("running", "queued", "orphaned", "error"):
                    continue
                if target and target not in (j.get("id"), j.get("code")):
                    continue
                st = j.get("status")
                j["status"] = "cancelled"
                j["ended"] = _now()
                targets.append((st, j.get("child_pid"), j.get("pid"), float(j.get("ended") or 0)))
            save_jobs(d)
    except Exception as exc:  # noqa: BLE001
        print(f"[team] cancel: {exc}", file=sys.stderr)
        return 1
    for st, child, pid, ended in targets:
        # Do not signal the supervisor pid for an orphaned job: it is known
        # dead and Windows may have reused the pid. Check liveness outside the
        # jobs lock (tasklist can take seconds).
        if st in ("orphaned", "error") and ended and _now() - ended > ORPHAN_KILL_MAX_S:
            continue  # too old; child_pid may belong to another process
        kill_tree(child)
        if st != "orphaned" and alive(pid):
            kill_tree(pid)
    print(f"[team] cancelled {len(targets)}")
    return 0


def _latest_candidate() -> str | None:
    if not TEAMS.is_dir():
        return None
    try:
        cands = []
        for x in TEAMS.glob("*/candidate*"):
            try:
                if x.name == "candidates.md" or not x.is_file() or x.stat().st_size == 0:
                    continue
                if any((x.parent / n).exists() for n in ("closed", "reaped.json")):
                    continue
                opened = _read_float(x.parent / "opened_at", 0.0)
                age = _now() - x.stat().st_mtime
                if opened and x.stat().st_mtime < opened - 5:
                    continue
                if not opened and age > CANDIDATE_MAX_AGE:
                    continue
                cands.append(x)
            except OSError:
                continue
        cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        if cands:
            return cands[0].parent.name
    except OSError:
        pass
    # Fallback: worker artifacts / notes written by the main agent.
    try:
        for pattern in ("*/worker-*.md", "*/candidates-*.md", "*/candidates.md", "*/notes.md", "*/result.md"):
            for p in sorted(TEAMS.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    if any((p.parent / n).exists() for n in ("closed", "reaped.json")):
                        continue
                    opened = _read_float(p.parent / "opened_at", 0.0)
                    age = _now() - p.stat().st_mtime
                    if opened and p.stat().st_mtime < opened - 5:
                        continue
                    if not opened and age > CANDIDATE_MAX_AGE:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                    # Only an explicit marker is a curated candidate; raw
                    # flag{...} strings are surfaced separately as FLAGLIKE.
                    if "FLAG_CANDIDATE" in text:
                        return p.parent.name
                except OSError:
                    continue
    except OSError:
        pass
    return None


def _read_float(path: Path, default: float = 0.0) -> float:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return default


def _last_primitive(tdir: Path, opened: float) -> float:
    """Latest evidence of progress: notes, result/candidate, triage card."""
    latest = opened
    try:
        for p in tdir.iterdir():
            name = p.name
            if name in ("notes.md", "start.json", "triage.txt", "triage.md", "result.md", "candidates.md"):
                pass
            elif (name.startswith("result-") or name.startswith("candidate-")
                  or name.startswith("worker-") or name.startswith("candidates-")):
                pass
            else:
                continue
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return latest


def _abandoned(tdir: Path) -> bool:
    """Partial multi-flag boxes need an explicit run_gate abandonment."""
    return (tdir / "abandon.json").exists()


def _local_flags(tdir: Path) -> int:
    n = 0
    try:
        for p in list(tdir.glob("candidate-*.txt")) + list(tdir.glob("candidates-*.md")):
            try:
                n = max(n, len(flags_in(p.read_text(encoding="utf-8", errors="replace"))))
            except OSError:
                continue
        # Raw flags / worker summaries are still evidence: never treat a box
        # with a flag-like string as 0-flag and stall-close it.
        for pat in ("flaglike-*.txt", "result-*.md", "worker-*.md"):
            for p in tdir.glob(pat):
                try:
                    n = max(n, len(flags_in(p.read_text(encoding="utf-8", errors="replace"))))
                except OSError:
                    continue
        # The main agent may have written the flag in notes instead of a
        # candidate file. Count it so the enforcer does not stall-close a
        # partially solved box.
        try:
            notes = (tdir / "notes.md").read_text(encoding="utf-8", errors="replace")
            n = max(n, len(flags_in(notes)))
        except OSError:
            pass
        try:
            n = max(n, int((tdir / ".flags").read_text(encoding="utf-8").strip() or 0))
        except (OSError, ValueError):
            pass
    except OSError:
        pass
    return n


def _enforce_state(tdir: Path) -> dict:
    try:
        data = json.loads((tdir / "enforce.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_enforce(tdir: Path, data: dict) -> None:
    try:
        _atomic(tdir / "enforce.json", data)
    except OSError:
        pass


def _mark_closed(code: str) -> None:
    try:
        (TEAMS / code / "closed").write_text(str(_now()), encoding="utf-8")
    except OSError:
        pass


def _ensure_note(code: str, reason: str) -> None:
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    p = tdir / "notes.md"
    if not p.exists():
        p.write_text(
            f"# {code} — 止损台账\n\nT0 {time.strftime('%Y-%m-%d %H:%M')}  (auto-close)\n\n"
            "- 入口: unknown (enforcer)\n- 已试: see .started/log/result-*\n"
            "- 下一招: re-open once, run triage.sh, then decide\n\n",
            encoding="utf-8",
        )
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {_hhmm()} [-] auto-close: {reason}\n")
    except OSError:
        pass


def _close_box(code: str, reason: str, api: Api | None = None,
               expected_opened: float | None = None,
               expected_lastp: float | None = None) -> bool:
    tdir = TEAMS / code
    pending = tdir / "close_pending"
    attempts = 0
    try:
        attempts = int(json.loads(pending.read_text(encoding="utf-8")).get("attempts", 0))
    except (OSError, ValueError, TypeError):
        pass
    api = api or Api()
    if not api.ok():
        print(f"[enforce] {code}: close skipped (no token) reason={reason}")
        return False
    if expected_opened is not None:
        cur = _read_float(tdir / "opened_at", 0.0)
        if abs(cur - expected_opened) > 0.5:
            print(f"[enforce] {code}: skip close (box re-opened); not touching the new instance")
            try:
                pending.unlink()
            except OSError:
                pass
            return False
    if expected_lastp is not None:
        curp = _last_primitive(tdir, expected_opened or 0.0)
        if curp > expected_lastp + 1.0:
            print(f"[enforce] {code}: skip close (new primitive arrived)")
            try:
                pending.unlink()
            except OSError:
                pass
            return False
    _ensure_note(code, reason)
    try:
        res = api.close(code)
        # Re-check after the HTTP close: a start/adopt may have re-opened the
        # same code while we were waiting. Do not mark/cancel the new instance.
        cur = _read_float(tdir / "opened_at", 0.0)
        if expected_opened is not None and abs(cur - expected_opened) > 0.5:
            print(f"[enforce] {code}: closed the old instance, but the code was re-opened; "
                  "not marking closed/cancelling new jobs")
            try:
                pending.unlink()  # old failed-close record; adopt() may have cleared it
            except OSError:
                pass
            return True
        _mark_closed(code)
        try:
            pending.unlink()
        except OSError:
            pass
        print(f"[enforce] {code}: CLOSED ({reason}) {json.dumps(res, ensure_ascii=False)[:160]}")
        cancel(code)
        return True
    except Exception as exc:  # noqa: BLE001
        try:
            pending.write_text(
                json.dumps(
                    {"at": _now(), "reason": reason, "attempts": attempts + 1, "error": str(exc)},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(f"[enforce] {code}: close failed (attempt {attempts + 1}): {exc}")
        return False


def _enforce_once(close: bool = False, since: float = 0.0, include_notes: bool = False) -> int:
    """One pass: completed 10s, hard deadline, stall, solved-hold, pending retry.

    Dry-run unless close=True. Only boxes opened at/after `since` are considered,
    so starting the watchdog cannot close boxes from an earlier run.
    """
    now = _now()
    api = Api()
    rows: list = []
    catalog_ok = False
    if api.ok():
        try:
            rows = api.challenges()
            catalog_ok = True
        except Exception as exc:  # noqa: BLE001
            print(f"[enforce] catalog failed: {exc}")
    catalog = {str(c.get("unique_code")): c for c in rows if isinstance(c, dict)}
    actions = 0
    try:
        dirs = sorted(TEAMS.iterdir())
    except OSError:
        return 0
    for tdir in dirs:
        if not tdir.is_dir():
            continue
        code = tdir.name
        opened = _read_float(tdir / "opened_at", 0.0)
        opened_from_notes = False
        if not opened and include_notes:
            # Opt-in: manage a box that was never adopted, using notes.md as the
            # open time. Off by default because stale notes can belong to an
            # older run; --since can bound it.
            try:
                opened = (tdir / "notes.md").stat().st_mtime
                opened_from_notes = True
            except OSError:
                opened = 0.0
        if not opened or (since and opened < since):
            continue
        if any((tdir / n).exists() for n in ("closed", "reaped.json")):
            continue
        if (tdir / "reap_token").exists() and triage_active():
            continue
        if (tdir / ".hold").exists():
            print(f"[enforce] {code}: hold marker; skipped")
            continue
        ch = catalog.get(code) or {}
        total = int(ch.get("flag_total") or ch.get("flag_count") or ch.get("total_flags") or 0)
        platform_got = int(ch.get("solved_flags") or ch.get("solved_flag_count") or ch.get("flag_solved") or ch.get("user_flags") or 0)
        got = max(_local_flags(tdir), platform_got)
        # Local candidate files are NOT completion evidence: a worker may find a
        # flag that the main has not submitted yet. Only is_completed or the
        # platform's per-user solved count may close a box as completed.
        completed = bool(ch.get("is_completed")) or (total > 0 and platform_got >= total)
        age = now - opened
        lastp = _last_primitive(tdir, opened)
        st = _enforce_state(tdir)
        action = ""
        reason = ""
        pending = tdir / "close_pending"
        if pending.exists():
            try:
                pdata = json.loads(pending.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pdata = {"at": 0}
            if now - float(pdata.get("at", 0)) >= CLOSE_RETRY_S:
                action, reason = "close", "retry close_pending"
        if not action:
            if completed:
                seen = float(st.get("completed_seen") or 0)
                if not seen:
                    if close:
                        st["completed_seen"] = now
                        _write_enforce(tdir, st)
                    print(f"[enforce] {code}: completed; grace {COMPLETED_GRACE_S:g}s"
                          + ("" if close else " (dry-run, not recorded)"))
                    continue
                if now - seen >= COMPLETED_GRACE_S:
                    action, reason = "close", "completed (is_completed=true)"
            else:
                hard = TIER_HARD.get(str(ch.get("difficulty") or "medium").lower(), TIER_HARD["medium"])
                # A box with flags and a known total is never auto-closed at the
                # tier hard cap: keep hunting the remaining flags until it is
                # completed, or until run_gate records an explicit abandonment
                # (fresh stall note / env-fail / user approval). 0-flag boxes
                # still follow the normal tier/stall rules.
                if age >= hard and got == 0 and catalog_ok:
                    action, reason = "close", f"hard deadline {int(age)}s>={hard}s"
                elif got == 0 and now - lastp >= STALL_CLOSE_S:
                    action, reason = "close", f"stall {int(now - lastp)}s>={int(STALL_CLOSE_S)}s without a primitive"
                elif got > 0 and total > 0 and _abandoned(tdir):
                    action, reason = "close", "abandoned with evidence (partial flags)"
                elif got > 0 and total <= 0 and now - lastp >= SOLVED_HOLD_S:
                    action, reason = "close", f"solved-hold {int(now - lastp)}s>={int(SOLVED_HOLD_S)}s, flag_total unknown"
        if not action:
            continue
        actions += 1
        flags_txt = f"{got}/{total or '?'}"
        if close:
            _close_box(
                code, f"{reason}; flags={flags_txt}", api,
                expected_opened=None if opened_from_notes else opened,
                expected_lastp=lastp,
            )
        else:
            print(f"[enforce] {code}: WOULD_CLOSE ({reason}; flags={flags_txt}, age={int(age)}s, lastp={int(now - lastp)}s)")
    if not close:
        print("[enforce] dry-run: pass --close (or use team_enforce close=true) to act")
    return actions


def hold_box(code: str, reason: str = "") -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        return 1
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / ".hold").write_text(reason or str(_now()), encoding="utf-8")
    print(f"[team] hold {code} ({reason or 'no reason'})")
    return 0


def unhold_box(code: str) -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        return 1
    try:
        (TEAMS / code / ".hold").unlink()
        print(f"[team] unhold {code}")
        return 0
    except OSError:
        print(f"[team] not held: {code}")
        return 1


def panic_teams() -> int:
    """Close every box this pool knows about (opened_at, no closed/reaped)."""
    if os.environ.get("TEAM_WORKER") == "1":
        print("[team] workers may not panic", file=sys.stderr)
        return 1
    api = Api()
    if not api.ok():
        print("[team] panic: BENCHMARK_BASE_URL/BENCHMARK_TOKEN (or TSEC_BASE_URL/TSEC_TOKEN) not set", file=sys.stderr)
        return 1
    n = 0
    try:
        dirs = sorted(TEAMS.iterdir())
    except OSError:
        return 0
    for tdir in dirs:
        if not tdir.is_dir():
            continue
        if not (tdir / "opened_at").exists():
            continue
        if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
            continue
        if _close_box(tdir.name, "panic", api):
            n += 1
    print(f"[team] panic closed {n}")
    return 0


def _enforce_loop(close: bool, since: float, max_seconds: float, include_notes: bool = False) -> int:
    end = _now() + max(1.0, max_seconds)
    stop = TEAMS / "enforce.stop"
    while _now() < end:
        if stop.exists():
            try:
                stop.unlink()
            except OSError:
                pass
            print("[enforce] stop requested")
            return 0
        try:
            _enforce_once(close, since, include_notes)
        except Exception as exc:  # noqa: BLE001
            print(f"[enforce] pass failed: {exc}", file=sys.stderr)
        time.sleep(max(2.0, ENFORCE_EVERY))
    return 0


def _ensure_enforcer(since: float) -> None:
    """Start one detached watchdog for boxes opened from now on."""
    if os.environ.get("TEAM_ENFORCE", "1") == "0":
        return
    if not Api().ok():
        return
    pidfile = TEAMS / "enforce.pid"
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        if alive(pid):
            return
    except (OSError, ValueError):
        pass
    log = TEAMS / "enforce.log"
    try:
        with log.open("ab") as f:
            p = subprocess.Popen(
                [PY, str(Path(__file__).resolve()), "enforce", "--watch", "--close",
                 "--since", str(since), "--max-seconds", str(ENFORCE_MAX_S)],
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                creationflags=NO_WINDOW,
            )
        pidfile.write_text(str(p.pid), encoding="utf-8")
        print(f"[team] enforcer started pid={p.pid}; manages boxes opened >= {since:.0f}")
    except Exception as exc:  # noqa: BLE001
        print(f"[team] enforcer start failed: {exc}")


def _stall_code() -> str | None:
    now = _now()
    try:
        for tdir in sorted(TEAMS.iterdir()):
            if not tdir.is_dir():
                continue
            opened = _read_float(tdir / "opened_at", 0.0)
            if not opened:
                # opened_at is the canonical "currently open" marker. Notes-only
                # boxes are handled by `enforce --include-notes`, not clock STALL.
                continue
            if any((tdir / n).exists() for n in ("closed", "reaped.json")):
                continue
            if (tdir / "reap_token").exists() and triage_active():
                continue
            if (tdir / ".hold").exists():
                continue
            if now - _last_primitive(tdir, opened) >= STALL_CLOSE_S:
                return tdir.name
    except OSError:
        pass
    return None


def _recon_code() -> str | None:
    """Surface a worker's NEED_RECON request to the main agent."""
    try:
        for tdir in sorted(TEAMS.iterdir()):
            if not tdir.is_dir():
                continue
            if any((tdir / n).exists() for n in ("closed", "reaped.json", ".hold")):
                continue
            candidates = (list(tdir.glob("worker-*.md"))
                          + list(tdir.glob("result-*.md"))
                          + list(tdir.glob("candidates-*.md")))
            candidates += [tdir / "result.md", tdir / "notes.md", tdir / "candidates.md"]
            opened = _read_float(tdir / "opened_at", 0.0)
            for p in candidates:
                try:
                    if not p.is_file():
                        continue
                    if opened and p.stat().st_mtime < opened - 5:
                        continue
                    if not opened and _now() - p.stat().st_mtime > CANDIDATE_MAX_AGE:
                        continue
                    if "NEED_RECON" in p.read_text(encoding="utf-8", errors="replace"):
                        return tdir.name
                except OSError:
                    continue
    except OSError:
        pass
    return None


def _latest_flaglike() -> str | None:
    if not TEAMS.is_dir():
        return None
    try:
        cands = []
        for x in TEAMS.glob("*/flaglike-*"):
            try:
                if not x.is_file() or x.stat().st_size == 0:
                    continue
                if any((x.parent / n).exists() for n in ("closed", "reaped.json")):
                    continue
                opened = _read_float(x.parent / "opened_at", 0.0)
                age = _now() - x.stat().st_mtime
                if opened and x.stat().st_mtime < opened - 5:
                    continue
                if not opened and age > CANDIDATE_MAX_AGE:
                    continue
                cands.append(x)
            except OSError:
                continue
        cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        if cands:
            return cands[0].parent.name
    except OSError:
        pass
    return None


def clock() -> str:
    try:
        d = load_jobs()
    except Exception as exc:  # noqa: BLE001
        return f"team ? | jobs.json unreadable: {exc}"
    jobs = list(d.get("jobs", {}).values())
    run = sum(1 for j in jobs if j.get("status") in ("running", "queued"))
    ok = sum(1 for j in jobs if j.get("status") == "done")
    bad = sum(1 for j in jobs if j.get("status") in ("error", "cancelled", "orphaned"))
    s = f"team {run}r/{ok}d" + (f"/{bad}o" if bad else "")
    if triage_active():
        s += " | TRIAGE auto"
    cand = _latest_candidate()
    if cand:
        s += f" | CANDIDATE {cand}"
    else:
        like = _latest_flaglike()
        if like:
            s += f" | FLAGLIKE {like} (second-path check)"
    stall = _stall_code()
    if stall:
        s += f" | STALL {stall}"
    recon = _recon_code()
    if recon:
        s += f" | RECON {recon}"
    try:
        pid = int((TEAMS / "enforce.pid").read_text(encoding="utf-8").strip())
        if alive(pid):
            s += " | ENF"
    except (OSError, ValueError):
        pass
    return s


def cancel_reaper(code: str) -> None:
    try:
        (TEAMS / code / "reap_token").unlink()
    except OSError:
        pass


def ensure_triage_card(code: str, reason: str, opened: float = 0.0) -> None:
    """An auto-closed triage box must still leave a card (image v12.5.2)."""
    try:
        tdir = TEAMS / code
        cards = [c for c in (tdir / "triage.txt", tdir / "triage.md") if c.exists()]
        if cards and (not opened or max(c.stat().st_mtime for c in cards) >= opened - 5):
            return
        (tdir / "triage.txt").write_text(
            f"AUTO-CLOSED at the triage probe deadline ({reason}).\n"
            "triage: unprobed\nstack: ?\nauth: ?\nclass: D (unknown)\n"
            "next: re-open once, run triage.sh, then decide.\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _reap(code: str, delay: float, token: str) -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        return 1
    time.sleep(max(1.0, float(delay)))
    tf = TEAMS / code / "reap_token"
    try:
        if not tf.exists() or tf.read_text(encoding="utf-8").strip() != token:
            return 0
        # The token starts with the opened_at it was scheduled for. If the box
        # was closed and re-opened, adopt() rewrote opened_at; this old timer
        # must not close the new instance.
        try:
            scheduled = float(token.split("-", 1)[0])
            opened = float((TEAMS / code / "opened_at").read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            scheduled = opened = 0.0
        if scheduled and opened and abs(scheduled - opened) > 0.5:
            return 0
        # Early `triage_gate.py done` or a new triage phase must cancel the
        # old 120s timer; otherwise A->B work is killed at the probe deadline.
        st = triage_state()
        if st.get("done"):
            print(f"[team] triage: {code} done/closed early; reaper stands down")
            try:
                if tf.read_text(encoding="utf-8").strip() == token:
                    tf.unlink()
            except OSError:
                pass
            return 0
        try:
            started = float(st.get("started") or 0)
        except (TypeError, ValueError):
            started = 0.0
        if started and scheduled and started > scheduled + 0.5:
            print(f"[team] triage: {code} belongs to a previous phase; reaper stands down")
            try:
                if tf.read_text(encoding="utf-8").strip() == token:
                    tf.unlink()
            except OSError:
                pass
            return 0
        if (TEAMS / code / ".hold").exists():
            print(f"[team] triage: {code} is held; reaper stands down")
            try:
                if tf.read_text(encoding="utf-8").strip() == token:
                    tf.unlink()
            except OSError:
                pass
            return 0
    except OSError:
        return 0
    ensure_triage_card(code, f"{int(delay)}s", opened)
    api = Api()
    ok = False
    try:
        res = api.close(code)
        cur = _read_float(TEAMS / code / "opened_at", 0.0)
        if opened and abs(cur - opened) > 0.5:
            print(f"[team] triage: {code} re-opened during close; not marking the new instance")
            try:
                # Only remove our own token; adopt() may have written a new one.
                if tf.read_text(encoding="utf-8").strip() == token:
                    tf.unlink()
            except OSError:
                pass
            return 0
        (TEAMS / code / "reaped.json").write_text(
            json.dumps({"at": _now(), "result": res}, ensure_ascii=False), encoding="utf-8"
        )
        _mark_closed(code)
        ok = True
        print(f"[team] triage reaped {code}")
    except Exception as exc:  # noqa: BLE001
        cur = _read_float(TEAMS / code / "opened_at", 0.0)
        if opened and abs(cur - opened) > 0.5:
            print(f"[team] triage: {code} re-opened while close failed; not queueing a retry for the new instance")
            return 0
        (TEAMS / code / "reap_error.txt").write_text(str(exc), encoding="utf-8")
        # Leave a retry record; the enforcer will pick it up. Do NOT cancel the
        # jobs on a failed close: the box may still be usable.
        try:
            (TEAMS / code / "close_pending").write_text(
                json.dumps({"at": _now(), "reason": "triage reap failed", "attempts": 1, "error": str(exc)}),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(f"[team] reap failed {code}: {exc}")
    try:
        # Only remove our own token; adopt() may have written a new one.
        if tf.read_text(encoding="utf-8").strip() == token:
            tf.unlink()
    except OSError:
        pass
    if ok:
        cancel(code)
    return 0


def adopt(code: str, addr: str) -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        print("[team] workers may not adopt/start boxes", file=sys.stderr)
        return 1
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    # Duplicate start callbacks (same box/address) must not reset opened_at or
    # wipe a running worker's markers. A genuinely new address is a re-open.
    old_addr = ""
    try:
        old_addr = (tdir / "addr.txt").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    already_open = ((tdir / "opened_at").exists()
                    and not (tdir / "closed").exists()
                    and not (tdir / "reaped.json").exists())
    if already_open and (not addr or str(addr).strip() == old_addr):
        if not triage_active():
            for name in ("reap_token", "close_pending"):
                try:
                    (tdir / name).unlink()
                except OSError:
                    pass
        print(f"[team] {code}: already adopted ({old_addr or 'no addr'}); ignored duplicate")
        return 0
    # A re-open must clear old close/watchdog markers, otherwise the next
    # enforcer pass skips the new instance as already closed.
    for name in ("closed", "reaped.json", "enforce.json", "close_pending",
                 "abandon.json", "env_fail.json"):
        try:
            (tdir / name).unlink()
        except OSError:
            pass
    # A stale triage reaper token would make the enforcer skip this box forever.
    if not triage_active():
        try:
            (tdir / "reap_token").unlink()
        except OSError:
            pass
    # First box of a run: start the 30-minute triage phase automatically unless
    # explicitly disabled. Without this, forgetting `triage_gate.py start`
    # silently disables the triage guard.
    st = triage_state()
    try:
        phase_end = float(st.get("phase_end") or 0)
    except (TypeError, ValueError):
        phase_end = 0.0
    new_run = _new_run()
    need_triage = (not st) or (
        new_run and (bool(st.get("done")) or (bool(phase_end) and phase_end < _now()))
    )
    if need_triage and os.environ.get("TBENCH_TRIAGE", "1") != "0":
        try:
            subprocess.run([PY, str(TRIAGE_GATE), "start"], capture_output=True, timeout=10,
                           creationflags=NO_WINDOW)
        except Exception:
            pass
    if addr:
        (tdir / "addr.txt").write_text(str(addr), encoding="utf-8")
    opened = _now()
    (tdir / "opened_at").write_text(str(opened), encoding="utf-8")
    _write_owner(tdir)
    if not triage_active():
        _ensure_enforcer(opened)
    if triage_active():
        delay = float(triage_state().get("probe") or TRIAGE_PROBE)
        token = f"{opened:.3f}-{random.randint(1000, 9999)}"
        (tdir / "reap_token").write_text(token, encoding="utf-8")
        try:
            subprocess.Popen(
                [PY, str(Path(__file__).resolve()), "_reap", code, str(delay), token],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                (tdir / "reap_token").unlink()
                (tdir / "reap_error.txt").write_text(f"spawn failed: {exc}", encoding="utf-8")
            except OSError:
                pass
            print(f"[team] TRIAGE {code}: reaper spawn failed: {exc}", file=sys.stderr)
            return 1
        print(f"[team] TRIAGE {code}: close in {delay:g}s; no auto-team")
        return 0
    if not str(addr).strip():
        try:
            addr = (tdir / "addr.txt").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if not str(addr).strip():
        print(f"[team] {code}: no container_addr; not dispatching")
        return 1
    mode = str(os.environ.get("TBENCH_AUTO_TEAM", "lead")).strip().lower()
    if mode in ("0", "none", "off", "false", "no"):
        print(f"[team] {code}: auto-team disabled (TBENCH_AUTO_TEAM={mode})")
        return 0
    if mode == "lead":
        # 默认模式：每箱一个队长（纯管理、不做题），由队长自己派 2-3 个执行者。
        task = chr(10).join([
            f"Box {code} at {addr}. You are this box's LEAD: manage, never attack.",
            f"1) read {tdir}/triage.txt, {tdir}/notes.md, {tdir}/addr.txt first;",
            "2) dispatch 2-3 workers with DISTINCT mechanisms, e.g.",
            f"   python bin/team_pool.py dispatch {code} recon '<surface to map>'",
            f"   python bin/team_pool.py dispatch {code} exploit '<hypothesis A>'",
            f"   python bin/team_pool.py dispatch {code} verifier '<what re-derive>'",
            "3) poll: python bin/team_pool.py poll ; aggregate into " + str(tdir / "lead-plan.md"),
            "4) report to the main ONLY on: (a) flag candidate, (b) request abandon/close with 3-line evidence, (c) budget out. Never stream progress.",
            f"Budget: at most {MAX_PER_BOX - 1} concurrent workers; 15 min without a new primitive -> stop and report.",
        ])
        dispatch(code, "lead", task)
        return 0
    for agent in ("recon", "exploit", "verifier"):
        task = (
            f"Box {code} at {addr}. Read {tdir}/triage.txt if present. "
            f"Work only on this box; write under {tdir}/. "
            f"Write your summary to the worker file named in your prompt (worker-<job-id>.md)."
        )
        dispatch(code, agent, task)
    return 0


def _schedule_reaper(code: str, opened: float) -> None:
    """Schedule the triage 120s close for a tracked box (no dispatch)."""
    if not triage_active():
        return
    tdir = TEAMS / code
    if (tdir / "reap_token").exists():
        return
    delay = float(triage_state().get("probe") or TRIAGE_PROBE)
    token = f"{opened:.3f}-{random.randint(1000, 9999)}"
    try:
        (tdir / "reap_token").write_text(token, encoding="utf-8")
        subprocess.Popen(
            [PY, str(Path(__file__).resolve()), "_reap", code, str(delay), token],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=NO_WINDOW,
        )
        print(f"[team] TRIAGE {code}: close in {delay:g}s (tracked)")
    except Exception as exc:  # noqa: BLE001
        try:
            (tdir / "reap_token").unlink()
            (tdir / "reap_error.txt").write_text(f"track spawn failed: {exc}", encoding="utf-8")
        except OSError:
            pass


def track(code: str, addr: str) -> int:
    """Record a box started outside team_pool (e.g. tsec.py/framework/curl).

    No dispatch. Same addr on an already-open box only refreshes addr.txt;
    a new addr resets opened_at and clears stale close markers.
    """
    if os.environ.get("TEAM_WORKER") == "1":
        print("[team] workers may not track boxes", file=sys.stderr)
        return 1
    if not code or not addr:
        return 1
    code = _valid_code(code)
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    old_addr = ""
    try:
        old_addr = (tdir / "addr.txt").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    opened = _read_float(tdir / "opened_at", 0.0)
    already = opened and not (tdir / "closed").exists() and not (tdir / "reaped.json").exists()
    if already and str(addr).strip() == old_addr:
        if triage_active():
            _schedule_reaper(code, opened)
        else:
            _ensure_enforcer(opened or _now())
        return 0
    reopened = bool(already and old_addr and str(addr).strip() != old_addr)
    if not already or reopened:
        # New/re-opened instance: clear stale close/watchdog markers.
        for name in ("closed", "reaped.json", "enforce.json", "close_pending",
                     "abandon.json", "env_fail.json"):
            try:
                (tdir / name).unlink()
            except OSError:
                pass
        if not triage_active():
            try:
                (tdir / "reap_token").unlink()
            except OSError:
                pass
    (tdir / "addr.txt").write_text(str(addr), encoding="utf-8")
    if not already or reopened or opened == 0:
        opened = _now()
        (tdir / "opened_at").write_text(str(opened), encoding="utf-8")
        _write_owner(tdir)
    if triage_active():
        _schedule_reaper(code, opened or _now())
    else:
        _ensure_enforcer(opened or _now())
    print(f"[team] tracked {code} at {addr}" + ("" if not already else " (refreshed)"))
    return 0


def _http_body(exc) -> tuple[str, str]:
    try:
        raw = exc.read().decode("utf-8", "replace")
        data = json.loads(raw)
        return str(data.get("code") or ""), str(data.get("message") or raw)
    except Exception:
        return "", str(exc)


def reconcile() -> int:
    """Clear local open markers when the platform says the task is finished.

    This does NOT call close (the platform task is over); it only stops the
    local doctor/panel from showing stale opened boxes forever.
    """
    api = Api()
    if not api.ok():
        print("[team] reconcile: no BENCHMARK/TSEC base+token", file=sys.stderr)
        return 2
    try:
        api.challenges()
    except urllib.error.HTTPError as exc:
        code, msg = _http_body(exc)
        if code in ("invalid_state", "task_not_found") or "finished" in msg.lower():
            n = 0
            closed_codes: list[str] = []
            try:
                dirs = list(TEAMS.iterdir())
            except OSError:
                dirs = []
            for tdir in dirs:
                if not tdir.is_dir() or not (tdir / "opened_at").exists():
                    continue
                if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
                    continue
                _mark_closed(tdir.name)
                try:
                    (tdir / "reconciled.json").write_text(
                        json.dumps({"at": _now(), "reason": msg, "code": code}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                closed_codes.append(tdir.name)
                n += 1
            for _code in closed_codes:
                try:
                    cancel(_code)
                except Exception:
                    pass
            print(f"[team] platform task finished; marked {n} local open box(es) closed")
            return 0
        print(f"[team] reconcile failed: {code} {msg}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[team] reconcile failed: {exc}", file=sys.stderr)
        return 1
    print("[team] reconcile: platform task is live; no local changes")
    return 0


def _oldest_open() -> str | None:
    """Oldest box that is safe to evict on 409.

    Only a 0-flag box or an explicitly abandoned box may be auto-closed here;
    evicting a partial multi-flag box would silently lose the remaining flags.
    """
    best = None
    try:
        for tdir in TEAMS.iterdir():
            if not tdir.is_dir():
                continue
            opened = _read_float(tdir / "opened_at", 0.0)
            if not opened:
                continue
            if any((tdir / n).exists() for n in ("closed", "reaped.json", ".hold")):
                continue
            if _local_flags(tdir) > 0 and not _abandoned(tdir):
                continue
            if best is None or opened < best[0]:
                best = (opened, tdir.name)
    except OSError:
        pass
    return best[1] if best else None


def api_start(code: str) -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        print("[team] workers may not start boxes", file=sys.stderr)
        return 3
    api = Api()
    try:
        ch = api.challenge(code) or {}
    except Exception:
        ch = {}
    if ch.get("is_completed"):
        print(f"[team] {code} is_completed=true; refusing start (use force if remaining flags exist)")
        return 2
    addr = None
    for attempt in range(2):
        try:
            addr = api.start(code)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt == 0:
                victim = _oldest_open()
                if victim:
                    print(f"[team] 409 max active; closing oldest safe box {victim}")
                    _close_box(victim, "409 max active", api)
                    continue
                print("[team] 409 max active; no 0-flag/abandoned box to evict "
                      "(partial boxes need an explicit run_gate abandonment)", file=sys.stderr)
            print(f"[team] start failed: HTTP {exc.code}", file=sys.stderr)
            return 3
        except Exception as exc:  # noqa: BLE001
            print(f"[team] start failed: {exc}", file=sys.stderr)
            return 3
    if addr is None:
        return 3
    (TEAMS / code).mkdir(parents=True, exist_ok=True)
    (TEAMS / code / "start.json").write_text(
        json.dumps(addr, ensure_ascii=False), encoding="utf-8"
    )
    a = addr.get("container_addr") if isinstance(addr, dict) else addr
    if isinstance(a, list):
        a = a[0] if a else ""
    if not a:
        print(f"[team] start returned no container_addr: {json.dumps(addr, ensure_ascii=False)[:300]}", file=sys.stderr)
        return 3
    rc = adopt(code, a if isinstance(a, str) else json.dumps(a, ensure_ascii=False))
    print(json.dumps(addr, ensure_ascii=False))
    return rc


def api_close(code: str) -> int:
    code = _valid_code(code)
    if os.environ.get("TEAM_WORKER") == "1":
        print("[team] workers may not close boxes", file=sys.stderr)
        return 1
    try:
        res = Api().close(code)
        print(json.dumps(res, ensure_ascii=False))
        cancel_reaper(code)
        _mark_closed(code)
        cancel(code)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[team] close failed (state kept): {exc}", file=sys.stderr)
        try:
            (TEAMS / code).mkdir(parents=True, exist_ok=True)
            (TEAMS / code / "close_pending").write_text(
                json.dumps({"at": _now(), "reason": "api_close failed", "attempts": 1, "error": str(exc)}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return 1


def doctor() -> int:
    """Print what is actually active: extension version, token, enforcer, ledgers."""
    print("team_pool: 2026-11-20 (jobs lock+backup, per-job artifacts, enforcer, candidates, worker guards)")
    base = bool(os.environ.get("BENCHMARK_BASE_URL") or os.environ.get("TSEC_BASE_URL"))
    token = bool(os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN"))
    print(f"platform env: base={base} token={token}"
          + ("" if base and token else "  -> close/start/enforcer auto-close are INERT"))
    _boxes, _src = platform_capacity()
    print(f"capacity: boxes={_boxes} ({_src}) per_box={MAX_PER_BOX} total={max_total()}")
    # The marker is written at session_start; a child pi inherits PI_SESSION_ID,
    # so this is only advisory. The authoritative signal is the tool-result
    # footer: it must contain "ext 2026-11-20.3".
    sid = os.environ.get("PI_SESSION_ID") or ""
    ext = TEAMS / f".team-pool-ext-{sid}" if sid else None
    if not sid:
        print("extension marker: PI_SESSION_ID not set (advisory only; check the footer)")
    elif ext and ext.exists():
        try:
            print("extension marker (advisory, may be a child):", ext.read_text(encoding="utf-8").strip())
        except OSError:
            print("extension marker: unreadable")
    else:
        print("extension marker: MISSING for this session id")
    print("extension authoritative check: tool-result footer must contain 'ext 2026-11-20.3'")
    try:
        others = sorted(TEAMS.glob(".team-pool-ext-*"), key=lambda x: x.stat().st_mtime, reverse=True)
        if others:
            print("extension marker latest:", others[0].read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        pass
    try:
        s = json.loads(TRIAGE.read_text(encoding="utf-8"))
        active = bool(s) and not s.get("done") and _now() < float(s.get("phase_end") or 0)
        print(f"triage: done={s.get('done')} active={active} cards={len(s.get('cards') or {})}")
    except Exception:
        print("triage: no state")
    try:
        d = load_jobs()
        jobs = list(d.get("jobs", {}).values())
        from collections import Counter
        print("jobs:", len(jobs), dict(Counter(str(j.get("status")) for j in jobs)))
    except Exception as e:  # noqa: BLE001
        print("jobs: unreadable", e)
    try:
        live = 0
        try:
            for tdir in TEAMS.iterdir():
                if (tdir / "opened_at").exists() and not (tdir / "closed").exists() and not (tdir / "reaped.json").exists():
                    live += 1
        except OSError:
            pass
        print("boxes: opened_at=%d live_open=%d closed=%d reaped=%d reap_token=%d hold=%d" % (
            len(list(TEAMS.glob("*/opened_at"))), live, len(list(TEAMS.glob("*/closed"))),
            len(list(TEAMS.glob("*/reaped.json"))), len(list(TEAMS.glob("*/reap_token"))),
            len(list(TEAMS.glob("*/.hold")))))
    except OSError:
        pass
    pf = TEAMS / "enforce.pid"
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
        print(f"enforcer: pid={pid} alive={alive(pid)}")
    except (OSError, ValueError):
        print("enforcer: not running")
    print("clock:", clock())
    print("rules at risk:")
    if not (base and token):
        print("  - platform actions are inert (no base/token)")
    has_ext = (TEAMS / f".team-pool-ext-{sid}").exists() if sid else True
    if not has_ext:
        print("  - extension likely old in this session (footer without ext 2026-11-20.3)")
    print(f"  - READ_GUARD={os.environ.get('READ_GUARD', 'block')} LOOP_GUARD={os.environ.get('LOOP_GUARD', 'block')}")
    if not WORKER_GUARD.exists():
        print("  - worker-guard missing: worker rate/flag-scan enforcement is OFF")
    cp = len(list(TEAMS.glob("*/close_pending"))) if TEAMS.is_dir() else 0
    if cp:
        print(f"  - {cp} close_pending record(s); run `enforce --close` with token to retry")
    try:
        d = load_jobs()
        jobs = list(d.get("jobs", {}).values())
        running = [j for j in jobs if j.get("status") in ("running", "queued")]
        if len(running) > max_total():
            print(f"  - total workers {len(running)} > {max_total()}")
        per = {}
        for j in running:
            per[j.get("code")] = per.get(j.get("code"), 0) + 1
        for code, n in per.items():
            if n > MAX_PER_BOX:
                print(f"  - {code}: {n} workers > {MAX_PER_BOX}")
    except Exception:
        pass
    now = _now()
    # Surface recent result/candidate files without an opened_at marker; these
    # are boxes started outside team_pool that track/adopt missed.
    for tdir in sorted(TEAMS.iterdir()) if TEAMS.is_dir() else []:
        if not tdir.is_dir() or (tdir / "opened_at").exists():
            continue
        if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
            continue
        files = list(tdir.glob("candidate*")) + list(tdir.glob("worker-*")) + list(tdir.glob("result*"))
        recent = False
        for f in files:
            try:
                if now - f.stat().st_mtime < 7200:
                    recent = True
                    break
            except OSError:
                continue
        if recent:
            print(f"  - {tdir.name}: untracked recent result/candidate; run track <code> <addr>")
    for tdir in sorted(TEAMS.iterdir()) if TEAMS.is_dir() else []:
        if not tdir.is_dir() or not (tdir / "opened_at").exists():
            continue
        if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
            continue
        code = tdir.name
        try:
            opened = _read_float(tdir / "opened_at", 0.0)
            lastp = _last_primitive(tdir, opened)
            idle = now - lastp
            if idle > STALL_CLOSE_S / 2:
                print(f"  - {code}: no primitive for {int(idle)}s (stall closes at {int(STALL_CLOSE_S)}s)")
            if not (tdir / "addr.txt").exists():
                print(f"  - {code}: no addr.txt (track the container addr)")
            if not (tdir / "notes.md").exists():
                print(f"  - {code}: no notes.md")
            if _local_flags(tdir) > 0:
                print(f"  - {code}: candidate/flag present; verify and submit before closing")
            if (tdir / ".hold").exists():
                print(f"  - {code}: held; enforcer will skip it")
        except OSError:
            continue
    if DEFAULT_JOB_TIMEOUT > STALL_CLOSE_S:
        print(f"  - TEAM_JOB_TIMEOUT={int(DEFAULT_JOB_TIMEOUT)}s > STALL_CLOSE_S={int(STALL_CLOSE_S)}s: "
              "long worker needs hold/notes")
    try:
        s = json.loads(TRIAGE.read_text(encoding="utf-8"))
        if s.get("done"):
            print("  - triage is done; new boxes go straight to A→B (a phase older than 6h restarts triage; TBENCH_TRIAGE=0 disables)")
    except Exception:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dispatch")
    d.add_argument("code")
    d.add_argument("agent")
    d.add_argument("task")
    d.add_argument("--timeout", type=float, default=0)
    p = sub.add_parser("poll")
    p.add_argument("--wait", type=float, default=0)
    c = sub.add_parser("cancel")
    c.add_argument("target", nargs="?")
    tr = sub.add_parser("track"); tr.add_argument("code"); tr.add_argument("addr")
    a = sub.add_parser("adopt")
    a.add_argument("code")
    a.add_argument("addr", nargs="?", default="")
    s = sub.add_parser("start")
    s.add_argument("code")
    cl = sub.add_parser("close")
    cl.add_argument("code")
    sub.add_parser("capacity")
    sub.add_parser("clock")
    sub.add_parser("doctor")
    h = sub.add_parser("hold"); h.add_argument("code"); h.add_argument("reason", nargs="?", default="")
    uh = sub.add_parser("unhold"); uh.add_argument("code")
    sub.add_parser("reconcile")
    sub.add_parser("panic")
    en = sub.add_parser("enforce")
    en.add_argument("--watch", action="store_true")
    en.add_argument("--close", action="store_true")
    en.add_argument("--since", type=float, default=0.0)
    en.add_argument("--include-notes", action="store_true")
    en.add_argument("--max-seconds", type=float, default=ENFORCE_MAX_S)
    r = sub.add_parser("_run")
    r.add_argument("job_id")
    rp = sub.add_parser("_reap")
    rp.add_argument("code")
    rp.add_argument("delay", type=float)
    rp.add_argument("token")
    x = ap.parse_args()
    if x.cmd == "dispatch":
        return dispatch(x.code, x.agent, x.task, x.timeout)
    if x.cmd == "poll":
        return poll(x.wait)
    if x.cmd == "cancel":
        return cancel(x.target)
    if x.cmd == "track":
        return track(x.code, x.addr)
    if x.cmd == "adopt":
        return adopt(x.code, x.addr)
    if x.cmd == "start":
        return api_start(x.code)
    if x.cmd == "close":
        return api_close(x.code)
    if x.cmd == "capacity":
        boxes, src = platform_capacity()
        print(json.dumps({
            "boxes": boxes, "source": src,
            "per_box": MAX_PER_BOX, "total": max_total(),
            "per_box_env": "TEAM_MAX_PER_BOX", "total_env": "TEAM_MAX_TOTAL",
        }, ensure_ascii=False))
        return 0
    if x.cmd == "clock":
        print(clock())
        return 0
    if x.cmd == "doctor":
        return doctor()
    if x.cmd == "hold":
        return hold_box(x.code, x.reason)
    if x.cmd == "unhold":
        return unhold_box(x.code)
    if x.cmd == "reconcile":
        return reconcile()
    if x.cmd == "panic":
        return panic_teams()
    if x.cmd == "enforce":
        if os.environ.get("TEAM_WORKER") == "1":
            print("[team] workers may not run the enforcer", file=sys.stderr)
            return 1
        if x.close and not Api().ok():
            print("[enforce] --close requested but BENCHMARK/TSEC base+token are not set; refusing to pretend", file=sys.stderr)
            return 1
        if x.watch:
            return _enforce_loop(x.close, x.since, x.max_seconds, x.include_notes)
        _enforce_once(x.close, x.since, x.include_notes)
        return 0
    if x.cmd == "_run":
        return _run(x.job_id)
    if x.cmd == "_reap":
        return _reap(x.code, x.delay, x.token)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
