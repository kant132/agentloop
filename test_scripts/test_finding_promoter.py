"""TC-FP-001 ~ TC-FP-003: finding-promoter tests."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("audit", "finding-promoter.py")
record_score = _mod.record_score
check_and_promote = _mod.check_and_promote


class FindingPromoterTests(unittest.TestCase):
    def test_tc_fp_001_record_score(self):
        """TC-FP-001: record_score 写 key=audit:g:commit:H:score:c1:r1, value="88"。"""
        cli = mock.Mock()
        cli.set = mock.Mock()
        r = record_score(cli, "g", "H", "c1", 1, 88)
        args = cli.set.call_args.args
        self.assertEqual(args[0], "audit:g:commit:H:score:c1:r1")
        self.assertEqual(args[1], "88")

    def test_tc_fp_002_3x85_promote(self):
        """TC-FP-002: 评分 [90,88,92] → promoted=True, 文件写出。"""
        with tempfile.TemporaryDirectory() as tmp:
            draft = json.dumps({
                "endpoint_fqn": "e", "chain_id": "c1", "description": "vuln",
            })
            cli = mock.Mock()
            # 脚本检查 range(27, 30) 三次
            cli.get = mock.Mock(side_effect=["90", "88", "92", draft])
            cli.set = mock.Mock()
            r = check_and_promote(cli, "g", "H", "c1", tmp)
            self.assertTrue(r["promoted"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "c1.json")))

    def test_tc_fp_003_below_85_no_promote(self):
        """TC-FP-003: 评分 [90,80,92] → promoted=False。"""
        with tempfile.TemporaryDirectory() as tmp:
            cli = mock.Mock()
            cli.get = mock.Mock(side_effect=["90", "80", "92", ""])
            r = check_and_promote(cli, "g", "H", "c1", tmp)
            self.assertFalse(r["promoted"])
            self.assertIn("未全部", r["reason"])


if __name__ == "__main__":
    unittest.main()
