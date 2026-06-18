# -*- coding: utf-8 -*-
"""test_db_schema_collector.py — DbSchemaCollector 单元测试。

覆盖：协议、元数据、JPA 实体解析、MyBatis mapper 解析、Jooq 解析、空项目。
"""
from __future__ import annotations

import sys
from pathlib import Path

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
    def test_jpa_entity_parsed(self, ctx, tmp_path):
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
        assert item["source"] == "jpa_entity"
        assert item["entity_fqn"] == "com.x.User"
        assert item["table_name"] == "users"
        assert item["has_sensitive_field"] is True
        col_names = {c["name"] for c in item["columns"]}
        assert "password" in col_names

    def test_empty_project(self, ctx):
        result = DbSchemaCollector().collect(ctx)
        assert result.items == []
        assert result.stats["total"] == 0
        assert result.degraded is False


class TestDbSchemaCollectorMyBatis:
    def test_mapper_xml_parsed(self, ctx, tmp_path):
        xml = tmp_path / "UserMapper.xml"
        xml.write_text(
            '<?xml version="1.0"?>\n'
            '<mapper namespace="com.x.UserMapper">\n'
            '  <resultMap id="u"><result column="id"/><result column="password"/></resultMap>\n'
            '</mapper>\n',
            encoding="utf-8",
        )
        result = DbSchemaCollector().collect(ctx)
        assert result.stats["total"] == 1
        item = result.items[0]
        assert item["source"] == "mybatis_mapper"
        assert item["entity_fqn"] == "com.x.UserMapper"
        assert item["has_sensitive_field"] is True
        cols = [c["name"] for c in item["columns"]]
        assert "id" in cols and "password" in cols


class TestDbSchemaCollectorJooq:
    def test_jooq_table_parsed(self, ctx, tmp_path):
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
        assert item["source"] == "jooq"
        assert item["table_name"] == "Users"
        assert item["entity_fqn"] == "gen.UsersTable"
