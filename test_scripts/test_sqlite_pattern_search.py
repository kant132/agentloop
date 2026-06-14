"""TC-SPS-001 ~ TC-SPS-003: sqlite-pattern-search tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("chain", "sqlite-pattern-search.py")
search_pattern = _mod.search_pattern

DB = os.path.join(os.path.dirname(__file__), "fixtures", "codegraph-fake.db")


class SqlitePatternSearchTests(unittest.TestCase):
    def test_tc_sps_001_sql_injection(self):
        """TC-SPS-001: sql_injection 模式匹配 executeQuery。"""
        rows = search_pattern(DB, "sql_injection")
        self.assertTrue(any(r["name"] == "executeQuery" for r in rows))

    def test_tc_sps_002_rce(self):
        """TC-SPS-002: rce 模式匹配 Runtime。"""
        rows = search_pattern(DB, "rce")
        self.assertTrue(any("Runtime" in r["qualified_name"] for r in rows))

    def test_tc_sps_003_unknown_pattern(self):
        """TC-SPS-003: 未知 pattern 抛 ValueError。"""
        with self.assertRaises(ValueError) as cm:
            search_pattern(DB, "xss")
        self.assertIn("未知 pattern", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
