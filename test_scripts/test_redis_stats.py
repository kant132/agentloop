"""TC-RS-001 ~ TC-RS-002: redis-stats tests."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("redis", "redis-stats.py")
get_stats = _mod.get_stats


class RedisStatsTests(unittest.TestCase):
    def test_tc_rs_001_five_key_counts(self):
        """TC-RS-001: get_stats 返回 5 类计数。"""
        cli = mock.Mock()
        # 真实 pattern: audit:g:commit:*:method:*  → -1='*', -2='method'
        #              audit:g:commit:*:finding:*:draft → -1='draft', -2='*'
        def _key(p):
            parts = p.split(":")
            if parts[-1] == "*":
                return parts[-2]  # method/chain/prefetch
            return parts[-1]  # draft/final

        cli.count = mock.Mock(side_effect=lambda p: {
            "method": 120, "chain": 10, "prefetch": 8, "draft": 15, "final": 5,
        }[_key(p)])
        cli.info = mock.Mock(return_value="used_memory_human:1M\n")
        r = get_stats(cli, "com.example.x")
        self.assertEqual(r["method_count"], 120)
        self.assertEqual(r["chain_count"], 10)
        self.assertEqual(r["prefetch_count"], 8)
        self.assertEqual(r["finding_draft_count"], 15)
        self.assertEqual(r["finding_final_count"], 5)

    def test_tc_rs_002_memory_info(self):
        """TC-RS-002: used_memory_human 解析。"""
        cli = mock.Mock()
        cli.count = mock.Mock(return_value=0)
        cli.info = mock.Mock(return_value="used_memory_human:12.5M\r\nother:foo\r\n")
        r = get_stats(cli, "g")
        self.assertEqual(r["used_memory_human"], "12.5M")


if __name__ == "__main__":
    unittest.main()
