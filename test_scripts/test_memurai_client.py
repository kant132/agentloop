"""TC-MC-001 ~ TC-MC-009: Memurai client tests."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "redis"))

from memurai_client import Memurai, MemuraiError  # noqa: E402


def _fake_result(rc=0, stdout="", stderr=""):
    m = mock.Mock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


class MemuraiClientTests(unittest.TestCase):
    def setUp(self):
        self.cli = Memurai(cli_path="/fake/memurai-cli.exe")

    def test_tc_mc_001_ping_success(self):
        """TC-MC-001: ping() 解析 PONG。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout="PONG\n")) as m:
            self.assertTrue(self.cli.ping())
            self.assertEqual(m.call_count, 1)
            self.assertEqual(m.call_args.args[0][-1], "PING")

    def test_tc_mc_002_set_get_roundtrip(self):
        """TC-MC-002: set/get 往返。"""
        sides = [
            _fake_result(rc=0, stdout="OK\n"),
            _fake_result(rc=0, stdout="value-1"),
        ]
        with mock.patch("subprocess.run", side_effect=sides) as m:
            self.cli.set("k1", "value-1", ex=60)
            self.assertEqual(self.cli.get("k1"), "value-1")
            # First call was SET, must include "EX 60"
            first_cmd = m.call_args_list[0].args[0]
            self.assertIn("EX", first_cmd)
            self.assertIn("60", first_cmd)

    def test_tc_mc_003_set_ex_clause(self):
        """TC-MC-003: set(...ex=120) 走 EX 120。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout="OK\n")) as m:
            self.cli.set("k", "v", ex=120)
            cmd = m.call_args.args[0]
            self.assertIn("EX", cmd)
            self.assertIn("120", cmd)

    def test_tc_mc_004_mset_multi_keys(self):
        """TC-MC-004: mset({k1:v1,k2:v2}) 命令行尾部 6 段 = [-e, MSET, k1, v1, k2, v2]。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout="OK\n")) as m:
            self.cli.mset({"k1": "v1", "k2": "v2"})
            cmd = m.call_args.args[0]
            # 实际: cli + -h host + -p port + -e + MSET + 4 段 kv
            # 最后 6 段: -e MSET k1 v1 k2 v2
            self.assertEqual(cmd[-6:], ["-e", "MSET", "k1", "v1", "k2", "v2"])

    def test_tc_mc_005_delete_returns_count(self):
        """TC-MC-005: delete(k1,k2,k3) 返回 (integer) 2。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout="(integer) 2\n")):
            self.assertEqual(self.cli.delete("k1", "k2", "k3"), 2)

    def test_tc_mc_006_scan_iter_uses_scan(self):
        """TC-MC-006: scan_iter 走 --scan。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout="k1\nk2\nk3\n")) as m:
            keys = list(self.cli.scan_iter("audit:*:method:*"))
            self.assertEqual(keys, ["k1", "k2", "k3"])
            cmd = m.call_args.args[0]
            self.assertIn("--scan", cmd)
            self.assertIn("--pattern", cmd)

    def test_tc_mc_007_pipe_setex_batch_single_subprocess(self):
        """TC-MC-007: 50 条 pipe_setex_batch 只触发 1 次 subprocess。"""
        out = "\n".join(["+OK"] * 50)
        with mock.patch("subprocess.run", return_value=_fake_result(rc=0, stdout=out)) as m:
            n = self.cli.pipe_setex_batch([(f"k{i}", 60, f"v{i}") for i in range(50)])
            self.assertEqual(m.call_count, 1)
            self.assertEqual(n, 50)
            # 确认 stdin 走 RESP 流：每条 SET 是 *5 (SET/k/v/EX/ttl)，
            # 50 条应含 50 个 *5\r\n
            kwargs = m.call_args.kwargs
            self.assertIn("input", kwargs)
            self.assertEqual(kwargs["input"].count(b"*5\r\n"), 50)

    def test_tc_mc_008_command_failure_raises(self):
        """TC-MC-008: returncode != 0 抛 MemuraiError。"""
        with mock.patch("subprocess.run", return_value=_fake_result(rc=1, stderr="ERR", stdout="")):
            with self.assertRaises(MemuraiError) as cm:
                self.cli.get("missing")
            self.assertEqual(cm.exception.returncode, 1)

    def test_tc_mc_009_cli_path_missing_raises(self):
        """TC-MC-009: cli_path 不存在构造即抛。"""
        with self.assertRaises(MemuraiError) as cm:
            Memurai(cli_path="/no/such/cli.exe")
        self.assertIn("不存在", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
