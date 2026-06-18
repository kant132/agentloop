# -*- coding: utf-8 -*-
"""hotspot_ranker.py — 决策阶段：标注 20% 决定 80% 安全的关键代码。

输入 synthesizer 的 exposure_assets.json，按风险/调用频次/sink 计数排序，
取 top 20% 作为热点。参考 RFC-0001 §3.2 / §3.5。

评分依据（综合分）::
    hotspot_score = risk_weight + sink_weight + fan_in_weight

    risk_weight : high=30, medium=15, low=5
    sink_weight : 每个命中预置 sink 加 10
    fan_in_weight : codegraph caller 数（可空，缺失=0）
"""
from __future__ import annotations

from typing import Any

from .contracts import ExposureContext, Ranker


class DefaultHotspotRanker(Ranker):
    """默认热点排序器。

    依据：风险等级 + sink 数量 + 入度（caller count，可选）。
    输出 top 20% 项作为 20% 关键代码。
    """
    name = "default_hotspot_ranker"

    def rank(
        self, assets: dict[str, Any], ctx: ExposureContext
    ) -> list[dict[str, Any]]:
        all_items = list(assets.get("assets", []) or [])
        if not all_items:
            return []

        # 计算分数
        for it in all_items:
            it["_hotspot_score"] = self._score(it)

        # 倒序排序
        all_items.sort(key=lambda x: x.get("_hotspot_score", 0), reverse=True)

        # 取 top 20%
        cutoff = max(1, len(all_items) // 5)
        return all_items[:cutoff]

    @staticmethod
    def _score(item: dict[str, Any]) -> int:
        risk = item.get("_risk", "medium")
        risk_w = {"high": 30, "medium": 15, "low": 5}.get(risk, 10)
        sink_w = int(item.get("sink_count", 0) or 0) * 10
        fanin_w = int(item.get("caller_count", 0) or 0)
        return risk_w + sink_w + fanin_w
