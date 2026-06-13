"""
sqlite-multi-hop-search.py

**用上 20 LEFT JOIN 能力**的真正工具（**真实 codegraph v0.9.9 schema**）。

本轮焦点 = 正向追踪：从端点出发，沿 calls 边前向 1~5 跳，
每跳关联"类/路由/字段/sink 模式"等元信息，一次 SQL 拉平返回。

对应真实 schema：
- `nodes(id, kind, name, qualified_name, file_path, start_line, end_line, signature, ...)`
- `edges(source, target, kind, line)` — kind='calls'/'references'/'contains'

**每跳 4 LEFT JOIN**：edge + callee_node + containing_class + route（首跳）
**5 跳 = 20 LEFT JOIN**（含 1 个 route JOIN + 4 个 class JOIN）

用法:
    # 用预定义模板
    python sqlite-multi-hop-search.py --db codegraph.db --template forward_5hop_20join

    # 限定端点
    python sqlite-multi-hop-search.py --db codegraph.db --template forward_5hop_20join \\
        --entry-fqn "org.owasp.webgoat.container::HammerHead::attack"

    # 找 SQL 注入候选链（多 sink 类型一次扫）
    python sqlite-multi-hop-search.py --db codegraph.db --template multi_sink_search

    # 自定义 SQL
    python sqlite-multi-hop-search.py --db codegraph.db --custom "SELECT ..."
"""
import argparse
import json
import sqlite3
import sys
import textwrap
from typing import Dict, List, Tuple


# ============================================================== 真实 schema 模板

# T1: 端点 → 5 跳前向 + 类上下文 + route 起点 = 20 LEFT JOIN
#     每跳: 1 edge (calls) + 1 callee (method) + 1 class (containing) = 3 JOIN/hop
#     加上首跳的 route 关联 = 1 JOIN
#     总: 5*3 + 1 = 16 JOIN ... 还差 4
#     加 m0/m5 的 caller 关联 + m0/m5 的 field 关联 = +4 = 20 JOIN
T1_FORWARD_5HOP_20JOIN = """
-- 端点 m0 → m1 → m2 → m3 → m4 → m5
-- 每跳 4 JOIN：edge + callee + containing_class
-- 首跳 +1 route JOIN；末跳 +1 field 关联；起跳 +1 caller 关联；再加 3 跳 class = 20
SELECT
    m0.qualified_name AS m0_fqn,
    m0.file_path      AS m0_file,
    m0.start_line     AS m0_line,
    m0.signature      AS m0_sig,
    r0.name           AS route_name,
    cls0.qualified_name AS cls0_fqn,
    caller0.qualified_name AS caller0_fqn,
    m1.qualified_name AS m1_fqn,
    m1.start_line     AS m1_line,
    cls1.qualified_name AS cls1_fqn,
    m2.qualified_name AS m2_fqn,
    m2.start_line     AS m2_line,
    cls2.qualified_name AS cls2_fqn,
    m3.qualified_name AS m3_fqn,
    m3.start_line     AS m3_line,
    cls3.qualified_name AS cls3_fqn,
    m4.qualified_name AS m4_fqn,
    m4.start_line     AS m4_line,
    cls4.qualified_name AS cls4_fqn,
    m5.qualified_name AS m5_fqn,
    m5.start_line     AS m5_line,
    cls5.qualified_name AS cls5_fqn,
    fld0.name         AS m0_class_field_name,
    fld0.qualified_name AS m0_class_field_fqn
FROM nodes m0
LEFT JOIN nodes r0          ON r0.kind = 'route' AND r0.file_path = m0.file_path AND r0.start_line = m0.start_line
LEFT JOIN nodes cls0        ON cls0.kind = 'class' AND cls0.file_path = m0.file_path
LEFT JOIN edges caller0_e   ON caller0_e.target = m0.id AND caller0_e.kind = 'calls'
LEFT JOIN nodes caller0     ON caller0.id = caller0_e.source AND caller0.kind = 'method'
LEFT JOIN nodes fld0        ON fld0.kind = 'field' AND fld0.file_path = cls0.file_path
LEFT JOIN edges e1          ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1          ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN nodes cls1        ON cls1.kind = 'class' AND cls1.file_path = m1.file_path
LEFT JOIN edges e2          ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes m2          ON m2.id = e2.target AND m2.kind = 'method'
LEFT JOIN nodes cls2        ON cls2.kind = 'class' AND cls2.file_path = m2.file_path
LEFT JOIN edges e3          ON e3.source = m2.id AND e3.kind = 'calls'
LEFT JOIN nodes m3          ON m3.id = e3.target AND m3.kind = 'method'
LEFT JOIN nodes cls3        ON cls3.kind = 'class' AND cls3.file_path = m3.file_path
LEFT JOIN edges e4          ON e4.source = m3.id AND e4.kind = 'calls'
LEFT JOIN nodes m4          ON m4.id = e4.target AND m4.kind = 'method'
LEFT JOIN nodes cls4        ON cls4.kind = 'class' AND cls4.file_path = m4.file_path
LEFT JOIN edges e5          ON e5.source = m4.id AND e5.kind = 'calls'
LEFT JOIN nodes m5          ON m5.id = e5.target AND m5.kind = 'method'
LEFT JOIN nodes cls5        ON cls5.kind = 'class' AND cls5.file_path = m5.file_path
WHERE m0.kind = 'method'
  AND m0.qualified_name = :entry_fqn
"""

