# -*- coding: utf-8 -*-
"""test_route_collector.py — route_collector 的单元测试。

TDD 红阶段：定义 route_collector 必须满足的行为。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

# 注入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult
from scripts.exposure.collectors.route_collector import RouteCollector


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """构造一个最小的 Java 测试项目。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    (src / "UserController.java").write_text(
        '''
package com.example;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @GetMapping("/{id}")
    public String getUser(@PathVariable String id) {
        return "user-" + id;
    }

    @PostMapping
    public String create(@RequestParam String name) {
        return name;
    }
}
''',
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
# Red 阶段：行为契约测试
# ============================================================

class TestRouteCollectorContract:
    """RouteCollector 必须满足 Collector 协议。"""

    def test_implements_collector_protocol(self):
        """RouteCollector 必须能通过 Collector 协议检查。"""
        from scripts.exposure.contracts import Collector
        assert isinstance(RouteCollector(), Collector)

    def test_has_correct_metadata(self):
        c = RouteCollector()
        assert c.name == "route_collector"
        assert c.asset_type == "route"

    def test_is_available_when_ast_grep_present(self, ctx):
        """ast-grep 在 PATH 时应可用。"""
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            assert RouteCollector().is_available(ctx) is True

    def test_unavailable_when_ast_grep_missing(self, ctx):
        """ast-grep 不在 PATH 时应不可用，不抛异常。"""
        with patch("shutil.which", return_value=None):
            assert RouteCollector().is_available(ctx) is False


class TestRouteCollectorOutput:
    """采集结果的字段契约。"""

    def test_returns_collector_result(self, ctx):
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            with patch.object(
                RouteCollector, "_run_ast_grep",
                return_value=[
                    {
                        "fqn": "com.example.UserController#getUser",
                        "annotation": "GetMapping",
                        "args": ["/{id}"],
                        "file": "src/main/java/com/example/UserController.java",
                        "line": 10,
                        "method_name": "getUser",
                        "source": "annotation",
                    },
                    {
                        "fqn": "com.example.UserController#create",
                        "annotation": "PostMapping",
                        "args": [],
                        "file": "src/main/java/com/example/UserController.java",
                        "line": 15,
                        "method_name": "create",
                        "source": "annotation",
                    },
                ],
            ):
                result = RouteCollector().collect(ctx)

        assert isinstance(result, CollectorResult)
        assert result.asset_type == "route"
        assert len(result.items) == 2
        assert result.items[0]["annotation"] == "GetMapping"
        # 应解析出 HTTP 方法
        assert result.items[0]["http_method"] == "GET"
        assert result.items[1]["http_method"] == "POST"
        # 应标注是否有外部入参
        assert result.items[0]["has_external_param"] is True
        assert result.items[1]["has_external_param"] is True

    def test_stats_total_correct(self, ctx):
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            with patch.object(RouteCollector, "_run_ast_grep", return_value=[]):
                result = RouteCollector().collect(ctx)

        assert result.stats["total"] == 0
        assert "degraded_md5" not in result.stats or result.stats["degraded_md5"] == 0

    def test_collect_returns_data_not_file(self, ctx):
        """collect() 只返回 CollectorResult，文件写入由 cli 负责（参见 AC-2）。"""
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            with patch.object(
                RouteCollector, "_run_ast_grep",
                return_value=[{"fqn": "x", "annotation": "GetMapping"}],
            ):
                result = RouteCollector().collect(ctx)

        assert isinstance(result, CollectorResult)
        assert len(result.items) == 1
        # 文件不应由 collector 写
        assert not (ctx.exposure_dir / "route.json").exists()


class TestRouteCollectorDegradation:
    """降级路径测试。"""

    def test_degraded_when_codegraph_missing(self, ctx):
        """codegraph_db 缺失时，nodes_id 字段应降级为 md5。"""
        ctx.codegraph_db = None
        with patch("shutil.which", return_value="/usr/bin/ast-grep"):
            with patch.object(
                RouteCollector, "_run_ast_grep",
                return_value=[{
                    "fqn": "x#m", "annotation": "GetMapping",
                    "file": "a.java", "line": 1,
                }],
            ):
                result = RouteCollector().collect(ctx)

        assert result.degraded is True
        assert result.items[0].get("nodes_id") is None
        assert "sig_hash" in result.items[0]
        assert result.stats["degraded_md5"] == 1


# ============================================================
# YAML 规则驱动测试
# ============================================================

class TestRouteCollectorRules:
    """路由扫描规则从 rules/*.yaml 加载。"""

    def test_load_rules_from_yaml(self):
        """加载 spring.yaml 应得到 6 个 Spring 注解规则，jaxrs.yaml 得到 5 个。"""
        from scripts.exposure.collectors.route_collector import _load_rules

        rules = _load_rules()
        spring = [r for r in rules if r.get("_framework") == "spring"]
        jaxrs = [r for r in rules if r.get("_framework") == "jaxrs"]

        # Spring 6 个注解全覆盖
        assert len(spring) == 6
        spring_annotations = {
            r["pattern"].split("(")[0].lstrip("@") for r in spring
        }
        assert spring_annotations == {
            "GetMapping", "PostMapping", "PutMapping",
            "DeleteMapping", "PatchMapping", "RequestMapping",
        }

        # JAX-RS 5 个注解全覆盖
        assert len(jaxrs) == 5
        jaxrs_annotations = {
            r["pattern"].split("(")[0].lstrip("@") for r in jaxrs
        }
        assert jaxrs_annotations == {"Path", "GET", "POST", "PUT", "DELETE"}

        # 每条规则必有 http_method 与 pattern 字段
        for r in rules:
            assert "pattern" in r
            assert "http_method" in r
            assert r["http_method"] in {"GET", "POST", "PUT", "DELETE", "PATCH", "ANY"}

    def test_load_rules_ignores_empty_struts(self):
        """struts.yaml 是预留文件（rules: []），不应贡献任何规则。"""
        from scripts.exposure.collectors.route_collector import _load_rules

        rules = _load_rules()
        struts = [r for r in rules if r.get("_framework") == "struts"]
        assert struts == []

    def test_build_ast_grep_query(self):
        """_build_ast_grep_rule_yaml 应生成合法 ast-grep any 语法。"""
        from scripts.exposure.collectors.route_collector import (
            _build_ast_grep_rule_yaml,
            _build_http_method_map,
            _load_rules,
        )

        rules = _load_rules()
        rule_yaml = _build_ast_grep_rule_yaml(rules)

        # 必须含 language + rule.any 头部
        assert rule_yaml.startswith("language: java\nrule:\n  any:\n")
        # 每条规则对应一行 pattern
        line_count = rule_yaml.count("    - pattern:")
        assert line_count == len(rules)
        # 合并后应包含所有原始 pattern
        for r in rules:
            assert f'- pattern: "{r["pattern"]}"' in rule_yaml

        # HTTP 方法映射完整：RequestMapping→ANY, GetMapping→GET 等
        http_map = _build_http_method_map(rules)
        assert http_map["GetMapping"] == "GET"
        assert http_map["PostMapping"] == "POST"
        assert http_map["PutMapping"] == "PUT"
        assert http_map["DeleteMapping"] == "DELETE"
        assert http_map["PatchMapping"] == "PATCH"
        assert http_map["RequestMapping"] == "ANY"
        assert http_map["GET"] == "GET"
        assert http_map["Path"] == "ANY"
