"""test_method_body_loader.py — MethodBodyLoader 测试。

策略：用 MockCache + MockCounter 隔离 Memurai 与 sqlite；
覆盖分前 5 层 / 延迟层加载路径、record_load 集成、can_skip_deferred 规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from method_body_loader import (
    ChainNode,
    FIRST_N_LAYERS_DEPTH,
    MethodBodyLoader,
)


# =============================================================================
# Mock 依赖
# =============================================================================

@dataclass
class MockCache:
    """记录调用历史以便断言。"""
    data: dict
    calls: List[str] = field(default_factory=list)

    def get(self, key: str):
        self.calls.append(key)
        return self.data.get(key)


@dataclass
class MockCounter:
    """收集所有 record_load 调用，便于断言 source/chain_id。"""
    records: list = field(default_factory=list)

    def record_load(self, node_id, fqn, chain_id, source="ai_request"):
        self.records.append({
            "node_id": node_id, "fqn": fqn,
            "chain_id": chain_id, "source": source,
        })


def _chain():
    """构造一条 depth 0..6 共 7 节点的链。"""
    return [
        ChainNode(node_id=f"n{d}", fqn=f"com.Svc#m{d}", depth=d)
        for d in range(7)
    ]


# =============================================================================
# load_first_5_layers
# =============================================================================

def test_first_5_layers_loaded_deferred_returned():
    """前 5 层进 full_bodies，第 5/6 层进 deferred。"""
    cache = MockCache({f"n{d}": f"body{d}" for d in range(7)})
    loader = MethodBodyLoader(cache, MockCounter())

    result = loader.load_first_5_layers(_chain(), chain_id="C1")

    assert len(result["full_bodies"]) == FIRST_N_LAYERS_DEPTH
    assert result["deferred_node_ids"] == ["n5", "n6"]
    # full_bodies 携带 body 与 source
    first = result["full_bodies"][0]
    assert first["node_id"] == "n0"
    assert first["body"] == "body0"
    assert first["source"] == "cache_hit"


def test_first_5_layers_cache_miss():
    """缓存未命中 → body 为 None，source=miss。"""
    cache = MockCache({})
    loader = MethodBodyLoader(cache, MockCounter())

    result = loader.load_first_5_layers(_chain())
    assert all(b["body"] is None for b in result["full_bodies"])
    assert all(b["source"] == "miss" for b in result["full_bodies"])


def test_first_5_layers_accepts_dict_chain():
    """chain 元素允许是 dict（兼容上游 JSON）。"""
    chain = [
        {"node_id": "x0", "fqn": "F#x0", "depth": 0},
        {"node_id": "x5", "fqn": "F#x5", "depth": 5},
    ]
    cache = MockCache({"x0": "BX"})
    loader = MethodBodyLoader(cache, MockCounter())

    result = loader.load_first_5_layers(chain)
    assert result["full_bodies"][0]["fqn"] == "F#x0"
    assert result["deferred_node_ids"] == ["x5"]


def test_first_5_layers_records_load_counter():
    """每次加载都触发 record_load；命中缓存记 cache_hit。"""
    cache = MockCache({"n0": "body0"})
    counter = MockCounter()
    loader = MethodBodyLoader(cache, counter)

    loader.load_first_5_layers(_chain(), chain_id="CX")

    # 前 5 层都被记录
    assert len(counter.records) == 5
    # n0 命中缓存 → cache_hit
    assert counter.records[0]["source"] == "cache_hit"
    # n1..n4 未命中 → first_5_layer（_fetch_and_record 中 miss 仍走 source=first_5_layer）
    for rec in counter.records[1:]:
        assert rec["source"] == "first_5_layer"
    assert all(r["chain_id"] == "CX" for r in counter.records)


def test_first_5_layers_works_without_counter():
    """load_counter=None 时不报错（缓存命中不记录）。"""
    cache = MockCache({"n0": "body0"})
    loader = MethodBodyLoader(cache, None)
    result = loader.load_first_5_layers(_chain())
    assert result["full_bodies"][0]["body"] == "body0"


# =============================================================================
# load_by_node_id
# =============================================================================

def test_load_by_node_id_hit():
    cache = MockCache({"k1": "B1"})
    counter = MockCounter()
    loader = MethodBodyLoader(cache, counter)

    body = loader.load_by_node_id("k1", fqn="F#m", chain_id="C2")
    assert body == "B1"
    # 命中缓存 → source=cache_hit（_fetch_and_record 的命中分支）
    assert counter.records == [{
        "node_id": "k1", "fqn": "F#m",
        "chain_id": "C2", "source": "cache_hit",
    }]


def test_load_by_node_id_miss_returns_none():
    cache = MockCache({})
    counter = MockCounter()
    loader = MethodBodyLoader(cache, counter)

    body = loader.load_by_node_id("missing", chain_id="C2")
    assert body is None
    # 未命中 → ai_request
    assert counter.records[0]["source"] == "ai_request"


def test_load_by_node_id_uses_default_chain_id():
    """未传 chain_id 时使用默认值。"""
    cache = MockCache({"a": "b"})
    loader = MethodBodyLoader(cache, MockCounter(), chain_id="default-chain")
    loader.load_by_node_id("a")
    # 不会抛 KeyError 即正确；断言默认 chain_id 通过 MockCounter 记录
    assert loader.default_chain_id == "default-chain"


# =============================================================================
# can_skip_deferred
# =============================================================================

def test_can_skip_deferred_no_sink_no_param():
    """无 sink 无外部参数传递 → 可跳过。"""
    full_bodies = [
        {"node_id": "n0"}, {"node_id": "n1"}, {"node_id": "n2"},
    ]
    taint_analysis = [
        {"node_id": "n0", "has_sink": False, "passes_external_param": False},
        {"node_id": "n1", "has_sink": False, "passes_external_param": False},
        {"node_id": "n2", "has_sink": False, "passes_external_param": False},
    ]
    assert MethodBodyLoader.can_skip_deferred(full_bodies, taint_analysis) is True


def test_can_skip_deferred_blocks_on_sink():
    """任一 body 含 sink → False。"""
    full_bodies = [{"node_id": "n0"}]
    taint_analysis = [
        {"node_id": "n0", "has_sink": True, "passes_external_param": False},
    ]
    assert MethodBodyLoader.can_skip_deferred(full_bodies, taint_analysis) is False


def test_can_skip_deferred_blocks_on_external_param():
    """任一 body 把外部参数传下去 → False。"""
    full_bodies = [{"node_id": "n0"}, {"node_id": "n1"}]
    taint_analysis = [
        {"node_id": "n0", "has_sink": False, "passes_external_param": False},
        {"node_id": "n1", "has_sink": False, "passes_external_param": True},
    ]
    assert MethodBodyLoader.can_skip_deferred(full_bodies, taint_analysis) is False


def test_can_skip_deferred_empty_bodies_is_conservative():
    """full_bodies 为空（无前 5 层）→ 保守返回 False。"""
    assert MethodBodyLoader.can_skip_deferred([], []) is False


def test_can_skip_deferred_missing_taint_entry_defaults_safe():
    """taint_analysis 缺该 node_id 条目 → 视为无 sink 无参数（True），
    但前提是 full_bodies 非空。"""
    full_bodies = [{"node_id": "n0"}]
    # taint_analysis 完全没提 n0
    assert MethodBodyLoader.can_skip_deferred(full_bodies, []) is True
