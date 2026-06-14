"""TC-LP-001 ~ TC-LP-002: list-pruned tests."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("audit", "list-pruned.py")
list_pruned = _mod.list_pruned


class ListPrunedTests(unittest.TestCase):
    def test_tc_lp_001_read_jsonl(self):
        """TC-LP-001: 3 条 pruning log → pruned_count=3。"""
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "pruning-log.jsonl")
            with open(log, "w", encoding="utf-8") as f:
                f.write("\n".join([json.dumps({"rule_id": "P-L1-001", "level": "L1"}) for _ in range(3)]))
            r = list_pruned(log)
            self.assertEqual(r["pruned_count"], 3)

    def test_tc_lp_002_by_rule_buckets(self):
        """TC-LP-002: 3 条 P-L1-001 + 2 条 P-L2-001 → by_rule 两 key。"""
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "pruning-log.jsonl")
            items = (
                [{"rule_id": "P-L1-001", "level": "L1"}] * 3
                + [{"rule_id": "P-L2-001", "level": "L2"}] * 2
            )
            with open(log, "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(i) for i in items))
            r = list_pruned(log)
            self.assertEqual(r["by_rule"]["P-L1-001"], 3)
            self.assertEqual(r["by_rule"]["P-L2-001"], 2)


if __name__ == "__main__":
    unittest.main()
