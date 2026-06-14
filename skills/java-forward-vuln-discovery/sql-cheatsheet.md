# codegraph SQLite Cheatsheet

> codegraph v0.9.9 真实 schema；最多 20 LEFT JOIN 表达复杂关系。

## 一、表结构（v0.9.9 实际验证）

| 表 | 关键字段 | 说明 |
|---|---------|------|
| `nodes` | `id, kind, name, qualified_name, file_path, start_line, end_line, signature, visibility, is_static, decorators, type_parameters, docstring` | **单表存所有符号**（method/class/field/route/...）|
| `edges` | `id, source, target, kind, line, col, metadata, provenance` | **单表存所有关系** |
| `nodes_fts` | FTS5 虚表 | `name, qualified_name, docstring, signature` |
| `files` | `path, content_hash, language, size, indexed_at` | 跟踪文件 |

### 1.1 node id 格式
```
method:hash          class:hash          field:hash
route:file:line:METHOD:path   file:hash    import:hash
namespace:hash       interface:hash      enum:hash
```

### 1.2 edge kind
```
calls         references     contains       imports
extends       implements     instantiates
```
> 注：`route -> method` 通过 **references** 边（**不是 contains**）；通过 `file_path + start_line` 对齐。

### 1.3 已知坑
- `decorators` 列存在但 **v0.9.9 对 Java 注解提取不完整**（WebGoat 测试中 0 个 method 有 decorators）
- 替代方案：通过 `class.name` 命名约定（`XxxController` / `XxxService` / `XxxRepository`）反推

## 二、基础查询

### 2.1 找 method
```sql
-- 按 qualified_name 精确
SELECT id, name, qualified_name, file_path, start_line
FROM nodes
WHERE kind = 'method' AND qualified_name = 'pkg::Class::method';

-- 按 simple name
SELECT id, name, qualified_name FROM nodes
WHERE kind = 'method' AND name LIKE '%executeQuery%';
```

### 2.2 找所有端点（routes）
```sql
SELECT id, name, file_path, start_line FROM nodes WHERE kind = 'route';
-- 269 routes in WebGoat
```

### 2.3 route ↔ method 关联
```sql
-- 同一 file:line 的 route 节点 和 method 节点 = 端点
SELECT r.name AS route, m.name AS handler, m.qualified_name
FROM nodes r
JOIN nodes m ON m.file_path = r.file_path AND m.start_line = r.start_line
WHERE r.kind = 'route' AND m.kind = 'method';
```

### 2.4 调用方 / 被调用方
```sql
-- callees (前向)：本方法调用的
SELECT callee.id, callee.qualified_name
FROM edges e
JOIN nodes callee ON callee.id = e.target
WHERE e.source = :method_id AND e.kind = 'calls';

-- callers (反向)：谁调用本方法
SELECT caller.id, caller.qualified_name
FROM edges e
JOIN nodes caller ON caller.id = e.source
WHERE e.target = :method_id AND e.kind = 'calls';
```

## 三、调用链提取（CTE RECURSIVE）

### 3.1 前向（callee 方向）
```sql
WITH RECURSIVE chain(id, qn, depth, path) AS (
    SELECT n.id, n.qualified_name, 0, '|' || n.id
    FROM nodes n
    WHERE n.id = :entry_id AND n.kind = 'method'

    UNION ALL

    SELECT callee.id, callee.qualified_name, c.depth + 1,
           c.path || '|' || callee.id
    FROM chain c
    JOIN edges e ON e.source = c.id AND e.kind = 'calls'
    JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
    WHERE c.depth < 20
      AND instr(c.path, '|' || callee.id || '|') = 0  -- 防环
)
SELECT id, qn, depth, file_path, start_line FROM chain ORDER BY depth;
```

**WebGoat 实测**：
- 入口 `LessonMenuService::showLeftNav` → 142 节点 / max_depth=7
- 大多数入口深度 < 5（Spring 浅调用特征）

### 3.2 反向（caller 方向，本轮不在 scope）
```sql
WITH RECURSIVE chain(id, qn, depth, path) AS (
    SELECT id, qualified_name, 0, '|' || id FROM nodes WHERE id = :sink_id
    UNION ALL
    SELECT caller.id, caller.qualified_name, c.depth + 1,
           c.path || '|' || caller.id
    FROM chain c
    JOIN edges e ON e.target = c.id AND e.kind = 'calls'
    JOIN nodes caller ON caller.id = e.source AND caller.kind = 'method'
    WHERE c.depth < 20 AND instr(c.path, '|' || caller.id || '|') = 0
)
SELECT id, qn, depth FROM chain;
```

## 四、多跳 LEFT JOIN（**真实能力**）

每跳成本 = **2 LEFT JOIN**（`edges` + `nodes`）。
5 跳 = 10 JOIN；加 4 跳 class 关联 = 14 JOIN；加 route + field = **20 JOIN 上限**。

