# -*- coding: utf-8 -*-
"""chain_db.py — 调用链 SQLite 存储。

单表设计：所有调用链数据存一张表，按优先级批量加载。

Schema:
    CREATE TABLE chains (
        chain_id      TEXT PRIMARY KEY,     -- sig_hash
        endpoint_fqn  TEXT NOT NULL,        -- 入口方法 FQN
        priority      INTEGER NOT NULL,     -- 优先级分数
        total_sinks   INTEGER DEFAULT 0,    -- sink 总数
        preset_sinks  INTEGER DEFAULT 0,    -- 命中预置库数量
        cycle_detected INTEGER DEFAULT 0,   -- 是否有环
        status        TEXT DEFAULT 'pending', -- pending/analyzed/vuln/safe
        chain_path    TEXT,                 -- com.example.A#m(sink num: 3) -> com.example.B#n(sink num: 0) -> ...
        node_path     TEXT,                 -- method:abc123 -> method:def456 -> method:ghi789
        created_at    TEXT
    );

用法:
    db = ChainDB("chains.db")
    db.insert_chain(chain_id, endpoint_fqn, priority, ...)
    batch = db.batch_by_priority(limit=100, offset=0)
    db.update_status(chain_id, "analyzed")
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chains (
    chain_id      TEXT PRIMARY KEY,
    endpoint_fqn  TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,
    total_sinks   INTEGER DEFAULT 0,
    preset_sinks  INTEGER DEFAULT 0,
    cycle_detected INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    chain_path    TEXT,
    node_path     TEXT,
    created_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_chains_priority ON chains(priority DESC);
CREATE INDEX IF NOT EXISTS idx_chains_status ON chains(status);
CREATE INDEX IF NOT EXISTS idx_chains_endpoint ON chains(endpoint_fqn);
"""


class ChainDB:
    """调用链 SQLite 存储。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ============================================================
    # 写入
    # ============================================================

    def insert_chain(
        self,
        chain_id: str,
        endpoint_fqn: str,
        priority: int,
        total_sinks: int = 0,
        preset_sinks: int = 0,
        cycle_detected: bool = False,
        chain_path: str = "",
        node_path: str = "",
    ) -> None:
        """插入或替换一条调用链。"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO chains
                   (chain_id, endpoint_fqn, priority, total_sinks, preset_sinks,
                    cycle_detected, status, chain_path, node_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    chain_id,
                    endpoint_fqn,
                    priority,
                    total_sinks,
                    preset_sinks,
                    1 if cycle_detected else 0,
                    chain_path,
                    node_path,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def insert_chains_batch(self, chains: list[dict[str, Any]]) -> int:
        """批量插入调用链。返回插入数量。"""
        if not chains:
            return 0
        rows = [
            (
                c["chain_id"],
                c["endpoint_fqn"],
                c.get("priority", 0),
                c.get("total_sinks", 0),
                c.get("preset_sinks", 0),
                1 if c.get("cycle_detected", False) else 0,
                "pending",
                c.get("chain_path", ""),
                c.get("node_path", ""),
                datetime.now(timezone.utc).isoformat(),
            )
            for c in chains
        ]
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO chains
                   (chain_id, endpoint_fqn, priority, total_sinks, preset_sinks,
                    cycle_detected, status, chain_path, node_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.commit()
        return len(rows)

    # ============================================================
    # 读取
    # ============================================================

    def batch_by_priority(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """按优先级降序批量加载调用链。

        Args:
            limit: 每批数量
            offset: 偏移量（分页）
            status: 只加载指定状态的链（None = 全部）

        Returns:
            list[dict]: 每条含所有字段
        """
        with self._conn() as conn:
            if status:
                cur = conn.execute(
                    """SELECT * FROM chains WHERE status = ?
                       ORDER BY priority DESC LIMIT ? OFFSET ?""",
                    (status, limit, offset),
                )
            else:
                cur = conn.execute(
                    """SELECT * FROM chains
                       ORDER BY priority DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                )
            return [dict(row) for row in cur.fetchall()]

    def get_chain(self, chain_id: str) -> dict[str, Any] | None:
        """按 chain_id 取单条。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id = ?", (chain_id,)
            ).fetchone()
            return dict(row) if row else None

    # ============================================================
    # 更新
    # ============================================================

    def update_status(self, chain_id: str, status: str) -> None:
        """更新链状态（pending/analyzed/vuln/safe）。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE chains SET status = ? WHERE chain_id = ?",
                (status, chain_id),
            )
            conn.commit()

    def update_status_batch(self, chain_ids: list[str], status: str) -> int:
        """批量更新状态。"""
        if not chain_ids:
            return 0
        with self._conn() as conn:
            placeholders = ",".join("?" * len(chain_ids))
            cur = conn.execute(
                f"UPDATE chains SET status = ? WHERE chain_id IN ({placeholders})",
                [status] + chain_ids,
            )
            conn.commit()
            return cur.rowcount

    # ============================================================
    # 统计
    # ============================================================

    def count_by_status(self) -> dict[str, int]:
        """按状态统计链数量。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM chains GROUP BY status"
            ).fetchall()
            return {row["status"]: row["cnt"] for row in rows}

    def total_chains(self) -> int:
        """总链数。"""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]

    def stats(self) -> dict[str, Any]:
        """综合统计。"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
            by_status = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM chains GROUP BY status"
                ).fetchall()
            }
            avg_priority = conn.execute(
                "SELECT AVG(priority) FROM chains"
            ).fetchone()[0]
            total_sinks = conn.execute(
                "SELECT SUM(total_sinks) FROM chains"
            ).fetchone()[0]
            endpoints = conn.execute(
                "SELECT COUNT(DISTINCT endpoint_fqn) FROM chains"
            ).fetchone()[0]
            return {
                "total_chains": total,
                "total_endpoints": endpoints,
                "by_status": by_status,
                "avg_priority": round(avg_priority or 0, 2),
                "total_sinks": total_sinks or 0,
            }

    # ============================================================
    # 维护
    # ============================================================

    def clear_all(self) -> None:
        """清空所有链（每轮审计前调用）。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM chains")
            conn.commit()

    def delete_by_endpoint(self, endpoint_fqn: str) -> int:
        """删除某端点的所有链。"""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM chains WHERE endpoint_fqn = ?", (endpoint_fqn,)
            )
            conn.commit()
            return cur.rowcount
