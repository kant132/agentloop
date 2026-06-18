"""
test_priority_calculator.py — PriorityCalculator 单元测试 (7 个)

公式 (RFC-0001 §3.5):
    priority = base + sink_count + preset_match * 10
    base: 无外部参数 = -100; POST/PUT/DELETE = 10; GET = 0

覆盖:
  - calculate_priority: 三种 base + sink + preset 组合
  - calculate_for_endpoint: 与 sink_registry 集成
  - rank_chains: 降序排列 + priority 字段注入
"""
import pytest

import sink_registry
from priority_calculator import (
    calculate_priority,
    calculate_for_endpoint,
    rank_chains,
)


def _ep(http_method="GET", has_external_params=True):
    """端点元数据 fixture"""
    return {"http_method": http_method, "has_external_params": has_external_params}


# =========================================================================
# calculate_priority
# =========================================================================

def test_priority_get_no_sinks_is_zero():
    """GET 有外部参数 + 无 sink → base 0"""
    p = calculate_priority(_ep("GET"), sink_count=0, preset_match_count=0)
    assert p == 0


def test_priority_post_with_sinks_and_preset():
    """POST + 2 sink + 1 preset → 10 + 2 + 10 = 22"""
    p = calculate_priority(_ep("POST"), sink_count=2, preset_match_count=1)
    assert p == 22


def test_priority_no_external_params_is_negative():
    """无外部参数 → base -100, 压过一切"""
    p = calculate_priority(_ep("POST", has_external_params=False),
                           sink_count=5, preset_match_count=2)
    assert p == -100 + 5 + 20   # = -75


def test_priority_delete_put_share_post_base():
    """DELETE/PUT 与 POST 同 base (10)"""
    assert calculate_priority(_ep("DELETE"), 0, 0) == 10
    assert calculate_priority(_ep("PUT"), 0, 0) == 10


# =========================================================================
# calculate_for_endpoint (集成 sink_registry)
# =========================================================================

def test_calculate_for_endpoint_uses_sink_registry():
    """从 chain_data 节点自动算 sink_count + preset_match"""
    endpoint_meta = _ep("POST")
    chain_data = {
        "entry_fqn": "com.A#m",
        "chain": [
            {"fqn": "a", "node_id": "1", "sinks": [
                "java.sql.Statement.executeQuery",   # preset 命中
                "com.internal.Util.get",            # 未命中
            ]},
            {"fqn": "b", "node_id": "2", "sinks": [
                "java.lang.Runtime.exec",           # preset 命中
            ]},
        ],
    }
    p = calculate_for_endpoint(endpoint_meta, chain_data, sink_registry)
    # sink_count=3, preset_match=2, base=10 → 10 + 3 + 20 = 33
    assert p == 33


# =========================================================================
# rank_chains
# =========================================================================

def test_rank_chains_sorts_descending_by_priority():
    """按 priority 降序, 高的在前"""
    chains = [
        {"entry_fqn": "A", "priority": 5},
        {"entry_fqn": "B", "priority": 30},
        {"entry_fqn": "C", "priority": -100},
    ]
    ranked = rank_chains(chains)
    assert [c["entry_fqn"] for c in ranked] == ["B", "A", "C"]


def test_rank_chains_computes_priority_when_missing():
    """item 无 priority 时, 从 metadata + sink 计数字段自动算"""
    chains = [
        {"entry_fqn": "A", "http_method": "GET",
         "sink_count": 0, "preset_match_count": 0},   # → 0
        {"entry_fqn": "B", "http_method": "POST",
         "sink_count": 1, "preset_match_count": 1},  # → 10+1+10=21
    ]
    ranked = rank_chains(chains)
    assert ranked[0]["entry_fqn"] == "B"
    assert ranked[0]["priority"] == 21
    assert ranked[1]["priority"] == 0
