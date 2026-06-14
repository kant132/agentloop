"""Tests for scanner_utils.py — 暴露面扫描核心函数。"""
import json
import os
import sys
import tempfile
from pathlib import Path

# 被测模块
import scanner_utils


# --------------------- node_hash_key tests ---------------------

def test_node_hash_key_string_passthrough():
    assert scanner_utils.node_hash_key("method:9c1a3f4e") == "method:9c1a3f4e"


def test_node_hash_key_int_to_str():
    assert scanner_utils.node_hash_key(123) == "123"


def test_node_hash_key_strip_whitespace():
    assert scanner_utils.node_hash_key("  spaced  ") == "spaced"


# --------------------- fqn_to_method_name tests ---------------------

def test_fqn_to_method_name_hash_separator():
    assert scanner_utils.fqn_to_method_name("org.example.Foo#bar") == "bar"


def test_fqn_to_method_name_dot_separator():
    assert scanner_utils.fqn_to_method_name("a.b.c.d") == "d"


def test_fqn_to_method_name_no_separator():
    assert scanner_utils.fqn_to_method_name("onlyName") == "onlyName"


# --------------------- inject_sink_comment tests ---------------------

def test_inject_sink_comment_no_match():
    line = "    some normal code();"
    result = scanner_utils.inject_sink_comment(
        line, ["com.External.foo"], {"com.External.foo"}
    )
    assert result == line  # 不匹配 → 原样


def test_inject_sink_comment_with_match():
    line = "    foo.doSomething();"
    result = scanner_utils.inject_sink_comment(
        line, ["com.External.foo"], {"com.External.foo"}
    )
    # 匹配 → 插入 sink 注释, 保留原行
    assert "// sink: com.External" in result
    assert "foo.doSomething();" in result


def test_inject_sink_comment_already_commented():
    line = "    // existing comment"
    result = scanner_utils.inject_sink_comment(
        line, ["com.External.foo"], {"com.External.foo"}
    )
    assert result == line  # 已是注释 → 跳过


# --------------------- get_body tests (mock memurai) ---------------------

class MockMemurai:
    def __init__(self, data: dict):
        self.data = data
    def get(self, key: str):
        val = self.data.get(key)
        return json.dumps(val) if val is not None else None


def test_get_body_cache_hit():
    memurai = MockMemurai({
        "org.example:method:m1": {"body": "public void m1() {}"}
    })
    body = scanner_utils.get_body("org.example", "m1", memurai)
    assert body == "public void m1() {}"


def test_get_body_cache_miss():
    memurai = MockMemurai({})
    body = scanner_utils.get_body("org.example", "m2", memurai)
    assert body is None


# --------------------- detect_annotation_changes tests ---------------------

def test_detect_annotation_changes_hash_match():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        group_dir = tmp_path / "org.example"
        ann_dir = group_dir / "注解"
        ann_dir.mkdir(parents=True)
        # 正确 hash (MD5 of "") = d41d8cd98f00b204
        import hashlib
        body = "some body"
        correct_hash = hashlib.md5(body.encode()).hexdigest()[:16]
        data = {"annotations": [{"fqn": "a.b.C", "body": body, "body_hash": correct_hash}]}
        (ann_dir / "specific.json").write_text(json.dumps(data), encoding="utf-8")
        result = scanner_utils.detect_annotation_changes(tmp_path, "org.example")
        assert result == []


def test_detect_annotation_changes_hash_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        group_dir = tmp_path / "org.example"
        ann_dir = group_dir / "注解"
        ann_dir.mkdir(parents=True)
        data = {"annotations": [{"fqn": "a.b.C", "body": "new body", "body_hash": "old_hash"}]}
        (ann_dir / "specific.json").write_text(json.dumps(data), encoding="utf-8")
        result = scanner_utils.detect_annotation_changes(tmp_path, "org.example")
        assert len(result) == 1
        assert result[0]["fqn"] == "a.b.C"
        assert result[0]["old_hash"] == "old_hash"


# --------------------- classify_route tests ---------------------

def test_classify_route_public():
    pub = {"annotations": [{"fqn": "org.springframework.GetMapping"}]}
    spec = {"annotations": [], "groupId": "org.example"}
    assert scanner_utils.classify_route("org.springframework.GetMapping", pub, spec) == "public"


def test_classify_route_specific_in_table():
    pub = {"annotations": []}
    spec = {"annotations": [{"fqn": "org.example.Custom"}], "groupId": "org.example"}
    assert scanner_utils.classify_route("org.example.Custom", pub, spec) == "specific"


def test_classify_route_specific_by_prefix():
    pub = {"annotations": []}
    spec = {"annotations": [], "groupId": "org.example"}
    assert scanner_utils.classify_route("org.example.Another", pub, spec) == "specific"


def test_classify_route_unknown():
    pub = {"annotations": []}
    spec = {"annotations": [], "groupId": "org.example"}
    assert scanner_utils.classify_route("unknown.Annotation", pub, spec) == "unknown"


# --------------------- validate tests ---------------------

def test_validate_happy_path():
    """所有 3 条断言通过。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        group_dir = tmp_path / "org.example"
        (group_dir / "chains").mkdir(parents=True)
        
        # routes.json
        routes = {
            "routes": [
                {"signature_hash": "abc123", "annotation_fqn": "org.springframework.GetMapping"},
                {"signature_hash": "def456", "annotation_fqn": "org.example.Custom"}
            ]
        }
        (group_dir / "routes.json").write_text(json.dumps(routes), encoding="utf-8")
        
        # chains/*.json
        (group_dir / "chains" / "abc123.json").write_text("{}", encoding="utf-8")
        (group_dir / "chains" / "def456.json").write_text("{}", encoding="utf-8")
        
        # 注解_public.json
        pub = {"annotations": [{"fqn": "org.springframework.GetMapping"}]}
        (tmp_path / "注解_public.json").write_text(json.dumps(pub), encoding="utf-8")
        
        # specific.json
        spec = {"annotations": [{"fqn": "org.example.Custom"}]}
        (group_dir / "注解").mkdir(parents=True)
        (group_dir / "注解" / "specific.json").write_text(json.dumps(spec), encoding="utf-8")
        
        # 不应抛异常
        scanner_utils.validate(tmp_path, "org.example")


def test_validate_routes_chain_mismatch():
    """路由数 != chain 文件数 → AssertionError。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        group_dir = tmp_path / "org.example"
        (group_dir / "chains").mkdir(parents=True)
        
        routes = {"routes": [{"signature_hash": "abc123"}]}
        (group_dir / "routes.json").write_text(json.dumps(routes), encoding="utf-8")
        # chains 目录为空 (0 个 chain 文件, 但有 1 个路由)
        
        # 注解表(为完整性建立, 不会走到第三断言)
        (tmp_path / "注解_public.json").write_text(json.dumps({"annotations": []}), encoding="utf-8")
        (group_dir / "注解").mkdir(parents=True)
        (group_dir / "注解" / "specific.json").write_text(json.dumps({"annotations": []}), encoding="utf-8")
        
        try:
            scanner_utils.validate(tmp_path, "org.example")
            assert False, "Should have raised AssertionError"
        except AssertionError as e:
            assert "路由数" in str(e) or "chain" in str(e)