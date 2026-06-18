# -*- coding: utf-8 -*-
"""test_hotspot_ranker.py — DefaultHotspotRanker 的单元测试。

覆盖点：
1. empty_assets_returns_empty
2. score_high_risk_gets_30
3. score_medium_risk_gets_15
4. score_low_risk_gets_5
5. score_with_sinks_adds_10_each
6. score_with_fanin_adds_count
7. ranks_descending_by_score
8. takes_top_20_percent
9. handles_ties_deterministically
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.exposure.contracts import ExposureContext
from scripts.exposure.hotspot_ranker import DefaultHotspotRanker


@pytest.fixture
def ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


@pytest.fixture
def ranker() -> DefaultHotspotRanker:
    return DefaultHotspotRanker()


# ============================================================
# 行为契约测试
# ============================================================

class TestDefaultHotspotRankerContract:
    """DefaultHotspotRanker 必须满足 Ranker 协议。"""

    def test_implements_ranker_protocol(self):
        from scripts.exposure.contracts import Ranker
        assert isinstance(DefaultHotspotRanker(), Ranker)

    def test_has_correct_name(self):
        assert DefaultHotspotRanker().name == "default_hotspot_ranker"


class TestRankEmpty:
    """空资产应返回空列表。"""

    def test_empty_assets_returns_empty(self, ranker, ctx):
        out = ranker.rank({"assets": []}, ctx)
        assert out == []

    def test_assets_key_missing_returns_empty(self, ranker, ctx):
        out = ranker.rank({}, ctx)
        assert out == []

    def test_assets_key_none_returns_empty(self, ranker, ctx):
        out = ranker.rank({"assets": None}, ctx)
        assert out == []


class TestScoreCalculation:
    """评分逻辑：risk_weight + sink_weight + fan_in_weight。"""

    def test_score_high_risk_gets_30(self):
        item = {"_risk": "high"}
        assert DefaultHotspotRanker._score(item) == 30

    def test_score_medium_risk_gets_15(self):
        item = {"_risk": "medium"}
        assert DefaultHotspotRanker._score(item) == 15

    def test_score_low_risk_gets_5(self):
        item = {"_risk": "low"}
        assert DefaultHotspotRanker._score(item) == 5

    def test_score_unknown_risk_defaults_10(self):
        item = {"_risk": "critical"}
        assert DefaultHotspotRanker._score(item) == 10

    def test_score_with_sinks_adds_10_each(self):
        item = {"_risk": "high", "sink_count": 3}
        assert DefaultHotspotRanker._score(item) == 30 + 30

    def test_score_with_fanin_adds_count(self):
        item = {"_risk": "medium", "caller_count": 7}
        assert DefaultHotspotRanker._score(item) == 15 + 7

    def test_score_sink_count_none_treated_as_zero(self):
        item = {"_risk": "low", "sink_count": None}
        assert DefaultHotspotRanker._score(item) == 5

    def test_score_caller_count_none_treated_as_zero(self):
        item = {"_risk": "low", "caller_count": None}
        assert DefaultHotspotRanker._score(item) == 5


class TestRankSorting:
    """排序逻辑：倒序排列。"""

    def test_ranks_descending_by_score(self, ranker, ctx):
        assets = {
            "assets": [
                {"fqn": "low_item", "_risk": "low"},
                {"fqn": "high_item", "_risk": "high"},
                {"fqn": "medium_item", "_risk": "medium"},
            ],
        }
        out = ranker.rank(assets, ctx)
        scores = [it["_hotspot_score"] for it in out]
        # All 3 items, top 20% = max(1, 3//5) = 1
        # The top 1 should be high (score=30)
        assert out[0]["fqn"] == "high_item"
        assert scores[0] >= scores[-1] if len(scores) > 1 else True


class TestRankTop20Percent:
    """取 top 20% 逻辑。"""

    def test_takes_top_20_percent(self, ranker, ctx):
        items = [{"fqn": f"item_{i}", "_risk": "high" if i < 3 else "low"} for i in range(10)]
        assets = {"assets": items}
        out = ranker.rank(assets, ctx)
        cutoff = max(1, 10 // 5)
        assert len(out) == cutoff  # 2

    def test_minimum_one_item_even_for_small_list(self, ranker, ctx):
        items = [{"fqn": "only_one", "_risk": "medium"}]
        assets = {"assets": items}
        out = ranker.rank(assets, ctx)
        assert len(out) == 1  # max(1, 1//5) = 1


class TestRankTies:
    """同分数项的确定性排序。"""

    def test_handles_ties_deterministically(self, ranker, ctx):
        # Two items with same score (medium=15), order should be stable
        a = {"fqn": "aaa", "_risk": "medium"}
        b = {"fqn": "bbb", "_risk": "medium"}
        assets = {"assets": [a, b]}
        out = ranker.rank(assets, ctx)
        # Both same score=15, but Python sort is stable, so order preserved
        assert len(out) == 1
        # First in original list should stay first after stable sort
        assert out[0]["fqn"] == "aaa"
