# -*- coding: utf-8 -*-
"""governance 的单元测试：SKIP、活动心跳、看门狗、探活、预检、CLI 本地命令。"""
from __future__ import annotations

import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from framework import governance as g

ROOT = Path(__file__).resolve().parents[2]


class SkipRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "SKIP.txt"
        self.reg = g.SkipRegistry(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_remove_dedupe(self):
        self.reg.add("a-03", "login 恒 500", now=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc))
        self.reg.add("a-03", "updated", now=datetime(2026, 11, 14, 12, 1, tzinfo=timezone.utc))
        self.reg.add("f2-01", "decoy", now=datetime(2026, 11, 14, 12, 2, tzinfo=timezone.utc))
        self.assertEqual(self.reg.codes(), {"a-03", "f2-01"})
        self.assertEqual(self.reg.get("a-03").reason, "updated")
        self.assertTrue(self.reg.contains("f2-01"))
        self.assertTrue(self.reg.remove("a-03"))
        self.assertFalse(self.reg.remove("a-03"))
        self.assertEqual(self.reg.codes(), {"f2-01"})

    def test_legacy_line(self):
        self.path.write_text("f2-05   VM 生成器写坏\n", encoding="utf-8")
        e = self.reg.get("f2-05")
        self.assertIsNotNone(e)
        self.assertIn("VM", e.reason)
        self.assertTrue(self.reg.contains("f2-05"))

    def test_header_only(self):
        self.path.write_text("# code\tadded_at\treason\n", encoding="utf-8")
        self.assertEqual(self.reg.entries(), [])


class ActivityAndWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch_at(self, code, when, name=".heartbeat"):
        p = g.code_dir(code, self.root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(when.isoformat(), encoding="utf-8")
        ts = when.timestamp()
        os.utime(p, (ts, ts))
        return p

    def test_started_touch_hold(self):
        start = self.now - timedelta(minutes=30)
        g.mark_started("b-01", self.root, now=start)
        self.assertIsNotNone(g.read_started("b-01", self.root))
        self._touch_at("b-01", self.now - timedelta(minutes=1))
        last = g.last_activity("b-01", self.root)
        self.assertLess(abs((last - (self.now - timedelta(minutes=1))).total_seconds()), 2)
        g.hold("b-01", self.root, "manual")
        self.assertTrue(g.is_held("b-01", self.root))
        self.assertTrue(g.unhold("b-01", self.root))
        self.assertFalse(g.is_held("b-01", self.root))

    def test_within_budget(self):
        g.mark_started("b-02", self.root, now=self.now - timedelta(minutes=5))
        d = g.decide_watch("b-02", soft=20 * 60, hard=25 * 60, idle=10 * 60,
                           root=self.root, now=self.now, allow_close=True)
        self.assertEqual(d.action, "keep")

    def test_soft_timebox_recent_activity_keeps(self):
        g.mark_started("b-03", self.root, now=self.now - timedelta(minutes=30))
        self._touch_at("b-03", self.now - timedelta(minutes=1))
        d = g.decide_watch("b-03", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                           root=self.root, now=self.now, allow_close=True)
        self.assertEqual(d.action, "warn")
        self.assertIn("仍有活动", d.reason)

    def test_idle_after_timebox_closes(self):
        g.mark_started("b-04", self.root, now=self.now - timedelta(minutes=30))
        self._touch_at("b-04", self.now - timedelta(minutes=10))
        d = g.decide_watch("b-04", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                           root=self.root, now=self.now, allow_close=True)
        self.assertEqual(d.action, "close")
        d2 = g.decide_watch("b-04", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                            root=self.root, now=self.now, allow_close=False)
        self.assertEqual(d2.action, "would_close")

    def test_hold_never_closes(self):
        g.mark_started("b-05", self.root, now=self.now - timedelta(hours=2))
        g.hold("b-05", self.root, "manual")
        d = g.decide_watch("b-05", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                           root=self.root, now=self.now, allow_close=True, force_hard=True)
        self.assertEqual(d.action, "hold")

    def test_hard_deadline(self):
        g.mark_started("b-06", self.root, now=self.now - timedelta(minutes=30))
        self._touch_at("b-06", self.now - timedelta(seconds=10))
        d = g.decide_watch("b-06", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                           root=self.root, now=self.now, allow_close=True, force_hard=True)
        self.assertEqual(d.action, "close")


    def test_extension_moves_deadlines(self):
        g.mark_started("b-07", self.root, now=self.now - timedelta(minutes=30))
        self._touch_at("b-07", self.now - timedelta(minutes=1))
        ext = g.grant_extension("b-07", 30 * 60, "new primitive", root=self.root, now=self.now)
        self.assertIsNotNone(ext)
        self.assertEqual(g.read_extension("b-07", self.root).seconds, 30 * 60)
        # 10/15 分钟 + 30 分钟延长 => soft 40m / hard 45m；30m 时仍在软截止内
        d = g.decide_watch("b-07", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                           root=self.root, now=self.now, allow_close=True)
        self.assertEqual(d.action, "keep")
        self.assertEqual(d.extension, 30 * 60)
        # 已有 extension 时默认不再叠加
        self.assertIsNone(g.grant_extension("b-07", 30 * 60, "again", root=self.root, now=self.now))
        # 20 分钟后再看：elapsed 50m > hard 45m，idle 21m > 5m -> close
        d2 = g.decide_watch("b-07", soft=10 * 60, hard=15 * 60, idle=5 * 60,
                            root=self.root, now=self.now + timedelta(minutes=20), allow_close=True)
        self.assertEqual(d2.action, "close")

    def test_tier_defaults(self):
        self.assertEqual(g.TIERS["easy"]["hard"], 10 * 60)
        self.assertEqual(g.TIERS["medium"]["hard"], 20 * 60)
        self.assertEqual(g.TIERS["hard"]["hard"], 60 * 60)
        self.assertEqual(g.TIERS["credential"]["hard"], 5 * 60)
        self.assertEqual(g.TIERS["intel"]["hard"], 45 * 60)


class StallAndNotesTest(unittest.TestCase):
    """停滞闸门（按原语，不按总时长）+ 关闭前的笔记闸门。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch_at(self, code, when, name=".primitive"):
        p = g.code_dir(code, self.root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        ts = when.timestamp()
        os.utime(p, (ts, ts))
        return p

    def test_stall_closes_before_soft_deadline(self):
        g.mark_started("s-01", self.root, now=self.now - timedelta(minutes=20))
        d = g.decide_watch("s-01", soft=30 * 60, hard=60 * 60,
                           root=self.root, now=self.now, allow_close=True, stall=15 * 60)
        self.assertEqual(d.action, "close")
        self.assertEqual(d.phase, "stall")
        self.assertGreaterEqual(d.idle_primitive, 15 * 60)
        d2 = g.decide_watch("s-01", soft=30 * 60, hard=60 * 60,
                            root=self.root, now=self.now, allow_close=False, stall=15 * 60)
        self.assertEqual(d2.action, "would_close")

    def test_stall_ignores_recent_primitive(self):
        g.mark_started("s-02", self.root, now=self.now - timedelta(minutes=20))
        self._touch_at("s-02", self.now - timedelta(minutes=2))
        d = g.decide_watch("s-02", soft=30 * 60, hard=60 * 60,
                           root=self.root, now=self.now, allow_close=True, stall=15 * 60)
        self.assertEqual(d.action, "keep")

    def test_heartbeat_counts_as_primitive(self):
        g.mark_started("s-03", self.root, now=self.now - timedelta(minutes=20))
        self._touch_at("s-03", self.now - timedelta(minutes=1), name=".heartbeat")
        d = g.decide_watch("s-03", soft=30 * 60, hard=60 * 60,
                           root=self.root, now=self.now, allow_close=True, stall=15 * 60)
        self.assertEqual(d.action, "keep")

    def test_stall_zero_keeps_old_behavior(self):
        g.mark_started("s-04", self.root, now=self.now - timedelta(minutes=20))
        d = g.decide_watch("s-04", soft=30 * 60, hard=60 * 60,
                           root=self.root, now=self.now, allow_close=True, stall=0)
        self.assertEqual(d.action, "keep")

    def test_stall_respects_hold(self):
        g.mark_started("s-05", self.root, now=self.now - timedelta(hours=1))
        g.hold("s-05", self.root, "next: dump /etc/passwd")
        d = g.decide_watch("s-05", soft=30 * 60, hard=60 * 60,
                           root=self.root, now=self.now, allow_close=True, stall=15 * 60)
        self.assertEqual(d.action, "hold")

    def test_notes_gate(self):
        g.mark_started("n-01", self.root, now=self.now - timedelta(minutes=30))
        allow, msg = g.close_note_gate("n-01", self.root, now=self.now)
        self.assertFalse(allow)
        self.assertIn("下一招", msg)
        # 写了 FINDINGS.md 就放行
        p = g.note_paths("n-01", self.root)[1]
        p.write_text("- 入口: :8080\n- 已试: admin/admin 反例\n- 下一招: 试 /api/v1\n",
                     encoding="utf-8")
        self.assertTrue(g.has_notes("n-01", self.root))
        self.assertTrue(g.close_note_gate("n-01", self.root, now=self.now)[0])

    def test_short_visit_needs_no_notes(self):
        g.mark_started("n-02", self.root, now=self.now - timedelta(minutes=2))
        self.assertTrue(g.close_note_gate("n-02", self.root, now=self.now)[0])

    def test_empty_note_does_not_count(self):
        g.mark_started("n-03", self.root, now=self.now - timedelta(minutes=30))
        g.note_paths("n-03", self.root)[2].write_text("", encoding="utf-8")
        self.assertFalse(g.has_notes("n-03", self.root))
        self.assertFalse(g.close_note_gate("n-03", self.root, now=self.now)[0])


class CloseGateCliTest(unittest.TestCase):
    """cli close 的笔记闸门：长停留无笔记 -> 不释放容器（exit 4）；--force 可强关。"""

    class FakeClient:
        def __init__(self):
            self.closed = []

        def close(self, code):
            self.closed.append(code)
            return {"closed": code}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = os.environ.get("TSEC_WORKDIR")
        os.environ["TSEC_WORKDIR"] = str(self.root)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TSEC_WORKDIR", None)
        else:
            os.environ["TSEC_WORKDIR"] = self._old
        self.tmp.cleanup()

    def test_refuses_then_force_closes(self):
        from framework import cli
        g.mark_started("c-01", now=self.now - timedelta(minutes=30))
        fake = self.FakeClient()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_close(fake, "c-01"), 4)
        self.assertEqual(fake.closed, [])          # 没关，容器还在
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_close(fake, "c-01", force=True), 0)
        self.assertEqual(fake.closed, ["c-01"])

    def test_short_visit_closes_without_notes(self):
        from framework import cli
        g.mark_started("c-02", now=self.now - timedelta(minutes=1))
        fake = self.FakeClient()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_close(fake, "c-02"), 0)
        self.assertEqual(fake.closed, ["c-02"])


class ProbeAndPreflightTest(unittest.TestCase):
    def test_split_addr(self):
        self.assertEqual(g.split_addr("1.2.3.4:8080"), ("1.2.3.4", 8080))
        self.assertEqual(g.split_addr("http://1.2.3.4:80/x"), ("1.2.3.4", 80))
        self.assertEqual(g.split_addr("[::1]:9000"), ("::1", 9000))
        self.assertEqual(g.split_addr("1.2.3.4"), ("1.2.3.4", 80))

    def test_probe_alive_and_dead(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def accept_one():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

        threading.Thread(target=accept_one, daemon=True).start()

        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()

        results = g.probe_addrs([f"127.0.0.1:{port}", f"127.0.0.1:{dead_port}"], timeout=2.0)
        srv.close()
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)

    def test_preflight_ok_and_fail(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status":"ok","client_ip":"10.0.0.1","time":"t"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ok = g.preflight(f"http://127.0.0.1:{port}", timeout=2)
            self.assertTrue(ok.ok, ok.error)
            self.assertEqual(ok.data.get("client_ip"), "10.0.0.1")
        finally:
            srv.shutdown()
            srv.server_close()

        bad = g.preflight("http://127.0.0.1:1", timeout=0.5)
        self.assertFalse(bad.ok)


class CliLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.env = {**os.environ, "TSEC_WORKDIR": str(self.work)}
        self.env.pop("TSEC_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "framework.cli", *args],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

    def test_skip_check_and_watch_once(self):
        r = self._run("skip", "a-03", "login 恒 500")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.work / "SKIP.txt").is_file())

        r = self._run("check-skip", "a-03")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self._run("check-skip", "b-01")
        self.assertEqual(r.returncode, 1, r.stdout)

        # watch --once 不需要 token：没有 .started 时以当前时刻起算，预算内 keep
        r = self._run("watch", "a-03", "--once", "--budget", "20m", "--idle", "5m")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KEEP", r.stdout)

        r = self._run("unskip", "a-03")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_extend_and_tier(self):
        r = self._run("extend", "a-03", "5m", "new primitive")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self._run("watch", "a-03", "--once", "--tier", "hard")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("extension=+5m", r.stdout)
        r = self._run("unextend", "a-03")
        self.assertEqual(r.returncode, 0, r.stdout)

        r = self._run("watch", "a-03", "--once", "--tier", "nope")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
