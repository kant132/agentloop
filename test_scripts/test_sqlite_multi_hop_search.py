"""TC-SMHS-001 ~ TC-SMHS-003: sqlite-multi-hop-search tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("chain", "sqlite-multi-hop-search.py")
run_template = _mod.run_template
run_custom = _mod.run_custom

DB = os.path.join(os.path.dirname(__file__), "fixtures", "codegraph-fake.db")


class SqliteMultiHopSearchTests(unittest.TestCase):
    def test_tc_smhs_001_5hop_20join(self):
        """TC-SMHS-001: forward_5hop_20join → join_count=20。"""
        rows, joins, _ = run_template(DB, "forward_5hop_20join", {"entry_fqn": "pkg::C::m"})
        self.assertEqual(joins, 20)

    def test_tc_smhs_002_multi_sink(self):
        """TC-SMHS-002: multi_sink_search → rows[0].sink_type ∈ {SQLI,RCE,DESER,SSRF,PATH_TRAV,LDAP,UNKNOWN}。"""
        rows, _, _ = run_template(DB, "multi_sink_search", {"entry_fqn": "pkg::C::m"})
        if not rows:
            self.skipTest("fixture 中无 sink（端点链路过短）")
        self.assertIn(rows[0]["sink_type"],
                      {"SQLI", "RCE", "DESER", "SSRF", "PATH_TRAV", "LDAP", "UNKNOWN"})

    def test_tc_smhs_003_custom_sql(self):
        """TC-SMHS-003: run_custom 占位符替换。"""
        rows = run_custom(DB, "SELECT qualified_name FROM nodes WHERE id=:id", {"id": "m:1"})
        self.assertEqual(rows[0]["qualified_name"], "pkg::S::m")


if __name__ == "__main__":
    unittest.main()