# T2: 多 sink 类型（SQLi + RCE + Deser + SSRF + Path Traversal）= 8 JOIN
T2_MULTI_SINK = """
-- 找"端点 → ... → 任意危险 sink"，1 次扫多种漏洞类型
SELECT
    m0.qualified_name AS ep_fqn,
    m0.file_path AS ep_file,
    sink.qualified_name AS sink_fqn,
    sink.start_line AS sink_line,
    sink.name AS sink_name,
    CASE
        WHEN sink.name LIKE '%executeQuery%' OR sink.name LIKE '%executeUpdate%' OR sink.qualified_name LIKE '%::Statement%' THEN 'SQLI'
        WHEN sink.name LIKE '%exec%' OR sink.qualified_name LIKE '%::Runtime%' OR sink.qualified_name LIKE '%::ProcessBuilder%' THEN 'RCE'
        WHEN sink.name LIKE '%readObject%' OR sink.name LIKE '%readValue%' OR sink.name LIKE '%XMLDecoder%' THEN 'DESER'
        WHEN sink.name LIKE '%openConnection%' OR sink.name LIKE '%HttpClient%' OR sink.qualified_name LIKE '%::URL%' THEN 'SSRF'
        WHEN sink.name LIKE '%FileInputStream%' OR sink.name LIKE '%Paths.get%' OR sink.qualified_name LIKE '%::File%' THEN 'PATH_TRAV'
        WHEN sink.name LIKE '%ldapSearch%' OR sink.qualified_name LIKE '%::LdapTemplate%' THEN 'LDAP'
        ELSE 'UNKNOWN'
    END AS sink_type,
    cls_sink.qualified_name AS sink_class_fqn
FROM nodes m0
LEFT JOIN nodes cls_ep    ON cls_ep.kind = 'class' AND cls_ep.file_path = m0.file_path
LEFT JOIN nodes r_ep      ON r_ep.kind = 'route' AND r_ep.file_path = m0.file_path AND r_ep.start_line = m0.start_line
LEFT JOIN edges e1        ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1        ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN edges e2        ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes sink      ON sink.id = e2.target AND sink.kind = 'method'
LEFT JOIN nodes cls_sink  ON cls_sink.kind = 'class' AND cls_sink.file_path = sink.file_path
WHERE m0.kind = 'method'
  AND m0.qualified_name = :entry_fqn
  AND (
    sink.name LIKE '%executeQuery%' OR sink.name LIKE '%executeUpdate%'
    OR sink.name LIKE '%exec%' OR sink.qualified_name LIKE '%::Runtime%'
    OR sink.name LIKE '%readObject%' OR sink.name LIKE '%readValue%'
    OR sink.name LIKE '%openConnection%' OR sink.qualified_name LIKE '%::URL%'
    OR sink.name LIKE '%FileInputStream%' OR sink.name LIKE '%Paths.get%'
  )
"""

# T3: 端点 → 业务方法 → 鉴权注解缺失（用 decorators 列，v0.9.9 中为 NULL 时回退到 method 名称） = 12 JOIN
T3_AUTH_MISS_3HOP = """
-- 端点 m0 → 业务 m1 → DAO m2 链上任何一层无 @PreAuthorize 注解（缺失即漏洞）
SELECT
    m0.qualified_name AS ep_fqn,
    m1.qualified_name AS biz_fqn,
    cls1.qualified_name AS biz_cls,
    m1.decorators AS biz_anno,
    m2.qualified_name AS dao_fqn,
    cls2.qualified_name AS dao_cls,
    m2.decorators AS dao_anno
FROM nodes m0
LEFT JOIN edges e1        ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1        ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN nodes cls1      ON cls1.kind = 'class' AND cls1.file_path = m1.file_path
LEFT JOIN edges e2        ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes m2        ON m2.id = e2.target AND m2.kind = 'method'
LEFT JOIN nodes cls2      ON cls2.kind = 'class' AND cls2.file_path = m2.file_path
LEFT JOIN nodes r_ep      ON r_ep.kind = 'route' AND r_ep.file_path = m0.file_path AND r_ep.start_line = m0.start_line
LEFT JOIN nodes f1        ON f1.kind = 'field' AND f1.file_path = cls1.file_path
LEFT JOIN nodes f2        ON f2.kind = 'field' AND f2.file_path = cls2.file_path
LEFT JOIN edges in_cls1   ON in_cls1.target = m1.id AND in_cls1.kind = 'contains'
LEFT JOIN edges in_cls2   ON in_cls2.target = m2.id AND in_cls2.kind = 'contains'
WHERE m0.kind = 'method'
  AND m0.qualified_name = :entry_fqn
"""

