"""
test_priority_calculator.py — PriorityCalculator 单元测试 (12 个)

公式 (RFC-0001 §3.5):
    priority = base + sink_count + preset_match * 10
    base: 无外部参数 = -100; POST/PUT/DELETE = 10; GET = 0

Filter 公式:
    filter_priority = filter_base + order_bonus + sink_bonus + custom_bonus
    filter_base: interceptor=20; servlet_filter=15; spring_filter=15; webfilter=10

覆盖:
  - calculate_priority: 三种 base + sink + preset 组合
  - calculate_for_endpoint: 与 sink_registry 集成
  - rank_chains: 降序排列 + priority 字段注入
  - calculate_filter_priority: filter base + order + sink + custom
  - rank_filters: 降序排列 + priority 字段注入
"""
import pytest

import sink_registry
from priority_calculator import (
    calculate_priority,
    calculate_for_endpoint,
    rank_chains,
    calculate_filter_priority,
    rank_filters,
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


# =========================================================================
# calculate_filter_priority
# =========================================================================

def test_filter_priority_interceptor_base():
    """interceptor base = 20, 无 sink/order → 20 + 0 + 0 + 5(custom) = 25"""
    p = calculate_filter_priority({"type": "interceptor", "is_custom": True})
    assert p == 25


def test_filter_priority_servlet_filter_with_sink():
    """servlet_filter + 1 sink → 15 + 0 + 10 + 5 = 30"""
    p = calculate_filter_priority({"type": "servlet_filter", "is_custom": True}, sink_count=1)
    assert p == 30


def test_filter_priority_with_order():
    """@Order(0) → order_bonus = 20, @Order(10) → order_bonus = 10"""
    p0 = calculate_filter_priority({"type": "spring_filter", "filter_order": 0, "is_custom": True})
    # 15 + 20 + 0 + 5 = 40
    assert p0 == 40

    p10 = calculate_filter_priority({"type": "spring_filter", "filter_order": 10, "is_custom": True})
    # 15 + 10 + 0 + 5 = 30
    assert p10 == 30


def test_filter_priority_framework_code_lower():
    """框架代码 (is_custom=False) 无 custom_bonus"""
    p_custom = calculate_filter_priority({"type": "servlet_filter", "is_custom": True})
    p_framework = calculate_filter_priority({"type": "servlet_filter", "is_custom": False})
    assert p_custom > p_framework


# =========================================================================
# rank_filters
# =========================================================================

def test_rank_filters_sorts_descending():
    """按 filter_priority 降序"""
    filters = [
        {"file": "A.java", "type": "webfilter", "is_custom": True},       # 10+0+0+5=15
        {"file": "B.java", "type": "interceptor", "is_custom": True},     # 20+0+0+5=25
        {"file": "C.java", "type": "servlet_filter", "is_custom": True},  # 15+0+0+5=20
    ]
    ranked = rank_filters(filters)
    assert [f["file"] for f in ranked] == ["B.java", "C.java", "A.java"]


def test_rank_filters_computes_priority_when_missing():
    """无 priority 字段时自动计算"""
    filters = [
        {"file": "A.java", "type": "webfilter", "is_custom": True},
        {"file": "B.java", "type": "interceptor", "is_custom": True, "sink_count": 1},
    ]
    ranked = rank_filters(filters)
    # B: 20+0+10+5=35, A: 10+0+0+5=15
    assert ranked[0]["file"] == "B.java"
    assert ranked[0]["priority"] == 35
    assert ranked[1]["priority"] == 15
