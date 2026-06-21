"""
test_scanner_utils.py — scanner_utils 纯函数测试集 (14 个测试)
覆盖 node_hash_key / fqn_to_method_name / inject_sink_comment /
   get_body / detect_annotation_changes / classify_route / validate
"""
import json
import tempfile
from pathlib import Path

import pytest

from scanner_utils import (
    node_hash_key,
    fqn_to_method_name,
    inject_sink_comment,
    get_body,
    detect_annotation_changes,
    classify_route,
    validate,
)


# =============================================================================
# node_hash_key
# =============================================================================

def test_node_hash_key_basic(tmp_path):
    """验证 nodes.id 直接返回字符串,无 md5 混淆"""
    assert node_hash_key("abc123") == "abc123"
    assert node_hash_key("  def456  ") == "def456"
    assert node_hash_key(12345) == "12345"


# =============================================================================
# fqn_to_method_name
# =============================================================================

def test_fqn_to_method_name_with_hash():
    """类#方法 格式,提取方法名"""
    assert fqn_to_method_name("com.example.MyClass#getData") == "getData"
    assert fqn_to_method_name("com.example.MyClass#") == ""


def test_fqn_to_method_name_with_dots():
    """包.类.方法 格式,提取方法名"""
    assert fqn_to_method_name("com.example.MyClass.getData") == "getData"
    assert fqn_to_method_name("com.example.MyClass") == "MyClass"


# =============================================================================
# inject_sink_comment
# =============================================================================

def test_inject_sink_comment_no_match():
    """行中没有 third_party_calls 不匹配,不注入"""
    line = '    String result = client.get(url);'
    third_party_calls = ["com.example.Utils#helper"]
    fqn_sink_set = {"com.example.Utils#helper"}
    result = inject_sink_comment(line, third_party_calls, fqn_sink_set)
    assert result == line


def test_inject_sink_comment_with_match():
    """行中包含外部调用且方法调用带括号,在行尾追加 fqn 注释"""
    line = '    client.get(url);\n'
    third_party_calls = ["com.example.MyClient#get"]
    fqn_sink_set = {"com.example.MyClient#get"}
    result = inject_sink_comment(line, third_party_calls, fqn_sink_set)
    assert "//fqn: com.example.MyClient#get" in result
    assert "client.get(url);" in result


def test_inject_sink_comment_already_commented():
    """已以 // /* * 开头的行不注入"""
    for prefix in ["// #xxx", "/* blocked", " * comment"]:
        line = f"    {prefix}  client.get(url);"
        result = inject_sink_comment(line, ["com.example.MyClient#get"],
                                     {"com.example.MyClient#get"})
        assert result == line


def test_inject_sink_comment_multiple_sinks():
    """多个外部调用匹配到第一个时注入"""
    line = '    resp = http.get(url);\n'
    third_party_calls = ["com.http.HttpClient#get", "com.http.HttpClient#post"]
    fqn_sink_set = {"com.http.HttpClient#get", "com.http.HttpClient#post"}
    result = inject_sink_comment(line, third_party_calls, fqn_sink_set)
    assert "//fqn: com.http.HttpClient#get" in result


# =============================================================================
# get_body
# =============================================================================

class MockMemurai:
    """模拟 memurai 缓存"""
    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str):
        return self._data.get(key)


def test_get_body_success():
    """正常从缓存获取 method body"""
    memurai = MockMemurai({
        "com.example.GroupA:method:node007": json.dumps({
            "body": "public String getData() { return \"data\"; }"
        })
    })
    result = get_body("com.example.GroupA", "node007", memurai)
    assert result == "public String getData() { return \"data\"; }"


def test_get_body_not_found():
    """key 不存在返回 None"""
    memurai = MockMemurai({})
    assert get_body("com.example.GroupA", "node999", memurai) is None


def test_get_body_invalid_json():
    """缓存值非合法 JSON 时返回 None"""
    memurai = MockMemurai({
        "com.example.GroupA:method:node007": "not json{"
    })
    assert get_body("com.example.GroupA", "node007", memurai) is None


# =============================================================================
# detect_annotation_changes
# =============================================================================

def test_detect_annotation_changes_no_changes(tmp_path):
    """body_hash 一致时返回空列表"""
    project = tmp_path / "project"
    group = project / "com.example.GroupA"
    group.mkdir(parents=True)
    annot_dir = group / "注解"
    annot_dir.mkdir()
    # 写入 body 的 md5 前 16 位与 recorded_hash 相同
    body_text = "public void test() {}"
    import hashlib
    body_hash = hashlib.md5(body_text.encode("utf-8")).hexdigest()[:16]
    data = {
        "annotations": [{
            "fqn": "com.example.GroupA.MyClass#test",
            "body": body_text,
            "body_hash": body_hash
        }]
    }
    with open(annot_dir / "specific.json", "w", encoding="utf-8") as f:
        json.dump(data, f)
    result = detect_annotation_changes(str(project), "com.example.GroupA")
    assert result == []


