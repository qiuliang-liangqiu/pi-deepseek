#!/usr/bin/env python3
"""run_gate — code-level "do not stop early" gate for full-auto benchmark runs.

Policy (local pi; the tbench image needs the same code):

  * `finish` is allowed only when every *opened* challenge is completed
    (`is_completed` or `flags >= flag_total`), or the platform says the task is
    over (`invalid_state` / `task_not_found` / "finished"), or the user approved
    it in a TTY.
  * Closing a box that already has flags but is not completed is blocked unless
    it is an explicit abandonment with stall evidence (notes updated within the
    15-minute window), a recorded environment failure, or a TTY approval.
  * A 0-flag box may still be closed by the normal stall gate / tier rule.
  * Exemptions are deliberately narrow: 15-minute stall gate, environment
    failure, triage's 120 s probe, and instance-only submission approval.

The gate is inert when no platform env and no open box exist. `RUN_GATE=off`
or `run_gate.py disarm` disables it for a human-driven session.

Exit codes for `check`: 0 allow, 1 block, 2 inert/unknown.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("RUN_GATE_HOME") or (Path.home() / ".pi" / "agent"))
TEAMS = ROOT / "teams"
STATE = ROOT / "run_gate.json"
DISABLED = ROOT / "run_gate.disabled"
APPROVALS = ROOT / "run_gate_approvals"

STALL_S = int(os.environ.get("RUN_GATE_STALL_S", "900"))
SOLVED_HOLD_S = int(os.environ.get("RUN_GATE_SOLVED_HOLD_S", "600"))
CACHE_S = int(os.environ.get("RUN_GATE_CACHE_S", "30"))
HTTP_TIMEOUT = float(os.environ.get("RUN_GATE_HTTP_TIMEOUT", "5"))
APPROVAL_TTL = int(os.environ.get("RUN_GATE_APPROVAL_TTL", "300"))

FLAG_RE = re.compile(r"flag\{[^}\s]{4,}\}", re.I)
NOTE_STALL_RE = re.compile(r"(无新原语|没有新原语|no\s+new\s+primitive|stall|give\s*up|放弃)", re.I)

# 多会话共用 teams/ 时的隔离：箱子由开启它的会话拥有（teams/<code>/.owner），
# 只把关自己的箱子；没有 .owner（旧箱）或无从得知自己是谁时，行为与以前完全一致。
def _ppid_of(pid: int) -> int:
    """Parent pid for process `pid` (Windows). Shims/aliases add a layer, so the
    caller walks up a few ancestors instead of trusting os.getppid() alone."""
    if os.name != "nt" or pid <= 4:
        return 0
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return 0

        class _PBI(ctypes.Structure):
            _fields_ = [
                ("ExitStatus", ctypes.c_long),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p),
                ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
            ]

        pbi = _PBI()
        need = ctypes.c_ulong(0)
        try:
            rc = ntdll.NtQueryInformationProcess(
                h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(need))
            return int(pbi.InheritedFromUniqueProcessId or 0) if rc == 0 else 0
        finally:
            k32.CloseHandle(h)
    except Exception:
        return 0


def session_from_parent() -> str:
    """Resolve this session when the caller cannot pass --session.

    extensions/run-gate writes teams/.pi-pids/<pi pid> = <session id> at
    session_start; this script (a child of that same pi process) walks its own
    ancestry to find the entry, which also survives python shims/aliases.
    Entries expire after 12h, so a recycled pid cannot inherit an old owner.
    """
    d = TEAMS / ".pi-pids"
    pid = os.getppid()
    for _ in range(6):
        if pid <= 4:
            break
        f = d / str(pid)
        try:
            if time.time() - f.stat().st_mtime <= 12 * 3600:
                sid = f.read_text(encoding="utf-8").strip()
                if sid and sid != "unknown":
                    return sid
        except OSError:
            pass
        pid = _ppid_of(pid)
    return ""


SESSION_ID = (os.environ.get("RUN_GATE_SESSION") or os.environ.get("PI_SESSION_ID")
              or session_from_parent()).strip()


def box_owner(tdir: Path) -> str:
    try:
        return (tdir / ".owner").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def is_foreign(tdir: Path) -> bool:
    """True when this box belongs to another session (not our run)."""
    if not SESSION_ID:
        return False
    owner = box_owner(tdir)
    return bool(owner) and owner != SESSION_ID


def now() -> float:
    return time.time()


def _atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_float(path: Path, default: float = 0.0) -> float:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return default


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- #
# platform catalog
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self) -> None:
        self.base = (os.environ.get("BENCHMARK_BASE_URL")
                     or os.environ.get("TSEC_BASE_URL") or "").rstrip("/")
        self.token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN") or ""

    def ok(self) -> bool:
        return bool(self.base and self.token)

    def challenges(self):
        req = urllib.request.Request(self.base + "/openapi/v1/challenges")
        req.add_header("BENCHMARK_TOKEN", self.token)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode() or "{}")
        if isinstance(data, dict):
            rows = data.get("challenges") or data.get("items") or []
        else:
            rows = data
        return rows if isinstance(rows, list) else []


def _is_finished_error(exc: urllib.error.HTTPError) -> bool:
    try:
        body = exc.read().decode("utf-8", "replace")[:800].lower()
    except Exception:
        body = ""
    return exc.code in (400, 404, 409, 410) and any(
        s in body for s in ("invalid_state", "task_not_found", "finished", "已结束")
    )


def load_catalog(force: bool = False) -> dict:
    """Fetch or reuse the platform catalog.

    Returns {ok, inert, finished, reason, rows, at}. `finished` is sticky:
    once the platform says the task is over, do not pretend it is running.
    """
    cached = _read_json(STATE, {}) or {}
    c = cached.get("catalog") or {}
    if not force and c and now() - float(c.get("at") or 0) < CACHE_S:
        return c

    fake = os.environ.get("RUN_GATE_FAKE_CATALOG")
    if fake:
        rows = _read_json(Path(fake), [])
        cat = {"ok": True, "inert": False, "finished": False,
               "reason": "fake catalog", "rows": rows if isinstance(rows, list) else [],
               "at": now()}
        _atomic(STATE, {**cached, "catalog": cat})
        return cat

    api = Api()
    if not api.ok():
        cat = {"ok": False, "inert": True, "finished": bool(c.get("finished")),
               "reason": "no platform env", "rows": [], "at": now()}
        _atomic(STATE, {**cached, "catalog": cat})
        return cat

    try:
        rows = api.challenges()
        cat = {"ok": True, "inert": False, "finished": False,
               "reason": "", "rows": rows, "at": now()}
    except urllib.error.HTTPError as exc:
        if _is_finished_error(exc):
            cat = {"ok": True, "inert": False, "finished": True,
                   "reason": f"HTTP {exc.code}: task finished", "rows": [], "at": now()}
        else:
            cat = {"ok": False, "inert": False, "finished": bool(c.get("finished")),
                   "reason": f"HTTP {exc.code}", "rows": [], "at": now()}
    except Exception as exc:  # noqa: BLE001
        cat = {"ok": False, "inert": False, "finished": bool(c.get("finished")),
               "reason": str(exc)[:300], "rows": [], "at": now()}
    _atomic(STATE, {**cached, "catalog": cat})
    return cat


def challenge_map(cat: dict) -> dict:
    return {str(x.get("unique_code")): x for x in cat.get("rows") or []
            if isinstance(x, dict) and x.get("unique_code")}


def challenge_numbers(ch: dict) -> tuple[int, int]:
    total = int(ch.get("flag_total") or ch.get("flag_count") or ch.get("total_flags") or 0)
    got = int(ch.get("solved_flags") or ch.get("solved_flag_count")
              or ch.get("flag_solved") or ch.get("user_flags") or 0)
    return got, total


def challenge_done(ch: dict) -> tuple[bool, int, int]:
    got, total = challenge_numbers(ch)
    return bool(ch.get("is_completed")) or (total > 0 and got >= total), got, total


# --------------------------------------------------------------------------- #
# local boxes
# --------------------------------------------------------------------------- #
def local_flags(tdir: Path) -> int:
    found: set[str] = set()
    for name in ("notes.md", "NOTES.md"):
        try:
            found.update(m.lower() for m in FLAG_RE.findall((tdir / name).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    for pat in ("candidates-*.md", "candidate-*.txt", "result-*.md"):
        for p in tdir.glob(pat):
            try:
                found.update(m.lower() for m in FLAG_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return len(found)


def last_activity(tdir: Path, opened: float) -> float:
    latest = opened
    for name in ("notes.md", "NOTES.md", ".heartbeat", ".primitive"):
        latest = max(latest, _mtime(tdir / name))
    for pat in ("candidates-*.md", "candidate-*.txt", "result-*.md", "triage.txt"):
        for p in tdir.glob(pat):
            latest = max(latest, _mtime(p))
    return latest


def local_boxes() -> list[dict]:
    out: list[dict] = []
    try:
        dirs = sorted(TEAMS.iterdir())
    except OSError:
        return out
    for tdir in dirs:
        if not tdir.is_dir():
            continue
        opened = _read_float(tdir / "opened_at", 0.0)
        if not opened:
            continue
        if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
            continue
        if is_foreign(tdir):
            continue
        out.append({
            "code": tdir.name,
            "dir": tdir,
            "opened": opened,
            "flags": local_flags(tdir),
            "last": last_activity(tdir, opened),
        })
    return out


def foreign_boxes() -> list[str]:
    """Boxes skipped because another session opened them (status display only)."""
    out: list[str] = []
    if not SESSION_ID:
        return out
    try:
        dirs = sorted(TEAMS.iterdir())
    except OSError:
        return out
    for tdir in dirs:
        if not tdir.is_dir():
            continue
        if not _read_float(tdir / "opened_at", 0.0):
            continue
        if (tdir / "closed").exists() or (tdir / "reaped.json").exists():
            continue
        if is_foreign(tdir):
            out.append(tdir.name)
    return out


def abandoned(tdir: Path) -> dict | None:
    d = _read_json(tdir / "abandon.json")
    return d if isinstance(d, dict) else None


def env_failed(tdir: Path | None = None) -> dict | None:
    paths = [ROOT / "run_gate_env_fail.json"]
    if tdir is not None:
        paths.insert(0, tdir / "env_fail.json")
    for p in paths:
        d = _read_json(p)
        if isinstance(d, dict):
            return d
    return None


def armed(cat: dict, boxes: list[dict]) -> bool:
    mode = (os.environ.get("RUN_GATE") or "").lower()
    if mode in ("off", "0", "false", "no"):
        return False
    if DISABLED.exists():
        return False
    if mode in ("1", "on", "true", "yes"):
        return True
    return bool(Api().ok() or boxes)


# --------------------------------------------------------------------------- #
# approvals (TTY-only, one-shot)
# --------------------------------------------------------------------------- #
def take_approval(action: str, code: str = "") -> bool:
    try:
        paths = sorted(APPROVALS.glob("*.json"))
    except OSError:
        return False
    for p in paths:
        d = _read_json(p, {})
        if not isinstance(d, dict):
            continue
        if d.get("action") != action:
            continue
        if code and d.get("code") not in ("", code):
            continue
        if float(d.get("expires") or 0) < now():
            try:
                p.unlink()
            except OSError:
                pass
            continue
        try:
            p.unlink()
        except OSError:
            pass
        return True
    return False


def make_approval(action: str, code: str, ttl: int) -> Path:
    APPROVALS.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(8)
    p = APPROVALS / f"run-gate-{nonce}.json"
    _atomic(p, {"action": action, "code": code, "expires": now() + ttl, "nonce": nonce})
    return p


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
def decide_finish(cat: dict, boxes: list[dict], consume: bool = True) -> tuple[int, str]:
    if cat.get("finished"):
        return 0, "platform task is finished"
    if not armed(cat, boxes):
        return 2, "run gate inert (no platform env / no open box / RUN_GATE=off)"
    # The human approval is an override even when the platform cannot be
    # reached: otherwise a broken API makes the documented escape hatch dead.
    if consume and take_approval("finish"):
        return 0, "user TTY approval consumed"
    if not cat.get("ok"):
        if cat.get("inert"):
            live = [b for b in boxes
                    if not abandoned(b["dir"]) and env_failed(b["dir"]) is None]
            if live:
                return 1, ("no platform env; open boxes: "
                           + ", ".join(b["code"] for b in live)
                           + ". Close a 0-flag box or record an abandonment before finish")
            return 0, "no platform env and no open box"
        if env_failed() is not None:
            return 0, "environment failure recorded; finish allowed"
        return 1, (f"cannot verify completion: platform error ({cat.get('reason')}); "
                   "record it with `run_gate.py env-fail --global --evidence ...` or retry")

    cmap = challenge_map(cat)
    unfinished: list[str] = []
    resolved: set[str] = set()
    for box in boxes:
        if abandoned(box["dir"]) or env_failed(box["dir"]) is not None:
            resolved.add(box["code"])
            continue  # explicitly given up with evidence; not a silent early stop
        ch = cmap.get(box["code"], {})
        done, got, total = challenge_done(ch)
        got = max(got, box["flags"])
        if not done:
            unfinished.append(f"{box['code']} {got}/{total or '?'}")

    # Partial challenges that no longer have an open box (closed too early /
    # tracked only on the platform) still block finish, unless explicitly
    # abandoned above.
    box_codes = {b["code"] for b in boxes}
    for code, ch in cmap.items():
        if code in box_codes or code in resolved:
            continue
        # A box that was closed after an explicit abandon / env-fail is still
        # listed by the platform (partial flags stay visible). Do not let that
        # historical state block finish forever.
        if abandoned(TEAMS / code) or env_failed(TEAMS / code) is not None:
            resolved.add(code)
            continue
        done, got, total = challenge_done(ch)
        if not done and got > 0:
            unfinished.append(f"{code} {got}/{total or '?'} (no open box)")
    if not unfinished and not boxes and not resolved \
            and not any(challenge_done(c)[0] for c in cmap.values()):
        unfinished.append("no challenge opened/completed")

    if unfinished:
        shown = ", ".join(unfinished[:12]) + (" ..." if len(unfinished) > 12 else "")
        return 1, (f"unfinished: {shown}. Continue to is_completed/flag_total, "
                   "or have the user run `run_gate.py approve --action finish` in a TTY")
    return 0, "no unfinished opened challenge (completed or explicitly abandoned)"


def decide_close(code: str, cat: dict, boxes: list[dict], consume: bool = True) -> tuple[int, str]:
    if cat.get("finished"):
        return 0, "platform task is finished"
    box = next((b for b in boxes if b["code"] == code), None)
    if not armed(cat, boxes):
        return 2, "run gate inert"
    tdir = box["dir"] if box else (TEAMS / code)
    if abandoned(tdir):
        return 0, f"abandonment recorded: {abandoned(tdir).get('reason', '')[:120]}"
    if env_failed(tdir) is not None:
        return 0, "environment failure recorded for this box"
    if consume and take_approval("close", code):
        return 0, "user TTY approval consumed"

    ch = challenge_map(cat).get(code, {})
    done, got, total = challenge_done(ch)
    if done:
        return 0, "challenge is completed"
    if box is not None:
        got = max(got, box["flags"])
    if got == 0:
        return 0, "no flag on this box; normal stall/tier close is allowed"
    if total <= 0 and box is not None and now() - box["last"] >= SOLVED_HOLD_S:
        return 0, f"solved-hold fallback {int(now() - box['last'])}s>={SOLVED_HOLD_S}s (flag_total unknown)"
    note = ""
    if box is not None:
        note = f"; last activity {int(now() - box['last'])}s ago"
    return 1, (f"{code} has {got}/{total or '?'} flags and is not completed{note}. "
               "Keep it until is_completed/flag_total. A stall abandon needs fresh notes: "
               f"`run_gate.py abandon --code {code} --stall --reason '...'` "
               "(notes.md must be updated within 15 min), or ask the user to run "
               f"`run_gate.py approve --action close --code {code}` in a TTY")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    cat = load_catalog(force=args.refresh)
    boxes = local_boxes()
    if args.action == "finish":
        rc, msg = decide_finish(cat, boxes)
    elif args.action == "close":
        if not args.code:
            print("[run-gate] BLOCK: --code is required for close")
            return 1
        rc, msg = decide_close(args.code, cat, boxes)
    else:
        print(f"[run-gate] unknown action {args.action}")
        return 1
    tag = {0: "allow", 1: "BLOCK", 2: "inert"}[rc]
    print(f"[run-gate] {tag}: {msg}")
    return rc


def cmd_status(args) -> int:
    cat = load_catalog(force=args.refresh)
    boxes = local_boxes()
    cmap = challenge_map(cat)
    print(f"[run-gate] armed={armed(cat, boxes)} platform_ok={cat.get('ok')} "
          f"finished={cat.get('finished')} reason={cat.get('reason') or '-'}")
    print(f"[run-gate] open boxes: {', '.join(b['code'] for b in boxes) or '-'}")
    skipped = foreign_boxes()
    if skipped:
        print(f"[run-gate] 跳过（别的会话开的）: {', '.join(skipped)} [session {SESSION_ID[:8]}]")
    for b in boxes:
        ch = cmap.get(b["code"], {})
        done, got, total = challenge_done(ch)
        got = max(got, b["flags"])
        print(f"  {b['code']}: {'COMPLETED' if done else 'unfinished'} "
              f"flags={got}/{total or '?'} last={int(now() - b['last'])}s ago")
    if not args.quiet:
        print(f"[run-gate] state: {STATE}")
    return 0


def cmd_abandon(args) -> int:
    code = args.code
    tdir = TEAMS / code
    tdir.mkdir(parents=True, exist_ok=True)
    reason = (args.reason or "").strip()
    if not reason:
        print("[run-gate] refuse: --reason is required")
        return 1
    notes = next((p for p in (tdir / "notes.md", tdir / "NOTES.md")
                  if p.exists() and now() - _mtime(p) <= STALL_S), None)
    fresh = notes is not None
    if args.approval:
        if not take_approval("close", code):
            print("[run-gate] refuse: no valid TTY approval for this box")
            return 1
        by = "user-approval"
    elif args.env_fail:
        if env_failed(tdir) is None:
            print("[run-gate] refuse: no env_fail.json; run `run_gate.py env-fail --code ... --evidence ...` first")
            return 1
        by = "environment"
    elif args.stall:
        if not fresh:
            print(f"[run-gate] refuse: notes.md/NOTES.md missing or older than {STALL_S}s; "
                  "write the three-line stall note first, then abandon")
            return 1
        if not NOTE_STALL_RE.search(notes.read_text(encoding="utf-8", errors="replace")):
            print("[run-gate] refuse: notes.md/NOTES.md has no stall/give-up line; add the three-line note")
            return 1
        by = "stall"
    else:
        print("[run-gate] refuse: choose --stall / --env-fail / --approval")
        return 1

    _atomic(tdir / "abandon.json", {
        "at": now(), "code": code, "reason": reason, "by": by,
        "flags": local_flags(tdir), "notes_mtime": _mtime(notes) if notes else 0,
    })
    print(f"[run-gate] abandoned {code} ({by}): {reason}")
    return 0


def cmd_env_fail(args) -> int:
    evidence = (args.evidence or "").strip()
    if not evidence:
        print("[run-gate] refuse: --evidence is required")
        return 1
    data = {"at": now(), "code": args.code or "", "evidence": evidence}
    path = (ROOT / "run_gate_env_fail.json") if args.global_ else (TEAMS / args.code / "env_fail.json")
    if not args.global_ and not args.code:
        print("[run-gate] refuse: use --global or --code")
        return 1
    _atomic(path, data)
    print(f"[run-gate] environment failure recorded at {path}")
    return 0


def _require_tty(what: str) -> bool:
    if not sys.stdin.isatty():
        print(f"[run-gate] refuse: {what} requires a TTY; the user must run this command")
        return False
    return True


def cmd_approve(args) -> int:
    if not _require_tty("approval"):
        return 10
    what = args.action + (f" {args.code}" if args.code else "")
    print(f"Approve run-gate {what} for {args.ttl}s?")
    ans = input("type YES to approve: ").strip()
    if ans != "YES":
        print("[run-gate] not approved")
        return 1
    p = make_approval(args.action, args.code or "", args.ttl)
    print(f"[run-gate] approval written: {p}")
    return 0


def cmd_arm(args) -> int:
    if not _require_tty("arm"):
        return 10
    try:
        DISABLED.unlink()
    except OSError:
        pass
    print("[run-gate] armed")
    return 0


def cmd_disarm(args) -> int:
    if not _require_tty("disarm"):
        return 10
    if not take_approval("disarm"):
        print("[run-gate] refuse: no valid TTY approval for disarm; "
              "run `run_gate.py approve --action disarm` in a TTY first")
        return 10
    DISABLED.write_text(f"{now()}\n", encoding="utf-8")
    print("[run-gate] disarmed (RUN_GATE.disabled); remove it or run `arm` to re-enable")
    return 0


def cmd_selftest(args) -> int:
    global ROOT, TEAMS, STATE, DISABLED, APPROVALS
    old = (ROOT, TEAMS, STATE, DISABLED, APPROVALS)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="run-gate-test-") as tmp:
        ROOT = Path(tmp)
        TEAMS = ROOT / "teams"
        STATE = ROOT / "run_gate.json"
        DISABLED = ROOT / "run_gate.disabled"
        APPROVALS = ROOT / "run_gate_approvals"
        os.environ["RUN_GATE"] = "1"
        t = TEAMS / "a-01"
        t.mkdir(parents=True)
        (t / "opened_at").write_text(str(now()), encoding="utf-8")

        cat = {"ok": True, "inert": False, "finished": False, "reason": "",
               "rows": [{"unique_code": "a-01", "is_completed": False,
                         "solved_flags": 1, "flag_total": 6}], "at": now()}
        boxes = local_boxes()
        rc, msg = decide_finish(cat, boxes, consume=False)
        if rc != 1:
            print(f"FAIL finish partial should block: {rc} {msg}")
            failures += 1
        rc, msg = decide_close("a-01", cat, boxes, consume=False)
        if rc != 1:
            print(f"FAIL close partial should block: {rc} {msg}")
            failures += 1

        (t / "notes.md").write_text("## a-01\nno new primitive; next: none\n", encoding="utf-8")
        (t / "candidates-a-01.md").write_text("flag{deadbeef}\n", encoding="utf-8")
        rc = cmd_abandon(argparse.Namespace(
            code="a-01", reason="stall 15m no primitive", stall=True, env_fail=False,
            approval=False))
        if rc != 0:
            print(f"FAIL abandon with fresh notes should pass: {rc}")
            failures += 1
        rc, msg = decide_close("a-01", cat, boxes, consume=False)
        if rc != 0:
            print(f"FAIL close after abandon should pass: {rc} {msg}")
            failures += 1

        cat_done = {"ok": True, "inert": False, "finished": False, "reason": "",
                    "rows": [{"unique_code": "a-01", "is_completed": True,
                              "solved_flags": 6, "flag_total": 6}], "at": now()}
        rc, msg = decide_finish(cat_done, boxes, consume=False)
        if rc != 0:
            print(f"FAIL finish completed should pass: {rc} {msg}")
            failures += 1

        t2 = TEAMS / "b-02"
        t2.mkdir(parents=True)
        (t2 / "opened_at").write_text(str(now() - 700), encoding="utf-8")
        (t2 / "notes.md").write_text("stall\n", encoding="utf-8")
        (t2 / "candidates-b-02.md").write_text("flag{feedface}\n", encoding="utf-8")
        old_ts = now() - (SOLVED_HOLD_S + 100)
        for p in (t2 / "notes.md", t2 / "candidates-b-02.md"):
            os.utime(p, (old_ts, old_ts))
        cat_unknown = {"ok": True, "inert": False, "finished": False, "reason": "",
                       "rows": [{"unique_code": "b-02", "is_completed": False}], "at": now()}
        rc, msg = decide_close("b-02", cat_unknown, local_boxes(), consume=False)
        if rc != 0:
            print(f"FAIL unknown-total solved-hold should pass: {rc} {msg}")
            failures += 1

        os.environ["RUN_GATE"] = "off"
        rc, msg = decide_finish(cat, boxes, consume=False)
        if rc != 2:
            print(f"FAIL RUN_GATE=off should be inert: {rc} {msg}")
            failures += 1
    os.environ.pop("RUN_GATE", None)
    ROOT, TEAMS, STATE, DISABLED, APPROVALS = old
    if failures:
        print(f"[run-gate] selftest FAILED ({failures})")
        return 1
    print("[run-gate] selftest OK")
    return 0


def main(argv=None) -> int:
    global SESSION_ID
    argv = list(sys.argv[1:] if argv is None else argv)
    # --session is accepted on any subcommand (the extension passes it after
    # session_start); pop it here so every parser stays unchanged.
    kept: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--session" and i + 1 < len(argv):
            SESSION_ID = argv[i + 1].strip()
            i += 2
            continue
        if tok.startswith("--session="):
            SESSION_ID = tok.split("=", 1)[1].strip()
            i += 1
            continue
        kept.append(tok)
        i += 1
    argv = kept
    p = argparse.ArgumentParser(description="code-level gate against stopping a full-auto run early")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--action", choices=["finish", "close"], required=True)
    c.add_argument("--code", default="")
    c.add_argument("--refresh", action="store_true")
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("status")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_status)

    a = sub.add_parser("abandon")
    a.add_argument("--code", required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("--stall", action="store_true")
    a.add_argument("--env-fail", action="store_true")
    a.add_argument("--approval", action="store_true")
    a.set_defaults(fn=cmd_abandon)

    e = sub.add_parser("env-fail")
    e.add_argument("--code", default="")
    e.add_argument("--evidence", required=True)
    e.add_argument("--global", dest="global_", action="store_true")
    e.set_defaults(fn=cmd_env_fail)

    ap = sub.add_parser("approve")
    ap.add_argument("--action", choices=["finish", "close", "disarm"], required=True)
    ap.add_argument("--code", default="")
    ap.add_argument("--ttl", type=int, default=APPROVAL_TTL)
    ap.set_defaults(fn=cmd_approve)

    sub.add_parser("arm").set_defaults(fn=cmd_arm)
    sub.add_parser("disarm").set_defaults(fn=cmd_disarm)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)

    args = p.parse_args(argv)
    if os.environ.get("TEAM_WORKER") == "1" and args.cmd in (
        "abandon", "env-fail", "approve", "arm", "disarm"
    ):
        print("[run-gate] workers may not change the gate", file=sys.stderr)
        return 1
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
