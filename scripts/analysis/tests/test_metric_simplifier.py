"""test_metric_simplifier.py — 最终评价简化器测试。

覆盖：verdict_for_ratio 阈值 / simplify_chain 字段规整 / simplify 全局聚合 /
边界条件（空列表 / cached=0 / 比率覆盖）。
"""
from __future__ import annotations

import pytest

from metric_simplifier import (
    GOOD_THRESHOLD,
    FAIR_THRESHOLD,
    simplify,
    simplify_chain,
    verdict_for_ratio,
)


# =============================================================================
# verdict_for_ratio
# =============================================================================

def test_verdict_below_good_threshold():
    """ratio < 0.3 → good。"""
    assert verdict_for_ratio(0.0) == "good"
    assert verdict_for_ratio(0.1) == "good"
    assert verdict_for_ratio(GOOD_THRESHOLD - 0.01) == "good"


def test_verdict_in_fair_range():
    """0.3 ≤ ratio < 0.6 → fair。"""
    assert verdict_for_ratio(GOOD_THRESHOLD) == "fair"
    assert verdict_for_ratio(0.45) == "fair"
    assert verdict_for_ratio(FAIR_THRESHOLD - 0.01) == "fair"


def test_verdict_at_or_above_poor_threshold():
    """ratio ≥ 0.6 → poor。"""
    assert verdict_for_ratio(FAIR_THRESHOLD) == "poor"
    assert verdict_for_ratio(0.9) == "poor"
    assert verdict_for_ratio(1.0) == "poor"


def test_verdict_handles_negative_ratio_gracefully():
    """负 ratio 走 good 分支（< 0.3）；不抛异常。"""
    # 负数严格小于 0.3 → good（语义上代表「优于理想」，不构成错误）
    assert verdict_for_ratio(-0.01) == "good"


# =============================================================================
# simplify_chain
# =============================================================================

def test_simplify_chain_computes_ratio_when_missing():
    """未提供 ratio 时按 loads/cached 计算。"""
    out = simplify_chain({"chain_id": "c1", "loads": 2, "cached": 10})
    assert out == {
        "chain_id": "c1", "loads": 2, "cached": 10,
        "ratio": 0.2, "verdict": "good",
    }


def test_simplify_chain_honors_explicit_ratio():
    """显式提供 ratio 时优先（允许上游覆盖）。"""
    out = simplify_chain({
        "chain_id": "c1", "loads": 0, "cached": 0, "ratio": 0.5,
    })
    assert out["ratio"] == 0.5
    assert out["verdict"] == "fair"


def test_simplify_chain_zero_cached_is_good():
    """cached=0 → ratio=0.0 → good（不下负面结论）。"""
    out = simplify_chain({"chain_id": "c1", "loads": 5, "cached": 0})
    assert out["ratio"] == 0.0
    assert out["verdict"] == "good"


def test_simplify_chain_defaults_missing_fields():
    """loads / cached / chain_id 缺失时填默认值。"""
    out = simplify_chain({})
    assert out["chain_id"] == ""
    assert out["loads"] == 0
    assert out["cached"] == 0
    assert out["ratio"] == 0.0
    assert out["verdict"] == "good"


def test_simplify_chain_invalid_explicit_ratio_falls_back():
    """显式 ratio 是非法值（字符串/None/NaN）时回退到 loads/cached 计算。"""
    out = simplify_chain({
        "chain_id": "c1", "loads": 3, "cached": 10, "ratio": "bad-value",
    })
    assert out["ratio"] == 0.3
    assert out["verdict"] == "fair"


# =============================================================================
# simplify (global)
# =============================================================================

def test_simplify_aggregates_totals_and_verdict():
    """聚合多链 loads / cached，overall_ratio 与 verdict 正确。"""
    out = simplify([
        {"chain_id": "c1", "loads": 2, "cached": 10},   # 0.2
        {"chain_id": "c2", "loads": 6, "cached": 10},   # 0.6
        {"chain_id": "c3", "loads": 4, "cached": 10},   # 0.4
    ])
    # 总 loads=12, 总 cached=30 → ratio=0.4 → fair
    assert out["total_chains"] == 3
    assert out["total_loads"] == 12
    assert out["total_cached"] == 30
    assert out["overall_ratio"] == pytest.approx(0.4)
    assert out["verdict"] == "fair"
    assert len(out["per_chain"]) == 3


def test_simplify_all_good_chains():
    """全部 chain ratio 都 < 0.3 → overall good。"""
    out = simplify([
        {"chain_id": "c1", "loads": 1, "cached": 10},
        {"chain_id": "c2", "loads": 2, "cached": 10},
    ])
    assert out["overall_ratio"] == pytest.approx(0.15)
    assert out["verdict"] == "good"


def test_simplify_empty_input():
    """空列表 → total_chains=0, overall_ratio=0.0, verdict=good。"""
    out = simplify([])
    assert out == {
        "total_chains": 0,
        "total_loads": 0,
        "total_cached": 0,
        "overall_ratio": 0.0,
        "verdict": "good",
        "per_chain": [],
    }


def test_simplify_one_chain_drives_overall_to_poor():
    """单链 loads 翻倍 → overall 立即转 poor。"""
    out = simplify([{"chain_id": "c1", "loads": 8, "cached": 10}])
    assert out["overall_ratio"] == pytest.approx(0.8)
    assert out["verdict"] == "poor"
    assert out["per_chain"][0]["verdict"] == "poor"


def test_simplify_preserves_per_chain_entries():
    """per_chain 保留 chain_id 与各项子指标。"""
    out = simplify([{"chain_id": "abc", "loads": 3, "cached": 10}])
    first = out["per_chain"][0]
    assert set(first.keys()) == {"chain_id", "loads", "cached", "ratio", "verdict"}
    assert first["chain_id"] == "abc"
