# -*- coding: utf-8 -*-
"""verify_edges.py — 验证链边正确性，从 jar-analyzer.db 读取 chains + method_call_table。

所有数据统一存储在 jar-analyzer.db 中：
- chains 表：chain_builder 写入的调用链
- method_call_table / method_impl_table：jar-analyzer-engine 写入的调用关系

对每条链的每对连续节点 (i, i+1)，检查 method_call_table 或 method_impl_table
中是否存在对应的边。若边缺失则标记为 broken，并丢弃所有以此前缀开始的后续链。

CLI
---
    python verify_edges.py \\
        --jar-analyzer-db path/to/jar-analyzer.db \\
        --group-id org.owasp.webgoat
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent  # D:\agentloop


# ============================================================ jar-analyzer mode

def _load_method_table(
    conn: sqlite3.Connection,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load method_table: method_id -> (class_name, method_name)."""
    id_to_class: Dict[str, str] = {}
    id_to_method: Dict[str, str] = {}
    for row in conn.execute(
        "SELECT method_id, class_name, method_name FROM method_table"
    ).fetchall():
        mid = str(row["method_id"])
        id_to_class[mid] = row["class_name"]
        id_to_method[mid] = row["method_name"]
    return id_to_class, id_to_method


def _build_edge_sets(
    conn: sqlite3.Connection,
) -> Tuple[Set[Tuple[str, str, str, str]], Set[Tuple[str, str, str, str]]]:
    """Build edge sets from method_call_table and method_impl_table."""
    call_edges: Set[Tuple[str, str, str, str]] = set()
    for row in conn.execute(
        "SELECT caller_class_name, caller_method_name, "
        "callee_class_name, callee_method_name FROM method_call_table"
    ).fetchall():
        call_edges.add((
            row["caller_class_name"],
            row["caller_method_name"],
            row["callee_class_name"],
            row["callee_method_name"],
        ))

    impl_edges: Set[Tuple[str, str, str, str]] = set()
    for row in conn.execute(
        "SELECT class_name, method_name, impl_class_name, method_name FROM method_impl_table"
    ).fetchall():
        impl_edges.add((
            row["class_name"],
            row["method_name"],
            row["impl_class_name"],
            row["method_name"],
        ))
    return call_edges, impl_edges


