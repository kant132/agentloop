"""TC-RBP-001 ~ TC-RBP-004: redis-batch-prefetch tests."""
import importlib.util
import json
import os
import unittest
from unittest import mock


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "redis_batch_prefetch",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "redis", "redis-batch-prefetch.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
prefetch_chain = _mod.prefetch_chain


class RedisBatchPrefetchTests(unittest.TestCase):
    def test_tc_rbp_001_empty_chain(self):
        """TC-RBP-001: 空 chain → methods_prefetched=0, 不调 pipe_setex_batch。"""
        cli = mock.Mock()
        cli.pipe_setex_batch = mock.Mock()
        cli.setex = mock.Mock(return_value=True)
        r = prefetch_chain(cli, [], "g", "chain-x")
        self.assertEqual(r["methods_prefetched"], 0)
        cli.pipe_setex_batch.assert_not_called()
        # prefetch summary 仍写入
        cli.setex.assert_called_once()

    def test_tc_rbp_002_30_methods_one_pipe(self):
        """TC-RBP-002: 30 个 method 触发 1 次 pipe_setex_batch。"""
        chain = [
            {"fqn": f"pkg::C::m{i}", "startLine": i + 1, "body": f"b{i}",
             "file": "F.java", "line": i + 1}
            for i in range(30)
        ]
        cli = mock.Mock()
        cli.pipe_setex_batch = mock.Mock(return_value=30)
        cli.setex = mock.Mock(return_value=True)
        prefetch_chain(cli, chain, "g", "cid")
        self.assertEqual(cli.pipe_setex_batch.call_count, 1)
        # 第 1 个 arg 是 items 列表
        items = cli.pipe_setex_batch.call_args.args[0]
        self.assertEqual(len(items), 30)

    def test_tc_rbp_003_chain_summary_key(self):
        """TC-RBP-003: prefetch key 以 prefetch:{chain_id} 结尾。"""
        cli = mock.Mock()
        cli.pipe_setex_batch = mock.Mock()
        cli.setex = mock.Mock(return_value=True)
        prefetch_chain(cli, [{"fqn": "a", "startLine": 1, "body": "b"}], "g", "abc123")
        key = cli.setex.call_args.args[0]
        self.assertTrue(key.endswith("prefetch:abc123"))

    def test_tc_rbp_004_startline_from_line(self):
        """TC-RBP-004: 缺 startLine 时从 line 字段取 start_line。"""
        cli = mock.Mock()
        cli.pipe_setex_batch = mock.Mock(return_value=1)
        cli.setex = mock.Mock()
        prefetch_chain(cli, [{"fqn": "a", "line": 42, "body": "hello world"}], "g", "c")
        items = cli.pipe_setex_batch.call_args.args[0]
        value = items[0][2]  # (key, ttl, value)
        self.assertEqual(json.loads(value)["start_line"], 42)


if __name__ == "__main__":
    unittest.main()
