# -*- coding: utf-8 -*-
"""enricher.py — 路由条目富化：HTTP方法、nodes_id、params。

两套入口：
- ``enrich``/``enrich_batch``/``scan_params``：旧 ast-grep 正则路径，保留向后兼容
- ``enrich_routes``：新 javaparser RouteExtractor 路径，full_url + http_methods
  列表已由 javaparser 解析，enricher 只补 nodes_id/has_external_param

依赖：
- contracts.ExposureContext
- RuleLoader（构造 param 扫描的 any-rule yaml，仅旧路径用）
- AstGrepScanner（参数注解扫描走同一子进程封装，仅旧路径用）

``scan_params`` 返回 ``{file||: [{param_type, name}, ...]}``，enrich 时按
file 取回（参数注解条目无 method_name 信息，与原实现一致）。
"""
from __future__ import annotations


import json
import re
import shutil
from pathlib import Path
from typing import Any

from ...contracts import ExposureContext
from .astgrep_scanner import AstGrepScanner
from .rule_loader import RuleLoader


class RouteEnricher:
    """富化路由条目：HTTP方法、nodes_id、params。"""

    @staticmethod
    def enrich(
        raw: dict[str, Any],
        ctx: ExposureContext,
        http_method_map: dict[str, str],
        param_index: dict[str, list[dict]] | None = None,
    ) -> dict[str, Any]:
        """富化单条路由。

        补字段：fqn（缺失时推导）、http_method、has_external_param、params、
        nodes_id（无 codegraph 时为 None）。
        """
        item = dict(raw)
        # fqn: 缺失时由 file + class_fqn 推导（ast-grep 命中未带 fqn 时）
        if not item.get("fqn"):
            item["fqn"] = RouteEnricher.derive_fqn(
                item.get("file", ""),
                item.get("class_fqn") or item.get("class_name"),
            )
        # HTTP 方法
        item["http_method"] = http_method_map.get(item.get("annotation", ""), "ANY")
        # 外部入参标记（粗判：注解参数或方法名非空）
        args = item.get("args") or []
        item["has_external_param"] = bool(args) or item.get("method_name", "") != ""
        # 请求参数列表：[{param_type, name}]
        item["params"] = RouteEnricher._lookup_params(item, param_index)
        # nodes_id（codegraph 可用时）
        nodes_id = RouteEnricher.lookup_nodes_id(item, ctx)
        item["nodes_id"] = nodes_id
        return item

    @staticmethod
    def enrich_batch(
        raws: list[dict[str, Any]],
        ctx: ExposureContext,
        http_method_map: dict[str, str],
        param_index: dict[str, list[dict]] | None = None,
    ) -> list[dict[str, Any]]:
        """批量富化。"""
        return [
            RouteEnricher.enrich(r, ctx, http_method_map, param_index)
            for r in raws
        ]

    @staticmethod
    def enrich_routes(
        routes: list[dict[str, Any]],
        ctx: ExposureContext,
    ) -> list[dict[str, Any]]:
        """富化 javaparser RouteExtractor 输出的路由列表。

        javaparser 已提供 full_url（类+方法拼接）和 http_methods（列表，含
        ``{GET,POST}`` 数组展开），enricher 只补：
        - ``nodes_id`` （codegraph 反查）
        - ``has_external_param`` （从 full_url 是否含 ``{param}`` 推断）
        - ``fqn`` （统一为 method_fqn）

        保留 javaparser 提供的 full_url/http_methods/class_base_path 等字段。
        """
        items: list[dict[str, Any]] = []
        for route in routes:
            item = dict(route)
            # nodes_id 反查（codegraph 可用）
            nodes_id = RouteEnricher.lookup_nodes_id(
                {
                    "fqn": route.get("method_fqn", ""),
                    "method_name": route.get("method_name", ""),
                },
                ctx,
            )
            item["nodes_id"] = nodes_id
            # has_external_param: 路径模板含 {param} 占位
            url = route.get("full_url", "") or ""
            item["has_external_param"] = "{" in url
            # fqn 字段统一指向 method_fqn（下游消费者按 fqn 取值）
            item["fqn"] = route.get("method_fqn", "")
            items.append(item)
        return items

    @staticmethod
    def derive_fqn(file_path: str, class_hint: str | None) -> str:
        """从文件路径推导 FQN，回退到 class_hint。"""
        if class_hint:
            return class_hint
        if not file_path:
            return ""
        # 取 src/main/java 之后的路径
        m = re.search(r"(?:src/main/java|src/test/java)[/\\](.+?)\.java$", file_path)
        if m:
            return m.group(1).replace("\\", ".").replace("/", ".")
        # 退化：用文件 stem
        return Path(file_path).stem

    @staticmethod
    def _lookup_params(
        raw: dict[str, Any],
        param_index: dict[str, list[dict]] | None,
    ) -> list[dict[str, Any]]:
        """从 param 索引中按 file|fqn|method 取参数绑定信息。

        缺失或无索引时返回空列表（保持向后兼容）。
        """
        if not param_index:
            return []
        key = RouteEnricher._param_index_key(raw)
        return list(param_index.get(key, []))

    @staticmethod
    def _param_index_key(raw: dict[str, Any]) -> str:
        """构造参数索引键：file|fqn|method_name（容忍缺字段）。"""
        return "|".join([
            str(raw.get("file", "")),
            str(raw.get("fqn", "")),
            str(raw.get("method_name", "")),
        ])

    @staticmethod
    def lookup_nodes_id(
        raw: dict[str, Any], ctx: ExposureContext
    ) -> str | None:
        """通过 codegraph SQLite 用 file_path LIKE 模糊匹配 + 行号范围查 nodes.id。

        用 LIKE '%包路径末尾4段' 匹配，天然兼容绝对路径和相对路径。
        """
        if not ctx.codegraph_db or not ctx.codegraph_db.exists():
            return None
        import sqlite3

        file_path = raw.get("file") or ""
        annotation_line = raw.get("start_line") or raw.get("line") or 0
        if not file_path or annotation_line < 1:
            return None

        # 统一路径分隔符为正斜杠
        norm = file_path.replace("\\", "/")
        # 取路径末尾的包路径段作为 LIKE 匹配键（避免同名文件冲突）
        parts = norm.split("/")
        # 取最后 4 段：如 org/owasp/webgoat/xxx/Foo.java
        key = "/".join(parts[-4:]) if len(parts) >= 4 else norm

        like_pattern = f"%{key}"

        try:
            conn = sqlite3.connect(f"file:{ctx.codegraph_db}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT id FROM nodes "
                    "WHERE kind = 'method' "
                    "  AND file_path LIKE ? "
                    "  AND start_line <= ? AND end_line >= ? "
                    "ORDER BY (end_line - start_line) ASC "
                    "LIMIT 1",
                    (like_pattern, annotation_line, annotation_line),
                ).fetchone()
                return str(row[0]) if row else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def count_by_method(items: list[dict[str, Any]]) -> dict[str, int]:
        """按 HTTP 方法统计条目数。

        支持两种字段：
        - ``http_methods``（列表，javaparser 路径）→ 列表中每个方法计一次
        - ``http_method``（单值，旧 ast-grep 路径）→ 直接计一次
        缺失/空时计入 ``ANY``。
        """
        counts: dict[str, int] = {}
        for it in items:
            methods = it.get("http_methods")
            if methods and isinstance(methods, list):
                for m in methods:
                    key = m or "ANY"
                    counts[key] = counts.get(key, 0) + 1
            else:
                key = it.get("http_method") or "ANY"
                counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def scan_params(
        ctx: ExposureContext,
        param_rules: list[dict],
    ) -> dict[str, list[dict[str, Any]]]:
        """扫描 param_rules 中的请求参数注解，按 ``file||`` 索引。

        返回 ``{key: [{"param_type": ..., "name": ...}, ...]}``。
        命中失败（ast-grep 不可用、无规则、无项目目录）返回空 dict。

        走 AstGrepScanner.scan 子进程封装，不重复 subprocess 代码。
        """
        if not param_rules or not ctx.project_root.exists():
            return {}
        if shutil.which("ast-grep") is None:
            return {}

        rule_yaml = RuleLoader.build_ast_grep_rule_yaml({"param_rules": param_rules})
        if not rule_yaml:
            return {}

        # 不传 http_method_map：参数注解名不与路由注解重合，但保留全部命中
        raw_entries = AstGrepScanner.scan(ctx, rule_yaml)

        # 构建 注解名 → param_type 映射
        name_to_type: dict[str, str] = {}
        for r in param_rules:
            ptype = r.get("param_type", "unknown")
            for pattern in RuleLoader.expand_patterns(r):
                m = re.match(r"@(\w+)", pattern)
                if m:
                    name_to_type[m.group(1)] = ptype

        # 解析每条命中（AstGrepScanner.scan 不解析参数名，需自行从 text 抽取）
        index: dict[str, list[dict[str, Any]]] = {}
        for entry in raw_entries:
            ann_name = entry.get("annotation", "")
            if ann_name not in name_to_type:
                continue
            file_path = entry.get("file", "")
            # 参数名：从 args 列表（首项）抽字符串字面量；ast-grep scan 无 method_name
            name = ""
            args = entry.get("args") or []
            if args:
                str_match = re.search(r'"([^"]+)"', args[0])
                if str_match:
                    name = str_match.group(1)
            # 用 file 作索引键主体（参数注解无 method_name 信息）
            key = f"{file_path}||"
            index.setdefault(key, []).append({
                "param_type": name_to_type[ann_name],
                "name": name,
            })
        return index
