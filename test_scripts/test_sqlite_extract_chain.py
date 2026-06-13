"""TC-SEC-001 ~ TC-SEC-005: sqlite-extract-chain tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("chain", "sqlite-extract-chain.py")
build_left_sql = _mod.build_left_sql
extract_recursive = _mod.extract_recursive
resolve_entry = _mod.resolve_entry

DB = os.path.join(os.path.dirname(__file__), "fixtures", "codegraph-fake.db")


class SqliteExtractChainTests(unittest.TestCase):
    def test_tc_sec_001_recursive_depth(self):
        """TC-SEC-001: extract_recursive(m:entry, 20) 链深=4 (entry→1→2→3 = 4 节点, depth 0~3)。"""
        rows = extract_recursive(DB, "m:entry", 20)
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0]["depth"], 0)
        # 末行 depth 应 ≥ 3 (fixture: entry→1→2→3, 末行 depth=3)
        self.assertGreaterEqual(rows[-1]["depth"], 3)

    def test_tc_sec_002_cycle_protection(self):
        """TC-SEC-002: 自环 m:1→m:1 不导致死循环。"""
        rows = extract_recursive(DB, "m:1", 20)
        self.assertLess(len(rows), 100)

    def test_tc_sec_003_real_schema(self):
        """TC-SEC-003: SQL 引用 nodes.qualified_name / edges.kind='calls' (不依赖 method 表)。"""
        try:
            extract_recursive(DB, "m:1", 5)
        except Exception as e:
            self.fail(f"schema 错: {e}")

    def test_tc_sec_004_left_join_count(self):
        """TC-SEC-004: build_left_sql(4) 含 8 个 LEFT JOIN。"""
        sql = build_left_sql(4)
        self.assertEqual(sql.count("LEFT JOIN"), 8)

    def test_tc_sec_005_resolve_entry(self):
        """TC-SEC-005: resolve_entry("pkg::C::m") → "m:entry"。"""
        self.assertEqual(resolve_entry(DB, "pkg::C::m"), "m:entry")


if __name__ == "__main__":
    unittest.main()
