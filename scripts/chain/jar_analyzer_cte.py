# -*- coding: utf-8 -*-
"""jar_analyzer_cte.py — CTE recursive query functions for jar-analyzer.db.

Uses jar-analyzer.db tables (method_table, method_call_table, method_impl_table)
instead of codegraph's nodes/edges tables. Key advantage: disambiguation by
class_name + method_name + method_desc avoids false matches that codegraph
suffers from (e.g. Queue.add vs WebWolfTraceRepository.add).

Schema reference:
    method_table(method_id, method_name, method_desc, is_static, class_name,
                 access, line_number, jar_id)
    method_call_table(mc_id, caller_method_name, caller_class_name,
                      caller_method_desc, caller_jar_id, callee_method_name,
                      callee_method_desc, callee_class_name, callee_jar_id, op_code)
    method_impl_table(impl_id, class_name, method_name, method_desc,
                      impl_class_name, class_jar_id, impl_class_jar_id)

Output format mirrors sqlite-extract-chain.py for compatibility:
    {id, qualified_name, depth, path, file_path, start_line}
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ============================================================ FQN conversion

def _fqn_to_jar_class_method(entry_fqn: str) -> tuple[str, str]:
    """Convert entry FQN to jar-analyzer (class_name, method_name).

    Input:  'org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b#completed'
    Output: ('org/owasp/webgoat/lessons/sqlinjection/advanced/SqlInjectionLesson6b', 'completed')

    class_name uses '/' separator (JVM internal format).
    method_name is the part after '#'.
    """
    if "#" in entry_fqn:
        class_part, method_part = entry_fqn.rsplit("#", 1)
    else:
        # No '#' — treat entire string as class, method_name empty
        class_part = entry_fqn
        method_part = ""

    # Replace '.' with '/' in the class part to get JVM internal format
    jar_class_name = class_part.replace(".", "/")
    return jar_class_name, method_part


def _jar_class_to_dot(class_name: str) -> str:
    """Convert JVM internal class_name ('org/owasp/...') to dot format ('org.owasp...')."""
    return class_name.replace("/", ".")


# ============================================================ resolve_entry

def resolve_entry_jar_analyzer(db_path: str, entry_fqn: str) -> str | None:
    """Convert entry FQN to jar-analyzer method_id.

    Args:
        db_path: path to jar-analyzer.db
        entry_fqn: e.g. 'org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b#completed'

    Returns:
        method_id as string (for compatibility with codegraph node_id format), or None.
    """
    jar_class_name, method_name = _fqn_to_jar_class_method(entry_fqn)

    if not method_name:
        return None

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT method_id FROM method_table "
            "WHERE class_name = ? AND method_name = ? LIMIT 1",
            (jar_class_name, method_name),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


# ============================================================ CTE recursive

_RECURSIVE_SQL = """
WITH RECURSIVE chain(method_id, class_name, method_name, method_desc, depth, path) AS (
    -- Anchor: start from the entry method
    SELECT m.method_id, m.class_name, m.method_name, m.method_desc, 0,
           '|' || CAST(m.method_id AS TEXT) || '|'
    FROM method_table m
    WHERE CAST(m.method_id AS TEXT) = :entry_id

    UNION ALL

    -- Recursive: walk caller -> callee edges
    SELECT callee.method_id, callee.class_name, callee.method_name,
           callee.method_desc, c.depth + 1,
           c.path || CAST(callee.method_id AS TEXT) || '|'
    FROM chain c
    JOIN method_call_table mc
        ON mc.caller_method_name = c.method_name
        AND mc.caller_class_name = c.class_name
        AND mc.caller_method_desc = c.method_desc
    JOIN method_table callee
        ON callee.method_name = mc.callee_method_name
        AND callee.class_name = mc.callee_class_name
        AND callee.method_desc = mc.callee_method_desc
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || CAST(callee.method_id AS TEXT) || '|') = 0
)
SELECT method_id, class_name, method_name, method_desc, depth, path
FROM chain ORDER BY depth
"""


def extract_recursive_jar_analyzer(
    db_path: str, entry_id: str, max_depth: int,
) -> list[dict]:
    """CTE recursive query on method_call_table starting from entry_id.

    Walks caller→callee edges using method_name + class_name + method_desc
    for disambiguation (the whole point of using jar-analyzer over codegraph).

    Args:
        db_path: path to jar-analyzer.db
        entry_id: method_id from resolve_entry_jar_analyzer (as string)
        max_depth: maximum recursion depth

    Returns:
        List of dicts with keys: id, qualified_name, depth, path, file_path, start_line
        file_path is always None for jar-analyzer (no source file path in method_table).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Run CTE query
        cur = conn.execute(_RECURSIVE_SQL, {"entry_id": entry_id, "max_depth": max_depth})
        rows = cur.fetchall()

        # Build line_number lookup for all method_ids in the chain
        method_ids = [str(r["method_id"]) for r in rows]
        line_lookup = _batch_line_numbers(conn, method_ids)

        result = []
        for r in rows:
            mid = str(r["method_id"])
            qname = f"{_jar_class_to_dot(r['class_name'])}::{r['method_name']}"
            result.append({
                "id": mid,
                "qualified_name": qname,
                "depth": r["depth"],
                "path": r["path"],
                "file_path": None,
                "start_line": line_lookup.get(mid),
            })
        return result
    finally:
        conn.close()


