"""
sqlite-extract-chain.py

从 codegraph SQLite 提取**前向调用链**。**两种模式**：

1. **recursive**（默认）：CTE RECURSIVE 无限深度（带环检测）
   - 适用：链深度未知
   - 性能：~100-500ms（深度 20）

2. **left**：LEFT JOIN 有限深度（1~4 跳）
   - 适用：精确控制深度 + 关联上下文（类/方法/注解）
   - 限制：4 跳 = 8 LEFT JOIN

**对应 codegraph v0.9.9 真实 schema**：
- `nodes(id, kind, name, qualified_name, file_path, start_line, ...)`
- `edges(source, target, kind, line, col, ...)` — kind='calls'/'references'/'contains'/...

用法:
    # 递归模式（深度 20）
    python sqlite-extract-chain.py --db codegraph.db \\
        --entry-method "method:HASH_ID" --depth 20

    # 递归模式（按 method qualified_name 自动找 id）
    python sqlite-extract-chain.py --db codegraph.db \\
        --entry-fqn "com.example.UserController.search" --depth 20

    # LEFT JOIN 模式
    python sqlite-extract-chain.py --db codegraph.db --mode left \\
        --entry-fqn "com.example.UserController.search" --depth 4
"""
import argparse
import json
import sqlite3
import sys


# ============================================================== 入口 fqn → id 解析

def resolve_entry(db_path: str, entry_fqn: str) -> str | None:
    """qualified_name (如 'pkg::Class::method') → nodes.id

    自动兼容两种格式:
    - 'pkg.Class#method' (route.json 格式) → 转换为 'pkg::Class::method'
    - 'pkg::Class::method' (codegraph 原生格式) → 直接使用
    """
    # 格式转换: pkg.Class#method → pkg::Class::method
    if "#" in entry_fqn and "::" not in entry_fqn:
        parts = entry_fqn.rsplit("#", 1)
        # parts[0] = "org.owasp.webgoat.container.HammerHead"
        # 需要变成 "org.owasp.webgoat.container::HammerHead"
        # 即把最后一个 . 换成 ::
        dot_pos = parts[0].rfind(".")
        if dot_pos > 0:
            entry_fqn = parts[0][:dot_pos] + "::" + parts[0][dot_pos+1:] + "::" + parts[1]
        else:
            entry_fqn = parts[0] + "::" + parts[1]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id FROM nodes WHERE kind='method' AND qualified_name = ? LIMIT 1",
        (entry_fqn,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ============================================================== recursive 模式

RECURSIVE_SQL = """
WITH RECURSIVE chain(id, qualified_name, depth, path, file_path, start_line) AS (
    -- 起点
    SELECT n.id, n.qualified_name, 0, '|' || n.id,
           n.file_path, n.start_line
    FROM nodes n
    WHERE n.id = :entry_id AND n.kind = 'method'

    UNION ALL

    -- 递归：沿 calls 边前向走
    SELECT callee.id, callee.qualified_name, c.depth + 1,
           c.path || '|' || callee.id,
           callee.file_path, callee.start_line
    FROM chain c
    JOIN edges e ON e.source = c.id AND e.kind = 'calls'
    JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || callee.id || '|') = 0  -- 防环
)
SELECT id, qualified_name, depth, file_path, start_line
FROM chain ORDER BY depth
"""


def extract_recursive(db_path: str, entry_id: str, max_depth: int) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(RECURSIVE_SQL, {"entry_id": entry_id, "max_depth": max_depth})
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================== left 模式

def build_left_sql(max_depth: int) -> tuple:
    """构造 LEFT JOIN 链查询 SQL（4 跳 = 8 LEFT JOIN）。

    起点 = method 别名 m0
    每跳 2 JOIN：edges e_i (calls) + nodes m_{i+1} (callee)
    """
    if not (1 <= max_depth <= 4):
        raise ValueError(f"left 模式深度必须 1~4（4 跳 = 8 JOIN），收到 {max_depth}")

    method_aliases = [f"m{i}" for i in range(max_depth + 1)]
    edge_aliases = [f"e{i}" for i in range(max_depth)]

    select_cols = [f"{method_aliases[0]}.qualified_name AS fqn_0",
                   f"{method_aliases[0]}.file_path AS file_0",
                   f"{method_aliases[0]}.start_line AS line_0"]

    joins = [f"FROM nodes {method_aliases[0]}"]

    for i in range(max_depth):
        prev = method_aliases[i]
        nxt = method_aliases[i + 1]
        edge = edge_aliases[i]
        joins.append(f"LEFT JOIN edges {edge} ON {edge}.source = {prev}.id AND {edge}.kind = 'calls'")
        joins.append(f"LEFT JOIN nodes {nxt} ON {nxt}.id = {edge}.target AND {nxt}.kind = 'method'")
        select_cols.append(f"{nxt}.qualified_name AS fqn_{i + 1}")
        select_cols.append(f"{nxt}.file_path AS file_{i + 1}")
        select_cols.append(f"{nxt}.start_line AS line_{i + 1}")

    where = f"WHERE {method_aliases[0]}.kind = 'method' AND {method_aliases[0]}.qualified_name = :entry_fqn"
    sql = f"SELECT {', '.join(select_cols)}\n" + "\n".join(joins) + f"\n{where}"
    return sql


def extract_left(db_path: str, entry_fqn: str, max_depth: int) -> tuple:
    sql = build_left_sql(max_depth)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, {"entry_fqn": entry_fqn})
    rows = cur.fetchall()
    conn.close()
    join_count = sql.count("LEFT JOIN")
    return [dict(r) for r in rows], join_count, sql


# ============================================================== CLI

def main():
    parser = argparse.ArgumentParser(description="从 codegraph SQLite 提取前向调用链")
    parser.add_argument("--db", required=True, help="codegraph SQLite 路径")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--entry-method", help="入口 method 的 nodes.id（如 method:HASH）")
    group.add_argument("--entry-fqn", help="入口 method 的 qualified_name（如 pkg::Class::method）")
    parser.add_argument("--depth", type=int, default=20, help="最大深度")
    parser.add_argument("--mode", choices=["recursive", "left"], default="recursive",
                        help="recursive=CTE 无限深度 / left=LEFT JOIN 1~4 跳")
    args = parser.parse_args()

    try:
        if args.mode == "recursive":
            entry_id = args.entry_method
            if not entry_id:
                entry_id = resolve_entry(args.db, args.entry_fqn)
                if not entry_id:
                    print(f"ERROR: 找不到 method: {args.entry_fqn}", file=sys.stderr)
                    sys.exit(2)
            rows = extract_recursive(args.db, entry_id, args.depth)
            print(json.dumps({
                "mode": "recursive",
                "entry_id": entry_id,
                "entry_fqn": args.entry_fqn or "",
                "depth_limit": args.depth,
                "actual_max_depth": max((r['depth'] for r in rows), default=0),
                "row_count": len(rows),
                "rows": rows,
            }, ensure_ascii=False, indent=2))
        else:  # left
            if not args.entry_fqn:
                print("ERROR: --mode left 必须用 --entry-fqn", file=sys.stderr)
                sys.exit(1)
            if args.depth > 4:
                args.depth = 4
            rows, join_count, sql = extract_left(args.db, args.entry_fqn, args.depth)
            print(json.dumps({
                "mode": "left",
                "join_count": join_count,
                "depth": args.depth,
                "row_count": len(rows),
                "rows": rows,
            }, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
