"""test_load_counter.py — LoadCounter sqlite 单元测试。

策略：每个用例用独立的 tmp_path，保证 sqlite 文件相互隔离；
不依赖任何外部资源（不连 Memurai、不读项目数据）。
"""
from __future__ import annotations

import sqlite3

import pytest

from load_counter import LoadCounter, SOURCES


def _make(tmp_path):
    """每个用例独立的 LoadCounter（tmp_path 隔离 db 文件）。"""
    return LoadCounter(tmp_path)


# =============================================================================
# 初始化与 schema
# =============================================================================

def test_init_creates_db_and_schema(tmp_path):
    """构造器应在 diag/ 下创建 loads.db 并建表。"""
    counter = _make(tmp_path)
    db_file = tmp_path / "diag" / "loads.db"
    assert db_file.exists()

    # 表存在且列齐全
    with sqlite3.connect(str(db_file)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(loads)")}
    assert cols == {"id", "node_id", "fqn", "chain_id", "loaded_at", "source"}


def test_init_idempotent(tmp_path):
    """重复构造不会破坏已有数据。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "com.Foo#bar", "chain-A", "first_5_layer")

    # 重新构造（模拟下一轮 agent 复用同一 db）
    counter2 = LoadCounter(tmp_path)
    assert counter2.count_total_loads() == 1


# =============================================================================
# record_load
# =============================================================================

def test_record_load_inserts_row(tmp_path):
    """正常写入一条记录。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "com.Foo#bar", "chain-A", "ai_request")

    assert counter.count_total_loads() == 1
    assert counter.count_loads_by_chain("chain-A") == 1


def test_record_load_default_source_is_ai_request(tmp_path):
    """默认 source = ai_request。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "com.Foo#bar", "chain-A")
    with sqlite3.connect(str(tmp_path / "diag" / "loads.db")) as conn:
        row = conn.execute(
            "SELECT source FROM loads WHERE node_id = ?", ("n1",)
        ).fetchone()
    assert row[0] == "ai_request"


def test_record_load_rejects_invalid_source(tmp_path):
    """非白名单 source 抛 ValueError。"""
    counter = _make(tmp_path)
    with pytest.raises(ValueError, match="非法 source"):
        counter.record_load("n1", "fqn", "chain-A", source="bogus")


def test_record_load_accepts_all_whitelisted_sources(tmp_path):
    """SOURCES 全部合法可写入。"""
    counter = _make(tmp_path)
    for src in SOURCES:
        counter.record_load(f"n_{src}", "fqn", "chain", src)
    assert counter.count_total_loads() == len(SOURCES)


# =============================================================================
# 聚合查询
# =============================================================================

def test_count_loads_by_chain_isolated(tmp_path):
    """不同 chain_id 计数互不干扰。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "f", "chain-A", "first_5_layer")
    counter.record_load("n2", "f", "chain-A", "first_5_layer")
    counter.record_load("n3", "f", "chain-B", "ai_request")

    assert counter.count_loads_by_chain("chain-A") == 2
    assert counter.count_loads_by_chain("chain-B") == 1
    assert counter.count_loads_by_chain("chain-unknown") == 0


def test_count_total_loads(tmp_path):
    """全局总计数。"""
    counter = _make(tmp_path)
    for i in range(5):
        counter.record_load(f"n{i}", f"f{i}", "chain-A", "ai_request")
    assert counter.count_total_loads() == 5


def test_count_unique_node_ids_dedup(tmp_path):
    """同一 node_id 多次加载只计一次。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "f", "chain-A", "first_5_layer")
    counter.record_load("n1", "f", "chain-A", "cache_hit")
    counter.record_load("n2", "f", "chain-A", "ai_request")

    assert counter.count_total_loads() == 3
    assert counter.count_unique_node_ids() == 2


# =============================================================================
# get_metric
# =============================================================================

def test_get_metric_ratio_below_threshold(tmp_path):
    """4 次加载 / 16 次缓存 = 0.25，理想区间。"""
    counter = _make(tmp_path)
    for i in range(4):
        counter.record_load(f"n{i}", f"f{i}", "chain-A", "cache_hit")

    metric = counter.get_metric("chain-A", cached_count=16)
    assert metric == {"loads": 4, "cached": 16, "ratio": 0.25}


def test_get_metric_zero_cached_returns_zero_ratio(tmp_path):
    """cached_count = 0 不应除零。"""
    counter = _make(tmp_path)
    counter.record_load("n1", "f", "chain-A", "ai_request")
    metric = counter.get_metric("chain-A", cached_count=0)
    assert metric["ratio"] == 0.0


def test_get_metric_rejects_negative_cached(tmp_path):
    """负 cached_count 抛 ValueError。"""
    counter = _make(tmp_path)
    with pytest.raises(ValueError, match="cached_count"):
        counter.get_metric("chain-A", cached_count=-1)
