# -*- coding: utf-8 -*-
"""route_collector.py — 路由采集器（编排器）。

薄 wrapper：仅串联 RuleLoader → AstGrepScanner → RouteEnricher 三个单一职责模块，
实现 Collector 协议。所有业务逻辑在子包 route/ 中。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector
from .route.astgrep_scanner import AstGrepScanner
from .route.enricher import RouteEnricher
from .route.rule_loader import RuleLoader

# 规则目录：collectors/rules/（YAML 规则文件路径不变）
_RULES_DIR: Path = Path(__file__).parent / "rules"


@register_collector("route")
class RouteCollector:
    """路由采集器：ast-grep 扫描路由注解。"""

    name = "route_collector"
    asset_type = "route"

    def is_available(self, ctx: ExposureContext) -> bool:
        """需要 ast-grep 在 PATH。"""
        return shutil.which("ast-grep") is not None

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """采集流程：加载规则 → ast-grep 扫描 → 富化。"""
        rules = RuleLoader.load(_RULES_DIR)
        # 主扫描只含 class_rules + method_rules；param_rules 由 scan_params 单独处理
        scan_rules = {
            "class_rules": rules["class_rules"],
            "method_rules": rules["method_rules"],
            "param_rules": [],
        }
        rule_yaml = RuleLoader.build_ast_grep_rule_yaml(scan_rules)
        http_map = RuleLoader.build_http_method_map(scan_rules)
        raw_routes = AstGrepScanner.scan(ctx, rule_yaml, http_map)

        param_index = RouteEnricher.scan_params(ctx, rules.get("param_rules") or [])
        items = RouteEnricher.enrich_batch(raw_routes, ctx, http_map, param_index)

        degraded_count = sum(1 for it in items if it.get("nodes_id") is None)
        degraded = degraded_count > 0 and ctx.codegraph_db is None
        return CollectorResult(
            asset_type=self.asset_type,
            source="ast-grep YAML rules + codegraph nodes_id",
            items=items,
            stats={
                "total": len(items),
                "degraded_md5": degraded_count if ctx.codegraph_db is None else 0,
                "by_http_method": RouteEnricher.count_by_method(items),
            },
            degraded=degraded,
        )