### 4.1 5 跳前向 + 完整上下文 = 20 LEFT JOIN（**端点 → 业务 → DAO → sink**）

```sql
-- 端点 m0 → m1 → m2 → m3 → m4 → m5
-- 每跳 3 JOIN: edge + callee + containing_class
-- + 首跳 route + caller + field = 20 JOIN 总
SELECT
    m0.qualified_name AS m0_fqn,
    r0.name           AS route_name,
    cls0.qualified_name AS cls0_fqn,
    caller0.qualified_name AS caller0_fqn,
    m1.qualified_name AS m1_fqn,
    cls1.qualified_name AS cls1_fqn,
    m2.qualified_name AS m2_fqn,
    cls2.qualified_name AS cls2_fqn,
    m3.qualified_name AS m3_fqn,
    cls3.qualified_name AS cls3_fqn,
    m4.qualified_name AS m4_fqn,
    cls4.qualified_name AS cls4_fqn,
    m5.qualified_name AS m5_fqn,
    cls5.qualified_name AS cls5_fqn,
    fld0.name         AS m0_class_field_name
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
WHERE m0.kind = 'method' AND m0.qualified_name = :entry_fqn
```

**WebGoat 实测**（`LessonMenuService::showLeftNav` 入口）：
- join_count: 20
- row_count: 590
- 耗时: ~50ms

### 4.2 多 sink 类型一次扫（8 JOIN）

```sql
-- 端点 → m1 → sink（任意 6 类 sink 模式）
SELECT
    m0.qualified_name AS ep_fqn,
    sink.qualified_name AS sink_fqn,
    CASE
        WHEN sink.name LIKE '%executeQuery%' THEN 'SQLI'
        WHEN sink.name LIKE '%exec%' THEN 'RCE'
        WHEN sink.name LIKE '%readObject%' OR sink.name LIKE '%readValue%' THEN 'DESER'
        WHEN sink.name LIKE '%openConnection%' THEN 'SSRF'
        WHEN sink.name LIKE '%FileInputStream%' OR sink.name LIKE '%Paths.get%' THEN 'PATH_TRAV'
        WHEN sink.name LIKE '%ldapSearch%' THEN 'LDAP'
    END AS sink_type
FROM nodes m0
LEFT JOIN nodes cls_ep    ON cls_ep.kind = 'class' AND cls_ep.file_path = m0.file_path
LEFT JOIN nodes r_ep      ON r_ep.kind = 'route' AND r_ep.file_path = m0.file_path AND r_ep.start_line = m0.start_line
LEFT JOIN edges e1        ON e1.source = m0.id AND e1.kind = 'calls'
LEFT JOIN nodes m1        ON m1.id = e1.target AND m1.kind = 'method'
LEFT JOIN edges e2        ON e2.source = m1.id AND e2.kind = 'calls'
LEFT JOIN nodes sink      ON sink.id = e2.target AND sink.kind = 'method'
LEFT JOIN nodes cls_sink  ON cls_sink.kind = 'class' AND cls_sink.file_path = sink.file_path
WHERE m0.kind = 'method' AND m0.qualified_name = :entry_fqn
  AND (sink.name LIKE '%executeQuery%' OR sink.name LIKE '%exec%'
       OR sink.name LIKE '%readObject%' OR sink.name LIKE '%openConnection%' ...)
```

### 4.3 3 跳鉴权缺失 = 12 JOIN

```sql
SELECT m0.qualified_name AS ep_fqn,
       m1.qualified_name AS biz_fqn, m1.decorators AS biz_anno,
       m2.qualified_name AS dao_fqn, m2.decorators AS dao_anno
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
WHERE m0.kind = 'method' AND m0.qualified_name = :entry_fqn
```

## 五、性能数量级

| 操作 | WebGoat 实测 |
|------|-------------|
| Schema inspect (全表统计) | 3ms |
| CTE RECURSIVE depth=20, 142 节点 | 1ms |
| LEFT JOIN 20 (5 跳前向 + 上下文) 590 行 | ~50ms |
| Pattern search 全 6 类 sink | 504ms (subprocess 启动开销) |
| 直接 SQLite | 1-50ms |

## 六、参考

- 工具脚本: `脚本/chain/sqlite-extract-chain.py`（CTE RECURSIVE + LEFT JOIN 双模式）
- 工具脚本: `脚本/chain/sqlite-pattern-search.py`（6 类 sink 模式）
- 工具脚本: `脚本/chain/sqlite-multi-hop-search.py`（5 模板最多 20 JOIN）
- 端到端测试: `test_webgoat.py`（6 步测试套件）
- 测试报告: `test-output/test-report.json`
- codegraph 文档: `doc/codegraph-usage-guide.md`
