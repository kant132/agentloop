"""
priority_calculator.py
======================

按 RFC-0001 §3.5 公式给调用链评分::

    priority = base + sink_count + preset_match * 10

base 取值:
  +---------+---------------------+-------+
  | 触发条件 |                     | base  |
  +---------+---------------------+-------+
  | 无外部参数 (无 @RequestParam/@PathVariable 等) | -100 |
  | POST / PUT / DELETE          | 10    |
  | GET (及其他)                 | 0     |
  +---------+---------------------+-------+

Filter 优先级公式::

    filter_priority = filter_base + order_bonus + sink_bonus + custom_bonus

filter_base 取值:
  +---------------------+-------+
  | 类型                | base  |
  +---------------------+-------+
  | interceptor         | 20    |
  | servlet_filter      | 15    |
  | spring_filter        | 15    |
  | webfilter            | 10    |
  +---------------------+-------+

API
---
- ``calculate_priority(chain, sink_count, preset_match_count) -> int``
- ``calculate_for_endpoint(endpoint_metadata, chain_data, sink_registry) -> int``
- ``rank_chains(chains_with_metadata) -> list[dict]``
- ``calculate_filter_priority(filter_meta, sink_count) -> int``
- ``rank_filters(filters_with_metadata) -> list[dict]``
"""
from __future__ import annotations

from typing import Any, List, Mapping, Sequence

# POST/PUT/DELETE 共享 base = 10 (设计文档: 写操作优先级高于读)
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
_PRESET_WEIGHT = 10          # 每命中一个预置 sink 的权重
_NO_PARAM_PENALTY = -100     # 无外部参数: 几乎不可能被利用, 强力降权


def _base(endpoint_meta: Mapping[str, Any]) -> int:
    """从端点元数据算 base 分。

    Parameters
    ----------
    endpoint_meta: 需含
        - ``has_external_params`` (bool, 默认 True): 是否接收外部输入
        - ``http_method`` (str, 默认 "GET"): HTTP 动词
    """
    if not endpoint_meta.get("has_external_params", True):
        return _NO_PARAM_PENALTY
    method = str(endpoint_meta.get("http_method", "GET")).upper()
    if method in _WRITE_METHODS:
        return 10
    return 0


def calculate_priority(
    chain: Mapping[str, Any],
    sink_count: int,
    preset_match_count: int,
) -> int:
    """核心评分公式: ``base + sink_count + preset_match * 10``。

    Parameters
    ----------
    chain:
        端点元数据 (含 ``http_method`` / ``has_external_params``)。
        决定 base。
    sink_count:
        链上 sink 总数 (含 preset + 非 preset)。
    preset_match_count:
        链上命中预置 sink 库的数量。
    """
    return (
        _base(chain)
        + int(sink_count)
        + int(preset_match_count) * _PRESET_WEIGHT
    )


def calculate_for_endpoint(
    endpoint_metadata: Mapping[str, Any],
    chain_data: Mapping[str, Any],
    sink_registry,
) -> int:
    """从 chain_data 节点自动算 sink_count + preset_match, 再套公式。

    Parameters
    ----------
    endpoint_metadata:
        ``{http_method, has_external_params}``
    chain_data:
        ``chain_builder`` 输出, 含 ``"chain"`` 节点列表。
    sink_registry:
        ``sink_registry`` 模块 (需有 ``count_sinks_in_chain`` /
        ``match_preset_sinks``)。
    """
    nodes = chain_data.get("chain", []) or []
    sink_count = sink_registry.count_sinks_in_chain(nodes)
    preset_match = sink_registry.match_preset_sinks(nodes)
    return calculate_priority(endpoint_metadata, sink_count, preset_match)


def rank_chains(chains_with_metadata: Sequence[Mapping[str, Any]]) -> List[dict]:
    """按 priority 降序排列。

    每条 item 可以:
    1. 已含 ``priority`` 字段 → 直接用
    2. 未含 → 从 ``http_method`` / ``has_external_params`` /
       ``sink_count`` / ``preset_match_count`` 自动计算并回填

    Returns
    -------
    list[dict]: 降序后的新列表 (不修改原列表), 每条含 ``priority`` 字段。
    """
    enriched: List[dict] = []
    for item in chains_with_metadata:
        d = dict(item)  # shallow copy, 不污染原 dict
        if "priority" not in d:
            d["priority"] = calculate_priority(
                d,
                int(d.get("sink_count", 0)),
                int(d.get("preset_match_count", 0)),
            )
        enriched.append(d)
    enriched.sort(key=lambda x: x["priority"], reverse=True)
    return enriched


# ============================================================
# Filter 优先级排序
# ============================================================

_FILTER_BASE = {
    "interceptor": 20,
    "servlet_filter": 15,
    "spring_filter": 15,
    "webfilter": 10,
}
_FILTER_SINK_BONUS = 10
_FILTER_CUSTOM_BONUS = 5
_ORDER_MAX_BONUS = 20


def _filter_base(filter_meta: Mapping[str, Any]) -> int:
    """从 filter 元数据算 base 分。

    Parameters
    ----------
    filter_meta: 需含
        - ``type`` (str): filter 类型 (interceptor/servlet_filter/spring_filter/webfilter)
    """
    ftype = str(filter_meta.get("type", "")).lower()
    return _FILTER_BASE.get(ftype, 5)


def calculate_filter_priority(
    filter_meta: Mapping[str, Any],
    sink_count: int = 0,
) -> int:
    """Filter 优先级公式: ``filter_base + order_bonus + sink_bonus + custom_bonus``。

    Parameters
    ----------
    filter_meta:
        ``{type, filter_order?, is_custom?}``
    sink_count:
        该 filter 文件中检测到的 sink 数量。
    """
    base = _filter_base(filter_meta)

    # @Order bonus: lower order = higher priority
    order = filter_meta.get("filter_order")
    if order is not None and isinstance(order, (int, float)) and order >= 0:
        order_bonus = max(0, _ORDER_MAX_BONUS - int(order))
    else:
        order_bonus = 0

    # Sink bonus: filters with sinks are more interesting
    sink_bonus = _FILTER_SINK_BONUS if sink_count > 0 else 0

    # Custom code bonus: non-framework code is more likely to have vulnerabilities
    custom_bonus = _FILTER_CUSTOM_BONUS if filter_meta.get("is_custom", True) else 0

    return base + order_bonus + sink_bonus + custom_bonus


def rank_filters(filters_with_metadata: Sequence[Mapping[str, Any]]) -> List[dict]:
    """按 filter_priority 降序排列。

    每条 item 可以:
    1. 已含 ``priority`` 字段 → 直接用
    2. 未含 → 从 ``type`` / ``filter_order`` / ``is_custom`` / ``sink_count`` 自动计算并回填

    Returns
    -------
    list[dict]: 降序后的新列表 (不修改原列表), 每条含 ``priority`` 字段。
    """
    enriched: List[dict] = []
    for item in filters_with_metadata:
        d = dict(item)
        if "priority" not in d:
            d["priority"] = calculate_filter_priority(
                d,
                int(d.get("sink_count", 0)),
            )
        enriched.append(d)
    enriched.sort(key=lambda x: x["priority"], reverse=True)
    return enriched
