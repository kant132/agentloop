# -*- coding: utf-8 -*-
"""test_auth_code_collector.py — auth_code_collector 单元测试。

覆盖：协议、元数据、正则降级路径、注解检测、JWT/Shiro 检测、is_custom 分类、Memurai 缓存。

不做：不再测试 Filter/Interceptor 检测（由 filter_collector 负责）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.auth_code_collector import AuthCodeCollector

# ── fixtures ──


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """最小 Java 测试项目：包含注解/JWT/Shiro。不含 Filter（由 filter_collector 负责）。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
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
    # Security 配置
    (src / "SecurityConfig.java").write_text(
        "package com.example;\n"
        "@EnableWebSecurity\n"
        "public class SecurityConfig {\n"
        "  void configure(HttpSecurity http) {}\n"
        "}\n",
        encoding="utf-8",
    )
    # 框架类（应标记 is_custom=False）
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

    def test_detect_security_config(self, ctx):
        """能检测 @EnableWebSecurity / SecurityFilterChain 配置。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        configs = [i for i in result.items if i["category"] == "config"]
        assert len(configs) >= 1

    def test_no_filter_detection(self, ctx):
        """认证鉴权采集器不再检测 Filter（由 filter_collector 负责）。"""
        src = ctx.project_root / "src" / "main" / "java" / "com" / "example"
        (src / "MyFilter.java").write_text(
            "package com.example;\npublic class MyFilter implements Filter {}\n",
            encoding="utf-8",
        )
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        filters = [i for i in result.items if i["category"] == "filter"]
        assert len(filters) == 0


# ── 字段质量 ──


class TestFieldQuality:
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


# ── Memurai 缓存 ──


class TestCaching:
    def test_cached_stat_present(self, ctx):
        """stats 应包含 cached 字段。"""
        with patch("shutil.which", return_value=None):
            result = AuthCodeCollector().collect(ctx)
        assert "cached" in result.stats

    def test_cache_graceful_failure(self, ctx):
        """Memurai 不可用时 cached 应为 0。"""
        with patch("shutil.which", return_value=None):
            with patch("scripts.redis.memurai_client.Memurai", side_effect=ImportError):
                result = AuthCodeCollector().collect(ctx)
        assert result.stats["cached"] == 0

    def test_dedup_caching(self, ctx):
        """同一文件多个注解只缓存一次。"""
        with patch("shutil.which", return_value=None):
            with patch("scripts.redis.memurai_client.Memurai") as MockMemurai:
                mock_instance = MagicMock()
                MockMemurai.return_value = mock_instance
                mock_instance.set = MagicMock()
                result = AuthCodeCollector().collect(ctx)
        # AdminController.java 可能有多个 @PreAuthorize 匹配，
        # 但 set 调用次数应 <= 文件总数（去重）
        assert result.stats["cached"] <= len({i["file"] for i in result.items})
