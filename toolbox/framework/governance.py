"""可执行的跑分纪律：SKIP 登记、时间盒看门狗、双地址探活、VPN 预检。

这些规则原本只写在 `skills/tsec-bench-run/SKILL.md` 和 `docs/SOP.md` 里，
靠模型自觉执行。这个模块把它们变成代码：

  * `SkipRegistry`  —— work/SKIP.txt 的读写/去重/查询，坏题只验一次
  * `mark_started` / `touch` / `hold` —— 题目活动心跳，供看门狗判断
  * `decide_watch`  —— 分层时间盒（soft/hard）+ 空闲判定 + 一次性 extend，默认只警告
  * `probe_addrs`   —— start 返回多个地址时逐个 TCP 探活
  * `preflight`     —— VPN 健康检查（默认取 TSEC_HEALTH_URL）

设计原则：**时间盒是提示，不是铡刀**。只有 `--close` 且确实长时间无活动时才关；
`--hard` 才允许到点无条件关闭。这样不会把“正在做、只是没写文件”的题误杀。
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
WORK = ROOT / "work"
SKIP_FILE = "SKIP.txt"

_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([smh])", re.I)
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------- 基础
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def work_root(root: str | Path | None = None) -> Path:
    """work 目录；支持 TSEC_WORKDIR 覆盖（测试/多任务隔离）。"""
    if root is not None:
        return Path(root)
    env = os.environ.get("TSEC_WORKDIR")
    return Path(env) if env else WORK


def safe_code(code: str) -> str:
    return _SAFE_RE.sub("_", str(code)).strip("_") or "unknown"


def code_dir(code: str, root: str | Path | None = None) -> Path:
    return work_root(root) / safe_code(code)


def parse_duration(text) -> int:
    """'20m' / '90s' / '1h30m' / '300' -> 秒。"""
    if text is None:
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total = 0.0
    pos = 0
    found = False
    for m in _DUR_RE.finditer(s):
        found = True
        val = float(m.group(1))
        unit = m.group(2).lower()
        total += val * {"s": 1, "m": 60, "h": 3600}[unit]
        pos = m.end()
    if not found or s[pos:].strip():
        raise ValueError(f"无法解析时长: {text!r}（示例：20m / 90s / 1h30m）")
    return int(total)


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------- SKIP
@dataclass(frozen=True)
class SkipEntry:
    code: str
    added_at: str = ""
    reason: str = ""


class SkipRegistry:
    """work/SKIP.txt 的读写。

    文件格式（兼容旧的人工写法）：
        # code<TAB>added_at<TAB>reason
        a-03<TAB>2026-11-14T15:30:00+08:00<TAB>/login GET/POST/任意 Host 恒 500
        f2-01  二进制内 flag 是 decoy，缺 /check oracle
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else (work_root() / SKIP_FILE)

    # ---- 读 ----
    @staticmethod
    def _parse_line(line: str) -> SkipEntry | None:
        raw = line.rstrip("\r\n")
        if not raw.strip() or raw.lstrip().startswith("#"):
            return None
        parts = raw.split("\t")
        if len(parts) >= 3:
            code, ts, reason = parts[0].strip(), parts[1].strip(), "\t".join(parts[2:]).strip()
        elif len(parts) == 2:
            code, ts, reason = parts[0].strip(), parts[1].strip(), ""
        else:
            bits = raw.strip().split(None, 1)
            code = bits[0]
            ts = ""
            reason = bits[1].strip() if len(bits) > 1 else ""
        if not code:
            return None
        return SkipEntry(code=code, added_at=ts, reason=reason)

    def entries(self) -> list[SkipEntry]:
        if not self.path.is_file():
            return []
        seen: dict[str, SkipEntry] = {}
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            e = self._parse_line(line)
            if e:
                seen[e.code] = e
        return list(seen.values())

    def codes(self) -> set[str]:
        return {e.code for e in self.entries()}

    def get(self, code: str) -> SkipEntry | None:
        code = str(code)
        for e in self.entries():
            if e.code == code:
                return e
        return None

    def contains(self, code: str) -> bool:
        return self.get(code) is not None

    # ---- 写 ----
    def _write(self, entries: list[SkipEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# code\tadded_at\treason"]
        for e in entries:
            reason = " ".join((e.reason or "").split())
            lines.append(f"{e.code}\t{e.added_at}\t{reason}".rstrip("\t"))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, code: str, reason: str = "", now: datetime | None = None) -> SkipEntry:
        code = str(code).strip()
        if not code:
            raise ValueError("code 不能为空")
        ts = (now or now_utc()).astimezone().isoformat(timespec="seconds")
        reason = " ".join((reason or "").split())
        entries = [e for e in self.entries() if e.code != code]
        entry = SkipEntry(code=code, added_at=ts, reason=reason)
        entries.append(entry)
        self._write(entries)
        return entry

    def remove(self, code: str) -> bool:
        code = str(code)
        entries = self.entries()
        kept = [e for e in entries if e.code != code]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True


# ---------------------------------------------------------------- 活动心跳
def _write_stamp(path: Path, now: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.astimezone().isoformat(timespec="seconds"), encoding="utf-8")
    try:
        ts = now.timestamp()
        os.utime(path, (ts, ts))
    except OSError:
        pass
    return path


def mark_started(code: str, root: str | Path | None = None, now: datetime | None = None,
                 overwrite: bool = True) -> Path:
    """写 work/<code>/.started；run 每次重开时覆盖，watch 只在缺失时补。

    重开时要清掉 `.closed`，否则面板仍把这题当“已结束”。
    """
    d = code_dir(code, root)
    p = d / ".started"
    if p.exists() and not overwrite:
        return p
    try:
        (d / ".closed").unlink()
    except OSError:
        pass
    if overwrite:
        # A fresh start must not inherit a previous run's extension/hold.
        for name in (".extend", ".hold"):
            try:
                (d / name).unlink()
            except OSError:
                pass
    return _write_stamp(p, now or now_utc())


def mark_closed(code: str, root: str | Path | None = None,
                now: datetime | None = None) -> Path:
    """写 work/<code>/.closed，`report` 据此把已结束的题从面板移走。"""
    return _write_stamp(code_dir(code, root) / ".closed", now or now_utc())


def is_closed(code: str, root: str | Path | None = None) -> bool:
    return (code_dir(code, root) / ".closed").is_file()


def touch(code: str, root: str | Path | None = None, now: datetime | None = None) -> Path:
    """更新活动心跳；`cli touch` 调用。"""
    return _write_stamp(code_dir(code, root) / ".heartbeat", now or now_utc())


def hold(code: str, root: str | Path | None = None, reason: str = "",
         now: datetime | None = None) -> Path:
    """放一个 .hold 标记，看门狗无论如何都不关。"""
    p = code_dir(code, root) / ".hold"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((reason or "held by operator").strip() + "\n", encoding="utf-8")
    if now is not None:
        try:
            ts = now.timestamp()
            os.utime(p, (ts, ts))
        except OSError:
            pass
    return p


def unhold(code: str, root: str | Path | None = None) -> bool:
    p = code_dir(code, root) / ".hold"
    if p.exists():
        p.unlink()
        return True
    return False


def is_held(code: str, root: str | Path | None = None) -> bool:
    return (code_dir(code, root) / ".hold").exists()


def read_started(code: str, root: str | Path | None = None) -> datetime | None:
    p = code_dir(code, root) / ".started"
    if not p.is_file():
        return None
    try:
        dt = datetime.fromisoformat(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _activity_files(code: str, root: str | Path | None = None):
    d = code_dir(code, root)
    if d.is_dir():
        for p in d.rglob("*"):
            if p.is_file() and p.name not in (".hold", ".extend", ".closed"):
                yield p
    base = work_root(root)
    for name in (f"bb_{safe_code(code)}.md", f"{safe_code(code)}.md"):
        p = base / name
        if p.is_file():
            yield p


def last_activity(code: str, root: str | Path | None = None,
                  now: datetime | None = None) -> datetime:
    """取 work/<code>/、work/bb_<code>.md 等文件的最大 mtime；没有则返回 started/now。"""
    latest = None
    for p in _activity_files(code, root):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if latest is None or m > latest:
            latest = m
    if latest is not None:
        return datetime.fromtimestamp(latest, tz=timezone.utc)
    return read_started(code, root) or (now or now_utc())


# ---------------------------------------------------------------- 停滞闸门 / 笔记
# “新原语”= 新事实（新注入点/新文件读/新凭据/新越权/新 gadget），不是“在敲键盘”。
PRIMITIVE_FILES = (".primitive", ".heartbeat")
NOTE_MIN_ELAPSED = 5 * 60      # 低于这个时长的“摸一下就关”不强制笔记


def last_primitive(code: str, root: str | Path | None = None,
                   now: datetime | None = None) -> datetime:
    """最近一次“新原语”时间。

    `.primitive`（`slots.py` 看门狗）/ `.heartbeat`（`cli touch`）优先；
    都没有时退回 `.started`，**不**使用任意文件 mtime（写脚本不算新原语）。
    """
    latest = None
    d = code_dir(code, root)
    for name in PRIMITIVE_FILES:
        p = d / name
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if latest is None or m > latest:
            latest = m
    if latest is not None:
        return datetime.fromtimestamp(latest, tz=timezone.utc)
    # Do NOT fall back to arbitrary file mtimes: writing scripts/logs is not a
    # new primitive. Fall back to the box start so a box with no touch is
    # judged stalled after the window, as the hard rule requires.
    return read_started(code, root) or (now or now_utc())


def note_paths(code: str, root: str | Path | None = None) -> list[Path]:
    """按优先级列出可被 caselib 归档的笔记路径（前面是推荐的）。"""
    d = code_dir(code, root)
    base = work_root(root)
    sc = safe_code(code)
    return [base / f"bb_{sc}.md", d / "FINDINGS.md", d / "NOTES.md"]


def has_notes(code: str, root: str | Path | None = None) -> bool:
    for p in note_paths(code, root):
        try:
            if p.is_file() and p.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def note_template(code: str) -> str:
    return (f"# {code}\n\n"
            "- 入口:\n- 版本/指纹:\n- 已试(含反例):\n- 下一招:\n")


def close_note_gate(code: str, root: str | Path | None = None,
                    now: datetime | None = None,
                    min_elapsed: int = NOTE_MIN_ELAPSED) -> tuple[bool, str]:
    """关闭前的笔记闸门：投入超过 min_elapsed 且无笔记 -> 不放行。

    短停留（指纹完就关、开局秒杀）不强制，避免为凑笔记反而不干活。
    """
    now = (now or now_utc())
    started = read_started(code, root)
    elapsed = (now - started).total_seconds() if started else 0.0
    if elapsed < min_elapsed:
        return True, ""
    if has_notes(code, root):
        return True, ""
    target = note_paths(code, root)[1]
    return False, (f"已投入 {fmt_duration(elapsed)} 但没有笔记：{target}\n"
                   + note_template(code))



# ---------------------------------------------------------------- 分层时间盒
TIERS = {
    # soft = 停止开新方向；hard = 必须返回/关闭；extend = 允许一次的延长量
    "easy":       {"soft": 5 * 60,  "hard": 10 * 60, "extend": 0},
    "medium":     {"soft": 10 * 60, "hard": 20 * 60, "extend": 10 * 60},
    "hard":       {"soft": 30 * 60, "hard": 60 * 60, "extend": 30 * 60},
    "credential": {"soft": 5 * 60,  "hard": 5 * 60,  "extend": 0},
    "intel":      {"soft": 20 * 60, "hard": 45 * 60, "extend": 15 * 60},
}
DEFAULT_TIER = "medium"
EXT_FILE = ".extend"


@dataclass(frozen=True)
class Extension:
    seconds: int
    reason: str = ""
    granted_at: str = ""


def grant_extension(code: str, seconds: int, reason: str = "",
                    root: str | Path | None = None, now: datetime | None = None,
                    force: bool = False) -> Extension | None:
    """主控批准的一次性延长；已存在且 force=False 时返回 None。"""
    seconds = int(seconds)
    if seconds <= 0:
        raise ValueError("extension 必须大于 0")
    p = code_dir(code, root) / EXT_FILE
    if p.exists() and not force:
        return None
    ext = Extension(
        seconds=seconds,
        reason=" ".join((reason or "").split()),
        granted_at=(now or now_utc()).astimezone().isoformat(timespec="seconds"),
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "seconds": ext.seconds,
        "reason": ext.reason,
        "granted_at": ext.granted_at,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return ext


def read_extension(code: str, root: str | Path | None = None) -> Extension | None:
    p = code_dir(code, root) / EXT_FILE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        seconds = int(data.get("seconds", 0) or 0)
    except (ValueError, OSError, TypeError):
        return None
    if seconds <= 0:
        return None
    return Extension(seconds, str(data.get("reason", "")), str(data.get("granted_at", "")))


def clear_extension(code: str, root: str | Path | None = None) -> bool:
    p = code_dir(code, root) / EXT_FILE
    if p.exists():
        p.unlink()
        return True
    return False


@dataclass(frozen=True)
class WatchDecision:
    action: str          # keep / warn / close / would_close / hold
    reason: str
    elapsed: float
    idle: float
    held: bool = False
    phase: str = ""      # keep / soft / hard / stall / hold
    soft: float = 0.0
    hard: float = 0.0
    extension: float = 0.0
    idle_primitive: float = 0.0


def decide_watch(code: str, soft: int, hard: int | None = None, idle: int = 0,
                 root: str | Path | None = None, now: datetime | None = None,
                 allow_close: bool = True, force_hard: bool = False,
                 stall: int = 0) -> WatchDecision:
    """分层时间盒判定。

    - **stall > 0 时优先判定停滞**：连续 stall 秒没有新原语（`.primitive`/`touch`）
      就直接警戒/关闭，**不管是否还在软截止内**。这是“止损”而非“预算”：
      实测长停留的失败 stint 中位 16.5 分钟，而做出题的只 5.3 分钟。
    - soft 之前：keep；idle 超限只 warn（可能只是在深挖）
    - soft ~ hard：warn，提示“停止开新方向，只验证当前路径”
    - hard 之后：
        * force_hard=True          -> 到点关闭（显式；credential 可用）
        * idle >= idle             -> 关闭（长时间无活动）
        * 否则                     -> warn（还有活动；主控决定 close 或 extend）
    - .hold 存在：无论如何 keep（`hold` 要写理由与下一招）。
    - .extend 存在：soft/hard 都加上延长量（主控批准，一次性）。
    - allow_close=False：只返回 would_close。
    """
    now = (now or now_utc()).astimezone(timezone.utc)
    started = read_started(code, root) or now
    started = started.astimezone(timezone.utc)
    last = last_activity(code, root, now=now).astimezone(timezone.utc)
    last_p = last_primitive(code, root, now=now).astimezone(timezone.utc)
    elapsed = max(0.0, (now - started).total_seconds())
    idle_s = max(0.0, (now - last).total_seconds())
    idle_p = max(0.0, (now - last_p).total_seconds())
    held = is_held(code, root)
    ext = read_extension(code, root)
    ext_s = float(ext.seconds) if ext else 0.0
    soft_eff = max(0.0, float(soft) + ext_s)
    hard_eff = max(soft_eff, float(hard if hard is not None else soft + 5 * 60) + ext_s)

    def act(kind: str, phase: str, reason: str) -> WatchDecision:
        if kind == "close" and not allow_close:
            kind = "would_close"
        return WatchDecision(kind, reason, elapsed, idle_s, held, phase, soft_eff, hard_eff,
                             ext_s, idle_p)

    if held:
        return act("hold", "hold", "存在 .hold 标记，不关闭（记得写理由+下一招）")

    if stall > 0 and idle_p >= stall:
        return act("close", "stall",
                   f"停滞 {fmt_duration(idle_p)} >= {fmt_duration(stall)} 无新原语；"
                   f"写三行笔记后 close（有新事实才 touch）")

    if soft_eff <= 0 and hard_eff <= 0:
        if idle > 0 and idle_s >= idle:
            return act("close", "hard", f"无时间盒，但空闲 {fmt_duration(idle_s)} >= {fmt_duration(idle)}")
        return act("keep", "keep", "未设时间盒")

    if elapsed < soft_eff:
        if idle > 0 and idle_s >= idle:
            return act("warn", "soft", f"空闲 {fmt_duration(idle_s)}，但仍在软截止内")
        return act("keep", "keep", f"软截止内（{fmt_duration(elapsed)}/{fmt_duration(soft_eff)}）")

    if elapsed < hard_eff:
        return act("warn", "soft",
                   f"到达软截止（{fmt_duration(elapsed)}）：停止开新方向；硬截止 {fmt_duration(hard_eff)}")

    if force_hard:
        return act("close", "hard", f"硬截止到点（{fmt_duration(elapsed)}/{fmt_duration(hard_eff)}）")

    if idle > 0 and idle_s >= idle:
        return act("close", "hard",
                   f"硬截止 + 空闲 {fmt_duration(idle_s)} >= {fmt_duration(idle)}")

    return act("warn", "hard",
               f"硬截止已到，但最近 {fmt_duration(idle_s)} 内仍有活动；主控决定 close 或 extend")


# ---------------------------------------------------------------- 探活 / 预检
@dataclass(frozen=True)
class ProbeResult:
    addr: str
    ok: bool
    ms: int
    error: str = ""


def split_addr(addr: str) -> tuple[str, int]:
    s = str(addr).strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    if s.startswith("["):
        host, _, rest = s[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else "80"
    else:
        host, sep, port = s.rpartition(":")
        if not sep:
            host, port = s, "80"
    return host, int(port or 80)


def probe_one(addr: str, timeout: float = 2.0) -> ProbeResult:
    t0 = time.monotonic()
    try:
        host, port = split_addr(addr)
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return ProbeResult(addr, True, int((time.monotonic() - t0) * 1000))
    except (OSError, ValueError) as e:
        return ProbeResult(addr, False, int((time.monotonic() - t0) * 1000), str(e))


def probe_addrs(addrs, timeout: float = 2.0, workers: int = 8) -> list[ProbeResult]:
    if isinstance(addrs, str):
        addrs = [addrs]
    addrs = [str(a) for a in (addrs or [])]
    if not addrs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(addrs))) as ex:
        return list(ex.map(lambda a: probe_one(a, timeout), addrs))


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    url: str
    data: dict
    error: str = ""


def preflight(url: str | None = None, timeout: float = 5.0) -> PreflightResult:
    url = url or os.environ.get("TSEC_HEALTH_URL") or ""
    if not url:
        return PreflightResult(
            False, "", {},
            "TSEC_HEALTH_URL is not set (the VPN/gateway health endpoint is deployment-specific)",
        )
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace") or "{}"
        data = json.loads(raw)
        return PreflightResult(str(data.get("status", "")).lower() == "ok", url, data)
    except Exception as e:  # noqa: BLE001 - 预检失败必须给出原因，不能吞
        return PreflightResult(False, url, {}, f"{type(e).__name__}: {e}")
