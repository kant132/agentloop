"""load_counter.py — 方法体加载次数追踪（sqlite3 标准库驱动）。

职责：
- 落地每一次方法体加载到 ``{loop_audit_dir}/diag/loads.db``
- 支持按 chain / 全局 / 唯一 node 维度聚合
- 提供 ``get_metric(chain_id, cached_count)`` 计算加载/缓存比（理想 < 0.3）

约定：
- ``source`` 取值受 ``SOURCES`` 白名单约束，非法值会抛 ValueError
- 表结构幂等：``_init_db`` 每次构造时 ``CREATE TABLE IF NOT EXISTS``
- 所有 SQL 走参数化（防 SQL 注入）
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

# 加载来源白名单
SOURCES = ("ai_request", "first_5_layer", "cache_hit")

# 表 schema（版本化留口）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS loads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    TEXT    NOT NULL,
    fqn        TEXT,
    chain_id   TEXT    NOT NULL,
    loaded_at TIMESTAMP NOT NULL,
    source     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loads_chain ON loads(chain_id);
CREATE INDEX IF NOT EXISTS idx_loads_node ON loads(node_id);
"""


@dataclass
class LoadRecord:
    """一次加载的内存表示（不直接序列化，便于断言）。"""
    node_id: str
    fqn: str
    chain_id: str
    source: str
    loaded_at: datetime


class LoadCounter:
    """加载计数器，对外只暴露 record + count + metric 接口。"""

    def __init__(self, loop_audit_dir: Union[str, Path]):
        base = Path(loop_audit_dir)
        # diag 子目录不存在则自动创建，便于在临时目录下测试
        self.diag_dir = base / "diag"
        self.diag_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.diag_dir / "loads.db"
        self._init_db()

    # -------------------------------------------------- 内部

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # -------------------------------------------------- 写入

    def record_load(
        self,
        node_id: str,
        fqn: str,
        chain_id: str,
        source: str = "ai_request",
    ) -> None:
        """记录一次加载；source 必须在白名单内。"""
        if source not in SOURCES:
            raise ValueError(
                f"非法 source={source!r}，允许值：{SOURCES}"
            )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO loads (node_id, fqn, chain_id, loaded_at, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (node_id, fqn, chain_id, datetime.now(timezone.utc).isoformat(), source),
            )
            conn.commit()

    # -------------------------------------------------- 读取

    def count_loads_by_chain(self, chain_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM loads WHERE chain_id = ?", (chain_id,)
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def count_total_loads(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM loads")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def count_unique_node_ids(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(DISTINCT node_id) FROM loads"
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_metric(self, chain_id: str, cached_count: int) -> dict:
        """计算 chain 的 loads / cached 比率。

        Args:
            chain_id: 调用链 ID
            cached_count: 该链在 Memurai 中缓存的方法体数量

        Returns:
            ``{"loads": int, "cached": int, "ratio": float}``，ratio 理想 < 0.3。
            cached_count == 0 时 ratio = 0.0（避免除零，表示无缓存可用）。
        """
        if cached_count < 0:
            raise ValueError(f"cached_count 不能为负数：{cached_count}")
        loads = self.count_loads_by_chain(chain_id)
        ratio = loads / cached_count if cached_count else 0.0
        return {"loads": loads, "cached": int(cached_count), "ratio": ratio}
