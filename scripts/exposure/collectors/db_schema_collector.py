# -*- coding: utf-8 -*-
"""db_schema_collector.py — 数据库结构文件采集器。

单一职责：扫描 Java 项目中的数据库结构相关文件（JPA 实体 / MyBatis mapper / Jooq 生成类），
记录文件路径，缓存完整文件内容到 Memurai 供后续 AI 分析。

不做：
- 不解析 JPA 实体提取列名
- 不解析 MyBatis mapper 提取 result column
- 不解析 Jooq 生成类提取表名

采集来源：
1. @Entity 注解的 Java 文件（JPA 实体）
2. *Mapper.xml 文件（MyBatis mapper）
3. *Table.java / Tables.java 含 org.jooq 的文件（Jooq 生成类）

输出：
- items: [{file, type, size_bytes, sha256}]
- Memurai 缓存: {groupId}:db_schema:{relative_file_path} → 完整文件内容
- stats: {total_files, by_type, cached}

参考 RFC-0001 §3.2 / AC-1
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


@register_collector("db_schema")
class DbSchemaCollector:
    """数据库结构文件采集器：扫描文件 → 记录路径 → 缓存文件内容。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """
    name = "db_schema_collector"
    asset_type = "db_schema"

    def is_available(self, ctx: ExposureContext) -> bool:
        """无外部依赖，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描数据库结构相关文件，记录路径，缓存文件内容。"""
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type,
                source="file scan (JPA @Entity / MyBatis mapper.xml / Jooq generated)",
                items=[],
                stats={"total_files": 0, "by_type": {}, "cached": 0},
            )

        # 1. 扫描数据库结构相关文件
        items = self._scan_files(root)

        # 2. 缓存文件内容到 Memurai
        cached_count = self._cache_files(ctx, items)

        # 3. 统计
        by_type: dict[str, int] = {}
        for it in items:
            t = it.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return CollectorResult(
            asset_type=self.asset_type,
            source="file scan (JPA @Entity / MyBatis mapper.xml / Jooq generated)",
            items=items,
            stats={
                "total_files": len(items),
                "by_type": by_type,
                "cached": cached_count,
            },
        )

    # ----------------------------------------------------------
    # 文件扫描
    # ----------------------------------------------------------
    def _scan_files(self, root: Path) -> list[dict[str, Any]]:
        """扫描 JPA 实体、MyBatis mapper、Jooq 生成类文件，记录路径。"""
        items: list[dict[str, Any]] = []

        # JPA 实体：含 @Entity 注解的 .java 文件
        for f in root.rglob("*.java"):
            if self._should_skip(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "@Entity" not in text:
                continue
            item = self._make_item(f, root, "jpa_entity")
            if item:
                items.append(item)

        # MyBatis mapper：*Mapper.xml 文件
        for f in root.rglob("*.xml"):
            if self._should_skip(f):
                continue
            if not f.name.lower().endswith("mapper.xml"):
                continue
            item = self._make_item(f, root, "mybatis_mapper")
            if item:
                items.append(item)

        # Jooq 生成类：*Table.java / Tables.java 含 org.jooq 的文件
        for f in root.rglob("*.java"):
            if self._should_skip(f):
                continue
            if not (f.name.endswith("Table.java") or f.name == "Tables.java"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "org.jooq" not in text:
                continue
            item = self._make_item(f, root, "jooq")
            if item:
                items.append(item)

        return items

    def _make_item(self, f: Path, root: Path, type_tag: str) -> dict[str, Any] | None:
        """构造单个 item：记录相对路径、类型、大小、sha256。"""
        try:
            relative = f.relative_to(root)
        except ValueError:
            return None

        sha256 = self._compute_sha256(f)
        try:
            size_bytes = f.stat().st_size
        except OSError:
            size_bytes = 0

        return {
            "file": str(relative),
            "type": type_tag,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }

    # ----------------------------------------------------------
    # Memurai 缓存
    # ----------------------------------------------------------
    def _cache_files(
        self, ctx: ExposureContext, items: list[dict[str, Any]]
    ) -> int:
        """缓存数据库结构文件内容到 Memurai。"""
        if not items:
            return 0

        try:
            from scripts.redis.memurai_client import Memurai
            memurai = Memurai()
        except (ImportError, Exception):
            return 0

        cached = 0
        root = ctx.project_root
        group_id = ctx.group_id

        for item in items:
            file_path = root / item["file"]
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            key = f"{group_id}:db_schema:{item['file']}"

            try:
                memurai.set(key, content)
                cached += 1
            except Exception:
                import logging; logging.getLogger("db_schema_collector").warning("memurai set failed for %s", key)

        return cached

    # ----------------------------------------------------------
    # sha256 计算
    # ----------------------------------------------------------
    @staticmethod
    def _compute_sha256(fp: Path) -> str:
        """计算文件 sha256 哈希值（hex digest）。"""
        h = hashlib.sha256()
        try:
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------
    @staticmethod
    def _should_skip(path: Path) -> bool:
        """跳过测试文件和生成代码。"""
        parts = path.parts
        skip_dirs = {"test", "tests", "target", "build", "generated", "node_modules", ".git"}
        return any(part in skip_dirs for part in parts)
