# -*- coding: utf-8 -*-
"""test_codegraph_collector.py — codegraph_collector 的单元测试。

TDD 红阶段：定义 codegraph_collector 必须满足的行为。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.codegraph_collector import CodegraphCollector


# ============================================================
# 辅助：创建临时 codegraph SQLite
# ============================================================

def _create_codegraph_db(db_path: Path) -> None:
    """在 db_path 创建一个含 nodes 表的最小 codegraph SQLite。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            name TEXT,
            qualified_name TEXT,
            file_path TEXT,
            start_line INTEGER,
            end_line INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS edges (
            source INTEGER,
            target INTEGER,
            kind TEXT
        )"""
    )
    # 插入测试数据（每类至少 2 条）
    conn.executemany(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, start_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            # sql
            (1, "method", "executeQuery", "com.example.Dao::executeQuery",
             "src/main/java/com/example/Dao.java", 30),
            (2, "method", "executeUpdate", "com.example.Dao::executeUpdate",
             "src/main/java/com/example/Dao.java", 45),
            # startup
            (3, "class", "AppConfig", "com.example.AppConfiguration",
             "src/main/java/com/example/AppConfiguration.java", 5),
            (4, "class", "Application", "com.example.Application",
             "src/main/java/com/example/Application.java", 10),
            # reflection
            (5, "method", "forName", "com.example.Util::forName",
             "src/main/java/com/example/Util.java", 20),
            (6, "method", "invoke", "com.example.Util::invoke",
             "src/main/java/com/example/Util.java", 35),
            # serialization
            (7, "method", "readObject", "com.example.Handler::readObject",
             "src/main/java/com/example/Handler.java", 50),
            (8, "method", "readValue", "com.example.Handler::readValue",
             "src/main/java/com/example/Handler.java", 60),
            # dynamic_route
            (9, "method", "registerMapping", "com.example.Router::registerMapping",
             "src/main/java/com/example/Router.java", 25),
            (10, "method", "route", "com.example.Gateway::RouterFunction",
             "src/main/java/com/example/Gateway.java", 40),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def codegraph_db(tmp_path: Path) -> Path:
    """创建临时 codegraph SQLite 并返回路径。"""
    db = tmp_path / "codegraph.db"
    _create_codegraph_db(db)
    return db


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """创建最小 Java 项目目录（含源文件用于 snippet 提取）。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    # 确保源文件行数覆盖 codegraph DB 中的 start_line（最大 60）
    dao_lines = [f"dao_line{i}" for i in range(1, 70)]
    dao_lines[29] = "executeQuery code"  # line 30
    dao_lines[44] = "executeUpdate code"  # line 45
    (src / "Dao.java").write_text("\n".join(dao_lines), encoding="utf-8")

    util_lines = [f"util_line{i}" for i in range(1, 70)]
    util_lines[19] = "forName call"  # line 20
    util_lines[34] = "invoke call"  # line 35
    (src / "Util.java").write_text("\n".join(util_lines), encoding="utf-8")

    handler_lines = [f"handler_line{i}" for i in range(1, 70)]
    handler_lines[49] = "readObject code"  # line 50
    handler_lines[59] = "readValue code"  # line 60
    (src / "Handler.java").write_text("\n".join(handler_lines), encoding="utf-8")
    return tmp_path


@pytest.fixture
def ctx(tmp_project: Path, codegraph_db: Path, tmp_path: Path) -> ExposureContext:
    """带 codegraph_db 的 ExposureContext。"""
    return ExposureContext(
        project_root=tmp_project,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
        codegraph_db=codegraph_db,
    )


@pytest.fixture
def ctx_no_db(tmp_project: Path, tmp_path: Path) -> ExposureContext:
    """无 codegraph_db 的 ExposureContext。"""
    return ExposureContext(
        project_root=tmp_project,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
        codegraph_db=None,
    )


# ============================================================
# 协议与元数据测试
# ============================================================

class TestCodegraphCollectorContract:
    """CodegraphCollector 必须满足 Collector 协议。"""

    def test_implements_collector_protocol(self):
        assert isinstance(CodegraphCollector(), Collector)

    def test_has_correct_metadata(self):
        c = CodegraphCollector()
        assert c.name == "codegraph_collector"
        assert c.asset_type == "codegraph"

    def test_unavailable_when_no_codegraph_db(self, ctx_no_db):
        assert CodegraphCollector().is_available(ctx_no_db) is False

    def test_available_when_codegraph_db_exists(self, ctx):
        assert CodegraphCollector().is_available(ctx) is True

    def test_unavailable_when_db_not_sqlite(self, tmp_path):
        bad_db = tmp_path / "not_sqlite.db"
        bad_db.write_text("garbage", encoding="utf-8")
        ctx = ExposureContext(
            project_root=tmp_path, group_id="x",
            loop_audit_dir=tmp_path / "loop_audit",
            codegraph_db=bad_db,
        )
        assert CodegraphCollector().is_available(ctx) is False


# ============================================================
# 分类查询测试
# ============================================================

class TestCodegraphCollectorQueries:

    def test_query_sql_statements(self, ctx):
        result = CodegraphCollector().collect(ctx)
        sql_items = [it for it in result.items if it["category"] == "sql"]
        assert len(sql_items) >= 2
        assert all(it["fqn"] for it in sql_items)
        assert all(it["nodes_id"] is not None for it in sql_items)
        # 检查 snippet 非空（Dao.java 存在于 tmp_project）
        assert any(it["snippet"] for it in sql_items)

    def test_query_reflection_calls(self, ctx):
        result = CodegraphCollector().collect(ctx)
        ref_items = [it for it in result.items if it["category"] == "reflection"]
        assert len(ref_items) >= 2
        assert any("forName" in it["fqn"] or "invoke" in it["fqn"] for it in ref_items)

    def test_query_serialization_sinks(self, ctx):
        result = CodegraphCollector().collect(ctx)
        ser_items = [it for it in result.items if it["category"] == "serialization"]
        assert len(ser_items) >= 2
        assert any("readObject" in it["fqn"] or "readValue" in it["fqn"] for it in ser_items)

    def test_category_distribution_stats(self, ctx):
        result = CodegraphCollector().collect(ctx)
        assert "by_category" in result.stats
        cats = result.stats["by_category"]
        assert cats.get("sql", 0) >= 2
        assert cats.get("reflection", 0) >= 2
        assert cats.get("serialization", 0) >= 2
        assert result.stats["total"] >= 8

    def test_collect_returns_collector_result(self, ctx):
        result = CodegraphCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert result.asset_type == "codegraph"
        assert result.source.startswith("codegraph")
