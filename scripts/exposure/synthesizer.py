# -*- coding: utf-8 -*-
"""synthesizer.py — 综合阶段：将多个 collector 结果清洗为高可用资产清单。

职责：
- 读取 {exposure_dir}/*.json 所有 collector 输出
- 去重（按 sig_hash/fqn/file:line）
- 归类汇总（按业务域、风险等级）
- 输出 exposure_assets.json

参考 RFC-0001 §3.2 / §3.4
"""
from __future__ import annotations

from typing import Any

from .contracts import ExposureContext, Synthesizer


class DefaultSynthesizer(Synthesizer):
    """默认综合器：去重 + 归类 + 风险分级。

    输出结构::

        {
          "total_assets": 200,
          "by_type": {"route": 145, "config": 30, ...},
          "by_risk": {"high": 50, "medium": 100, "low": 50},
          "assets": [...],
          "dedup_report": {"removed_duplicates": 12}
        }
    """
    name = "default_synthesizer"

    def synthesize(
        self, results: list[dict[str, Any]], ctx: ExposureContext
    ) -> dict[str, Any]:
        all_items: list[dict[str, Any]] = []
        by_type: dict[str, int] = {}

        for r in results:
            asset_type = r.get("asset_type", "unknown")
            items = r.get("items", []) or []
            for it in items:
                it.setdefault("_source_type", asset_type)
            all_items.extend(items)
            by_type[asset_type] = by_type.get(asset_type, 0) + len(items)

        # 去重：按 (fqn, file, line) 元组键
        seen: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        removed = 0
        for it in all_items:
            key = (
                it.get("fqn") or it.get("name") or "",
                it.get("file") or "",
                it.get("line") or 0,
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(it)

        # 风险分级（简化版：依据 asset_type 与字段标记）
        by_risk = {"high": 0, "medium": 0, "low": 0}
        for it in deduped:
            risk = self._classify_risk(it)
            it["_risk"] = risk
            by_risk[risk] += 1

        return {
            "total_assets": len(deduped),
            "by_type": by_type,
            "by_risk": by_risk,
            "assets": deduped,
            "dedup_report": {
                "before": len(all_items),
                "after": len(deduped),
                "removed_duplicates": removed,
            },
        }

    @staticmethod
    def _classify_risk(item: dict[str, Any]) -> str:
        """简易风险分级。

        高：含 sink 调用、敏感操作、修改类请求
        中：普通业务逻辑、有外部入参
        低：GET 无参、内部工具方法
        """
        atype = item.get("_source_type", "")
        if atype in ("sensitive_info", "auth_code") or item.get("is_sink"):
            return "high"
        if atype == "route":
            method = (item.get("http_method") or "GET").upper()
            if method in ("POST", "PUT", "DELETE", "PATCH"):
                return "high"
            if item.get("has_external_param"):
                return "medium"
            return "low"
        if atype == "config":
            return "medium" if item.get("contains_secret") else "low"
        return "medium"