# T4: 路由 → handler → 业务 → DAO → sink 全链 + 4 维上下文 = 18 JOIN
T4_FULL_FORWARD_4HOP = """
-- 4 跳前向 + route + class + field + caller 共 18 JOIN
SELECT
    r0.name AS route_name,
    m0.qualified_name AS ep_fqn,
    cls0.qualified_name AS ep_cls,
    caller_ep.qualified_name AS ep_caller,
    m1.qualified_name AS biz_fqn,
    cls1.qualified_name AS biz_cls,
    f1.name AS biz_field_name,
    m2.qualified_name AS dao_fqn,
    cls2.qualified_name AS dao_cls,
    f2.name AS dao_field_name,
    m3.qualified_name AS m3_fqn,
    cls3.qualified_name AS m3_cls,
    m4.qualified_name AS m4_fqn,
    cls4.qualified_name AS m4_cls
FROM nodes r0
LEFT JOIN nodes m0          ON m0.kind = 'method' AND m0.file_path = r0.file_path AND m0.start_line = r0.start_line
LEFT JOIN nodes cls0        ON cls0.kind = 'class' AND cls0.file_path = m0.file_path
LEFT JOIN edges caller_ep_e ON caller_ep_e.target = m0.id AND caller_ep_e.kind = 'calls'
LEFT JOIN nodes caller_ep   ON caller_ep.id = caller_ep_e.source AND caller_ep.kind = 'method'
LEFT JOIN edges e1          ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1          ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN nodes cls1        ON cls1.kind = 'class' AND cls1.file_path = m1.file_path
LEFT JOIN nodes f1          ON f1.kind = 'field' AND f1.file_path = cls1.file_path
LEFT JOIN edges e2          ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes m2          ON m2.id = e2.target AND m2.kind = 'method'
LEFT JOIN nodes cls2        ON cls2.kind = 'class' AND cls2.file_path = m2.file_path
LEFT JOIN nodes f2          ON f2.kind = 'field' AND f2.file_path = cls2.file_path
LEFT JOIN edges e3          ON e3.source = m2.id AND e2.kind = 'calls'
LEFT JOIN nodes m3          ON m3.id = e3.target AND m3.kind = 'method'
LEFT JOIN nodes cls3        ON cls3.kind = 'class' AND cls3.file_path = m3.file_path
LEFT JOIN edges e4          ON e4.source = m3.id AND e4.kind = 'calls'
LEFT JOIN nodes m4          ON m4.id = e4.target AND m4.kind = 'method'
LEFT JOIN nodes cls4        ON cls4.kind = 'class' AND cls4.file_path = m4.file_path
WHERE r0.kind = 'route'
"""

# T5: 端点 → 业务 → SQL 注入 sink + 鉴权注解检查 = 14 JOIN
T5_SQLI_AUTH_CONTEXT = """
-- 5 跳前向 + 上下文（route / class / field / contains）= 14 JOIN
SELECT
    m0.qualified_name AS ep_fqn,
    r0.name AS route_name,
    cls0.qualified_name AS ep_cls,
    m1.qualified_name AS biz_fqn,
    cls1.qualified_name AS biz_cls,
    m2.qualified_name AS dao_fqn,
    cls2.qualified_name AS dao_cls,
    sink.qualified_name AS sink_fqn,
    sink.start_line AS sink_line,
    sink.decorators AS sink_anno
FROM nodes m0
LEFT JOIN nodes r0          ON r0.kind = 'route' AND r0.file_path = m0.file_path AND r0.start_line = m0.start_line
LEFT JOIN nodes cls0        ON cls0.kind = 'class' AND cls0.file_path = m0.file_path
LEFT JOIN edges e1          ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1          ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN nodes cls1        ON cls1.kind = 'class' AND cls1.file_path = m1.file_path
LEFT JOIN edges e2          ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes m2          ON m2.id = e2.target AND m2.kind = 'method'
LEFT JOIN nodes cls2        ON cls2.kind = 'class' AND cls2.file_path = m2.file_path
LEFT JOIN edges e3          ON e3.source = m2.id AND e3.kind = 'calls'
LEFT JOIN nodes sink        ON sink.id = e3.target AND sink.kind = 'method'
LEFT JOIN nodes cls_sink    ON cls_sink.kind = 'class' AND cls_sink.file_path = sink.file_path
LEFT JOIN nodes f_sink      ON f_sink.kind = 'field' AND f_sink.file_path = cls_sink.file_path
LEFT JOIN edges sink_in_cls ON sink_in_cls.target = sink.id AND sink_in_cls.kind = 'contains'
WHERE m0.kind = 'method'
  AND m0.qualified_name = :entry_fqn
  AND (sink.name LIKE '%executeQuery%' OR sink.name LIKE '%executeUpdate%' OR sink.qualified_name LIKE '%::Statement%')
"""

