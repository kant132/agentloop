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
        agent_results TEXT DEFAULT '{}',    -- JSON: {injection:{verdict,vulns}, file:{}, auth:{}, biz:{}}
        created_at    TEXT
    );

agent_results JSON 结构:
    {
      "injection": {
        "verdict": "vuln" | "safe" | "inconclusive",
        "vulnerabilities": [
          {"type": "SQL注入", "root_cause": "...", "poc_status": "pending", "poc_verified_by": null}
        ]
      },
      "file": {...},
      "auth": {...},
      "biz": {...}
    }

用法:
    db = ChainDB("chains.db")
    db.insert_chain(chain_id, endpoint_fqn, priority, ...)
    batch = db.batch_by_priority(limit=100, offset=0)
    db.update_status(chain_id, "analyzed")
    db.update_agent_result(chain_id, "injection", result_json)
    vuln_chains = db.batch_for_poc(limit=4)
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LAST_SINKS_RE = re.compile(r"sink num:\s*(\d+)")


def _extract_last_sinks(chain_path: str | None) -> int:
    """从 chain_path 最后一个节点提取 sink 数。
    如 "fqn(sink num: 3) -> fqn(sink num: 0)" → 0
    """
    if not chain_path:
        return 0
    nodes = chain_path.split(" -> ")
    last = nodes[-1] if nodes else ""
    m = _LAST_SINKS_RE.search(last)
    return int(m.group(1)) if m else 0


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
    agent_results TEXT DEFAULT '{}',
    last_sinks    INTEGER DEFAULT 0,     -- 最后一个节点的 sink 数
    is_sink       INTEGER DEFAULT 0,     -- 最后一个节点是否有 sink (0/1)
    node_count    INTEGER DEFAULT 1,     -- 调用链节点数（路径长度）
    created_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_chains_priority ON chains(priority DESC);
