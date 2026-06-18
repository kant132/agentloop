"""metric_simplifier.py — 最终评价简化器。

职责：聚合所有链的 load_counter 数据，**仅输出一个核心指标**——
加载/缓存比 ratio（符合 RFC §3.6「简化要求」）。

输出 schema：

```
{
    "total_chains":   int,
    "total_loads":    int,
    "total_cached":   int,
    "overall_ratio":  float,    # = total_loads / total_cached
    "verdict":        "good" | "fair" | "poor",
    "per_chain":      [ {"chain_id", "loads", "cached", "ratio", "verdict"}, ... ],
}
```

判定阈值（与 LoadCounter.get_metric 的 ``理想 < 0.3`` 一致）：
- ``ratio < 0.3``  → good（前 5 层 + 缓存命中已覆盖大部分需求）
- ``0.3 ≤ ratio < 0.6`` → fair（延迟层加载偏多，需关注）
- ``ratio ≥ 0.6``   → poor（缓存未发挥作用，需调优）

设计要点：
- 纯函数，无外部依赖；输入是 list[dict]，输出 dict
- ``verdict_for_ratio`` 单独暴露，便于其他模块复用
- ``cached == 0`` 时 ratio=0.0，verdict=good（无可比较基准时不下负面结论）
"""
from __future__ import annotations

from typing import Any, List, Mapping

# ------------------------------------------------------------------ 阈值常量

GOOD_THRESHOLD = 0.3
FAIR_THRESHOLD = 0.6


# ------------------------------------------------------------------ 公开 API

def verdict_for_ratio(ratio: float) -> str:
    """根据 ratio 输出 verdict 字符串。

    Args:
        ratio: loads / cached，非负

    Returns:
        ``"good"`` / ``"fair"`` / ``"poor"``
    """
    if ratio < GOOD_THRESHOLD:
        return "good"
    if ratio < FAIR_THRESHOLD:
        return "fair"
    return "poor"


def simplify_chain(chain_metric: Mapping[str, Any]) -> dict:
    """规整单条 chain 的 metric 字段并附加 verdict。

    Args:
        chain_metric: 至少含 ``chain_id`` / ``loads`` / ``cached``；
                      若有 ``ratio`` 优先使用，否则按 loads/cached 计算

    Returns:
        ``{"chain_id", "loads", "cached", "ratio", "verdict"}``
    """
    chain_id = chain_metric.get("chain_id", "")
    loads = int(chain_metric.get("loads", 0))
    cached = int(chain_metric.get("cached", 0))

    ratio = _safe_ratio(loads, cached)
    # 显式提供 ratio 时优先（允许上游覆盖）
    if "ratio" in chain_metric and chain_metric["ratio"] is not None:
        try:
            ratio = float(chain_metric["ratio"])
        except (TypeError, ValueError):
            pass

    return {
        "chain_id": chain_id,
        "loads": loads,
        "cached": cached,
        "ratio": ratio,
        "verdict": verdict_for_ratio(ratio),
    }


def simplify(per_chain_metrics: List[Mapping[str, Any]]) -> dict:
    """聚合所有链，输出最终单一指标块。

    Args:
        per_chain_metrics: 每条 chain 的 ``{"chain_id", "loads", "cached"}`` 列表

    Returns:
        完整 dict（见模块 docstring）
    """
    per_chain: List[dict] = [simplify_chain(m) for m in per_chain_metrics]

    total_chains = len(per_chain)
    total_loads = sum(c["loads"] for c in per_chain)
    total_cached = sum(c["cached"] for c in per_chain)
    overall_ratio = _safe_ratio(total_loads, total_cached)
    overall_verdict = verdict_for_ratio(overall_ratio)

    return {
        "total_chains": total_chains,
        "total_loads": total_loads,
        "total_cached": total_cached,
        "overall_ratio": overall_ratio,
        "verdict": overall_verdict,
        "per_chain": per_chain,
    }


# ------------------------------------------------------------------ 辅助

def _safe_ratio(loads: int, cached: int) -> float:
    """cached == 0 时返回 0.0，避免除零。"""
    if cached <= 0:
        return 0.0
    return float(loads) / float(cached)
