"""TC-DR-001 ~ TC-DR-002: data-reconcile tests."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("audit", "data-reconcile.py")
reconcile = _mod.reconcile


def _write_findings(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def _write_endpoints(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


class DataReconcileTests(unittest.TestCase):
    def test_tc_dr_001_all_ok(self):
        """TC-DR-001: 8 项全过。"""
        with tempfile.TemporaryDirectory() as tmp:
            findings_p = os.path.join(tmp, "findings.jsonl")
            endpoints_p = os.path.join(tmp, "endpoints.jsonl")
            findings = [
                {"endpoint_fqn": f"ep{i}", "chain_id": f"c{i}",
                 "severity": "CRITICAL" if i < 2 else "HIGH",
                 "poc_status": "confirmed_vuln",
                 "score": 90, "status": "final"}
                for i in range(5)
            ]
            endpoints = [{"endpoint_fqn": f"ep{i}"} for i in range(10)]
            _write_findings(findings_p, findings)
            _write_endpoints(endpoints_p, endpoints)

            cli = mock.Mock()
            # final=5 (5 promoted findings), chain=5 (5 unique chain_ids 一致)
            cli.count = mock.Mock(side_effect=lambda p: 5)
            r = reconcile(findings_p, endpoints_p, cli, "g", "H")
            self.assertTrue(r["all_ok"], f"items={r['items']}")
            self.assertEqual(r["warn_count"], 0)

    def test_tc_dr_002_chain_mismatch(self):
        """TC-DR-002: cli.count(chain) 与 findings chain_id 去重数不等 → item8 ok=False。"""
        with tempfile.TemporaryDirectory() as tmp:
            findings_p = os.path.join(tmp, "findings.jsonl")
            endpoints_p = os.path.join(tmp, "endpoints.jsonl")
            findings = [
                {"endpoint_fqn": f"ep{i}", "chain_id": f"c{i}",
                 "severity": "HIGH", "poc_status": "confirmed_vuln",
                 "score": 90, "status": "final"}
                for i in range(5)
            ]
            endpoints = [{"endpoint_fqn": f"ep{i}"} for i in range(10)]
            _write_findings(findings_p, findings)
            _write_endpoints(endpoints_p, endpoints)

            cli = mock.Mock()
            # final=5 (一致), chain=2 (5 chain_ids 声明 vs 2 实际)
            cli.count = mock.Mock(side_effect=lambda p: 5 if "final" in p else 2)
            r = reconcile(findings_p, endpoints_p, cli, "g", "H")
            self.assertFalse(r["items"]["8_Memurai链key防造数据"]["ok"])


if __name__ == "__main__":
    unittest.main()
