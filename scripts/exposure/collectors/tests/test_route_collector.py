# -*- coding: utf-8 -*-
"""test_route_collector.py — route_collector + 子模块的单单元测试。

测试覆盖：
- RouteCollector: Collector 协议契约、采集结果字段、降级路径
  （新架构：FileLocator + JavaparserScanner + RouteEnricher.enrich_routes）
- RuleLoader: YAML 规则加载、pattern-either 展开、ast-grep any 规则生成
- AstGrepScanner: JSON 输出解析（旧 ast-grep 路径仍保留）
- JavaparserScanner: java -jar RouteExtractor --routes 输出解析
- FileLocator: ast-grep 定位 + glob 降级
- RouteEnricher: 富化（HTTP方法、nodes_id、params + enrich_routes）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

# 注入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult
from scripts.exposure.collectors.route_collector import RouteCollector
from scripts.exposure.collectors.route.astgrep_scanner import AstGrepScanner
from scripts.exposure.collectors.route.enricher import RouteEnricher
from scripts.exposure.collectors.route.file_locator import FileLocator
from scripts.exposure.collectors.route.javaparser_scanner import JavaparserScanner
from scripts.exposure.collectors.route.rule_loader import RuleLoader


def _jp_route(**overrides) -> dict:
    """构造一条 javaparser RouteExtractor 输出格式的路由。"""
    base = {
        "class_fqn": "com.example.UserController",
        "class_base_path": "/api/users",
        "method_fqn": "com.example.UserController#getUser",
        "method_name": "getUser",
        "full_url": "/api/users/{id}",
        "http_methods": ["GET"],
        "annotation": "GetMapping",
        "file": "src/main/java/com/example/UserController.java",
        "start_line": 9,
    }
    base.update(overrides)
    return base


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
# RouteCollector 协议契约测试
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
        """ast-grep 在 PATH 时应可用（即使 java 不可用）。"""
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/ast-grep" if x == "ast-grep" else None):
            assert RouteCollector().is_available(ctx) is True

    def test_is_available_when_java_present(self, ctx):
        """java 在 PATH 但 ast-grep 不可用时也应可用（降级 glob 定位）。"""
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/java" if x == "java" else None):
            assert RouteCollector().is_available(ctx) is True

    def test_unavailable_when_both_missing(self, ctx):
        """ast-grep 与 java 都不在 PATH 时应不可用，不抛异常。"""
        with patch("shutil.which", return_value=None):
            assert RouteCollector().is_available(ctx) is False


class TestRouteCollectorOutput:
    """采集结果的字段契约（新架构：javaparser RouteExtractor）。"""

    def test_returns_collector_result(self, ctx):
        """collect 应返回 CollectorResult，items 含 full_url + http_methods 列表。"""
        routes = [
            _jp_route(
                full_url="/api/users/{id}",
                http_methods=["GET"],
                method_fqn="com.example.UserController#getUser",
                method_name="getUser",
                annotation="GetMapping",
                file="src/main/java/com/example/UserController.java",
                start_line=9,
            ),
            _jp_route(
                full_url="/api/users",
                http_methods=["POST"],
                method_fqn="com.example.UserController#create",
                method_name="create",
                annotation="PostMapping",
                file="src/main/java/com/example/UserController.java",
                start_line=14,
            ),
        ]
        with patch.object(FileLocator, "locate", return_value=[Path("UserController.java")]):
            with patch.object(JavaparserScanner, "scan_directory", return_value=routes):
                result = RouteCollector().collect(ctx)

        assert isinstance(result, CollectorResult)
        assert result.asset_type == "route"
        assert len(result.items) == 2
        # full_url 应是 javaparser 拼接结果（类+方法）
        assert result.items[0]["full_url"] == "/api/users/{id}"
        # http_methods 是列表（支持 {GET,POST} 展开后的多方法）
        assert result.items[0]["http_methods"] == ["GET"]
        assert result.items[1]["http_methods"] == ["POST"]
        # has_external_param 由 full_url 含 {param} 推断
        assert result.items[0]["has_external_param"] is True
        assert result.items[1]["has_external_param"] is False
        # fqn 应指向 method_fqn
        assert result.items[0]["fqn"] == "com.example.UserController#getUser"

    def test_http_methods_supports_array_expansion(self, ctx):
        """``@RequestMapping(method={GET,POST})`` 应展开为 ``["GET","POST"]`` 列表。"""
        routes = [
            _jp_route(
                full_url="/api/users/multi",
                http_methods=["GET", "POST"],
                method_fqn="com.example.UserController#multi",
                method_name="multi",
                annotation="RequestMapping",
                start_line=20,
            ),
        ]
        with patch.object(FileLocator, "locate", return_value=[Path("UserController.java")]):
            with patch.object(JavaparserScanner, "scan_directory", return_value=routes):
                result = RouteCollector().collect(ctx)

        assert len(result.items) == 1
        # 列表保留双方法
        assert result.items[0]["http_methods"] == ["GET", "POST"]
        # stats 按 method 分别计数
        assert result.stats["by_http_method"] == {"GET": 1, "POST": 1}

    def test_stats_total_correct(self, ctx):
        """空 routes 应返回 total=0，stats.by_http_method 为空 dict。"""
        with patch.object(FileLocator, "locate", return_value=[]):
            with patch.object(JavaparserScanner, "scan_directory", return_value=[]):
                result = RouteCollector().collect(ctx)

        assert result.stats["total"] == 0
        assert result.stats["by_http_method"] == {}
        assert result.stats["files_scanned"] == 0

    def test_collect_returns_data_not_file(self, ctx):
        """collect() 只返回 CollectorResult，文件写入由 cli 负责（参见 AC-2）。"""
        with patch.object(FileLocator, "locate", return_value=[Path("UserController.java")]):
            with patch.object(
                JavaparserScanner, "scan_directory",
                return_value=[_jp_route()],
            ):
                result = RouteCollector().collect(ctx)

        assert isinstance(result, CollectorResult)
        assert len(result.items) == 1
        # 文件不应由 collector 写
        assert not (ctx.exposure_dir / "route.json").exists()


class TestRouteCollectorDegradation:
    """降级路径测试。"""

    def test_degraded_when_jar_missing(self, ctx, tmp_path: Path):
        """javaparser JAR 不存在时，degraded=True 且 items 为空。"""
        # 让 jar_path 返回一个不存在的路径
        fake_jar = tmp_path / "nonexistent.jar"
        with patch.object(JavaparserScanner, "jar_path", return_value=fake_jar):
            with patch.object(FileLocator, "locate", return_value=[Path("X.java")]):
                with patch.object(JavaparserScanner, "scan_directory", return_value=[]):
                    result = RouteCollector().collect(ctx)

        assert result.degraded is True
        assert result.items == []

    def test_nodes_id_none_when_codegraph_missing(self, ctx):
        """codegraph_db 缺失时，nodes_id=None。"""
        ctx.codegraph_db = None
        routes = [_jp_route(file="a.java", start_line=1, annotation="GetMapping")]
        with patch.object(FileLocator, "locate", return_value=[Path("a.java")]):
            with patch.object(JavaparserScanner, "scan_directory", return_value=routes):
                result = RouteCollector().collect(ctx)

        # nodes_id 缺失 → degraded=True（新逻辑）
        assert result.degraded is True
        assert result.items[0].get("nodes_id") is None


# ============================================================
# YAML 规则驱动测试（RuleLoader）
# ============================================================

class TestRouteCollectorRules:
    """路由扫描规则从 rules/*.yaml 加载（经 RuleLoader）。"""

    def test_load_rules_from_yaml(self):
        """加载 spring.yaml/jaxrs.yaml 应得到分类规则。

        返回 dict {class_rules, method_rules, param_rules}：
        - spring: 4 class + 8 method + 9 param
        - jaxrs: 1 class + 6 method + 8 param
        """
        categorized = RuleLoader.load(_RULES_DIR())
        # 三个分类 key 都存在
        assert set(categorized.keys()) == {"class_rules", "method_rules", "param_rules"}

        spring_method = [r for r in categorized["method_rules"] if r.get("_framework") == "spring"]
        jaxrs_method = [r for r in categorized["method_rules"] if r.get("_framework") == "jaxrs"]

        # Spring 8 个方法级路由注解全覆盖（含 MessageMapping + ExceptionHandler）
        assert len(spring_method) == 8
        spring_annotations = set()
        for r in spring_method:
            for p in RuleLoader.expand_patterns(r):
                m = re.match(r"@(\w+)", p)
                if m:
                    spring_annotations.add(m.group(1))
        assert spring_annotations == {
            "GetMapping", "PostMapping", "PutMapping",
            "DeleteMapping", "PatchMapping", "RequestMapping",
            "MessageMapping", "ExceptionHandler", "org",
        }
        # 短名注解全覆盖（忽略全限定名截到的 "org"）
        expected_short = {
            "GetMapping", "PostMapping", "PutMapping",
            "DeleteMapping", "PatchMapping", "RequestMapping",
            "MessageMapping", "ExceptionHandler",
        }
        assert expected_short.issubset(spring_annotations)

        # JAX-RS 6 个方法级规则（含 pattern-either 的 Path）
        assert len(jaxrs_method) == 6
        jaxrs_annotations = set()
        for r in jaxrs_method:
            for p in RuleLoader.expand_patterns(r):
                m = re.match(r"@(\w+)", p)
                if m:
                    jaxrs_annotations.add(m.group(1))
        # 短名注解全覆盖（全限定名 @javax.ws.rs.Path 会被 @(\w+) 截到 "javax"，
        # 这是 build_http_method_map 的既有行为，short-name 短名同样会注册）
        expected = {"Path", "GET", "POST", "PUT", "DELETE", "PATCH"}
        assert expected.issubset(jaxrs_annotations)

        # 每条 method rule 必有可展开的 pattern 与 http_method
        for r in categorized["method_rules"]:
            assert RuleLoader.expand_patterns(r)  # 至少 1 个 pattern
            assert "http_method" in r
            assert r["http_method"] in {"GET", "POST", "PUT", "DELETE", "PATCH", "ANY", "WS"}

    def test_load_rules_ignores_empty_struts(self):
        """struts.yaml 是预留文件（无任何 rules），不应贡献任何规则。"""
        categorized = RuleLoader.load(_RULES_DIR())
        for key in ("class_rules", "method_rules", "param_rules"):
            struts = [r for r in categorized[key] if r.get("_framework") == "struts"]
            assert struts == []

    def test_build_ast_grep_query(self):
        """RuleLoader.build_ast_grep_rule_yaml 应生成合法 ast-grep any 语法。

        pattern-either 的多变体应展开为多行 pattern。
        """
        categorized = RuleLoader.load(_RULES_DIR())
        rule_yaml = RuleLoader.build_ast_grep_rule_yaml(categorized)

        # 必须含 language + rule.any 头部
        assert rule_yaml.startswith("language: java\nrule:\n  any:\n")
        # 每条规则的每个展开 pattern 都对应一行
        all_patterns: list[str] = []
        for key in ("class_rules", "method_rules", "param_rules"):
            for r in categorized[key]:
                all_patterns.extend(RuleLoader.expand_patterns(r))
        line_count = rule_yaml.count("    - pattern:")
        assert line_count == len(all_patterns)
        # 合并后应包含所有原始 pattern
        for p in all_patterns:
            assert f'- pattern: "{p}"' in rule_yaml

        # HTTP 方法映射完整：RequestMapping→ANY, GetMapping→GET, MessageMapping→WS 等
        http_map = RuleLoader.build_http_method_map(categorized)
        assert http_map["GetMapping"] == "GET"
        assert http_map["PostMapping"] == "POST"
        assert http_map["PutMapping"] == "PUT"
        assert http_map["DeleteMapping"] == "DELETE"
        assert http_map["PatchMapping"] == "PATCH"
        assert http_map["RequestMapping"] == "ANY"
        assert http_map["MessageMapping"] == "WS"
        assert http_map["ExceptionHandler"] == "ANY"
        assert http_map["GET"] == "GET"
        # pattern-either 中短名 Path 与全限定 javax.ws.rs.Path 都注册
        assert http_map["Path"] == "ANY"
        # class_rules 的 @RestController 也注册到映射表（pattern-either 变体都命中）
        assert http_map["RestController"] == "ANY"
        # ControllerAdvice / RestControllerAdvice 注册到映射表
        assert http_map["ControllerAdvice"] == "ANY"
        assert http_map["RestControllerAdvice"] == "ANY"


def _RULES_DIR() -> Path:
    """定位 collectors/rules 目录（与 route_collector.py 同源）。"""
    return Path(__file__).resolve().parents[1] / "rules"


# ============================================================
# pattern-either + 三组规则分类（class/method/param）
# ============================================================

class TestRouteCollectorPatternEither:
    """pattern-either 多变体语法 + 三组规则分类加载。"""

    def test_load_rules_returns_three_categories(self):
        """RuleLoader.load() 返回 dict，含 class_rules/method_rules/param_rules 三组，且都非空。"""
        categorized = RuleLoader.load(_RULES_DIR())
        assert isinstance(categorized, dict)
        # 三个分类 key 都存在
        for key in ("class_rules", "method_rules", "param_rules"):
            assert key in categorized, f"missing category: {key}"
            assert isinstance(categorized[key], list)
            assert len(categorized[key]) > 0, f"{key} 不应为空（spring+jaxrs 都应贡献规则）"

    def test_spring_yaml_has_rest_controller_pattern_either(self):
        """spring.yaml 的 rest_controller 规则必须用 pattern-either，且至少 2 个变体。"""
        categorized = RuleLoader.load(_RULES_DIR())
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
        """RuleLoader.normalize_pattern 应正确解析 pattern 与 pattern-either 两种形式。

        - pattern（单条）→ 返回该 pattern
        - pattern-either（多变体）→ 返回第一个变体的 pattern
        - 都没有 → 返回空字符串
        """
        # 单条 pattern
        single = {"pattern": "@GetMapping($$$)"}
        assert RuleLoader.normalize_pattern(single) == "@GetMapping($$$)"
        assert RuleLoader.expand_patterns(single) == ["@GetMapping($$$)"]

        # 多变体 pattern-either
        multi = {
            "pattern-either": [
                {"pattern": "@RestController($$$)"},
                {"pattern": "@org.springframework.web.bind.annotation.RestController($$$)"},
            ]
        }
        # normalize 取第一个变体
        assert RuleLoader.normalize_pattern(multi) == "@RestController($$$)"
        # expand 展开所有变体
        expanded = RuleLoader.expand_patterns(multi)
        assert len(expanded) == 2
        assert expanded[0] == "@RestController($$$)"
        assert "RestController" in expanded[1]

        # 空规则
        empty: dict = {}
        assert RuleLoader.normalize_pattern(empty) == ""
        assert RuleLoader.expand_patterns(empty) == []

    def test_spring_method_rules_pattern_either_three_variants(self):
        """spring.yaml 每条 method_rules 必须用 pattern-either，且至少 3 个变体（全限定名+短名+无括号）。"""
        categorized = RuleLoader.load(_RULES_DIR())
        spring_method = [r for r in categorized["method_rules"] if r.get("_framework") == "spring"]
        for r in spring_method:
            assert "pattern-either" in r, f"Spring method rule {r.get('id', '?')} must use pattern-either"
            variants = r["pattern-either"]
            assert len(variants) >= 3, f"Spring method rule {r.get('id', '?')} needs >=3 variants, got {len(variants)}"

    def test_spring_yaml_has_message_mapping(self):
        """spring.yaml 必含 MessageMapping 规则（WebSocket 端点）。"""
        categorized = RuleLoader.load(_RULES_DIR())
        spring_method = [r for r in categorized["method_rules"] if r.get("_framework") == "spring"]
        msg = [r for r in spring_method if r.get("id") == "message_mapping"]
        assert len(msg) == 1
        assert msg[0]["http_method"] == "WS"

    def test_spring_yaml_has_exception_handler(self):
        """spring.yaml 必含 ExceptionHandler 规则（异常处理端点）。"""
        categorized = RuleLoader.load(_RULES_DIR())
        spring_method = [r for r in categorized["method_rules"] if r.get("_framework") == "spring"]
        exc = [r for r in spring_method if r.get("id") == "exception_handler"]
        assert len(exc) == 1
        assert exc[0]["http_method"] == "ANY"

    def test_spring_yaml_has_controller_advice(self):
        """spring.yaml class_rules 必含 ControllerAdvice 和 RestControllerAdvice。"""
        categorized = RuleLoader.load(_RULES_DIR())
        spring_class = [r for r in categorized["class_rules"] if r.get("_framework") == "spring"]
        ids = [r.get("id") for r in spring_class]
        assert "controller_advice" in ids
        assert "rest_controller_advice" in ids

    def test_spring_yaml_has_matrix_variable(self):
        """spring.yaml param_rules 必含 MatrixVariable、ModelAttribute、RequestPart、SessionAttribute。"""
        categorized = RuleLoader.load(_RULES_DIR())
        spring_param = [r for r in categorized["param_rules"] if r.get("_framework") == "spring"]
        ids = set(r.get("id") for r in spring_param)
        for expected_id in ("matrix_variable", "model_attribute", "request_part", "session_attribute"):
            assert expected_id in ids, f"spring.yaml param_rules 缺少 {expected_id}"

    def test_jaxrs_method_rules_pattern_either(self):
        """jaxrs.yaml 每条 method_rules（HTTP 动词）必须用 pattern-either（全限定名+短名）。"""
        categorized = RuleLoader.load(_RULES_DIR())
        jaxrs_method = [r for r in categorized["method_rules"] if r.get("_framework") == "jaxrs"]
        for r in jaxrs_method:
            assert "pattern-either" in r, f"JAX-RS method rule {r.get('id', '?')} must use pattern-either"
            variants = r["pattern-either"]
            assert len(variants) >= 2, f"JAX-RS method rule {r.get('id', '?')} needs >=2 variants"


# ============================================================
# 新增：模块单元测试（RuleLoader / AstGrepScanner / RouteEnricher）
# ============================================================

class TestRuleLoaderUnits:
    """RuleLoader 单元测试。"""

    def test_rule_loader_loads_spring_and_jaxrs(self):
        """RuleLoader.load 应同时加载 spring + jaxrs 两个框架。"""
        categorized = RuleLoader.load(_RULES_DIR())
        frameworks = set()
        for key in ("class_rules", "method_rules", "param_rules"):
            for r in categorized[key]:
                frameworks.add(r.get("_framework"))
        # 至少含 spring + jaxrs（struts.yaml 为空不贡献）
        assert "spring" in frameworks
        assert "jaxrs" in frameworks

    def test_rule_loader_skips_nonexistent_dir(self, tmp_path: Path):
        """不存在的目录应返回空分类 dict，不抛异常。"""
        result = RuleLoader.load(tmp_path / "no_such_dir")
        assert set(result.keys()) == {"class_rules", "method_rules", "param_rules"}
        for v in result.values():
            assert v == []

    def test_rule_loader_expand_patterns_annotation_compat(self):
        """annotation 字段（JAX-RS @GET 无参数历史兼容）应展开为 1 个 pattern。"""
        rule = {"annotation": "@GET", "http_method": "GET"}
        assert RuleLoader.expand_patterns(rule) == ["@GET"]


class TestAstGrepScannerUnits:
    """AstGrepScanner 单元测试（不调用 subprocess）。"""

    def test_astgrep_scanner_parse_output(self):
        """parse_output 应从 ast-grep JSON 输出提取标准化条目。"""
        sample = json.dumps([
            {
                "text": '@GetMapping("/{id}")',
                "file": "src/main/java/com/example/UserController.java",
                "range": {"start": {"line": 9, "column": 4}},
            },
            {
                "text": "@PostMapping",
                "file": "src/main/java/com/example/UserController.java",
                "range": {"start": {"line": 14, "column": 4}},
            },
            # 不在 http_method_map 的注解应被过滤掉
            {
                "text": '@Override',
                "file": "x.java",
                "range": {"start": {"line": 0, "column": 0}},
            },
        ])
        http_map = {"GetMapping": "GET", "PostMapping": "POST"}
        results = AstGrepScanner.parse_output(sample, http_map)
        assert len(results) == 2
        # 第一条：注解名、行号（0-indexed + 1）、file 全部正确
        assert results[0]["annotation"] == "GetMapping"
        assert results[0]["args"] == ['"/{id}"']
        assert results[0]["file"].endswith("UserController.java")
        assert results[0]["line"] == 10  # 9 + 1
        assert results[0]["source"] == "annotation"
        # 第二条：无括号注解 args 应为空列表
        assert results[1]["annotation"] == "PostMapping"
        assert results[1]["args"] == []

    def test_astgrep_scanner_parse_output_empty(self):
        """空/非法 JSON 应返回空列表，不抛异常。"""
        assert AstGrepScanner.parse_output("") == []
        assert AstGrepScanner.parse_output("not json") == []

    def test_astgrep_scanner_parse_output_no_filter(self):
        """不传 http_method_map 时保留全部 ast-grep 命中。"""
        sample = json.dumps([
            {"text": "@GetMapping", "file": "a.java",
             "range": {"start": {"line": 0}}},
            {"text": "@Unknown", "file": "b.java",
             "range": {"start": {"line": 5}}},
        ])
        results = AstGrepScanner.parse_output(sample, http_method_map=None)
        assert len(results) == 2


class TestRouteEnricherUnits:
    """RouteEnricher 单元测试。"""

    def test_enricher_adds_http_method(self, ctx):
        """enrich 应从 http_method_map 补全 http_method 字段。"""
        raw = {
            "fqn": "com.example.UserController#getUser",
            "annotation": "GetMapping",
            "args": ["/{id}"],
            "file": "UserController.java",
            "line": 10,
            "method_name": "getUser",
        }
        http_map = {"GetMapping": "GET", "PostMapping": "POST"}
        result = RouteEnricher.enrich(raw, ctx, http_map)
        assert result["http_method"] == "GET"
        # 未知注解回退到 ANY
        assert RouteEnricher.enrich(
            {"annotation": "Unknown", "fqn": "x"}, ctx, http_map,
        )["http_method"] == "ANY"

    def test_enricher_adds_nodes_id_none_when_no_codegraph(self, ctx):
        """codegraph_db 缺失时，nodes_id=None。"""
        ctx.codegraph_db = None
        raw = {"fqn": "x#m", "annotation": "GetMapping", "file": "a.java", "line": 1}
        result = RouteEnricher.enrich(raw, ctx, {"GetMapping": "GET"})
        assert result["nodes_id"] is None

    def test_enricher_lookup_nodes_id_sqlite_like_match(self, ctx, tmp_path: Path):
        """lookup_nodes_id 用 file_path LIKE 模糊匹配 + 行号范围查 nodes.id。"""
        import sqlite3
        fake_db = tmp_path / "codegraph.db"
        conn = sqlite3.connect(str(fake_db))
        conn.execute(
            "CREATE TABLE nodes (id TEXT, kind TEXT, file_path TEXT, "
            "start_line INTEGER, end_line INTEGER)"
        )
        conn.execute(
            "INSERT INTO nodes VALUES (?, 'method', ?, ?, ?)",
            ("method:abc123def456", "src/main/java/com/example/UserController.java", 5, 15),
        )
        conn.commit()
        conn.close()
        ctx.codegraph_db = fake_db

        # 绝对路径也能匹配（LIKE 取末尾4段）
        raw = {"file": "D:/proj/src/main/java/com/example/UserController.java", "start_line": 9}
        result = RouteEnricher.lookup_nodes_id(raw, ctx)
        assert result == "method:abc123def456"

        # 行号不在范围内返回 None
        raw_out = {"file": "src/main/java/com/example/UserController.java", "start_line": 20}
        assert RouteEnricher.lookup_nodes_id(raw_out, ctx) is None

    def test_enricher_lookup_nodes_id_returns_none_on_sqlite_error(self, ctx, tmp_path: Path):
        """sqlite3 连接失败时 lookup_nodes_id 返回 None。"""
        # 用无效文件作为 codegraph_db（非 SQLite 格式）
        fake_db = tmp_path / "codegraph.db"
        fake_db.write_text("not-a-sqlite-file", encoding="utf-8")
        ctx.codegraph_db = fake_db

        raw = {"file": "a.java", "start_line": 5}
        result = RouteEnricher.lookup_nodes_id(raw, ctx)
        assert result is None

    def test_enricher_lookup_nodes_id_returns_none_missing_file_or_line(self, ctx, tmp_path: Path):
        """file 或 start_line 缺失时返回 None。"""
        fake_db = tmp_path / "codegraph.db"
        fake_db.write_text("dummy", encoding="utf-8")
        ctx.codegraph_db = fake_db

        # 缺 file
        assert RouteEnricher.lookup_nodes_id({"start_line": 5}, ctx) is None
        # 缺 start_line
        assert RouteEnricher.lookup_nodes_id({"file": "a.java"}, ctx) is None
        # start_line = 0
        assert RouteEnricher.lookup_nodes_id({"file": "a.java", "start_line": 0}, ctx) is None

    def test_enricher_derives_fqn_from_path(self, ctx):
        """fqn 缺失时由 file + class_fqn 推导。"""
        raw = {
            "annotation": "GetMapping",
            "file": "src/main/java/com/example/UserController.java",
            "line": 10,
            "method_name": "getUser",
        }
        result = RouteEnricher.enrich(raw, ctx, {"GetMapping": "GET"})
        assert result["fqn"] == "com.example.UserController"

    def test_enricher_count_by_method(self):
        """count_by_method 同时支持旧 http_method（单值）与新 http_methods（列表）。"""
        # 旧 ast-grep 路径：单值
        legacy_items = [
            {"http_method": "GET"},
            {"http_method": "GET"},
            {"http_method": "POST"},
            {"http_method": "ANY"},
        ]
        assert RouteEnricher.count_by_method(legacy_items) == {"GET": 2, "POST": 1, "ANY": 1}

        # 新 javaparser 路径：列表，含 {GET,POST} 展开后的多方法
        jp_items = [
            {"http_methods": ["GET"]},
            {"http_methods": ["GET", "POST"]},
            {"http_methods": ["POST"]},
        ]
        # GET: 2 条贡献（第一条 + 第二条的 GET）；POST: 2 条贡献
        assert RouteEnricher.count_by_method(jp_items) == {"GET": 2, "POST": 2}


# ============================================================
# 新增：JavaparserScanner 单元测试
# ============================================================

class TestJavaparserScannerUnits:
    """JavaparserScanner 单元测试（mock subprocess，不实际调 java）。"""

    def test_javaparser_scanner_parses_routes(self, tmp_path: Path):
        """scan 应解析 javaparser --routes 输出为路由列表（含 full_url + http_methods）。"""
        sample_output = json.dumps([
            {
                "class_fqn": "com.example.UserController",
                "class_base_path": "/api/users",
                "method_fqn": "com.example.UserController#getUser",
                "method_name": "getUser",
                "full_url": "/api/users/{id}",
                "http_methods": ["GET"],
                "annotation": "GetMapping",
                "file": str(tmp_path / "UserController.java"),
                "start_line": 9,
            },
            {
                "class_fqn": "com.example.UserController",
                "class_base_path": "/api/users",
                "method_fqn": "com.example.UserController#multi",
                "method_name": "multi",
                "full_url": "/api/users/multi",
                "http_methods": ["GET", "POST"],  # {GET,POST} 数组展开
                "annotation": "RequestMapping",
                "file": str(tmp_path / "UserController.java"),
                "start_line": 14,
            },
        ])
        java_file = tmp_path / "UserController.java"
        java_file.write_text("// dummy", encoding="utf-8")

        fake_proc = type("P", (), {
            "returncode": 0,
            "stdout": sample_output,
            "stderr": "",
        })()
        with patch.object(JavaparserScanner, "jar_path", return_value=java_file):
            with patch("scripts.exposure.collectors.route.javaparser_scanner.subprocess.run",
                       return_value=fake_proc) as mock_run:
                routes = JavaparserScanner.scan(java_file, source_root=tmp_path)

        # 调用形态：java -jar <jar> --routes <file> <source_root>
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "java"
        assert cmd[1] == "-jar"
        assert "--routes" in cmd
        # 两条路由全部解析成功
        assert len(routes) == 2
        assert routes[0]["full_url"] == "/api/users/{id}"
        assert routes[0]["http_methods"] == ["GET"]
        assert routes[1]["http_methods"] == ["GET", "POST"]
        assert routes[1]["method_fqn"] == "com.example.UserController#multi"

    def test_scan_directory_aggregates(self, tmp_path: Path):
        """scan_directory 应聚合多文件的解析结果。"""
        f1 = tmp_path / "A.java"
        f2 = tmp_path / "B.java"
        f1.write_text("// a", encoding="utf-8")
        f2.write_text("// b", encoding="utf-8")

        route_a = [_jp_route(method_fqn="com.example.A#m1", method_name="m1")]
        route_b = [
            _jp_route(method_fqn="com.example.B#m2", method_name="m2"),
            _jp_route(method_fqn="com.example.B#m3", method_name="m3"),
        ]
        with patch.object(JavaparserScanner, "scan", side_effect=[route_a, route_b]):
            result = JavaparserScanner.scan_directory([f1, f2])

        assert len(result) == 3
        assert result[0]["method_fqn"].endswith("A#m1")
        assert result[2]["method_fqn"].endswith("B#m3")

    def test_scan_returns_empty_when_jar_missing(self, tmp_path: Path):
        """JAR 不存在时 scan 返回空列表（不抛异常）。"""
        java_file = tmp_path / "X.java"
        java_file.write_text("// x", encoding="utf-8")
        with patch.object(JavaparserScanner, "jar_path",
                          return_value=tmp_path / "no_jar.jar"):
            assert JavaparserScanner.scan(java_file) == []

    def test_scan_returns_empty_on_nonzero_returncode(self, tmp_path: Path):
        """子进程非零返回码时返回空列表。"""
        java_file = tmp_path / "X.java"
        java_file.write_text("// x", encoding="utf-8")
        fake_proc = type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        with patch.object(JavaparserScanner, "jar_path", return_value=java_file):
            with patch("scripts.exposure.collectors.route.javaparser_scanner.subprocess.run",
                       return_value=fake_proc):
                assert JavaparserScanner.scan(java_file) == []

    def test_scan_returns_empty_on_invalid_json(self, tmp_path: Path):
        """非法 JSON 输出时返回空列表。"""
        java_file = tmp_path / "X.java"
        java_file.write_text("// x", encoding="utf-8")
        fake_proc = type("P", (), {"returncode": 0, "stdout": "not json", "stderr": ""})()
        with patch.object(JavaparserScanner, "jar_path", return_value=java_file):
            with patch("scripts.exposure.collectors.route.javaparser_scanner.subprocess.run",
                       return_value=fake_proc):
                assert JavaparserScanner.scan(java_file) == []


# ============================================================
# 新增：FileLocator 单元测试
# ============================================================

class TestFileLocatorUnits:
    """FileLocator 单元测试。"""

    def test_file_locator_returns_files_with_astgrep(self, tmp_path: Path):
        """ast-grep 可用时返回命中文件列表（去重）。"""
        with patch("scripts.exposure.collectors.route.file_locator.shutil.which",
                   return_value="/usr/bin/ast-grep"):
            fake_proc = type("P", (), {
                "returncode": 0,
                "stdout": json.dumps([
                    {"file": str(tmp_path / "A.java")},
                    {"file": str(tmp_path / "A.java")},  # 重复
                    {"file": str(tmp_path / "B.java")},
                ]),
                "stderr": "",
            })()
            with patch("scripts.exposure.collectors.route.file_locator.subprocess.run",
                       return_value=fake_proc):
                files = FileLocator.locate(tmp_path)

        # 去重后 2 个文件
        assert len(files) == 2
        paths = {f.name for f in files}
        assert paths == {"A.java", "B.java"}

    def test_file_locator_fallback_to_glob(self, tmp_path: Path):
        """ast-grep 不可用时降级为 glob 全量扫描 .java。"""
        src = tmp_path / "src" / "main" / "java"
        src.mkdir(parents=True)
        (src / "A.java").write_text("// a", encoding="utf-8")
        (src / "B.java").write_text("// b", encoding="utf-8")
        (src / "readme.txt").write_text("not java", encoding="utf-8")

        with patch("scripts.exposure.collectors.route.file_locator.shutil.which",
                   return_value=None):
            files = FileLocator.locate(tmp_path)

        names = {f.name for f in files}
        assert names == {"A.java", "B.java"}

    def test_file_locator_returns_empty_when_dir_missing(self, tmp_path: Path):
        """项目目录不存在时返回空列表，不抛异常。"""
        with patch("scripts.exposure.collectors.route.file_locator.shutil.which",
                   return_value=None):
            assert FileLocator.locate(tmp_path / "no_such_dir") == []

    def test_file_locator_fallback_when_astgrep_times_out(self, tmp_path: Path):
        """ast-grep 超时时降级为 glob。"""
        java_file = tmp_path / "X.java"
        java_file.write_text("// x", encoding="utf-8")
        import subprocess as sp
        with patch("scripts.exposure.collectors.route.file_locator.shutil.which",
                   return_value="/usr/bin/ast-grep"):
            with patch("scripts.exposure.collectors.route.file_locator.subprocess.run",
                       side_effect=sp.TimeoutExpired(cmd=["ast-grep"], timeout=1)):
                files = FileLocator.locate(tmp_path)
        assert any(f.name == "X.java" for f in files)


# ============================================================
# 新增：RouteEnricher.enrich_routes 单元测试
# ============================================================

class TestEnrichRoutesUnits:
    """RouteEnricher.enrich_routes 单元测试。"""

    def test_enrich_routes_preserves_javaparser_fields(self, ctx):
        """enrich_routes 应保留 javaparser 提供的 full_url/http_methods/class_base_path。"""
        routes = [
            _jp_route(
                full_url="/api/users/{id}",
                http_methods=["GET"],
                class_base_path="/api/users",
                method_fqn="com.example.UserController#getUser",
                method_name="getUser",
            ),
        ]
        items = RouteEnricher.enrich_routes(routes, ctx)
        assert len(items) == 1
        it = items[0]
        assert it["full_url"] == "/api/users/{id}"
        assert it["http_methods"] == ["GET"]
        assert it["class_base_path"] == "/api/users"
        assert it["fqn"] == "com.example.UserController#getUser"

    def test_enrich_routes_nodes_id_none_when_no_codegraph(self, ctx):
        """codegraph_db 缺失时 nodes_id=None。"""
        ctx.codegraph_db = None
        routes = [_jp_route(file="a.java", start_line=1, annotation="GetMapping")]
        items = RouteEnricher.enrich_routes(routes, ctx)
        assert items[0]["nodes_id"] is None

    def test_enrich_routes_has_external_param_from_url(self, ctx):
        """has_external_param 由 full_url 含 {param} 推断。"""
        routes = [
            _jp_route(full_url="/api/users/{id}", method_fqn="c.A#m1", method_name="m1"),
            _jp_route(full_url="/api/users", method_fqn="c.A#m2", method_name="m2"),
        ]
        items = RouteEnricher.enrich_routes(routes, ctx)
        assert items[0]["has_external_param"] is True
        assert items[1]["has_external_param"] is False
