# -*- coding: utf-8 -*-
"""test_route_collector.py — route_collector 的单元测试。

TDD 红阶段：定义 route_collector 必须满足的行为。
"""
from __future__ import annotations

import json
import re
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
        """加载 spring.yaml/jaxrs.yaml 应得到分类规则。

        返回 dict {class_rules, method_rules, param_rules}：
        - spring: 2 class + 6 method + 5 param
        - jaxrs: 1 class + 6 method + 8 param
        """
        from scripts.exposure.collectors.route_collector import (
            _expand_patterns,
            _load_rules,
        )

        categorized = _load_rules()
        # 三个分类 key 都存在
        assert set(categorized.keys()) == {"class_rules", "method_rules", "param_rules"}

        spring_method = [r for r in categorized["method_rules"] if r.get("_framework") == "spring"]
        jaxrs_method = [r for r in categorized["method_rules"] if r.get("_framework") == "jaxrs"]

        # Spring 6 个方法级路由注解全覆盖
        assert len(spring_method) == 6
        spring_annotations = set()
        for r in spring_method:
            for p in _expand_patterns(r):
                m = re.match(r"@(\w+)", p)
                if m:
                    spring_annotations.add(m.group(1))
        assert spring_annotations == {
            "GetMapping", "PostMapping", "PutMapping",
            "DeleteMapping", "PatchMapping", "RequestMapping",
        }

        # JAX-RS 6 个方法级规则（含 pattern-either 的 Path）
        assert len(jaxrs_method) == 6
        jaxrs_annotations = set()
        for r in jaxrs_method:
            for p in _expand_patterns(r):
                m = re.match(r"@(\w+)", p)
                if m:
                    jaxrs_annotations.add(m.group(1))
        # 短名注解全覆盖（全限定名 @javax.ws.rs.Path 会被 @(\w+) 截到 "javax"，
        # 这是 _build_http_method_map 的既有行为，short-name 短名同样会注册）
        expected = {"Path", "GET", "POST", "PUT", "DELETE", "PATCH"}
        assert expected.issubset(jaxrs_annotations)

        # 每条 method rule 必有可展开的 pattern 与 http_method
        for r in categorized["method_rules"]:
            assert _expand_patterns(r)  # 至少 1 个 pattern
            assert "http_method" in r
            assert r["http_method"] in {"GET", "POST", "PUT", "DELETE", "PATCH", "ANY"}

    def test_load_rules_ignores_empty_struts(self):
        """struts.yaml 是预留文件（无任何 rules），不应贡献任何规则。"""
        from scripts.exposure.collectors.route_collector import _load_rules

        categorized = _load_rules()
        for key in ("class_rules", "method_rules", "param_rules"):
            struts = [r for r in categorized[key] if r.get("_framework") == "struts"]
            assert struts == []

    def test_build_ast_grep_query(self):
        """_build_ast_grep_rule_yaml 应生成合法 ast-grep any 语法。

        pattern-either 的多变体应展开为多行 pattern。
        """
        from scripts.exposure.collectors.route_collector import (
            _build_ast_grep_rule_yaml,
            _build_http_method_map,
            _expand_patterns,
            _load_rules,
        )

        categorized = _load_rules()
        rule_yaml = _build_ast_grep_rule_yaml(categorized)

        # 必须含 language + rule.any 头部
        assert rule_yaml.startswith("language: java\nrule:\n  any:\n")
        # 每条规则的每个展开 pattern 都对应一行
        all_patterns: list[str] = []
        for key in ("class_rules", "method_rules", "param_rules"):
            for r in categorized[key]:
                all_patterns.extend(_expand_patterns(r))
        line_count = rule_yaml.count("    - pattern:")
        assert line_count == len(all_patterns)
        # 合并后应包含所有原始 pattern
        for p in all_patterns:
            assert f'- pattern: "{p}"' in rule_yaml

        # HTTP 方法映射完整：RequestMapping→ANY, GetMapping→GET 等
        http_map = _build_http_method_map(categorized)
        assert http_map["GetMapping"] == "GET"
        assert http_map["PostMapping"] == "POST"
        assert http_map["PutMapping"] == "PUT"
        assert http_map["DeleteMapping"] == "DELETE"
        assert http_map["PatchMapping"] == "PATCH"
        assert http_map["RequestMapping"] == "ANY"
        assert http_map["GET"] == "GET"
        # pattern-either 中短名 Path 与全限定 javax.ws.rs.Path 都注册
        assert http_map["Path"] == "ANY"
        # class_rules 的 @RestController 也注册到映射表（pattern-either 两个变体都命中）
        assert http_map["RestController"] == "ANY"


