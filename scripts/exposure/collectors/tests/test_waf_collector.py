# -*- coding: utf-8 -*-
"""test_waf_collector.py — WafCollector 的单元测试。

覆盖：Collector 协议、元数据、各类安全配置文件发现、空项目、Memurai 缓存。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.waf_collector import WafCollector


@pytest.fixture
def ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


class TestWafCollectorContract:
    def test_implements_collector_protocol(self):
        assert isinstance(WafCollector(), Collector)

    def test_metadata(self):
        c = WafCollector()
        assert c.name == "waf_collector"
        assert c.asset_type == "waf"

    def test_always_available(self, ctx):
        assert WafCollector().is_available(ctx) is True


class TestWafCollectorFileDiscovery:
    def test_nginx_conf_found(self, ctx, tmp_path):
        nginx = tmp_path / "nginx.conf"
        nginx.write_text(
            "load_module modsecurity.so;\nSecRuleEngine On;\n",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "nginx"
        assert "nginx.conf" in item["file"]
        assert item["sha256"] == hashlib.sha256(nginx.read_bytes()).hexdigest()

    def test_pom_security_dep_found(self, ctx, tmp_path):
        pom = tmp_path / "pom.xml"
        pom.write_text(
            "<dependency><groupId>com.googlecode.owasp-java-html-sanitizer</groupId></dependency>",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "pom_security_dep"
        assert "pom.xml" in item["file"]

    def test_spring_security_found(self, ctx, tmp_path):
        java = tmp_path / "src" / "main" / "java" / "SecurityConfig.java"
        java.parent.mkdir(parents=True)
        java.write_text(
            "package x;\n"
            "@EnableWebSecurity\n"
            "public class SecurityConfig {\n"
            "  void configure(HttpSecurity http) {}\n"
            "}\n",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "spring_security"
        assert "SecurityConfig.java" in item["file"]

    def test_web_xml_found(self, ctx, tmp_path):
        webxml = tmp_path / "web.xml"
        webxml.write_text(
            '<web-app><filter><filter-name>ModSecurity</filter-name></filter></web-app>',
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "web_xml"

    def test_yaml_security_found(self, ctx, tmp_path):
        """application.yml 含 X-CDN 头应被发现。"""
        yml = tmp_path / "src" / "main" / "resources" / "application.yml"
        yml.parent.mkdir(parents=True)
        yml.write_text(
            "headers:\n  X-CDN: cloudfront\n",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert len(result.items) >= 1
        types = [it["type"] for it in result.items]
        assert "yaml_security" in types


class TestWafCollectorStats:
    def test_by_type_stats(self, ctx, tmp_path):
        """stats.by_type 应正确统计各类型文件数。"""
        nginx = tmp_path / "nginx.conf"
        nginx.write_text("SecRuleEngine On\n", encoding="utf-8")
        java = tmp_path / "src" / "SecurityConfig.java"
        java.parent.mkdir(parents=True)
        java.write_text("@EnableWebSecurity\npublic class SC {}\n", encoding="utf-8")

        result = WafCollector().collect(ctx)
        by_type = result.stats["by_type"]
        assert "nginx" in by_type
        assert "spring_security" in by_type
        assert result.stats["total_files"] == 2

    def test_empty_project(self, ctx):
        """空项目应返回空结果。"""
        result = WafCollector().collect(ctx)
        assert result.items == []
        assert result.stats["total_files"] == 0
        assert result.stats["cached"] == 0


class TestWafCollectorCaching:
    def test_cached_stat_present(self, ctx, tmp_path):
        """stats 应包含 cached 字段。"""
        nginx = tmp_path / "nginx.conf"
        nginx.write_text("SecRuleEngine On\n", encoding="utf-8")
        result = WafCollector().collect(ctx)
        assert "cached" in result.stats

    def test_cache_graceful_failure(self, ctx, tmp_path):
        """Memurai 不可用时 cached 应为 0。"""
        nginx = tmp_path / "nginx.conf"
        nginx.write_text("SecRuleEngine On\n", encoding="utf-8")
        with patch("scripts.redis.memurai_client.Memurai", side_effect=ImportError):
            result = WafCollector().collect(ctx)
        assert result.stats["cached"] == 0

    def test_no_snippet_extraction(self, ctx, tmp_path):
        """不再提取 evidence/snippet。"""
        nginx = tmp_path / "nginx.conf"
        nginx.write_text("SecRuleEngine On\n", encoding="utf-8")
        result = WafCollector().collect(ctx)
        for item in result.items:
            assert "snippet" not in item
            assert "line" not in item
            assert "evidence" not in item
