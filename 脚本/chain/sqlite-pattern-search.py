"""
sqlite-pattern-search.py

通过 codegraph SQLite 查找匹配模式的 method（**真实 v0.9.9 schema**）。

**匹配策略**：
- 主：nodes.name / nodes.qualified_name LIKE 模式
- 辅：读源文件检查方法体（含 sink 调用时）

对应 schema：
- `nodes(id, kind, name, qualified_name, file_path, start_line, end_line)`
- `edges(source, target, kind)` — kind='calls'

用法:
    python sqlite-pattern-search.py --db codegraph.db --pattern sql_injection
    python sqlite-pattern-search.py --db codegraph.db --pattern rce
    python sqlite-pattern-search.py --db codegraph.db --pattern ssrf
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path


# ============================================================== 预定义模式

# 注意：name 用 LIKE（小写），匹配 'execute' / 'executeQuery' / 'executeUpdate' 等
# 加 source 路径以缩小范围（如 sqlinjection / taintaudit / deserialization 包）
PATTERNS = {
    "sql_injection": {
        "description": "SQL 注入 sink（Statement / executeQuery / executeUpdate）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%executeQuery%'
                OR n.name LIKE '%executeUpdate%'
                OR n.name LIKE '%executeSql%'
                OR n.name LIKE '%buildUnexecutedSql%'
                OR n.qualified_name LIKE '%::Statement%'
                OR n.qualified_name LIKE '%::JdbcTemplate%'
                OR n.qualified_name LIKE '%::prepareStatement%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "rce": {
        "description": "RCE sink（Runtime.exec / ProcessBuilder / reflective）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%exec%'
                OR n.name LIKE '%runtimeExec%'
                OR n.name LIKE '%reflectiveExec%'
                OR n.qualified_name LIKE '%::Runtime%'
                OR n.qualified_name LIKE '%::ProcessBuilder%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "deserialize": {
        "description": "反序列化 sink（readObject / XMLDecoder / ObjectMapper.readValue）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%readObject%'
                OR n.name LIKE '%readValue%'
                OR n.name LIKE '%XMLDecoder%'
                OR n.qualified_name LIKE '%::ObjectInputStream%'
                OR n.qualified_name LIKE '%::ObjectMapper%'
                OR n.qualified_name LIKE '%::SnakeYaml%'
                OR n.qualified_name LIKE '%::XMLDecoder%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "ssrf": {
        "description": "SSRF sink（URL.openConnection / HttpClient.execute）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%openConnection%'
                OR n.name LIKE '%HttpClient%'
                OR n.name LIKE '%RestTemplate%'
                OR n.name LIKE '%WebClient%'
                OR n.name LIKE '%downloadFileFromURL%'
                OR n.qualified_name LIKE '%::URL%'
                OR n.qualified_name LIKE '%::URI%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "path_traversal": {
        "description": "路径遍历 sink（FileInputStream / Paths.get / new File）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%FileInputStream%'
                OR n.name LIKE '%FileReader%'
                OR n.name LIKE '%Paths.get%'
                OR n.name LIKE '%newFile%'
                OR n.name LIKE '%ProfileUpload%'
                OR n.qualified_name LIKE '%::FileInputStream%'
                OR n.qualified_name LIKE '%::Path%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "ldap": {
        "description": "LDAP 注入（DirContext.search）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.name LIKE '%ldapSearch%'
                OR n.name LIKE '%DirContext%'
                OR n.qualified_name LIKE '%::LdapTemplate%'
              )
            ORDER BY n.qualified_name
        """,
    },
    "endpoint_route": {
        "description": "所有端点（kind=route）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'route'
            ORDER BY n.file_path, n.start_line
        """,
    },
    "controller_method": {
        "description": "所有 method（粗筛：按 qualified_name 命名约定）",
        "sql": """
            SELECT n.id, n.name, n.qualified_name, n.file_path, n.start_line
            FROM nodes n
            WHERE n.kind = 'method'
              AND (
                n.qualified_name LIKE '%::Controller::%'
                OR n.qualified_name LIKE '%::Endpoint::%'
                OR n.qualified_name LIKE '%::HammerHead::%'
              )
            ORDER BY n.qualified_name
        """,
    },
}


# ============================================================== 执行

def search_pattern(db_path: str, pattern_name: str, limit: int = 1000) -> list:
    if pattern_name not in PATTERNS:
        available = ", ".join(PATTERNS.keys())
        raise ValueError(f"未知 pattern: {pattern_name}。可用: {available}")

    sql = PATTERNS[pattern_name]["sql"] + f"\nLIMIT {int(limit)}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="codegraph SQLite 模式搜索（真实 schema）")
    parser.add_argument("--db", required=True, help="codegraph SQLite 路径")
    parser.add_argument("--pattern", required=True, choices=list(PATTERNS.keys()),
                        help=f"预定义 pattern: {', '.join(PATTERNS.keys())}")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    try:
        results = search_pattern(args.db, args.pattern, args.limit)
        print(json.dumps({
            "pattern": args.pattern,
            "description": PATTERNS[args.pattern]["description"],
            "count": len(results),
            "results": results,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
