# -*- coding: utf-8 -*-
"""verify_edges.py — 验证链边正确性，支持 jar-analyzer.db 置信度评分。

两种模式:
1. jar-analyzer.db 可用时: 用 method_call_table 作为 ground truth，
   计算每条链的 mismatch_score (不在 jar-analyzer 中的边比例)，
   >0.5 标记 low_confidence，≤0.5 保持 pending。
2. jar-analyzer.db 不可用时: 保留原始 //fqn: 注释验证逻辑，
   二值 broken/not-broken。

CLI
---
    python verify_edges.py \\
        --jar-analyzer-db path/to/jar-analyzer.db \\
        --chains-db path/to/chains.db \\
        --group-id org.owasp.webgoat
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent  # D:\agentloop
FQN_RE = re.compile(r"//fqn:\s*(\S+)")


# ============================================================ FQN conversion
# Same logic as compare_call_graphs.py / jar_analyzer_cte.py

def _cg_qn_to_jar_parts(qualified_name: str) -> Tuple[str, str]:
    """Convert codegraph qualified_name to jar-analyzer (class_name, method_name).

    codegraph: ``org.owasp.webgoat.lessons.xxx::ClassName::methodName``
    jar-analyzer: class_name = ``org/owasp/webgoat/lessons/xxx/ClassName``
                   method_name = ``methodName``
    """
    if "::" in qualified_name:
        parts = qualified_name.split("::")
        pkg = parts[0] if len(parts) >= 3 else ""
        cls = parts[-2] if len(parts) >= 2 else ""
        method = parts[-1]
        pkg_slash = pkg.replace(".", "/") if pkg else ""
        class_name = f"{pkg_slash}/{cls}" if pkg_slash else cls
        return class_name, method

    last_dot = qualified_name.rfind(".")
    if last_dot > 0:
        method = qualified_name[last_dot + 1:]
        prefix = qualified_name[:last_dot]
        last_pkg_dot = prefix.rfind(".")
        if last_pkg_dot > 0:
            pkg = prefix[:last_pkg_dot].replace(".", "/")
            cls = prefix[last_pkg_dot + 1:]
            class_name = f"{pkg}/{cls}"
        else:
            class_name = prefix.replace(".", "/")
        return class_name, method

    return "", qualified_name


# ============================================================ jar-analyzer mode

def _load_method_table(
    jar_conn: sqlite3.Connection,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load method_table: method_id -> (class_name, method_name)."""
    id_to_class: Dict[str, str] = {}
    id_to_method: Dict[str, str] = {}
    for row in jar_conn.execute(
        "SELECT method_id, class_name, method_name FROM method_table"
    ).fetchall():
        mid = str(row["method_id"])
        id_to_class[mid] = row["class_name"]
        id_to_method[mid] = row["method_name"]
    return id_to_class, id_to_method


