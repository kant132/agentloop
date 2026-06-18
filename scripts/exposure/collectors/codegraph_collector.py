# -*- coding: utf-8 -*-
"""codegraph_collector.py — codegraph 代码资产采集器。

单一职责：通过 codegraph SQLite 查询关键代码资产，输出标准化条目列表。

5 类资产：sql / startup / reflection / serialization / dynamic_route

不做：不调 memurai、不写文件、不做调用链追踪。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector

# 5 类资产的 SQL 查询（codegraph nodes 表 schema: id,kind,name,qualified_name,file_path,start_line）
_CATEGORY_QUERIES: dict[str, str] = {
    "sql":
        "SELECT n.id,n.qualified_name,n.file_path,n.start_line FROM nodes n "
        "WHERE n.kind='method' AND("
        "n.name LIKE '%executeQuery%' OR n.name LIKE '%executeUpdate%' "
        "OR n.name LIKE '%execute%' OR n.qualified_name LIKE '%::Statement%' "
        "OR n.qualified_name LIKE '%::JdbcTemplate%' "
        "OR n.name LIKE '%@Select%' OR n.name LIKE '%@Query%') ORDER BY n.qualified_name",
    "startup":
        "SELECT n.id,n.qualified_name,n.file_path,n.start_line FROM nodes n "
        "WHERE n.kind='class' AND("
        "n.qualified_name LIKE '%Configuration%' "
        "OR n.qualified_name LIKE '%SpringBootApplication%' "
        "OR n.qualified_name LIKE '%EnableAutoConfiguration%') ORDER BY n.qualified_name",
    "reflection":
        "SELECT n.id,n.qualified_name,n.file_path,n.start_line FROM nodes n "
        "WHERE n.kind='method' AND("
        "n.name LIKE '%forName%' OR n.name LIKE '%invoke%' OR n.name LIKE '%getMethod%' "
        "OR n.qualified_name LIKE '%::Class%' OR n.qualified_name LIKE '%::Method%') "
        "ORDER BY n.qualified_name",
    "serialization":
        "SELECT n.id,n.qualified_name,n.file_path,n.start_line FROM nodes n "
        "WHERE n.kind='method' AND("
        "n.name LIKE '%readObject%' OR n.name LIKE '%readValue%' "
        "OR n.qualified_name LIKE '%::ObjectInputStream%' "
        "OR n.qualified_name LIKE '%::ObjectMapper%' "
        "OR n.qualified_name LIKE '%::XMLDecoder%' "
        "OR n.qualified_name LIKE '%::SnakeYaml%') ORDER BY n.qualified_name",
    "dynamic_route":
        "SELECT n.id,n.qualified_name,n.file_path,n.start_line FROM nodes n "
        "WHERE n.kind='method' AND("
        "n.name LIKE '%registerMapping%' "
        "OR n.qualified_name LIKE '%::RouterFunction%' "
        "OR n.qualified_name LIKE '%::HandlerMapping%') ORDER BY n.qualified_name",
}

_SNIPPET_MARGIN = 3


@register_collector("codegraph")
class CodegraphCollector:
    """codegraph 代码资产采集器。依据 Collector 协议。"""
    name = "codegraph_collector"
    asset_type = "codegraph"

    def is_available(self, ctx: ExposureContext) -> bool:
        """需要 ctx.codegraph_db 存在且是有效 SQLite 文件。"""
        if not ctx.codegraph_db or not ctx.codegraph_db.exists():
            return False
        try:
            conn = sqlite3.connect(f"file:{ctx.codegraph_db}?mode=ro", uri=True)
            conn.execute("SELECT 1 FROM nodes LIMIT 1")
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
            source="codegraph SQLite pattern search",
            items=items,
            stats={"total": len(items), "by_category": self._count_by_category(items)},
            errors=errors,
        )

    def _query_category(self, ctx: ExposureContext, sql: str, category: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(f"file:{ctx.codegraph_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        results: list[dict[str, Any]] = []
        for row in rows:
            fqn = row["qualified_name"] or ""
            fp = row["file_path"] or ""
            line = row["start_line"] or 0
            nid = row["id"]
            results.append({
                "fqn": fqn, "category": category, "nodes_id": nid,
                "file": fp, "line": line,
                "snippet": self._extract_snippet(ctx.project_root, fp, line),
            })
        return results

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
