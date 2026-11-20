# -*- coding: utf-8 -*-
"""平台容量自动学习：409 文案解析、env 覆盖、缓存、只关最老容器。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from framework import api


class CapacityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_work = os.environ.get("TSEC_WORKDIR")
        self.old_max = os.environ.get("TSEC_MAX_ACTIVE")
        os.environ["TSEC_WORKDIR"] = self.tmp.name
        os.environ.pop("TSEC_MAX_ACTIVE", None)

    def tearDown(self):
        if self.old_work is None:
            os.environ.pop("TSEC_WORKDIR", None)
        else:
            os.environ["TSEC_WORKDIR"] = self.old_work
        if self.old_max is None:
            os.environ.pop("TSEC_MAX_ACTIVE", None)
        else:
            os.environ["TSEC_MAX_ACTIVE"] = self.old_max
        self.tmp.cleanup()

    def _client(self):
        return api.BenchmarkClient(base_url="https://example.test", token="test-token")

    def test_parse_max_active(self):
        self.assertEqual(api.parse_max_active(
            "max active challenge instances reached (3), please close an existing challenge"), 3)
        self.assertEqual(api.parse_max_active("max active 5"), 5)
        self.assertEqual(api.parse_max_active("max active limit: 4"), 4)
        self.assertIsNone(api.parse_max_active("invalid_state: task already finished"))

    def test_env_override(self):
        os.environ["TSEC_MAX_ACTIVE"] = "6"
        c = self._client()
        self.assertEqual(c.max_active, 6)
        self.assertEqual(c.capacity_source, "env")

    def test_cache_roundtrip(self):
        c = self._client()
        self.assertEqual(c.max_active, api.DEFAULT_MAX_ACTIVE)
        c.set_capacity(5, "409")
        path = Path(self.tmp.name) / api.CAPACITY_FILE
        self.assertTrue(path.is_file())
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("test-token", raw)
        c2 = self._client()
        self.assertEqual(c2.max_active, 5)
        self.assertEqual(c2.capacity_source, "cache")

    def test_start_409_learns_and_closes_oldest(self):
        c = self._client()
        c.owned = {"old1", "old2"}
        c.owned_at = {"old1": 1.0, "old2": 2.0}
        calls = []
        state = {"starts": 0}

        def fake_req(path, method="GET", body=None, timeout=None):
            calls.append(path)
            if "start" in path:
                state["starts"] += 1
                if state["starts"] == 1:
                    raise api.ApiError(
                        "invalid_state",
                        "max active challenge instances reached (4), please close an existing challenge",
                        status=409,
                    )
                return 200, {"container_addr": ["1.2.3.4:80"]}
            if "close" in path:
                return 200, {"closed": True}
            return 200, {}

        c._req = fake_req
        addrs = c.start("new")
        self.assertEqual(addrs, ["1.2.3.4:80"])
        self.assertEqual(c.max_active, 4)
        self.assertEqual(c.capacity_source, "409")
        self.assertIn("new", c.owned)
        self.assertIn("old2", c.owned)
        self.assertNotIn("old1", c.owned)
        self.assertFalse(c.dead)
        self.assertTrue(any("close" in p for p in calls))


    def test_close_failure_keeps_owned(self):
        c = self._client()
        c.owned = {"a"}
        c.owned_at = {"a": 1.0}

        def fake_req(path, method="GET", body=None, timeout=None):
            raise api.ApiError("server_error", "500", status=500)

        c._req = fake_req
        with self.assertRaises(api.ApiError):
            c.close("a")
        self.assertIn("a", c.owned)

    def test_close_404_forgets(self):
        c = self._client()
        c.owned = {"a"}

        def fake_req(path, method="GET", body=None, timeout=None):
            raise api.ApiError("challenge_not_found", "gone", status=404)

        c._req = fake_req
        self.assertEqual(c.close("a"), {"closed": False})
        self.assertNotIn("a", c.owned)

    def test_submit_flags_and_completed(self):
        c = self._client()
        calls = {"n": 0}

        def fake_req(path, method="GET", body=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return 200, {"correct": True, "awarded": 100, "remaining_flags": 5, "flag_total": 6}
            if calls["n"] == 2:
                return 200, {"correct": True, "duplicate": True, "awarded": 0}
            return 200, {"correct": True, "awarded": 100, "is_completed": True, "remaining_flags": 0}

        c._req = fake_req
        r = c.submit("x", "flag{1}")
        self.assertTrue(r["correct"])
        self.assertEqual(c.flags["x"], 1)
        self.assertNotIn("x", c.completed)
        c.submit("x", "flag{1}")  # duplicate must not double-count
        self.assertEqual(c.flags["x"], 1)
        c.submit("x", "flag{2}")
        self.assertIn("x", c.completed)

    def test_start_409_no_number_keeps_default(self):
        c = self._client()
        c.owned = {"old1"}
        c.owned_at = {"old1": 1.0}
        state = {"starts": 0}

        def fake_req(path, method="GET", body=None, timeout=None):
            if "start" in path:
                state["starts"] += 1
                if state["starts"] == 1:
                    raise api.ApiError("invalid_state", "max active reached", status=409)
                return 200, {"container_addr": ["1.2.3.4:80"]}
            return 200, {"closed": True}

        c._req = fake_req
        c.start("new")
        self.assertEqual(c.max_active, api.DEFAULT_MAX_ACTIVE)
        self.assertEqual(c.capacity_source, "default")
        self.assertFalse(c.dead)

    def test_start_409_no_owned_raises(self):
        c = self._client()

        def fake_req(path, method="GET", body=None, timeout=None):
            raise api.ApiError("invalid_state", "max active challenge instances reached (3)", status=409)

        c._req = fake_req
        with self.assertRaises(api.ApiError):
            c.start("new")
        self.assertFalse(c.dead)


if __name__ == "__main__":
    unittest.main()
