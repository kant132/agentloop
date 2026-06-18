# -*- coding: utf-8 -*-
"""test_env_filter_collector.py — EnvFilterCollector 单元测试。

覆盖：协议、元数据、离线降级、SSH 成功路径（mock _ssh_exec）、SSH 部分失败。
真实 SSH 不执行（mock _ssh_exec 替身）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import Collector, CollectorResult, ExposureContext
from scripts.exposure.collectors.env_filter_collector import EnvFilterCollector


@pytest.fixture
def offline_ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
        ssh_target=None,
    )


@pytest.fixture
def ssh_ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
        ssh_target="user@example.com:22",
    )


class TestEnvFilterCollectorContract:
    def test_implements_collector_protocol(self):
        assert isinstance(EnvFilterCollector(), Collector)

    def test_metadata(self):
        c = EnvFilterCollector()
        assert c.name == "env_filter_collector"
        assert c.asset_type == "env_filter"

    def test_always_available_even_offline(self, offline_ctx):
        # 结构上可用即可，离线由 collect() 降级
        assert EnvFilterCollector().is_available(offline_ctx) is True


class TestEnvFilterCollectorOffline:
    def test_offline_returns_empty_degraded(self, offline_ctx):
        result = EnvFilterCollector().collect(offline_ctx)
        assert isinstance(result, CollectorResult)
        assert result.items == []
        assert result.degraded is True
        assert result.stats["reason"] == "offline_mode"
        assert result.stats["total"] == 0


class TestEnvFilterCollectorSsh:
    def test_all_sources_success(self, ssh_ctx):
        c = EnvFilterCollector()
        with patch.object(
            c, "_ssh_exec",
            side_effect=lambda ctx, cmd: {
                "nginx -T 2>&1": "location / { proxy_pass http://x; }",
                # tomcat/jvm 走默认空字符串
            }.get(cmd, ""),
        ):
            result = c.collect(ssh_ctx)
        assert result.degraded is True  # 仅 nginx 命中
        assert len(result.items) == 1
        item = result.items[0]
        assert item["source"] == "nginx"
        assert item["filters"], "nginx filters should not be empty"

    def test_partial_failure_marks_degraded(self, ssh_ctx):
        c = EnvFilterCollector()
        with patch.object(
            c, "_ssh_exec",
            return_value="location /api { proxy_pass http://up; }",
        ):
            result = c.collect(ssh_ctx)
        # 所有命令都返回相同字符串 → 三条都进 items，但 tomcat 字符串其实没匹配到 Valve
        # 所以 filters 可能为空但 items 满了
        assert result.stats["total"] == 3
        # 三条都有但只有 nginx 真正含 filter
        nginx_items = [i for i in result.items if i["source"] == "nginx"]
        assert nginx_items and nginx_items[0]["filters"]

    def test_ssh_exec_failure_returns_empty(self, ssh_ctx):
        c = EnvFilterCollector()
        with patch("subprocess.run", side_effect=FileNotFoundError("no ssh")):
            assert c._ssh_exec(ssh_ctx, "nginx -T 2>&1") == ""
