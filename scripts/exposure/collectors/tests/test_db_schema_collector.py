# -*- coding: utf-8 -*-
"""test_db_schema_collector.py — DbSchemaCollector 单元测试。

覆盖：协议、元数据、JPA 实体发现、MyBatis mapper 发现、Jooq 发现、空项目、Memurai 缓存。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.db_schema_collector import DbSchemaCollector


@pytest.fixture
def ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


class TestDbSchemaCollectorContract:
    def test_implements_collector_protocol(self):
        assert isinstance(DbSchemaCollector(), Collector)

    def test_metadata(self):
        c = DbSchemaCollector()
        assert c.name == "db_schema_collector"
        assert c.asset_type == "db_schema"

    def test_always_available(self, ctx):
        assert DbSchemaCollector().is_available(ctx) is True


class TestDbSchemaCollectorJpa:
    def test_jpa_entity_found(self, ctx, tmp_path):
        java = tmp_path / "src" / "com" / "x" / "User.java"
        java.parent.mkdir(parents=True)
        java.write_text(
            "package com.x;\n"
            "import jakarta.persistence.*;\n"
            "@Entity\n@Table(name = \"users\")\n"
            "public class User {\n"
            "  @Id private Long id;\n"
            "  @Column(name = \"pwd\") private String password;\n"
            "}\n",
            encoding="utf-8",
        )
        result = DbSchemaCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "jpa_entity"
        assert "User.java" in item["file"]
        assert item["sha256"] == hashlib.sha256(java.read_bytes()).hexdigest()
        assert item["size_bytes"] > 0
        # 不应包含解析出的 columns
        assert "columns" not in item
        assert "entity_fqn" not in item

    def test_jpa_entity_not_parsed(self, ctx, tmp_path):
        """确认不再解析列名/表名等。"""
        java = tmp_path / "src" / "com" / "x" / "User.java"
        java.parent.mkdir(parents=True)
        java.write_text(
            "package com.x;\n@Entity\npublic class User { @Column private String password; }\n",
            encoding="utf-8",
        )
        result = DbSchemaCollector().collect(ctx)
        item = result.items[0]
        assert "columns" not in item
        assert "table_name" not in item
        assert "entity_fqn" not in item

    def test_empty_project(self, ctx):
        result = DbSchemaCollector().collect(ctx)
        assert result.items == []
        assert result.stats["total_files"] == 0
        assert result.stats["cached"] == 0
        assert result.degraded is False


class TestDbSchemaCollectorMyBatis:
    def test_mapper_xml_found(self, ctx, tmp_path):
        xml = tmp_path / "UserMapper.xml"
        xml.write_text(
            '<?xml version="1.0"?>\n'
            '<mapper namespace="com.x.UserMapper">\n'
            '  <resultMap id="u"><result column="id"/><result column="password"/></resultMap>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        result = DbSchemaCollector().collect(ctx)
        assert result.stats["total_files"] == 1
        item = result.items[0]
        assert item["type"] == "mybatis_mapper"
        assert "UserMapper.xml" in item["file"]
        assert item["sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()
        # 不应包含解析出的 columns
        assert "columns" not in item


class TestDbSchemaCollectorJooq:
    def test_jooq_table_found(self, ctx, tmp_path):
        java = tmp_path / "gen" / "UsersTable.java"
        java.parent.mkdir(parents=True)
        java.write_text(
            "package gen;\n"
            "import org.jooq.Table;\n"
            "public class UsersTable extends Table {\n"
            "}\n",
            encoding="utf-8",
        )
        result = DbSchemaCollector().collect(ctx)
        item = result.items[0]
        assert item["type"] == "jooq"
        assert "UsersTable.java" in item["file"]
        assert item["sha256"] == hashlib.sha256(java.read_bytes()).hexdigest()
        # 不应包含解析出的 table_name/entity_fqn
        assert "table_name" not in item
        assert "entity_fqn" not in item


class TestDbSchemaCollectorStats:
    def test_by_type_stats(self, ctx, tmp_path):
        """stats.by_type 应正确统计各类型文件数。"""
        java = tmp_path / "src" / "Entity.java"
        java.parent.mkdir(parents=True)
        java.write_text("@Entity\npublic class Entity {}\n", encoding="utf-8")
        xml = tmp_path / "XMapper.xml"
        xml.write_text('<mapper namespace="x"></mapper>\n', encoding="utf-8")

        result = DbSchemaCollector().collect(ctx)
        by_type = result.stats["by_type"]
        assert "jpa_entity" in by_type
        assert "mybatis_mapper" in by_type
        assert result.stats["total_files"] == 2


class TestDbSchemaCollectorCaching:
    def test_cached_count_in_stats(self, ctx, tmp_path):
        """stats 应包含 cached 计数。"""
        java = tmp_path / "src" / "Entity.java"
        java.parent.mkdir(parents=True)
        java.write_text("@Entity\npublic class Entity {}\n", encoding="utf-8")
        result = DbSchemaCollector().collect(ctx)
        # Memurai 不可用时 cached 为 0
        assert "cached" in result.stats

    def test_cache_graceful_failure(self, ctx, tmp_path):
        """Memurai 不可用时缓存应优雅降级（cached=0）。"""
        java = tmp_path / "src" / "Entity.java"
        java.parent.mkdir(parents=True)
        java.write_text("@Entity\npublic class Entity {}\n", encoding="utf-8")
        with patch("scripts.redis.memurai_client.Memurai", side_effect=ImportError):
            result = DbSchemaCollector().collect(ctx)
        assert result.stats["cached"] == 0
