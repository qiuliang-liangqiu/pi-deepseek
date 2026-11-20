"""平台 API 客户端。

设计要点（都是踩过坑后定下来的）：
  * token 走环境变量 TSEC_TOKEN，缺失/404 task_not_found 时直接抛错，不要继续瞎跑
  * start 遇 409 max active：从错误消息学习平台上限，close 最老的一个自管容器后重试
  * 上限不是写死的 3：默认 3，`TSEC_MAX_ACTIVE` 可覆盖，409 消息里的 `(N)` 会写入
    `work/capacity.json`；缓存只是提示，`start` 仍先尝试，409 才是权威
  * 409 max active 不算任务结束（不能置 dead=True）
  * submit 遇 409 duplicate：视为已得分，返回 success
  * 任何真正的 invalid_state：置 self.dead=True，上层主循环应立刻停止
  * 每道题记开始时间，409 时优先关最老的容器，避免把并行 worker 的活全清掉
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = "<your-platform-base-url>"
DEFAULT_MAX_ACTIVE = 3
CAPACITY_FILE = "capacity.json"
OWNED_FILE = "owned.json"


def _work_dir() -> Path:
    env = os.environ.get("TSEC_WORKDIR")
    return Path(env) if env else (Path(__file__).resolve().parent.parent / "work")


def parse_max_active(message: str) -> int | None:
    """从 409 文案里提取上限。

    常见：max active challenge instances reached (3), please close ...
    兼容：max active 5 / max active limit: 4
    """
    text = message or ""
    patterns = (
        r"max active[^0-9]{0,80}\(\s*(\d+)\s*\)",
        r"max active[^0-9]{0,80}(\d+)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            n = int(m.group(1))
            if n > 0:
                return n
    return None


class ApiError(Exception):
    def __init__(self, code, message, detail=None, status=None):
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.status = status
        super().__init__(f"[{code}] {message}")


class BenchmarkClient:
    def __init__(self, base_url=None, token=None, timeout=60):
        self.base = (base_url or os.environ.get("TSEC_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.token = token or os.environ.get("TSEC_TOKEN")
        if not self.token:
            raise ApiError("no_token", "环境变量 TSEC_TOKEN 未设置")
        self.timeout = timeout
        self.dead = False
        self.owned = set()          # 当前自己 start 过的题，退出一键清理
        self.owned_at = {}          # code -> monotonic 开始时间，409 时关最老的
        self.flags = {}             # code -> 已计分 flag 数（本地观测）
        self.completed = set()      # code -> is_completed/remaining_flags=0
        self._capacity_path = _work_dir() / CAPACITY_FILE
        self._owned_path = _work_dir() / OWNED_FILE
        self._capacity_key = os.environ.get("TSEC_CAPACITY_KEY") or self.base
        self.max_active = DEFAULT_MAX_ACTIVE
        self.capacity_source = "default"
        self._load_capacity()
        self._load_owned()

    def _load_owned(self):
        """Restore boxes opened by an earlier process (409/panic across runs)."""
        try:
            data = json.loads(self._owned_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.owned = {str(x) for x in data if isinstance(x, str)}
        except (OSError, ValueError, TypeError):
            pass

    def _save_owned(self):
        try:
            self._owned_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._owned_path.with_name(f"{self._owned_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(sorted(self.owned), ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._owned_path)
        except OSError:
            pass

    # ---------- 容量 ----------
    def _load_capacity(self):
        env = os.environ.get("TSEC_MAX_ACTIVE")
        if env:
            try:
                n = int(env)
                if n > 0:
                    self.max_active = n
                    self.capacity_source = "env"
                    return
            except ValueError:
                pass
        try:
            data = json.loads(self._capacity_path.read_text(encoding="utf-8"))
            entry = data.get(self._capacity_key)
            if isinstance(entry, int):
                n = entry
            elif isinstance(entry, dict):
                n = int(entry.get("max_active", 0) or 0)
            else:
                n = 0
            if n > 0:
                self.max_active = n
                self.capacity_source = "cache"
        except (OSError, ValueError, TypeError):
            pass

    def _save_capacity(self, n: int, source: str):
        """只存 base/key 和数字，不存 token；失败不影响 start。"""
        try:
            data = {}
            if self._capacity_path.is_file():
                try:
                    data = json.loads(self._capacity_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
            if not isinstance(data, dict):
                data = {}
            data[self._capacity_key] = {
                "max_active": int(n),
                "source": source,
                "learned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._capacity_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._capacity_path.with_name(f"{self._capacity_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._capacity_path)
        except Exception:
            pass

    def set_capacity(self, n: int, source: str = "manual"):
        n = int(n)
        if n <= 0:
            return
        self.max_active = n
        self.capacity_source = source
        self._save_capacity(n, source)

    def capacity_info(self):
        return {"max_active": self.max_active, "source": self.capacity_source}

    @staticmethod
    def _is_max_active(e: ApiError) -> bool:
        msg = (e.message or "").lower()
        return "max active" in msg or (e.status == 409 and "active" in msg)

    def _oldest_owned(self):
        # Prefer in-process owned boxes (owned_at is monotonic); a fresh process
        # falls back to .started mtimes so a 409 can still be satisfied.
        if self.owned:
            return min(self.owned, key=lambda c: self.owned_at.get(c, 0.0))
        candidates = []
        try:
            for p in _work_dir().glob("*/.started"):
                code = p.parent.name
                if (p.parent / ".closed").exists() or (p.parent / ".hold").exists():
                    continue
                try:
                    candidates.append((p.stat().st_mtime, code))
                except OSError:
                    continue
        except OSError:
            pass
        return min(candidates)[1] if candidates else None

    # ---------- 底层 ----------
    def _req(self, path, method="GET", body=None, timeout=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("BENCHMARK_TOKEN", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read().decode() or "{}"
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"code": "http_%d" % e.code, "message": raw[:300]}
            code = payload.get("code") or "http_%d" % e.code
            msg = payload.get("message", "") or ""
            # 409 max active 是名额问题，不是任务结束；不要置 dead
            if code == "task_not_found" or (code == "invalid_state" and "max active" not in msg.lower()):
                self.dead = True
            raise ApiError(code, msg, payload.get("detail"), e.code)

    # ---------- 业务 ----------
    def challenges(self):
        return self._req("/openapi/v1/challenges")[1]

    def pending(self):
        """未通关题目，按 难度(easy<medium<hard) -> 分值 升序，先拿稳的分。"""
        order = {"easy": 0, "medium": 1, "hard": 2}
        items = [c for c in self.challenges() if not c.get("is_completed")]
        items.sort(key=lambda c: (order.get(c.get("difficulty"), 9), c.get("total_score", 0)))
        return items

    def start(self, code):
        path = f"/openapi/v1/challenges/start?unique_code={urllib.parse.quote(code)}"
        try:
            _, j = self._req(path, "POST")
        except ApiError as e:
            if not self._is_max_active(e):
                raise
            n = parse_max_active(e.message)
            if n:
                self.set_capacity(n, "409")
            victim = self._oldest_owned()
            if not victim:
                raise
            try:
                self.close(victim)
            except Exception:
                pass
            _, j = self._req(path, "POST")
        self.owned.add(code)
        self.owned_at[code] = time.monotonic()
        self._save_owned()
        # A successful start/restart supersedes an old failed-close record.
        try:
            (_work_dir() / code / ".close_pending").unlink()
        except OSError:
            pass
        return j.get("container_addr") or []

    def close(self, code):
        """Close one box. On a non-404 failure keep it in `owned` for a retry.

        Removing the local entry before the request leaves the box running on
        the platform with no local owner to retry it: the enforcer believes it
        is closed, and the account keeps holding a slot until the deadline.

        2026-11-20: retry transient network/5xx failures with backoff. 4xx
        business errors (invalid_state/task_not_found) still raise immediately.
        """
        last: Exception | None = None
        for attempt in range(3):
            try:
                data = self._req(f"/openapi/v1/challenges/close?unique_code={urllib.parse.quote(code)}", "POST")[1]
                self._forget(code)
                return data
            except ApiError as e:
                if e.code == "challenge_not_found" or e.status == 404:
                    self._forget(code)
                    return {"closed": False}
                last = e
                if e.status and 500 <= int(e.status) < 600 and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise
        raise last  # type: ignore[misc]

    def _forget(self, code):
        self.owned.discard(code)
        self.owned_at.pop(code, None)
        self._save_owned()

    def challenge(self, code):
        for c in self.challenges():
            if c.get("unique_code") == code:
                return c
        return None

    def submit(self, code, flag):
        """返回 dict：correct/awarded/cumulative_score/...；duplicate 归一成 correct=True。"""
        try:
            data = self._req("/openapi/v1/challenges/submit", "POST",
                             {"unique_code": code, "flag": flag})[1]
        except ApiError as e:
            if e.code == "duplicate":
                data = {"correct": True, "duplicate": True, "awarded": 0}
            else:
                raise
        if data.get("correct"):
            if data.get("duplicate"):
                self.flags.setdefault(code, 1)
            else:
                self.flags[code] = self.flags.get(code, 0) + 1
        if data.get("is_completed") or data.get("remaining_flags") == 0 or data.get("remaining") == 0:
            self.completed.add(code)
        return data

    def hint(self, code):
        """注意：看过 hint 后该题后续得分按比例扣。默认不打。"""
        return self._req(f"/openapi/v1/challenges/hint?unique_code={urllib.parse.quote(code)}")[1]

    def close_all(self):
        for c in list(self.owned):
            try:
                self.close(c)
            except Exception:
                # keep it in `owned`: the platform may still have it open
                pass
        self._save_owned()
