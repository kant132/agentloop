# -*- coding: utf-8 -*-
"""test_auth_code_collector.py — auth_code_collector 单元测试。

8 个测试覆盖：协议、元数据、ast-grep/regex 双路径、各类别检测。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.auth_code_collector import AuthCodeCollector

# ── fixtures ──


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """最小 Java 测试项目：包含 Filter/注解/JWT。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    # Filter 实现
    (src / "AuthFilter.java").write_text(
        "package com.example;\n"
        "import javax.servlet.Filter;\n"
        "import org.springframework.core.annotation.Order;\n"
        "@Order(1)\n"
        "public class AuthFilter implements Filter {\n"
        "  public void doFilter(...) {}\n"
        "}\n",
        encoding="utf-8",
    )
    # @PreAuthorize 注解
    (src / "AdminController.java").write_text(
        "package com.example;\n"
        "import org.springframework.security.access.prepost.PreAuthorize;\n"
        "@PreAuthorize(\"hasRole('ADMIN')\")\n"
        "public class AdminController {\n"
        "  @PreAuthorize(\"hasAuthority('write')\")\n"
        "  public void delete() {}\n"
        "}\n",
        encoding="utf-8",
    )
    # JWT 类
    (src / "CustomJwtDecoder.java").write_text(
        "package com.example;\n"
        "import org.springframework.security.oauth2.jwt.JwtDecoder;\n"
        "public class CustomJwtDecoder implements JwtDecoder {}\n",
        encoding="utf-8",
    )
    # 框架类（应标记 is_custom=False）— FQN 由文件路径推导，需放对目录
    shiro_src = tmp_path / "src" / "main" / "java" / "org" / "apache" / "shiro" / "realm"
    shiro_src.mkdir(parents=True)
    (shiro_src / "ShiroRealm.java").write_text(
        "package org.apache.shiro.realm;\n"
        "public class ShiroRealm extends AuthorizingRealm {}\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def ctx(tmp_project: Path, tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_project,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


# ── 协议与元数据 ──


class TestProtocol:
    def test_implements_collector_protocol(self):
        """AuthCodeCollector 必须通过 Collector 协议检查。"""
        assert isinstance(AuthCodeCollector(), Collector)

    def test_has_correct_metadata(self):
        c = AuthCodeCollector()
        assert c.name == "auth_code_collector"
        assert c.asset_type == "auth_code"


# ── 可用性 ──


class TestAvailability:
    def test_available_with_ast_grep(self, ctx):
        """ast-grep 在 PATH 时，is_available 返回 True。"""
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            assert AuthCodeCollector().is_available(ctx) is True

    def test_fallback_to_regex(self, ctx):
        """ast-grep 缺失时仍可用，走正则降级路径。"""
        with patch("shutil.which", return_value=None):
            c = AuthCodeCollector()
            assert c.is_available(ctx) is True
            result = c.collect(ctx)
            assert result.degraded is True
            assert result.source == "regex"
            assert len(result.items) > 0


# ── 检测能力（正则模式，不依赖 ast-grep）──


class TestDetection:
    def test_detect_filter_implementation(self, ctx):
        """能检测 implements Filter。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        filters = [i for i in result.items if i["category"] == "filter"]
        assert len(filters) >= 1
        assert any("AuthFilter" in i["fqn"] for i in filters)

    def test_detect_pre_authorize_annotation(self, ctx):
        """能检测 @PreAuthorize 注解。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        anns = [i for i in result.items if i["category"] == "annotation"]
        assert len(anns) >= 1
        assert any("PreAuthorize" in i["matched"] for i in anns)

    def test_detect_jwt_classes(self, ctx):
        """能检测 JwtDecoder 相关类。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        jwts = [i for i in result.items if i["category"] == "jwt"]
        assert len(jwts) >= 1
        assert any("JwtDecoder" in i["matched"] for i in jwts)


# ── 字段质量 ──


class TestFieldQuality:
    def test_detect_filter_order(self, ctx):
        """能提取 @Order 值。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        filters = [i for i in result.items if i["category"] == "filter"]
        ordered = [i for i in filters if i["filter_order"] is not None]
        assert len(ordered) >= 1
        assert ordered[0]["filter_order"] == 1

    def test_is_custom_classification(self, ctx):
        """com.example 包下为自定义，org.apache.shiro 包下非自定义。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        customs = [i for i in result.items if i["is_custom"] is True]
        frameworks = [i for i in result.items if i["is_custom"] is False]
        assert len(customs) >= 1
        assert any("com.example" in i["fqn"] for i in customs)
        assert len(frameworks) >= 1
        assert any("org.apache.shiro" in i["fqn"] for i in frameworks)
