# -*- coding: utf-8 -*-
"""codegraph_collector.py — 代码资产采集器（jar-analyzer 模式）。

单一职责：通过 jar-analyzer.db 的 method_table 查询关键代码资产，输出标准化条目列表。

5 类资产：sql / startup / reflection / serialization / dynamic_route

不做：不调 memurai、不写文件、不做调用链追踪。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector

# 5 类资产的 SQL 查询（jar-analyzer method_table schema: method_id, class_name, method_name, method_desc, line_number）
_CATEGORY_QUERIES: dict[str, str] = {
    "sql":
        "SELECT method_id, class_name, method_name, method_desc, line_number "
        "FROM method_table WHERE "
        "method_name LIKE '%executeQuery%' OR method_name LIKE '%executeUpdate%' "
        "OR method_name LIKE '%execute%' OR class_name LIKE '%Statement%' "
        "OR class_name LIKE '%JdbcTemplate%' "
        "OR method_name LIKE '%@Select%' OR method_name LIKE '%@Query%' "
        "ORDER BY class_name, method_name",
    "startup":
        "SELECT method_id, class_name, method_name, method_desc, line_number "
        "FROM method_table WHERE "
        "class_name LIKE '%Configuration%' "
        "OR class_name LIKE '%SpringBootApplication%' "
        "OR class_name LIKE '%EnableAutoConfiguration%' "
        "ORDER BY class_name, method_name",
    "reflection":
        "SELECT method_id, class_name, method_name, method_desc, line_number "
        "FROM method_table WHERE "
        "method_name LIKE '%forName%' OR method_name LIKE '%invoke%' OR method_name LIKE '%getMethod%' "
        "OR class_name LIKE '%Class%' OR class_name LIKE '%Method%' "
        "ORDER BY class_name, method_name",
    "serialization":
        "SELECT method_id, class_name, method_name, method_desc, line_number "
        "FROM method_table WHERE "
        "method_name LIKE '%readObject%' OR method_name LIKE '%readValue%' "
        "OR class_name LIKE '%ObjectInputStream%' "
        "OR class_name LIKE '%ObjectMapper%' "
        "OR class_name LIKE '%XMLDecoder%' "
        "OR class_name LIKE '%SnakeYaml%' "
        "ORDER BY class_name, method_name",
    "dynamic_route":
        "SELECT method_id, class_name, method_name, method_desc, line_number "
        "FROM method_table WHERE "
        "method_name LIKE '%registerMapping%' "
        "OR class_name LIKE '%RouterFunction%' "
        "OR class_name LIKE '%HandlerMapping%' "
        "ORDER BY class_name, method_name",
}

_SNIPPET_MARGIN = 3


@register_collector("codegraph")
class CodegraphCollector:
    """代码资产采集器。依据 Collector 协议。"""
    name = "codegraph_collector"
    asset_type = "codegraph"

    def is_available(self, ctx: ExposureContext) -> bool:
        """需要 ctx.jar_analyzer_db 存在且是有效 SQLite 文件。"""
        if not ctx.jar_analyzer_db or not ctx.jar_analyzer_db.exists():
            return False
        try:
            conn = sqlite3.connect(str(ctx.jar_analyzer_db))
            conn.execute("SELECT 1 FROM method_table LIMIT 1")
            conn.close()
            return True
        except sqlite3.Error:
            return False

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        items: list[dict[str, Any]] = []
        errors: list[str] = []
        for category, sql in _CATEGORY_QUERIES.items():
            try:
                rows = self._query_category(ctx, sql, category)
                items.extend(rows)
            except sqlite3.Error as exc:
                errors.append(f"{category}: {exc}")
        return CollectorResult(
            asset_type=self.asset_type,
            source="jar-analyzer SQLite pattern search",
            items=items,
            stats={"total": len(items), "by_category": self._count_by_category(items)},
            errors=errors,
        )

    def _query_category(self, ctx: ExposureContext, sql: str, category: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(ctx.jar_analyzer_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        results: list[dict[str, Any]] = []
        for row in rows:
            cn = row["class_name"] or ""
            mn = row["method_name"] or ""
            # jar-analyzer class_name 用 '/' 分隔, 转为 '.' 格式
            fqn = f"{cn.replace('/', '.')}::{mn}" if cn else mn
            # 通过 class_file_table 查文件路径
            file_path = self._lookup_file_path(ctx, cn)
            line = row["line_number"] or 0
            nid = str(row["method_id"])
            results.append({
                "fqn": fqn, "category": category, "nodes_id": nid,
                "file": file_path or "", "line": line,
                "snippet": self._extract_snippet(ctx.project_root, file_path or "", line),
            })
        return results

    @staticmethod
    def _lookup_file_path(ctx: ExposureContext, class_name: str) -> str | None:
        """从 jar-analyzer class_file_table 查 class_name → path_str。"""
        if not class_name:
            return None
        try:
            conn = sqlite3.connect(str(ctx.jar_analyzer_db))
            row = conn.execute(
                "SELECT path_str FROM class_file_table WHERE class_name = ? LIMIT 1",
                (class_name,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _extract_snippet(project_root: Path, file_path: str, line: int) -> str:
        """从源文件提取前后 _SNIPPET_MARGIN 行，失败返回空字符串。"""
        if not file_path or not line:
            return ""
        src = project_root / file_path
        if not src.exists():
            return ""
        try:
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        start = max(1, line - _SNIPPET_MARGIN) - 1
        end = min(len(lines), line + _SNIPPET_MARGIN)
        return "\n".join(lines[start:end])

    @staticmethod
    def _count_by_category(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in items:
            counts[it.get("category", "")] = counts.get(it.get("category", ""), 0) + 1
        return counts