CREATE INDEX IF NOT EXISTS idx_chains_status ON chains(status);
CREATE INDEX IF NOT EXISTS idx_chains_endpoint ON chains(endpoint_fqn);
"""

# agent_results JSON 中已存在该 key 时替换 value，否则添加
_AGENT_RESULT_SQL = """
UPDATE chains SET agent_results = json_set(
    COALESCE(agent_results, '{}'),
    '$.{agent_key}', json(?)
) WHERE chain_id = ?
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
        # 迁移：兼容旧库（忽略已存在的列）
        try:
            with self._conn() as conn:
                conn.execute("ALTER TABLE chains ADD COLUMN last_sinks INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            with self._conn() as conn:
                conn.execute("ALTER TABLE chains ADD COLUMN is_sink INTEGER DEFAULT 0")
        except Exception:
            pass

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
        last_sinks: int = 0,
        is_sink: bool = False,
        node_count: int = 1,
    ) -> None:
        """插入或替换一条调用链。"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO chains
                   (chain_id, endpoint_fqn, priority, total_sinks, preset_sinks,
                    cycle_detected, status, chain_path, node_path, created_at,
                    last_sinks, is_sink, node_count)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
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
                    last_sinks,
                    1 if is_sink else 0,
                    node_count,
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
                c.get("last_sinks", 0),
                1 if c.get("is_sink", False) else 0,
                c.get("node_count", 1),
            )
            for c in chains
        ]
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO chains
                   (chain_id, endpoint_fqn, priority, total_sinks, preset_sinks,
                    cycle_detected, status, chain_path, node_path, created_at,
                    last_sinks, is_sink, node_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        is_sink: int | None = None,
    ) -> list[dict[str, Any]]:
        """按优先级降序批量加载调用链。

        Args:
            limit: 每批数量
            offset: 偏移量（分页）
            status: 只加载指定状态的链（None = 全部）
            is_sink: 1=只加载最后一个节点有 sink 的链，0=只加载无 sink 的，None=不限
        """
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if is_sink is not None:
            conditions.append("is_sink = ?")
            params.append(int(bool(is_sink)))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT * FROM chains {where} ORDER BY priority DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
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
    # agent_results 操作
    # ============================================================

    def update_agent_result(self, chain_id: str, agent_key: str, result: dict) -> None:
        """写入某类 agent 的审计结果。

        Args:
            chain_id: 链 ID
            agent_key: "injection" | "file" | "auth" | "biz"
            result: {"verdict": "vuln", "vulnerabilities": [...], ...}
        """
        with self._conn() as conn:
            conn.execute(
                _AGENT_RESULT_SQL.replace("{agent_key}", agent_key),
                (json.dumps(result, ensure_ascii=False), chain_id),
            )
            conn.commit()

    def update_vuln_poc_status(
        self, chain_id: str, agent_key: str, vuln_index: int, poc_status: str
    ) -> None:
        """更新某个 agent 的某个漏洞的 PoC 验证状态。

        Args:
            chain_id: 链 ID
            agent_key: "injection" | "file" | "auth" | "biz"
            vuln_index: 漏洞在 vulnerabilities 数组中的索引
            poc_status: "confirmed" | "denied" | "inconclusive"
        """
        with self._conn() as conn:
            conn.execute(
                f"""UPDATE chains SET agent_results = json_set(
                    agent_results,
                    '$.{agent_key}.vulnerabilities[{vuln_index}].poc_status',
                    ?
                ) WHERE chain_id = ?""",
                (poc_status, chain_id),
            )
            conn.commit()

    def batch_for_poc(self, limit: int = 4) -> list[dict[str, Any]]:
        """取待 PoC 验证的链（status=analyzed，agent_results 中有 vuln 且 poc_status=pending）。

        按 priority DESC 排序，每次取 limit 条。
        """
        with self._conn() as conn:
            # 不能用 JSON 函数做复杂过滤，用 Python 后处理
            rows = conn.execute(
                """SELECT * FROM chains
                   WHERE status IN ('analyzed', 'vuln', 'safe')
                   ORDER BY priority DESC"""
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if self._has_pending_vulns(d.get("agent_results", "{}")):
                result.append(d)
                if len(result) >= limit:
                    break
        return result

    @staticmethod
    def _has_pending_vulns(agent_results_str: str) -> bool:
        """检查 agent_results 中是否有待验证的漏洞（vuln 或 inconclusive）。"""
        try:
            results = json.loads(agent_results_str) if isinstance(agent_results_str, str) else agent_results_str
        except json.JSONDecodeError:
            return False
        for key in ("injection", "file", "auth", "biz"):
            agent_data = results.get(key, {})
            verdict = agent_data.get("verdict", "")
            if verdict == "vuln":
                for vuln in agent_data.get("vulnerabilities", []):
                    if vuln.get("poc_status", "pending") == "pending":
                        return True
            if verdict == "inconclusive":
                return True  # 不确定项也需要验证
        return False

    @staticmethod
    def get_pending_vulns(agent_results_str: str) -> list[dict]:
        """从 agent_results 中提取所有待验证的漏洞列表。

        Returns:
            [{"agent_key": "injection", "vuln_index": 0, "type": "SQL注入", "root_cause": "..."}, ...]
            inconclusive 项也返回（vuln_index = -1，无具体漏洞索引）
        """
        try:
            results = json.loads(agent_results_str) if isinstance(agent_results_str, str) else agent_results_str
        except json.JSONDecodeError:
            return []
        pending = []
        for key in ("injection", "file", "auth", "biz"):
            agent_data = results.get(key, {})
            verdict = agent_data.get("verdict", "")
            if verdict == "vuln":
                for i, vuln in enumerate(agent_data.get("vulnerabilities", [])):
                    if vuln.get("poc_status", "pending") == "pending":
                        pending.append({
                            "agent_key": key,
                            "vuln_index": i,
                            "type": vuln.get("type", ""),
                            "root_cause": vuln.get("root_cause", ""),
                        })
            if verdict == "inconclusive":
                pending.append({
                    "agent_key": key,
                    "vuln_index": -1,  # -1 表示不确定项
                    "type": "inconclusive",
                    "root_cause": agent_data.get("reason", ""),
                })
        return pending

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