# ============================================================
# pattern-either + 三组规则分类（class/method/param）
# ============================================================

class TestRouteCollectorPatternEither:
    """pattern-either 多变体语法 + 三组规则分类加载。"""

    def test_load_rules_returns_three_categories(self):
        """_load_rules() 返回 dict，含 class_rules/method_rules/param_rules 三组，且都非空。"""
        from scripts.exposure.collectors.route_collector import _load_rules

        categorized = _load_rules()
        assert isinstance(categorized, dict)
        # 三个分类 key 都存在
        for key in ("class_rules", "method_rules", "param_rules"):
            assert key in categorized, f"missing category: {key}"
            assert isinstance(categorized[key], list)
            assert len(categorized[key]) > 0, f"{key} 不应为空（spring+jaxrs 都应贡献规则）"

    def test_spring_yaml_has_rest_controller_pattern_either(self):
        """spring.yaml 的 rest_controller 规则必须用 pattern-either，且至少 2 个变体。"""
        from scripts.exposure.collectors.route_collector import _load_rules

        categorized = _load_rules()
        spring_class = [
            r for r in categorized["class_rules"]
            if r.get("_framework") == "spring" and r.get("id") == "rest_controller"
        ]
        assert len(spring_class) == 1
        rest_controller = spring_class[0]
        # 必须用 pattern-either 语法
        assert "pattern-either" in rest_controller
        variants = rest_controller["pattern-either"]
        # 至少 2 个变体（全限定名 + 短名）
        assert len(variants) >= 2
        # 每个变体都是 {pattern: ...}
        for v in variants:
            assert "pattern" in v
            assert "RestController" in str(v["pattern"])
        # 短名 @RestController 与全限定名都应出现
        patterns = [str(v["pattern"]) for v in variants]
        short_names = [p for p in patterns if "RestController" in p and "org.springframework" not in p]
        fully_qualified = [p for p in patterns if "org.springframework" in p]
        assert short_names, "缺少短名 @RestController 变体"
        assert fully_qualified, "缺少全限定名 @org.springframework...RestController 变体"

    def test_normalize_pattern_handles_pattern_either(self):
        """_normalize_pattern 应正确解析 pattern 与 pattern-either 两种形式。

        - pattern（单条）→ 返回该 pattern
        - pattern-either（多变体）→ 返回第一个变体的 pattern
        - 都没有 → 返回空字符串
        """
        from scripts.exposure.collectors.route_collector import (
            _expand_patterns,
            _normalize_pattern,
        )

        # 单条 pattern
        single = {"pattern": "@GetMapping($$$)"}
        assert _normalize_pattern(single) == "@GetMapping($$$)"
        assert _expand_patterns(single) == ["@GetMapping($$$)"]

        # 多变体 pattern-either
        multi = {
            "pattern-either": [
                {"pattern": "@RestController($$$)"},
                {"pattern": "@org.springframework.web.bind.annotation.RestController($$$)"},
            ]
        }
        # normalize 取第一个变体
        assert _normalize_pattern(multi) == "@RestController($$$)"
        # expand 展开所有变体
        expanded = _expand_patterns(multi)
        assert len(expanded) == 2
        assert expanded[0] == "@RestController($$$)"
        assert "RestController" in expanded[1]

        # 空规则
        empty: dict = {}
        assert _normalize_pattern(empty) == ""
        assert _expand_patterns(empty) == []