def _batch_line_numbers(
    conn: sqlite3.Connection, method_ids: list[str],
) -> dict[str, int | None]:
    """Batch query line_number from method_table for a list of method_ids.
    
    Handles large lists by deduplicating and batching (500 at a time).
    """
    if not method_ids:
        return {}
    
    # Deduplicate
    unique_ids = list(set(method_ids))
    
    # Batch in chunks of 500 to avoid SQLite variable limit
    result = {}
    chunk_size = 500
    for i in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT method_id, line_number FROM method_table "
            f"WHERE CAST(method_id AS TEXT) IN ({placeholders})",
            chunk,
        ).fetchall()
        result.update({str(r["method_id"]): r["line_number"] for r in rows})
    
    return result


# ============================================================ method_impl resolution

_IMPL_CTE_SQL = """
WITH RECURSIVE chain(method_id, class_name, method_name, method_desc, depth, path) AS (
    SELECT m.method_id, m.class_name, m.method_name, m.method_desc, 0,
           '|' || CAST(m.method_id AS TEXT) || '|'
    FROM method_table m
    WHERE CAST(m.method_id AS TEXT) = :entry_id

    UNION ALL

    -- Branch 1: direct call via method_call_table (caller→callee)
    SELECT callee.method_id, callee.class_name, callee.method_name,
           callee.method_desc, c.depth + 1,
           c.path || CAST(callee.method_id AS TEXT) || '|'
    FROM chain c
    JOIN method_call_table mc
        ON mc.caller_method_name = c.method_name
        AND mc.caller_class_name = c.class_name
        AND mc.caller_method_desc = c.method_desc
    JOIN method_table callee
        ON callee.method_name = mc.callee_method_name
        AND callee.class_name = mc.callee_class_name
        AND callee.method_desc = mc.callee_method_desc
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || CAST(callee.method_id AS TEXT) || '|') = 0

    UNION ALL

    -- Branch 2: interface/abstract → concrete impl resolution
    SELECT impl.method_id, impl.class_name, impl.method_name,
           impl.method_desc, c.depth + 1,
           c.path || CAST(impl.method_id AS TEXT) || '|'
    FROM chain c
    JOIN method_impl_table mi
        ON mi.class_name = c.class_name
        AND mi.method_name = c.method_name
        AND mi.method_desc = c.method_desc
    JOIN method_table impl
        ON impl.class_name = mi.impl_class_name
        AND impl.method_name = mi.method_name
        AND impl.method_desc = mi.method_desc
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || CAST(impl.method_id AS TEXT) || '|') = 0
)
SELECT method_id, class_name, method_name, method_desc, depth, path
FROM chain
"""


def extract_recursive_with_impl(
    db_path: str, entry_id: str, max_depth: int,
) -> list[dict]:
    """CTE recursive query including interface→implementation resolution.

    Like extract_recursive_jar_analyzer but adds a second UNION ALL branch
    that resolves interface/abstract calls to concrete implementations via
    method_impl_table.

    Args:
        db_path: path to jar-analyzer.db
        entry_id: method_id from resolve_entry_jar_analyzer
        max_depth: maximum recursion depth

    Returns:
        Same format as extract_recursive_jar_analyzer.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(_IMPL_CTE_SQL, {"entry_id": entry_id, "max_depth": max_depth})
        rows = cur.fetchall()

        method_ids = [str(r["method_id"]) for r in rows]
        line_lookup = _batch_line_numbers(conn, method_ids)

        result = []
        for r in rows:
            mid = str(r["method_id"])
            qname = f"{_jar_class_to_dot(r['class_name'])}::{r['method_name']}"
            result.append({
                "id": mid,
                "qualified_name": qname,
                "depth": r["depth"],
                "path": r["path"],
                "file_path": None,
                "start_line": line_lookup.get(mid),
            })
        return result
    finally:
        conn.close()


# ============================================================ batch metadata

def get_method_meta(db_path: str, method_ids: list[str]) -> dict[str, dict]:
    """Batch query method_table for metadata.

    Args:
        db_path: path to jar-analyzer.db
        method_ids: list of method_id strings

    Returns:
        Dict keyed by method_id with values:
        {qualified_name, class_name, method_name, method_desc, line_number, is_static}
    """
    if not method_ids:
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Deduplicate
        unique_ids = list(set(method_ids))
        
        # Batch in chunks of 500 to avoid SQLite variable limit
        result = {}
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT method_id, class_name, method_name, method_desc, "
                f"is_static, line_number, end_line "
                f"FROM method_table "
                f"WHERE CAST(method_id AS TEXT) IN ({placeholders})",
                chunk,
            ).fetchall()

            for r in rows:
                mid = str(r["method_id"])
                qname = f"{_jar_class_to_dot(r['class_name'])}::{r['method_name']}"
                result[mid] = {
                    "qualified_name": qname,
                    "class_name": r["class_name"],
                    "method_name": r["method_name"],
                    "method_desc": r["method_desc"],
                    "line_number": r["line_number"],
                    "end_line": r["end_line"] if r["end_line"] > 0 else None,
                    "is_static": r["is_static"],
                }
        return result
    finally:
        conn.close()


# ============================================================ file path helpers

def get_file_paths(db_path: str, class_names: list[str]) -> dict[str, str]:
    """批量查询 class_file_table 获取 class_name → path_str（文件路径）。

    Args:
        db_path: jar-analyzer.db 路径
        class_names: class_name 列表 (如 ['org/owasp/webgoat/lessons/xxx/ClassName'])

    Returns:
        dict: class_name → path_str (如 'BOOT-INF/classes/org/owasp/...ClassName.class')
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(class_names))
        rows = conn.execute(
            f"SELECT class_name, path_str FROM class_file_table WHERE class_name IN ({placeholders})",
            tuple(class_names),
        ).fetchall()
        result = {row["class_name"]: row["path_str"] for row in rows}
        return result
    finally:
        conn.close()


