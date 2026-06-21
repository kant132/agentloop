"""
compare_call_graphs.py
=====================

Compare codegraph.db and jar-analyzer.db call graphs to measure edge accuracy.

Five comparison sections:
1. Method count comparison
2. Edge count comparison
3. Spring endpoint comparison
4. Edge accuracy (codegraph→jar-analyzer direction)
5. Reverse comparison (jar-analyzer→codegraph direction)

CLI
---
    python compare_call_graphs.py \
        --codegraph-db path/to/codegraph.db \
        --jar-analyzer-db path/to/jar-analyzer.db \
        --output comparison.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _open_db(path: Path, readonly: bool = True) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(f"file:{path}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cg_qn_to_jar_parts(qualified_name: str) -> Tuple[str, str]:
    """Convert codegraph qualified_name to jar-analyzer (class_name, method_name).

    codegraph format: ``org.owasp.webgoat.lessons.xxx::ClassName::methodName``
    jar-analyzer format: class_name uses ``/`` separator, e.g.
    ``org/owasp/webgoat/lessons/xxx/ClassName``; method_name is bare.
    """
    if "::" in qualified_name:
        parts = qualified_name.split("::")
        # parts[0] = package, parts[-2] = class, parts[-1] = method
        pkg = parts[0] if len(parts) >= 3 else ""
        cls = parts[-2] if len(parts) >= 2 else ""
        method = parts[-1]
        # jar-analyzer uses / for ALL separators (package levels + class boundary)
        pkg_slash = pkg.replace(".", "/") if pkg else ""
        class_name = f"{pkg_slash}/{cls}" if pkg_slash else cls
        return class_name, method

    # Fallback: last dot-separation gives method, rest is package+class
    last_dot = qualified_name.rfind(".")
    if last_dot > 0:
        method = qualified_name[last_dot + 1:]
        prefix = qualified_name[:last_dot]
        # Last segment of prefix is the class name; rest is package with dots→slashes
        last_pkg_dot = prefix.rfind(".")
        if last_pkg_dot > 0:
            pkg = prefix[:last_pkg_dot].replace(".", "/")
            cls = prefix[last_pkg_dot + 1:]
            class_name = f"{pkg}/{cls}"
        else:
            class_name = prefix.replace(".", "/")
        return class_name, method

    # Single token — treat as method with empty class
    return "", qualified_name


def _jar_parts_to_cg_qn(class_name: str, method_name: str) -> str:
    """Convert jar-analyzer (class_name, method_name) to codegraph qualified_name.

    ``org/owasp/webgoat/lessons/xxx/ClassName`` + ``methodName``
    → ``org.owasp.webgoat.lessons.xxx::ClassName::methodName``
    """
    dot_pkg_cls = class_name.replace("/", ".")
    last_dot = dot_pkg_cls.rfind(".")
    if last_dot > 0:
        pkg = dot_pkg_cls[:last_dot]
        cls = dot_pkg_cls[last_dot + 1:]
        return f"{pkg}::{cls}::{method_name}"
    # class_name has no package depth — just class::method
    return f"{dot_pkg_cls}::{method_name}"


def compare(codegraph_db: Path, jar_analyzer_db: Path) -> Dict[str, Any]:
    cg = _open_db(codegraph_db)
    jar = _open_db(jar_analyzer_db)

    # ── Section 1: Method count ──────────────────────────────────────────
    cg_methods = cg.execute("SELECT COUNT(*) FROM nodes WHERE kind='method'").fetchone()[0]
    jar_methods = jar.execute("SELECT COUNT(*) FROM method_table").fetchone()[0]

    # ── Section 2: Edge count ─────────────────────────────────────────────
    cg_calls = cg.execute("SELECT COUNT(*) FROM edges WHERE kind='calls'").fetchone()[0]
    jar_calls = jar.execute("SELECT COUNT(*) FROM method_call_table").fetchone()[0]

    # ── Section 3: Spring endpoint count ──────────────────────────────────
    cg_routes = cg.execute("SELECT COUNT(*) FROM nodes WHERE kind='route'").fetchone()[0]
    # spring_method_table may not exist; handle gracefully
    try:
        jar_spring = jar.execute("SELECT COUNT(*) FROM spring_method_table").fetchone()[0]
    except sqlite3.OperationalError:
        jar_spring = 0

    # ── Section 4: Edge accuracy (codegraph → jar-analyzer) ──────────────
    # Build jar-analyzer edge set for fast lookup.
    # Key: (caller_class_name, caller_method_name, callee_class_name, callee_method_name)
    jar_edge_rows = jar.execute(
        "SELECT caller_class_name, caller_method_name, "
        "callee_class_name, callee_method_name "
        "FROM method_call_table"
    ).fetchall()

    jar_edge_set: set = set()
    for r in jar_edge_rows:
        key = (r["caller_class_name"], r["caller_method_name"],
               r["callee_class_name"], r["callee_method_name"])
        jar_edge_set.add(key)

    # Load codegraph node info: qualified_name + file_path for Java/non-Java classification.
    cg_nodes: Dict[str, str] = {}
    cg_node_paths: Dict[str, str] = {}
    for r in cg.execute("SELECT id, qualified_name, file_path FROM nodes"):
        cg_nodes[r["id"]] = r["qualified_name"]
        cg_node_paths[r["id"]] = r["file_path"] or ""

    # Load codegraph call edges.
    cg_edges = cg.execute(
        "SELECT source, target FROM edges WHERE kind='calls'"
    ).fetchall()

    cg_edges_in_jar = 0
    cg_edges_not_in_jar = 0
    cg_java_edges_in_jar = 0
    cg_java_edges_not_in_jar = 0
    cg_java_edge_count = 0
    false_positive_examples: List[Dict[str, str]] = []

    for edge in cg_edges:
        src_qn = cg_nodes.get(edge["source"], "")
        tgt_qn = cg_nodes.get(edge["target"], "")
        if not src_qn or not tgt_qn:
            cg_edges_not_in_jar += 1
            continue

        # Classify as Java edge if both source and target are in .java files
        src_path = cg_node_paths.get(edge["source"], "")
        tgt_path = cg_node_paths.get(edge["target"], "")
        is_java_edge = src_path.endswith(".java") and tgt_path.endswith(".java")
        if is_java_edge:
            cg_java_edge_count += 1

        src_cls, src_meth = _cg_qn_to_jar_parts(src_qn)
        tgt_cls, tgt_meth = _cg_qn_to_jar_parts(tgt_qn)

        lookup_key = (src_cls, src_meth, tgt_cls, tgt_meth)
        if lookup_key in jar_edge_set:
            cg_edges_in_jar += 1
            if is_java_edge:
                cg_java_edges_in_jar += 1
        else:
            cg_edges_not_in_jar += 1
            if is_java_edge:
                cg_java_edges_not_in_jar += 1
            # Prioritize Java false positives in examples (show up to 10 Java first)
            if is_java_edge and len(false_positive_examples) < 10:
                false_positive_examples.append({
                    "source": src_qn,
                    "target": tgt_qn,
                    "jar_source_class": src_cls,
                    "jar_source_method": src_meth,
                    "jar_target_class": tgt_cls,
                    "jar_target_method": tgt_meth,
                    "note": "codegraph edge not found in jar-analyzer",
                })

    # If fewer than 10 Java examples, fill remaining slots with non-Java ones
    for edge in cg_edges:
        if len(false_positive_examples) >= 10:
            break
        src_qn = cg_nodes.get(edge["source"], "")
        tgt_qn = cg_nodes.get(edge["target"], "")
        if not src_qn or not tgt_qn:
            continue
        src_path = cg_node_paths.get(edge["source"], "")
        tgt_path = cg_node_paths.get(edge["target"], "")
        is_java_edge = src_path.endswith(".java") and tgt_path.endswith(".java")
        if is_java_edge:
            continue  # Already collected above
        src_cls, src_meth = _cg_qn_to_jar_parts(src_qn)
        tgt_cls, tgt_meth = _cg_qn_to_jar_parts(tgt_qn)
        lookup_key = (src_cls, src_meth, tgt_cls, tgt_meth)
        if lookup_key not in jar_edge_set:
            false_positive_examples.append({
                "source": src_qn,
                "target": tgt_qn,
                "note": "non-Java codegraph edge (not in jar-analyzer scope)",
            })

    mismatch_rate = cg_edges_not_in_jar / cg_calls if cg_calls > 0 else 0.0
    java_mismatch_rate = (
        cg_java_edges_not_in_jar / cg_java_edge_count
        if cg_java_edge_count > 0 else 0.0
    )

    # ── Section 5: Reverse comparison (jar-analyzer → codegraph) ──────────
    # Build codegraph edge set for reverse lookup.
    # Key: (src_qn, tgt_qn) — using qualified_name directly for identity.
    cg_edge_set: set = set()
    for edge in cg_edges:
        src_qn = cg_nodes.get(edge["source"], "")
        tgt_qn = cg_nodes.get(edge["target"], "")
        if src_qn and tgt_qn:
            cg_edge_set.add((src_qn, tgt_qn))

    # Build reverse lookup: (jar_class, jar_method) → cg qualified_name
    # for resolving jar edges against codegraph.
    jar_to_cg_map: Dict[Tuple[str, str], str] = {}
    for nid, qn in cg_nodes.items():
        cls, meth = _cg_qn_to_jar_parts(qn)
        if cls and meth:
            jar_to_cg_map[(cls, meth)] = qn

    jar_edges_in_cg = 0
    jar_edges_not_in_cg = 0

    for r in jar_edge_rows:
        src_key = (r["caller_class_name"], r["caller_method_name"])
        tgt_key = (r["callee_class_name"], r["callee_method_name"])
        src_qn = jar_to_cg_map.get(src_key, "")
        tgt_qn = jar_to_cg_map.get(tgt_key, "")
        if src_qn and tgt_qn and (src_qn, tgt_qn) in cg_edge_set:
            jar_edges_in_cg += 1
        else:
            jar_edges_not_in_cg += 1

    cg.close()
    jar.close()

    return {
        "codegraph": {
            "methods": cg_methods,
            "calls": cg_calls,
            "routes": cg_routes,
        },
        "jar_analyzer": {
            "methods": jar_methods,
            "calls": jar_calls,
            "spring_endpoints": jar_spring,
        },
        "edge_comparison": {
            "cg_edges_checked": cg_calls,
            "cg_edges_in_jar": cg_edges_in_jar,
            "cg_edges_not_in_jar": cg_edges_not_in_jar,
            "mismatch_rate": round(mismatch_rate, 3),
            "java_edges_checked": cg_java_edge_count,
            "java_edges_in_jar": cg_java_edges_in_jar,
            "java_edges_not_in_jar": cg_java_edges_not_in_jar,
            "java_mismatch_rate": round(java_mismatch_rate, 3),
            "false_positive_examples": false_positive_examples,
        },
        "reverse_comparison": {
            "jar_edges_checked": jar_calls,
            "jar_edges_in_cg": jar_edges_in_cg,
            "jar_edges_not_in_cg": jar_edges_not_in_cg,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare codegraph.db and jar-analyzer.db call graphs"
    )
    parser.add_argument(
        "--codegraph-db", required=True, type=Path,
        help="Path to codegraph SQLite DB"
    )
    parser.add_argument(
        "--jar-analyzer-db", required=True, type=Path,
        help="Path to jar-analyzer SQLite DB"
    )
    parser.add_argument(
        "--output", default=None, type=Path,
        help="Output JSON path (default: stdout)"
    )
    args = parser.parse_args()

    if not args.codegraph_db.exists():
        print(f"Error: codegraph-db not found: {args.codegraph_db}", file=sys.stderr)
        sys.exit(1)
    if not args.jar_analyzer_db.exists():
        print(f"Error: jar-analyzer-db not found: {args.jar_analyzer_db}", file=sys.stderr)
        sys.exit(1)

    result = compare(args.codegraph_db, args.jar_analyzer_db)

    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_str, encoding="utf-8")
        print(f"Written to {args.output}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
