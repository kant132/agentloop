#!/usr/bin/env python3
"""batch_verify_chains.py — 批量验证所有链边正确性 (已废弃，使用 verify_edges.py)。

核心逻辑:
1. 对 jar-analyzer.db chains 表中每条 chain，逐对检查 (nodes[i], nodes[i+1]) 是否在 method_call_table 或 method_impl_table 中存在边
2. 若某条边不存在，标记该 chain 为 broken，并丢弃所有以此 chain 前缀开始的后续 chain
3. 输出统计报告

推荐使用: python scripts/chain/verify_edges.py --jar-analyzer-db {db}
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


ROOT = Path(__file__).resolve().parent.parent.parent


def _open_jar_db(jar_analyzer_db_path: Path) -> sqlite3.Connection:
    """Open jar-analyzer.db with row factory."""
    conn = sqlite3.connect(str(jar_analyzer_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_method_table(
    jar_conn: sqlite3.Connection,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load method_table: method_id → (class_name, method_name).

    Returns:
        id_to_class: method_id → class_name
        id_to_method: method_id → method_name
    """
    id_to_class: Dict[str, str] = {}
    id_to_method: Dict[str, str] = {}
    for row in jar_conn.execute(
        "SELECT method_id, class_name, method_name FROM method_table"
    ).fetchall():
        mid = str(row["method_id"])
        id_to_class[mid] = row["class_name"]
        id_to_method[mid] = row["method_name"]
    return id_to_class, id_to_method


def _build_edge_set(
    jar_conn: sqlite3.Connection,
) -> Set[Tuple[str, str, str, str]]:
    """Build set of (caller_class, caller_method, callee_class, callee_method) from method_call_table."""
    edges: Set[Tuple[str, str, str, str]] = set()
    for row in jar_conn.execute(
        "SELECT caller_class_name, caller_method_name, "
        "callee_class_name, callee_method_name FROM method_call_table"
    ).fetchall():
        edges.add((
            row["caller_class_name"],
            row["caller_method_name"],
            row["callee_class_name"],
            row["callee_method_name"],
        ))
    return edges


def _build_impl_edge_set(
    jar_conn: sqlite3.Connection,
) -> Set[Tuple[str, str, str, str]]:
    """Build set of (interface_class, method, impl_class, method) from method_impl_table."""
    impl_edges: Set[Tuple[str, str, str, str]] = set()
    for row in jar_conn.execute(
        "SELECT class_name, method_name, impl_class_name, method_name FROM method_impl_table"
    ).fetchall():
        impl_edges.add((
            row["class_name"],
            row["method_name"],
            row["impl_class_name"],
            row["method_name"],
        ))
    return impl_edges