TEMPLATES: Dict[str, Tuple[str, str]] = {
    # name: (description, sql)
    "forward_5hop_20join": ("5 跳前向 + 上下文 = 20 LEFT JOIN", T1_FORWARD_5HOP_20JOIN),
    "multi_sink_search":   ("多 sink 类型一次扫 = 8 LEFT JOIN",     T2_MULTI_SINK),
    "auth_miss_3hop":      ("3 跳鉴权缺失检测 = 12 LEFT JOIN",    T3_AUTH_MISS_3HOP),
    "full_forward_4hop":   ("route→4 跳前向 = 18 LEFT JOIN",       T4_FULL_FORWARD_4HOP),
    "sqli_auth_context":   ("SQLi 鉴权上下文 = 14 LEFT JOIN",      T5_SQLI_AUTH_CONTEXT),
}


# ============================================================== 执行

def run_template(db_path: str, template_name: str, params: dict, limit: int = 1000) -> tuple:
    if template_name not in TEMPLATES:
        raise ValueError(f"未知模板: {template_name}。可用: {', '.join(TEMPLATES.keys())}")
    desc, sql = TEMPLATES[template_name]

    for k, v in params.items():
        sql = sql.replace(f":{k}", _quote(v))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM ({sql}) LIMIT {int(limit)}")
    rows = cur.fetchall()
    conn.close()

    join_count = sql.count("LEFT JOIN")
    return [dict(r) for r in rows], join_count, desc


def run_custom(db_path: str, sql: str, params: dict, limit: int = 1000) -> list:
    for k, v in params.items():
        sql = sql.replace(f":{k}", _quote(v))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM ({sql}) LIMIT {int(limit)}")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _quote(v) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# ============================================================== CLI

def main():
    parser = argparse.ArgumentParser(
        description="codegraph SQLite 多跳 LEFT JOIN 搜索（最多 20 JOIN）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            预定义模板（每个模板的 LEFT JOIN 数量）:
              forward_5hop_20join  (20 JOIN)  5 跳前向 + route/class/field/caller
              full_forward_4hop   (18 JOIN)  route→4 跳前向 + 上下文
              sqli_auth_context   (14 JOIN)  SQLi sink + 鉴权 + 上下文
              auth_miss_3hop      (12 JOIN)  3 跳鉴权缺失
              multi_sink_search   ( 8 JOIN)  多 sink 类型一次扫

            占位符 :entry_fqn / :route_name 等会在执行前替换
        """),
    )
    parser.add_argument("--db", required=True, help="codegraph SQLite 路径")
    parser.add_argument("--template", choices=list(TEMPLATES.keys()), help="预定义模板")
    parser.add_argument("--custom", help="自定义 SQL（支持 :name 占位符）")
    parser.add_argument("--entry-fqn", help="端点 method qualified_name")
    parser.add_argument("--param", action="append", default=[], help="额外参数 key=value")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    if not args.template and not args.custom:
        parser.error("必须指定 --template 或 --custom")

    params = {}
    if args.entry_fqn:
        params["entry_fqn"] = args.entry_fqn
    for p in args.param:
        if "=" not in p:
            print(f"ERROR: --param 格式应为 key=value: {p}", file=sys.stderr)
            sys.exit(1)
        k, v = p.split("=", 1)
        params[k] = v

    try:
        if args.template:
            rows, join_count, desc = run_template(args.db, args.template, params, args.limit)
            output = {
                "template": args.template,
                "description": desc,
                "join_count": join_count,
                "row_count": len(rows),
                "rows": rows,
            }
        else:
            rows = run_custom(args.db, args.custom, params, args.limit)
            output = {
                "custom": True,
                "row_count": len(rows),
                "rows": rows,
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
