# -*- coding: utf-8 -*-
"""test_filter_collector.py — filter_collector 的单元测试。

测试 FilterCollector 的行为契约：
1. implements_collector_protocol
2. has_correct_metadata
3. is_available
4. scan_servlet_filter
5. scan_spring_filter
6. scan_interceptor
7. scan_webfilter_annotation
8. exclude_framework_code
9. cache_to_memurai
10. stats_correct
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import sys

# 注入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult, Collector
from scripts.exposure.collectors.filter_collector import FilterCollector


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """构造一个含 Filter/Interceptor 的测试项目。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)

    # Servlet Filter
    (src / "AuthFilter.java").write_text(
        "package com.example;\n"
        "import javax.servlet.Filter;\n"
        "public class AuthFilter implements Filter {\n"
        "    public void doFilter() {}\n"
        "}\n",
        encoding="utf-8",
    )

    # Spring OncePerRequestFilter
    (src / "LoggingFilter.java").write_text(
        "package com.example;\n"
        "import org.springframework.web.filter.OncePerRequestFilter;\n"
        "public class LoggingFilter extends OncePerRequestFilter {\n"
        "    protected void doFilterInternal() {}\n"
        "}\n",
        encoding="utf-8",
    )

    # HandlerInterceptor
    (src / "AuthInterceptor.java").write_text(
        "package com.example;\n"
        "import org.springframework.web.servlet.HandlerInterceptor;\n"
        "public class AuthInterceptor implements HandlerInterceptor {\n"
        "    public boolean preHandle() { return true; }\n"
        "}\n",
        encoding="utf-8",
    )

    # @WebFilter annotation
    (src / "CorsFilter.java").write_text(
        "package com.example;\n"
        "import javax.servlet.annotation.WebFilter;\n"
        "@WebFilter(urlPatterns = \"/*\")\n"
        "public class CorsFilter {\n"
        "    public void doFilter() {}\n"
        "}\n",
        encoding="utf-8",
    )

    # 框架代码（应被排除）
    framework = tmp_path / "src" / "main" / "java" / "org" / "springframework"
    framework.mkdir(parents=True)
    (framework / "FrameworkFilter.java").write_text(
        "package org.springframework.web.filter;\n"
        "public class FrameworkFilter implements Filter {}\n",
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


# ============================================================
# 1-3. 协议与元数据
# ============================================================

class TestFilterCollectorContract:

    def test_implements_collector_protocol(self):
        """FilterCollector 必须能通过 Collector 协议检查。"""
        assert isinstance(FilterCollector(), Collector)

    def test_has_correct_metadata(self):
        c = FilterCollector()
        assert c.name == "filter_collector"
        assert c.asset_type == "filter"

    def test_is_available(self, ctx):
        """无外部依赖，始终 True。"""
        assert FilterCollector().is_available(ctx) is True


# ============================================================
# 4-7. Filter 类型检测
# ============================================================

class TestFilterDetection:

    def test_scan_servlet_filter(self, ctx):
        """应检测到 implements Filter 的类。"""
        result = FilterCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert any("AuthFilter.java" in f for f in files)

    def test_scan_spring_filter(self, ctx):
        """应检测到 extends OncePerRequestFilter 的类。"""
        result = FilterCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert any("LoggingFilter.java" in f for f in files)

    def test_scan_interceptor(self, ctx):
        """应检测到 implements HandlerInterceptor 的类。"""
        result = FilterCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert any("AuthInterceptor.java" in f for f in files)

    def test_scan_webfilter_annotation(self, ctx):
        """应检测到 @WebFilter 注解的类。"""
        result = FilterCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert any("CorsFilter.java" in f for f in files)


# ============================================================
# 8. 框架代码排除
# ============================================================

class TestFrameworkExclusion:

    def test_exclude_framework_code(self, ctx):
        """应排除 org.springframework.* 等框架代码。"""
        result = FilterCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert not any("FrameworkFilter.java" in f for f in files)


# ============================================================
# 9. Memurai 缓存
# ============================================================

class TestMemuraiCache:

    def test_cache_to_memurai(self, ctx):
        """应将文件内容缓存到 Memurai。"""
        with patch("scripts.redis.memurai_client.Memurai") as mock_memurai:
            mock_instance = Mock()
            mock_instance.set.return_value = True
            mock_memurai.return_value = mock_instance

            result = FilterCollector().collect(ctx)

            # 验证 set 被调用
            assert mock_instance.set.called
            # 验证 key 格式
            for call in mock_instance.set.call_args_list:
                key = call[0][0]
                assert key.startswith("com.example:filter:")


# ============================================================
# 10. 统计正确性
# ============================================================

class TestStatsCorrectness:

    def test_stats_correct(self, ctx):
        """stats 应正确统计。"""
        result = FilterCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert result.asset_type == "filter"
        assert result.stats["total"] == len(result.items)
        assert "by_type" in result.stats
        assert "cached" in result.stats

    def test_collect_no_project_dir(self, tmp_path: Path):
        """项目根不存在时返回空结果。"""
        bad_ctx = ExposureContext(
            project_root=tmp_path / "nonexistent",
            group_id="x",
            loop_audit_dir=tmp_path,
        )
        result = FilterCollector().collect(bad_ctx)
        assert result.stats["total"] == 0
        assert result.items == []
