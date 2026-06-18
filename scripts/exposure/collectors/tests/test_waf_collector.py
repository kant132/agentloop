# -*- coding: utf-8 -*-
"""test_waf_collector.py — WafCollector 的单元测试。

覆盖：Collector 协议、元数据、各类 WAF 信号解析、空项目降级。
"""
from __future__ import annotations

import sys
from pathlib import Path

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


class TestWafCollectorParsing:
    def test_nginx_modsec_detected(self, ctx, tmp_path):
        (tmp_path / "nginx.conf").write_text(
            "load_module modsecurity.so;\nSecRuleEngine On;\n",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        item = result.items[0]
        assert item["waf_type"] == "nginx_modsec"
        assert item["presence"] is True
        assert any(e["type"] == "nginx_modsec" for e in item["evidence"])

    def test_pom_library_detected(self, ctx, tmp_path):
        (tmp_path / "pom.xml").write_text(
            "<dependency><groupId>com.googlecode.owasp-java-html-sanitizer</groupId></dependency>",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        item = result.items[0]
        assert item["waf_type"] == "library"
        assert item["presence"] is True
        assert result.stats["waf_present"] == 1

    def test_spring_security_detected(self, ctx, tmp_path):
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
        item = result.items[0]
        assert item["waf_type"] == "spring_security"
        assert item["presence"] is True

    def test_cloud_waf_header_detected(self, ctx, tmp_path):
        (tmp_path / "application.yml").write_text(
            "headers:\n  X-CDN: cloudfront\n",
            encoding="utf-8",
        )
        result = WafCollector().collect(ctx)
        assert result.items[0]["waf_type"] == "cloud"


class TestWafCollectorDegradation:
    def test_empty_project_yields_unknown(self, ctx):
        result = WafCollector().collect(ctx)
        item = result.items[0]
        assert item["waf_type"] == "unknown"
        assert item["presence"] is False
        assert item["evidence"] == []
        assert result.degraded is False
        assert result.stats["waf_present"] == 0
