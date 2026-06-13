"""TC-RSC-001 ~ TC-RSC-003: redis-self-check tests."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("redis", "redis-self-check.py")
self_check = _mod.self_check
DB = os.path.join(os.path.dirname(__file__), "fixtures", "codegraph-fake.db")


class RedisSelfCheckTests(unittest.TestCase):
    def test_tc_rsc_001_all_pass(self):
        """TC-RSC-001: 50 个 key 全过（一致时）= passed=50, failed=0。"""
        cli = mock.Mock()
        cli.scan_iter = mock.Mock(return_value=iter([f"audit:g:commit:H:method:pkg::C::m{i}#h{i}" for i in range(50)]))
        cli.get = mock.Mock(return_value=json.dumps({"fqn": "pkg::C::m", "body": "OK"}))
        cli.delete = mock.Mock()
        with mock.patch("random.sample", side_effect=lambda x, n: x[:n]):
            r = self_check(cli, "g", "HEAD", DB, 50)
        self.assertEqual(r["passed"], 50)
        self.assertEqual(r["failed"], 0)

    def test_tc_rsc_002_body_mismatch(self):
        """TC-RSC-002: body 不一致 → mismatch reason=body_mismatch。"""
        cli = mock.Mock()
        cli.scan_iter = mock.Mock(return_value=iter(["audit:g:commit:H:method:x#h"]))
        cli.get = mock.Mock(return_value=json.dumps({"fqn": "x", "body": "OLD"}))
        cli.delete = mock.Mock()
        r = self_check(cli, "g", "HEAD", DB, 1)
        self.assertTrue(r["mismatches"], "expected at least 1 mismatch")
        self.assertEqual(r["mismatches"][0]["reason"], "body_mismatch")
        self.assertGreaterEqual(cli.delete.call_count, 1)

    def test_tc_rsc_003_no_keys(self):
        """TC-RSC-003: scan 0 key → warning='no keys found'。"""
        cli = mock.Mock()
        cli.scan_iter = mock.Mock(return_value=iter([]))
        r = self_check(cli, "g", "HEAD", DB, 50)
        self.assertEqual(r["checked"], 0)
        self.assertIn("warning", r)
        self.assertEqual(r["warning"], "no keys found")


if __name__ == "__main__":
    unittest.main()
