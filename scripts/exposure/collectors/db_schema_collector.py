# -*- coding: utf-8 -*-
"""db_schema_collector.py — 数据库结构推断采集器。

从代码（JPA 实体 / MyBatis mapper / Jooq 生成类）推断数据库结构。
不调 ast-grep、不连接真实数据库。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


_SENSITIVE_FIELD = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[-_]?key|"
    r"private[-_]?key|priv[-_]?key|salt|credential)",
    re.IGNORECASE,
)
_FIELD_AFTER_ANNOT = re.compile(
    r"@(?:Column|Id)(?:\([^)]*\))?\s*(?:[\w<>,\s.]+?)\s+(\w+)\s*[;=,)]"
)
_TABLE_NAME = re.compile(r'@Table\s*\(\s*(?:name\s*=\s*)?"([^"]+)"')
_ENTITY_CLASS = re.compile(r'(?:public\s+)?class\s+(\w+)\b')
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_MAPPER_NAMESPACE = re.compile(r'namespace\s*=\s*"([^"]+)"')
_MAPPER_RESULT_COL = re.compile(
    r'<(?:result|id)\s+[^>]*column\s*=\s*"([^"]+)"', re.IGNORECASE,
)


@register_collector("db_schema")
class DbSchemaCollector:
    """数据库结构采集器：JPA + MyBatis + Jooq。"""
    name = "db_schema_collector"
    asset_type = "db_schema"

    def is_available(self, ctx: ExposureContext) -> bool:
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        root = ctx.project_root
        items: list[dict[str, Any]] = []
        if root.exists():
            items.extend(self._scan_jpa_entities(root))
            items.extend(self._scan_mybatis(root))
            items.extend(self._scan_jooq(root))
        return CollectorResult(
            asset_type=self.asset_type,
            source="file scan (JPA @Entity / MyBatis mapper.xml / Jooq generated)",
            items=items,
            stats={
                "total": len(items),
                "by_source": self._count_by(items, "source"),
                "sensitive_field_count": sum(
                    1 for i in items if i.get("has_sensitive_field")
                ),
            },
            degraded=False,
        )

    @staticmethod
    def _scan_jpa_entities(root: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in root.rglob("*.java"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "@Entity" not in text:
                continue
            cols = DbSchemaCollector._extract_fields(text)
            table_match = _TABLE_NAME.search(text)
            out.append({
                "source": "jpa_entity",
                "entity_fqn": DbSchemaCollector._derive_fqn(f, text),
                "table_name": table_match.group(1) if table_match else "",
                "columns": cols,
                "has_sensitive_field": any(c.get("sensitive") for c in cols),
                "file": str(f),
            })
        return out

    @staticmethod
    def _scan_mybatis(root: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in root.rglob("*.xml"):
            if not f.name.lower().endswith("mapper.xml"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            ns_match = _MAPPER_NAMESPACE.search(text)
            cols = [
                {"name": c, "sensitive": bool(_SENSITIVE_FIELD.search(c))}
                for c in _MAPPER_RESULT_COL.findall(text)
            ]
            out.append({
                "source": "mybatis_mapper",
                "entity_fqn": ns_match.group(1) if ns_match else f.stem,
                "table_name": f.stem.removesuffix("Mapper"),
                "columns": cols,
                "has_sensitive_field": any(c["sensitive"] for c in cols),
                "file": str(f),
            })
        return out

    @staticmethod
    def _scan_jooq(root: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in root.rglob("*.java"):
            if not (f.name.endswith("Table.java") or f.name == "Tables.java"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "org.jooq" not in text:
                continue
            cls_match = _ENTITY_CLASS.search(text)
            if not cls_match:
                continue
            out.append({
                "source": "jooq",
                "entity_fqn": DbSchemaCollector._derive_fqn(f, text),
                "table_name": cls_match.group(1).removesuffix("Table"),
                "columns": [],
                "has_sensitive_field": False,
                "file": str(f),
            })
        return out

    @staticmethod
    def _extract_fields(java_text: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        cols: list[dict[str, Any]] = []
        for m in _FIELD_AFTER_ANNOT.finditer(java_text):
            name = m.group(1)
            if name in seen or name in {"get", "set", "is", "return"}:
                continue
            seen.add(name)
            cols.append({
                "name": name,
                "sensitive": bool(_SENSITIVE_FIELD.search(name)),
            })
        return cols

    @staticmethod
    def _derive_fqn(f: Path, java_text: str) -> str:
        pkg_match = _PACKAGE.search(java_text)
        pkg = pkg_match.group(1) if pkg_match else ""
        return f"{pkg}.{f.stem}" if pkg else f.stem

    @staticmethod
    def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in items:
            v = str(it.get(key, ""))
            out[v] = out.get(v, 0) + 1
        return out