def verify_edges(
    jar_analyzer_db_path: Path,
    group_id: str,
) -> Dict[str, Any]:
    """用 jar-analyzer.db 作为 ground truth, 逐边验证链的正确性。

    chains 表和 method_call_table 都在同一个 jar-analyzer.db 中。
    """
    t0 = time.time()

    conn = sqlite3.connect(str(jar_analyzer_db_path))
    conn.row_factory = sqlite3.Row

    # Load method_table
    print("加载 method_table...")
    id_to_class, id_to_method = _load_method_table(conn)
    print(f"  {len(id_to_class)} 个方法")

    # Load edge sets
    print("加载边信息...")
    call_edges, impl_edges = _build_edge_sets(conn)
    print(f"  {len(call_edges)} 条调用边, {len(impl_edges)} 条实现边")

    # Load chains
    all_chains = conn.execute(
        "SELECT chain_id, node_path, endpoint_fqn, node_count "
        "FROM chains WHERE status='pending' ORDER BY node_count DESC"
    ).fetchall()

    total_chains = len(all_chains)
    if total_chains == 0:
        print("无 pending 链，无需验证")
        conn.close()
        return {"total_chains": 0, "low_confidence": 0, "high_confidence": 0}

    print(f"正在验证 {total_chains} 条链...")
    broken_chains: List[str] = []
    broken_prefixes: Set[str] = set()
    ok_chains = 0
    total_edges_checked = 0
    broken_edges_count = 0

    for idx, chain in enumerate(all_chains):
        chain_id = chain["chain_id"]
        node_path = chain["node_path"]
        nodes = [n.strip() for n in node_path.split(" -> ")]

        # Check prefix
        matched_prefix: Optional[str] = None
        for bp in broken_prefixes:
            bp_parts = bp.split(" -> ")
            if len(nodes) >= len(bp_parts) and nodes[:len(bp_parts)] == bp_parts:
                matched_prefix = bp
                break

        if matched_prefix is not None:
            broken_chains.append(chain_id)
            conn.execute(
                "UPDATE chains SET status='broken', mismatch_score=1.0 WHERE chain_id=?",
                (chain_id,),
            )
            continue

        # Verify edges
        chain_broken = False
        broken_at = 0
        for j in range(len(nodes) - 1):
            total_edges_checked += 1
            src_id = nodes[j]
            tgt_id = nodes[j + 1]
            src_class = id_to_class.get(src_id, "")
            src_method = id_to_method.get(src_id, "")
            tgt_class = id_to_class.get(tgt_id, "")
            tgt_method = id_to_method.get(tgt_id, "")

            if not src_class or not tgt_class:
                broken_edges_count += 1
                chain_broken = True
                broken_at = j
                break

            edge_key = (src_class, src_method, tgt_class, tgt_method)
            if edge_key in call_edges or edge_key in impl_edges:
                continue

            broken_edges_count += 1
            chain_broken = True
            broken_at = j
            print(f"  BROKEN: {chain_id} [{j}->{j+1}] "
                  f"{src_class}::{src_method} -> {tgt_class}::{tgt_method}")
            break

        if chain_broken:
            broken_chains.append(chain_id)
            prefix_len = min(broken_at + 2, len(nodes))
            broken_prefix = " -> ".join(nodes[:prefix_len])
            broken_prefixes.add(broken_prefix)
            conn.execute(
                "UPDATE chains SET status='broken', mismatch_score=1.0 WHERE chain_id=?",
                (chain_id,),
            )
        else:
            ok_chains += 1

        if (idx + 1) % 500 == 0:
            conn.commit()
            print(f"  进度: {idx + 1}/{total_chains}, OK={ok_chains}, Broken={len(broken_chains)}")

    conn.commit()
    conn.close()
    elapsed = time.time() - t0

    print(f"\n=== jar-analyzer 边验证结果 ===")
    print(f"总链: {total_chains}, 通过: {ok_chains}, 断裂: {len(broken_chains)}")
    print(f"总边: {total_edges_checked}, 断裂边: {broken_edges_count}")
    print(f"断裂前缀: {len(broken_prefixes)}")
    print(f"耗时: {elapsed:.1f}s")

    return {
        "total_chains": total_chains,
        "broken": len(broken_chains),
        "ok": ok_chains,
        "total_edges": total_edges_checked,
        "broken_edges": broken_edges_count,
        "broken_prefixes": len(broken_prefixes),
        "elapsed_seconds": round(elapsed, 1),
    }


# ============================================================ CLI

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="验证链边正确性 (从 jar-analyzer.db 读取 chains + 调用关系)",
    )
    p.add_argument(
        "--jar-analyzer-db", required=True, type=Path,
        help="jar-analyzer.db 路径 (包含 chains 表 + method_call_table)",
    )
    p.add_argument(
        "--group-id", default=None,
        help="项目 groupId (默认从 preset.json 探测)",
    )
    return p


def _find_preset(group_id: Optional[str]) -> Dict[str, Any]:
    """从 projects 目录找 preset.json。"""
    if group_id:
        preset_path = ROOT / "projects" / group_id / "preset.json"
        if preset_path.is_file():
            return json.loads(preset_path.read_text(encoding="utf-8"))
    projects_dir = ROOT / "projects"
    for d in projects_dir.iterdir():
        if d.is_dir() and d.name != "_template":
            pp = d / "preset.json"
            if pp.is_file():
                return json.loads(pp.read_text(encoding="utf-8"))
    return {}


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    # Resolve group_id
    group_id = args.group_id
    if not group_id:
        preset = _find_preset(None)
        group_id = preset.get("groupId", "")

    if not group_id:
        print("ERROR: 无法确定 groupId，请通过 --group-id 参数指定", file=sys.stderr)
        return 2

    jar_analyzer_db = args.jar_analyzer_db
    if not jar_analyzer_db.is_file():
        print(f"ERROR: jar-analyzer.db 不存在: {jar_analyzer_db}", file=sys.stderr)
        return 2

    print(f"jar-analyzer.db = {jar_analyzer_db}")
    result = verify_edges(jar_analyzer_db, group_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