def verify_all_chains(
    chains_db_path: Path,
    jar_analyzer_db_path: Path,
) -> Dict[str, Any]:
    """Verify ALL chains' edges against jar-analyzer.db method_call_table.

    Returns dict with stats and broken chain IDs.
    """
    jar_conn = _open_jar_db(jar_analyzer_db_path)

    # Load method_table
    print("Loading method_table...")
    id_to_class, id_to_method = _load_method_table(jar_conn)
    print(f"  {len(id_to_class)} methods loaded")

    # Load all edges
    print("Loading method_call_table edges...")
    call_edges = _build_edge_set(jar_conn)
    print(f"  {len(call_edges)} call edges loaded")

    print("Loading method_impl_table edges...")
    impl_edges = _build_impl_edge_set(jar_conn)
    print(f"  {len(impl_edges)} impl edges loaded")

    jar_conn.close()

    # Load chains
    chains_conn = sqlite3.connect(str(chains_db_path))
    chains_conn.row_factory = sqlite3.Row

    all_chains = chains_conn.execute(
        "SELECT chain_id, node_path, endpoint_fqn, node_count "
        "FROM chains WHERE status='pending' "
        "ORDER BY node_count DESC"
    ).fetchall()

    total_chains = len(all_chains)
    print(f"\nVerifying {total_chains} chains...")

    broken_chains: List[str] = []
    broken_prefixes: Set[str] = set()
    ok_chains = 0
    total_edges_checked = 0
    broken_edges_count = 0
    break_reasons: List[Dict[str, Any]] = []

    for idx, chain in enumerate(all_chains):
        chain_id = chain["chain_id"]
        node_path = chain["node_path"]
        nodes = [n.strip() for n in node_path.split(" -> ")]
        endpoint = chain["endpoint_fqn"]

        # Check if this chain's prefix is already broken
        # A "prefix" is the first k nodes of a chain
        matched_broken_prefix: str | None = None
        for bp in broken_prefixes:
            bp_parts = bp.split(" -> ")
            # If the first len(bp_parts) nodes of this chain match bp_parts
            if len(nodes) >= len(bp_parts) and nodes[:len(bp_parts)] == bp_parts:
                matched_broken_prefix = bp
                break

        if matched_broken_prefix is not None:
            # Mark as broken without re-checking
            broken_chains.append(chain_id)
            break_reasons.append({
                "chain_id": chain_id,
                "reason": "prefix_broken",
                "prefix": matched_broken_prefix,
                "edge_idx": 0,
                "node_count": len(nodes),
            })
            continue

        # Verify each edge
        chain_broken = False
        broken_at_idx = 0
        for j in range(len(nodes) - 1):
            total_edges_checked += 1
            src_id = nodes[j]
            tgt_id = nodes[j + 1]

            src_class = id_to_class.get(src_id, "")
            src_method = id_to_method.get(src_id, "")
            tgt_class = id_to_class.get(tgt_id, "")
            tgt_method = id_to_method.get(tgt_id, "")

            if not src_class or not tgt_class:
                # Unknown node → broken
                broken_edges_count += 1
                chain_broken = True
                break_reasons.append({
                    "chain_id": chain_id,
                    "reason": "unknown_node",
                    "edge_idx": j,
                    "src_id": src_id,
                    "tgt_id": tgt_id,
                })
                break

            # Check method_call_table
            edge_key = (src_class, src_method, tgt_class, tgt_method)
            if edge_key in call_edges:
                continue  # Edge OK

            # Check method_impl_table
            impl_key = (src_class, src_method, tgt_class, tgt_method)
            if impl_key in impl_edges:
                continue  # Impl edge OK

            # Edge missing → broken
            broken_edges_count += 1
            chain_broken = True
            broken_at_idx = j
            break_reasons.append({
                "chain_id": chain_id,
                "reason": "missing_edge",
                "edge_idx": j,
                "src": f"{src_class}::{src_method}",
                "tgt": f"{tgt_class}::{tgt_method}",
            })
            break

        if chain_broken:
            broken_chains.append(chain_id)
            # Record the prefix up to the broken edge
            prefix_len = min(broken_at_idx + 2, len(nodes))
            broken_prefix = " -> ".join(nodes[:prefix_len])
            broken_prefixes.add(broken_prefix)
        else:
            ok_chains += 1

        if (idx + 1) % 500 == 0 or idx == total_chains - 1:
            print(f"  Progress: {idx + 1}/{total_chains}, "
                  f"OK: {ok_chains}, Broken: {len(broken_chains)}")

    # Update chains.db: mark broken chains
    if broken_chains:
        # Use batch update
        chunk_size = 500
        for i in range(0, len(broken_chains), chunk_size):
            chunk = broken_chains[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            chains_conn.execute(
                f"UPDATE chains SET status='broken', mismatch_score=1.0 "
                f"WHERE chain_id IN ({placeholders})",
                chunk,
            )
        chains_conn.commit()
        print(f"\nMarked {len(broken_chains)} chains as broken")

    chains_conn.close()

    return {
        "total_chains": total_chains,
        "ok": ok_chains,
        "broken": len(broken_chains),
        "total_edges_checked": total_edges_checked,
        "broken_edges": broken_edges_count,
        "broken_prefixes": len(broken_prefixes),
        "break_reasons": break_reasons[:20],  # First 20 for inspection
    }


def print_report(stats: Dict[str, Any]) -> None:
    """Print verification report."""
    print("\n" + "=" * 70)
    print("批量链边验证报告")
    print("=" * 70)
    print(f"  总链数:           {stats['total_chains']}")
    print(f"  通过 (OK):        {stats['ok']}")
    print(f"  断裂 (BROKEN):    {stats['broken']}")
    print(f"  已检查边数:       {stats['total_edges_checked']}")
    print(f"  断裂边数:         {stats['broken_edges']}")
    print(f"  断裂前缀数:       {stats['broken_prefixes']}")

    if stats["break_reasons"]:
        print(f"\n  前 20 个断裂原因:")
        for br in stats["break_reasons"][:20]:
            if br["reason"] == "missing_edge":
                print(f"    [{br['chain_id']}] 边缺失 "
                      f"({br['edge_idx']}): {br['src']} -> {br['tgt']}")
            elif br["reason"] == "unknown_node":
                print(f"    [{br['chain_id']}] 未知节点 "
                      f"({br['edge_idx']}): src={br['src_id']} tgt={br['tgt_id']}")
            elif br["reason"] == "prefix_broken":
                print(f"    [{br['chain_id']}] 此前缀已断裂 (prefix: {br.get('prefix', '')[:80]})")

    print("=" * 70)

    if stats["broken"] == 0:
        print("  ✓ 全部链验证通过！")
    else:
        print(f"  ✗ {stats['broken']}/{stats['total_chains']} 条链标记为 broken")
    print("=" * 70)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="批量验证链边正确性")
    p.add_argument(
        "--chains-db", type=Path, required=True,
        help="chains.db 路径",
    )
    p.add_argument(
        "--jar-analyzer-db", type=Path, required=True,
        help="jar-analyzer.db 路径",
    )
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if not args.chains_db.is_file():
        print(f"ERROR: chains.db 不存在: {args.chains_db}", file=sys.stderr)
        return 2
    if not args.jar_analyzer_db.is_file():
        print(f"ERROR: jar-analyzer.db 不存在: {args.jar_analyzer_db}", file=sys.stderr)
        return 2

    t0 = time.time()
    stats = verify_all_chains(args.chains_db, args.jar_analyzer_db)
    elapsed = time.time() - t0
    stats["elapsed_seconds"] = round(elapsed, 2)

    print_report(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
