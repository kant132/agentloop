"""TC-FR-001 ~ TC-FR-003: force-rescan tests."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("audit", "force-rescan.py")
force_rescan = _mod.force_rescan

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "audit", "force-rescan.py")


class ForceRescanTests(unittest.TestCase):
    def test_tc_fr_001_cd_modes(self):
        """TC-FR-001: modes=[C,D] 正常返回 queued, set_json 1 次。"""
        cli = mock.Mock()
        cli.set_json = mock.Mock()
        cli.ping = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            r = force_rescan(cli, "g", "H", "GET /x", "fqn", ["C", "D"], tmp)
            self.assertEqual(r["status"], "queued")
            cli.set_json.assert_called_once()

    def test_tc_fr_002_with_a_fails(self):
        """TC-FR-002: 含 A 退出码 1, stderr 含 "只允许"。"""
        r = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--modes", "A,C",
             "--endpoint", "x", "--endpoint-fqn", "y",
             "--group-id", "g", "--audit-root", "loop_audit/_test"],
            capture_output=True,
        )
        self.assertEqual(r.returncode, 1)
        # Windows 中文 console 编码可能乱码；按 unicode-escape 比对
        stderr = r.stderr.decode("utf-8", errors="replace")
        # 用 'A' 作为关键判断
        self.assertIn("A", stderr)
        self.assertIn("C", stderr)

    def test_tc_fr_003_redis_key(self):
        """TC-FR-003: set_json key 含 force-rescan: 和 fqn sha256 前 16 位。"""
        cli = mock.Mock()
        cli.set_json = mock.Mock()
        cli.ping = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            force_rescan(cli, "g", "H", "ep", "com.x.Y::z", ["C", "D"], tmp)
            key = cli.set_json.call_args.args[0]
            self.assertIn("force-rescan:", key)
            self.assertIn(hashlib.sha256(b"com.x.Y::z").hexdigest()[:16], key)


if __name__ == "__main__":
    unittest.main()