def test_detect_annotation_changes_with_changes(tmp_path):
    """body_hash 不一致时返回变更条目"""
    project = tmp_path / "project"
    group = project / "com.example.GroupA"
    group.mkdir(parents=True)
    annot_dir = group / "注解"
    annot_dir.mkdir()
    data = {
        "annotations": [{
            "fqn": "com.example.GroupA.MyClass#test",
            "body": "public void test() {}",
            "body_hash": "0000000000000000"
        }]
    }
    with open(annot_dir / "specific.json", "w", encoding="utf-8") as f:
        json.dump(data, f)
    result = detect_annotation_changes(str(project), "com.example.GroupA")
    assert len(result) == 1
    assert result[0]["fqn"] == "com.example.GroupA.MyClass#test"
    assert result[0]["old_hash"] == "0000000000000000"


# =============================================================================
# classify_route
# =============================================================================

def test_classify_route_public():
    """fqn 在 global_public 注解中 → public"""
    global_public = {
        "annotations": [{"fqn": "com.example.SharedLib#openApi"}]
    }
    project_specific = {"annotations": []}
    result = classify_route("com.example.SharedLib#openApi",
                           global_public, project_specific)
    assert result == "public"


def test_classify_route_specific():
    """fqn 在 project_specific 注解中 → specific"""
    global_public = {"annotations": []}
    project_specific = {
        "annotations": [{"fqn": "com.example.GroupA.PrivateAPI#getSecret"}]
    }
    result = classify_route("com.example.GroupA.PrivateAPI#getSecret",
                           global_public, project_specific)
    assert result == "specific"


def test_classify_route_unknown():
    """fqn 不在任何注解中且不以 groupId 前缀开头 → unknown"""
    global_public = {"annotations": []}
    project_specific = {"annotations": [], "groupId": "com.example.GroupA"}
    # 使用与 groupId 无前缀关系的 fqn
    result = classify_route("com.otherlib.Utils#randomMethod",
                           global_public, project_specific)
    assert result == "unknown"


# =============================================================================
# validate (happy path)
# =============================================================================

def test_validate_happy_path(tmp_path):
    """所有断言通过: routes 数 == chain 数 == signature_hash 对应"""
    project = tmp_path / "project"
    group = project / "com.example.GroupA"
    group.mkdir(parents=True)

    # chains/ 目录
    chains_dir = group / "chains"
    chains_dir.mkdir()
    for h in ["hash1", "hash2", "hash3"]:
        (chains_dir / f"{h}.json").write_text("{}", encoding="utf-8")

    # routes.json
    routes_data = {
        "routes": [
            {"signature_hash": "hash1", "annotation_fqn": "com.example.A#m1"},
            {"signature_hash": "hash2", "annotation_fqn": "com.example.A#m2"},
            {"signature_hash": "hash3", "annotation_fqn": "com.example.A#m3"},
        ]
    }
    with open(group / "routes.json", "w", encoding="utf-8") as f:
        json.dump(routes_data, f)

    # 注解目录
    annot_dir = group / "注解"
    annot_dir.mkdir()
    specific_data = {
        "annotations": [
            {"fqn": "com.example.A#m1"},
            {"fqn": "com.example.A#m2"},
            {"fqn": "com.example.A#m3"},
        ]
    }
    with open(annot_dir / "specific.json", "w", encoding="utf-8") as f:
        json.dump(specific_data, f)

    # 全局注解
    public_data = {"annotations": []}
    with open(project / "注解_public.json", "w", encoding="utf-8") as f:
        json.dump(public_data, f)

    validate(str(project), "com.example.GroupA")  # 不抛异常 = 通过


def test_validate_routes_chain_mismatch(tmp_path):
    """路由数 ≠ chain 文件数时抛出 AssertionError"""
    project = tmp_path / "project"
    group = project / "com.example.GroupA"
    group.mkdir(parents=True)

    chains_dir = group / "chains"
    chains_dir.mkdir()
    (chains_dir / "hash1.json").write_text("{}", encoding="utf-8")

    routes_data = {
        "routes": [
            {"signature_hash": "hash1", "annotation_fqn": "com.example.A#m1"},
            {"signature_hash": "hash2", "annotation_fqn": "com.example.A#m2"},
        ]
    }
    with open(group / "routes.json", "w", encoding="utf-8") as f:
        json.dump(routes_data, f)

    annot_dir = group / "注解"
    annot_dir.mkdir()
    with open(annot_dir / "specific.json", "w", encoding="utf-8") as f:
        json.dump({"annotations": []}, f)
    with open(project / "注解_public.json", "w", encoding="utf-8") as f:
        json.dump({"annotations": []}, f)

    with pytest.raises(AssertionError, match="路由数"):
        validate(str(project), "com.example.GroupA")