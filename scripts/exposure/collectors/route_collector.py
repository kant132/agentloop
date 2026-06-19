# -*- coding: utf-8 -*-
"""route_collector.py — 路由采集器（编排器）。

新架构：ast-grep 定位 + javaparser RouteExtractor 精确解析。

流程：
1. FileLocator 用 ast-grep 快速定位含路由注解的 .java 文件（不做参数解析）
2. JavaparserScanner.scan_directory 调用 java -jar javaparser.jar --routes
   精确解析路径拼接 + HTTP method 展开（含 {GET,POST} 数组）
3. RouteEnricher.enrich_routes 只做 codegraph nodes_id + has_external_param 富化

替代旧 astgrep_scanner.parse_output 的正则路径解析（不可靠）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector
from .route.enricher import RouteEnricher
from .route.file_locator import FileLocator
from .route.javaparser_scanner import JavaparserScanner


@register_collector("route")
class RouteCollector:
    """路由采集器：ast-grep 定位 + javaparser 精确解析。"""

    name = "route_collector"
    asset_type = "route"

    def is_available(self, ctx: ExposureContext) -> bool:
        """需要 javaparser JAR 或 ast-grep 至少一个。

        ast-grep 仅用于加速文件定位；缺失时降级为 glob 全量扫描。
        """
        return (
            shutil.which("ast-grep") is not None
            or shutil.which("java") is not None
        )

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """采集流程：定位文件 → javaparser 解析 → 富化。"""
        # 1. 定位含路由注解的文件
        java_files = FileLocator.locate(ctx.project_root)

        # 2. javaparser 精确解析（路径拼接 + method 数组展开由 JAR 完成）
        routes = JavaparserScanner.scan_directory(java_files, ctx.project_root)

        # 3. 富化（codegraph nodes_id + has_external_param）
        items = RouteEnricher.enrich_routes(routes, ctx)

        # 降级判定：nodes_id 缺失或 javaparser JAR 不存在
        degraded = any(it.get("nodes_id") is None for it in items) \
            or not JavaparserScanner.jar_path().exists()
        return CollectorResult(
            asset_type=self.asset_type,
            source="javaparser RouteExtractor + ast-grep file location",
            items=items,
            stats={
                "total": len(items),
                "files_scanned": len(java_files),
                "by_http_method": RouteEnricher.count_by_method(items),
            },
            degraded=degraded,
        )