def class_name_to_java_source_path(class_name: str, project_root: Path) -> Path:
    """Convert jar-analyzer class_name (JVM internal) → .java source file path relative to project_root.

    Args:
        class_name: JVM internal class_name (如 'org/owasp/webgoat/lessons/xxx/ClassName$Inner')
                    uses '/' separator
        project_root: project root directory

    Returns:
        Path to .java source file (如 project_root/src/main/java/org/owasp/...ClassName.java)
        For inner/anonymous classes ($1, $2 etc.), returns the outer class's .java file.
    """
    # Strip inner/anonymous class suffixes ($1, $2, etc.)
    outer_class = class_name
    dollar_idx = outer_class.find("$")
    if dollar_idx > 0:
        outer_class = outer_class[:dollar_idx]

    # Convert '/' separator → .java extension
    # org/owasp/.../ClassName → org/owasp/.../ClassName.java
    java_rel_path = outer_class + ".java"
    return project_root / "src" / "main" / "java" / Path(java_rel_path)


def get_method_line_numbers(db_path: str, method_ids: list[str]) -> dict[str, int]:
    """批量查询 method_table 获取 method_id → line_number.

    Args:
        db_path: jar-analyzer.db 路径
        method_ids: method_id 列表
    Returns:
        dict: method_id → line_number
    """
    if not method_ids:
        return {}
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Deduplicate
        unique_ids = list(set(method_ids))
        
        # Batch in chunks of 500 to avoid SQLite variable limit
        result = {}
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT method_id, line_number FROM method_table WHERE CAST(method_id AS TEXT) IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            result.update({str(row["method_id"]): row["line_number"] for row in rows})
        
        return result
    finally:
        conn.close()


def get_method_call_edges(db_path: str, method_ids: list[str]) -> dict[str, list[str]]:
    """批量查询 method_call_table 获取 method_id 的 caller → callee 边信息.

    For each method_id, find all edges where that method is a caller.

    Args:
        db_path: jar-analyzer.db 路径
        method_ids: method_id 列表 (jar-analyzer method_ids)

    Returns:
        dict: caller_method_id → [callee_method_id, ...]
    """
    if not method_ids:
        return {}
    
    # Build (class_name, method_name, method_desc) → method_id mapping
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Deduplicate
        unique_ids = list(set(method_ids))
        
        # Batch in chunks of 500 to avoid SQLite variable limit
        sig_to_id = {}
        all_ids = set()
        chunk_size = 500
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            method_rows = conn.execute(
                f"SELECT method_id, class_name, method_name, method_desc FROM method_table WHERE CAST(method_id AS TEXT) IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for r in method_rows:
                mid = str(r["method_id"])
                sig_to_id[(r["class_name"], r["method_name"], r["method_desc"])] = mid
                all_ids.add(mid)

        edges_map: dict[str, list[str]] = {}
        # Re-query all method_rows for edge lookup (reuse the data)
        all_method_rows = []
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT method_id, class_name, method_name, method_desc FROM method_table WHERE CAST(method_id AS TEXT) IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            all_method_rows.extend(rows)
        
        for r in all_method_rows:
            mid = str(r["method_id"])
            caller_class = r["class_name"]
            caller_method = r["method_name"]
            caller_desc = r["method_desc"]
            callees = []
            for edge_row in conn.execute(
                "SELECT callee_class_name, callee_method_name, callee_method_desc "
                "FROM method_call_table "
                "WHERE caller_class_name = ? AND caller_method_name = ? AND caller_method_desc = ?",
                (caller_class, caller_method, caller_desc),
            ).fetchall():
                callee_sig = (edge_row["callee_class_name"], edge_row["callee_method_name"], edge_row["callee_method_desc"])
                callee_id = sig_to_id.get(callee_sig)
                if callee_id and callee_id in all_ids:
                    callees.append(callee_id)
            if callees:
                edges_map[mid] = callees
        return edges_map
    finally:
        conn.close()