def _build_edge_sets(
    jar_conn: sqlite3.Connection,
) -> Tuple[Set[Tuple[str, str, str, str]], Set[Tuple[str, str, str, str]]]:
    """Build edge sets from method_call_table and method_impl_table."""
    call_edges: Set[Tuple[str, str, str, str]] = set()
    for row in jar_conn.execute(
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
    for row in jar_conn.execute(
        "SELECT class_name, method_name, impl_class_name, method_name FROM method_impl_table"
    ).fetchall():
        impl_edges.add((
            row["class_name"],
            row["method_name"],
            row["impl_class_name"],
            row["method_name"],
        ))
    return call_edges, impl_edges


def verify_with_jar_analyzer(
    chains_db_path: Path,
    jar_analyzer_db_path: Path,
    group_id: str,
) -> Dict[str, Any]:
    """用 jar-analyzer.db 作为 ground truth, 逐边验证链的正确性。

    对每条链的每对连续节点 (i, i+1)，检查 method_call_table 或 method_impl_table
    中是否存在对应的边。若边缺失则标记为 broken，并丢弃所有以此前缀开始的后续链。
    """
    import time
    t0 = time.time()

    # Load jar-analyzer data
    print("加载 method_table...")
    jar_conn = sqlite3.connect(str(jar_analyzer_db_path))
    jar_conn.row_factory = sqlite3.Row
    id_to_class, id_to_method = _load_method_table(jar_conn)
    print(f"  {len(id_to_class)} 个方法")

    print("加载边信息...")
    call_edges, impl_edges = _build_edge_sets(jar_conn)
    print(f"  {len(call_edges)} 条调用边, {len(impl_edges)} 条实现边")
    jar_conn.close()

    # Load chains
    chains_conn = sqlite3.connect(str(chains_db_path))
    chains_conn.row_factory = sqlite3.Row
    all_chains = chains_conn.execute(
        "SELECT chain_id, node_path, endpoint_fqn, node_count "
        "FROM chains WHERE status='pending' ORDER BY node_count DESC"
    ).fetchall()

    total_chains = len(all_chains)
    if total_chains == 0:
        print("无 pending 链，无需验证")
        chains_conn.close()
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
            chains_conn.execute(
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
            chains_conn.execute(
                "UPDATE chains SET status='broken', mismatch_score=1.0 WHERE chain_id=?",
                (chain_id,),
            )
        else:
            ok_chains += 1

        if (idx + 1) % 500 == 0:
            chains_conn.commit()
            print(f"  进度: {idx + 1}/{total_chains}, OK={ok_chains}, Broken={len(broken_chains)}")

    chains_conn.commit()
    chains_conn.close()
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


# ============================================================ legacy mode (no jar-analyzer)

def verify_with_annotations(
    chains_db_path: Path,
    codegraph_path: Path,
    group_id: str,
) -> Dict[str, Any]:
    """原始 //fqn: 注释验证逻辑 (二值: broken/not-broken)。"""
    # Connect to chains.db
    chains_conn = sqlite3.connect(str(chains_db_path))
    chains_conn.row_factory = sqlite3.Row

    # Connect to Memurai for method body cache
    sys.path.insert(0, str(ROOT / "scripts" / "redis"))
    from memurai_client import Memurai

    m = Memurai()

    # Connect to codegraph for qualified_name
    cg = sqlite3.connect(f"file:{codegraph_path}?mode=ro", uri=True)
    cg.row_factory = sqlite3.Row

    # Step 1: collect all node_ids
    chains = chains_conn.execute(
        "SELECT chain_id, node_path FROM chains WHERE status='pending'"
    ).fetchall()
    all_node_ids: Set[str] = set()
    for chain in chains:
        nodes = [n.strip() for n in chain["node_path"].split("->")]
        all_node_ids.update(nodes)

    print(f"pending chains: {len(chains)}, unique nodes: {len(all_node_ids)}")

    # Step 2: batch load method bodies from Memurai
    body_cache: Dict[str, str] = {}
    for nid in all_node_ids:
        key = f"{group_id}:method:{nid}"
        raw = m.get(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            body_cache[nid] = data.get("body", "")

    print(f"bodies loaded: {len(body_cache)}/{len(all_node_ids)}")

    # Step 3: batch load qualified_name
    qn_cache: Dict[str, str] = {}
    placeholders = ",".join("?" * len(all_node_ids))
    for r in cg.execute(
        f"SELECT id, qualified_name FROM nodes WHERE id IN ({placeholders})",
        tuple(all_node_ids),
    ):
        qn_cache[r["id"]] = r["qualified_name"]

    print(f"qualified_names loaded: {len(qn_cache)}")

    # Step 4: verify each chain's edges
    def fqn_to_class(fqn: str) -> str:
        if "::" in fqn:
            parts = fqn.split("::")
            return parts[-2] if len(parts) >= 2 else ""
        parts = fqn.rsplit(".", 1)
        return parts[0].rsplit(".", 1)[-1] if parts else ""

    broken_chains: List[str] = []
    total_edges = 0
    broken_edges = 0

    for chain in chains:
        nodes = [n.strip() for n in chain["node_path"].split("->")]
        for i in range(len(nodes) - 1):
            total_edges += 1
            src, tgt = nodes[i], nodes[i + 1]
            body = body_cache.get(src, "")
            if not body:
                continue  # 无 body 无法验证，保守通过
            fqns = set(FQN_RE.findall(body))
            if not fqns:
                continue  # 无注释，保守通过
            tgt_qn = qn_cache.get(tgt, "")
            tgt_class = fqn_to_class(tgt_qn)
            tgt_method = tgt_qn.split("::")[-1] if "::" in tgt_qn else tgt_qn.rsplit(".", 1)[-1]
            matched = any(tgt_class in f or tgt_method in f for f in fqns)
            if not matched:
                broken_edges += 1
                src_qn = qn_cache.get(src, "?")
                print(f"  BROKEN: {chain['chain_id']}")
                print(f"    [{i}->{i+1}] {src_qn} -> {tgt_qn}")
                print(f"    body fqns: {fqns}")
                broken_chains.append(chain["chain_id"])
                break

    print(f"\n=== //fqn: 注释验证结果 ===")
    print(f"总边: {total_edges}, 断裂边: {broken_edges}")
    print(f"总链: {len(chains)}, 断裂链: {len(broken_chains)}")

    if broken_chains:
        ph = ",".join("?" * len(broken_chains))
        chains_conn.execute(
            f"UPDATE chains SET status='broken' WHERE chain_id IN ({ph})",
            broken_chains,
        )
        chains_conn.commit()
        print(f"已标记 {len(broken_chains)} 条链为 broken")

    chains_conn.close()
    cg.close()

    return {
        "total_chains": len(chains),
        "broken": len(broken_chains),
        "total_edges": total_edges,
        "broken_edges": broken_edges,
    }


# ============================================================ CLI

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="验证链边正确性 (支持 jar-analyzer 置信度评分)",
    )
    p.add_argument(
        "--jar-analyzer-db", default=None, type=Path,
        help="jar-analyzer.db 路径 (提供时启用置信度评分模式)",
    )
    p.add_argument(
        "--chains-db", default=None, type=Path,
        help="chains.db 路径 (默认从 preset 或项目目录自动探测)",
    )
    p.add_argument(
        "--codegraph-db", default=None, type=Path,
        help="codegraph.db 路径 (仅 //fqn: 模式需要; jar-analyzer 模式自动探测)",
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
    # Scan all projects for a single preset
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
    preset: Dict[str, Any] = {}

    if not group_id:
        preset = _find_preset(None)
        group_id = preset.get("groupId", "")

    if not group_id:
        print("ERROR: 无法确定 groupId，请通过 --group-id 参数指定", file=sys.stderr)
        return 2

    if not preset:
        preset = _find_preset(group_id)

    # Resolve chains-db
    chains_db = args.chains_db
    if not chains_db:
        loop_dir = Path(preset.get("loopDir", ""))
        if loop_dir:
            chains_db = Path(loop_dir) / "chains.db"
        else:
            # Heuristic: projects/{groupId}/loop_audit/chains.db
            chains_db = ROOT / "projects" / group_id / "loop_audit" / "chains.db"

    if not chains_db or not chains_db.is_file():
        print(f"ERROR: chains.db 不存在: {chains_db}", file=sys.stderr)
        return 2

    # Resolve codegraph-db (needed for both modes, but jar-analyzer mode auto-detects)
    codegraph_db = args.codegraph_db
    if not codegraph_db:
        cg_from_preset = preset.get("codegraphDb", "")
        if cg_from_preset:
            codegraph_db = Path(cg_from_preset)

    # Auto-detect jar-analyzer.db from preset if not provided
    jar_analyzer_db = args.jar_analyzer_db
    if not jar_analyzer_db or not jar_analyzer_db.is_file():
        ja_from_preset = preset.get("jarAnalyzerDb", "")
        if ja_from_preset:
            jar_analyzer_db = Path(ja_from_preset)
        if not jar_analyzer_db or not jar_analyzer_db.is_file():
            loop_dir = Path(preset.get("loopDir", ""))
            if loop_dir:
                candidate = loop_dir / "jar-analyzer.db"
                if candidate.is_file():
                    jar_analyzer_db = candidate

    # Choose mode
    if jar_analyzer_db and jar_analyzer_db.is_file():
        print(f"模式: jar-analyzer 边验证 (jar-analyzer.db = {jar_analyzer_db})")
        result = verify_with_jar_analyzer(chains_db, jar_analyzer_db, group_id)
    else:
        print("模式: //fqn: 注释验证 (jar-analyzer.db 不可用)")

        # Need codegraph for legacy mode
        if not codegraph_db or not codegraph_db.is_file():
            print("ERROR: codegraph.db 不存在 (//fqn: 模式必需)", file=sys.stderr)
            return 2

        print(f"codegraph.db = {codegraph_db}")
        result = verify_with_annotations(chains_db, codegraph_db, group_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
