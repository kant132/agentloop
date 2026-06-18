"""
test_sink_registry.py — SinkRegistry / sink 预置库 单元测试 (8 个)

覆盖:
  - PRESET_SINKS 数据完整性
  - is_preset_sink: 预置 sink 命中 (含带实参签名)
  - identify_dynamic_sinks: 非 groupId 命名空间 = 动态 sink
  - count_sinks_in_chain: 节点 sinks 汇总
  - match_preset_sinks: 链上 sink 命中预置库计数
"""
import pytest

from sink_registry import (
    PRESET_SINKS,
    is_preset_sink,
    identify_dynamic_sinks,
    count_sinks_in_chain,
    match_preset_sinks,
)


# =========================================================================
# PRESET_SINKS 数据
# =========================================================================

def test_preset_sinks_non_empty_dict():
    """PRESET_SINKS 是非空 dict, 含主要注入类别"""
    assert isinstance(PRESET_SINKS, dict)
    assert len(PRESET_SINKS) > 0
    keys_joined = " ".join(PRESET_SINKS.keys())
    # 至少含 sql / deserialization / ssrf 三类核心
    assert any("sql" in k.lower() for k in PRESET_SINKS)
    assert any("deser" in k.lower() for k in PRESET_SINKS)
    assert any("ssrf" in k.lower() for k in PRESET_SINKS)


def test_preset_sinks_values_are_string_lists():
    """每个类别值是 list[str], 非空"""
    for cat, fqns in PRESET_SINKS.items():
        assert isinstance(fqns, list), f"{cat} 应为 list"
        assert all(isinstance(x, str) for x in fqns), f"{cat} 元素应为 str"
        assert len(fqns) > 0, f"{cat} 不应为空"


# =========================================================================
# is_preset_sink
# =========================================================================

def test_is_preset_sink_true_for_known():
    """已知 JDBC Statement.executeQuery 命中预置"""
    assert is_preset_sink("java.sql.Statement.executeQuery") is True


def test_is_preset_sink_false_for_unknown():
    """未知方法不命中"""
    assert is_preset_sink("com.myproject.SafeUtil.doNothing") is False


def test_is_preset_sink_strips_args():
    """带实参签名 'Class.method(args)' 仍能命中"""
    assert is_preset_sink("java.sql.Statement.executeQuery(sql + userInput)") is True


# =========================================================================
# identify_dynamic_sinks
# =========================================================================

def test_identify_dynamic_sinks_excludes_group_namespace():
    """groupId 命名空间方法不是动态 sink, 外部库是"""
    methods = [
        "org.owasp.webgoat.LoginController#doLogin",
        "org.owasp.webgoat.Helper#util",
        "java.sql.Statement.executeQuery",
        "org.springframework.jdbc.core.JdbcTemplate.query",
    ]
    sinks = identify_dynamic_sinks("org.owasp.webgoat", methods)
    fqns = [s["fqn"] for s in sinks]
    # webgoat 内部方法不算
    assert all(not fqn.startswith("org.owasp.webgoat") for fqn in fqns)
    # 外部方法算
    assert any("java.sql.Statement" in f for f in fqns)
    assert any("JdbcTemplate" in f for f in fqns)


# =========================================================================
# count_sinks_in_chain
# =========================================================================

def test_count_sinks_in_chain_sums_node_sinks():
    """节点 sinks 列表长度之和"""
    nodes = [
        {"fqn": "a", "node_id": "1", "sinks": ["X.query", "Y.exec"]},
        {"fqn": "b", "node_id": "2", "sinks": ["Z.load"]},
        {"fqn": "c", "node_id": "3", "sinks": []},
    ]
    assert count_sinks_in_chain(nodes) == 3


# =========================================================================
# match_preset_sinks
# =========================================================================

def test_match_preset_sinks_counts_only_preset_hits():
    """只数命中预置库的 sink"""
    nodes = [
        {"fqn": "a", "node_id": "1", "sinks": [
            "java.sql.Statement.executeQuery",    # 命中 preset
            "com.internal.Util.get",             # 未命中
        ]},
        {"fqn": "b", "node_id": "2", "sinks": [
            "java.lang.Runtime.exec",            # 命中 preset
        ]},
    ]
    assert match_preset_sinks(nodes) == 2
