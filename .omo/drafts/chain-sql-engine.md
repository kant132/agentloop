# Draft: 调用链引擎 — 后续 plan(scripts-refactor 完成后启动)

## 背景

本 draft 是为 **scripts-refactor** 完成后的下一步工作记录的思路。不是本次重构的范围。

### 触发场景
- 用户提供了调用链 SQL 思考(分层 inner join)
- 用户提供了 `tools/java-method-call-extractor-1.0.0.jar` 工具,用于提取 method 内所有调用
- 用户在 scripts-refactor 期间决定: **hashkey 策略从 md5 改为 nodes.id**
- 但 attack_surface_scanner.py 暂保持 md5(它用 ast-grep 拿不到 nodes.id); 本次 draft 计划把攻击面扫描也迁移到 nodes.id 体系

## 工具现状调研

### 1. `tools/java-method-call-extractor-1.0.0.jar`

**用途**: 给定一个 Java 源文件,提取该文件中**每个 method 的所有方法调用**(带 FQN)。

**CLI**:
```bash
java -jar java-method-call-extractor-1.0.0.jar <file.java> [sourceRoot]
```
- sourceRoot: 可选,项目的源码根目录,用于跨文件 FQN 解析

**输出格式(JSON 数组)**:
```json
[
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.util.List.add(item)"},
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.io.PrintStream.println(\"Added: \" + item)"},
  {"startLine": 14, "methodSignature": "public int countItems()",           "calledFQN": "java.util.List.size()"},
  {"startLine": 18, "methodSignature": "public void processAll(...)",       "calledFQN": "com.example.SampleService.addItem(s.toUpperCase())"},
  {"startLine": 18, "methodSignature": "public void processAll(...)",       "calledFQN": "java.lang.String.toUpperCase()"}
]
```

**关键字段**:
- `startLine` (1-based): method 起始行号 — **用这个关联 codegraph nodes**
- `methodSignature`: method 签名(仅用于展示,关联不靠它)
- `calledFQN`: 该方法内的每个外部调用 FQN

**与 codegraph 关联**:
```sql
-- 在 codegraph SQLite 中反查 method 的 nodes.id
SELECT id, file_path, qualified_name, start_line, end_line
FROM nodes
WHERE file_path = :file
  AND start_line - 1 = :startLine  -- codegraph start_line 是 0-based, jar 是 1-based
  AND kind = 'method'
  AND language = 'java';
-- id 即可作为本 method 的 hashkey(notes.id 策略)
```

注: 用户明确**只用 startLine + 文件名关联即可**,method 名不需参与匹配(避免重载干扰)。

---

### 2. `scripts/chain/sqlite-extract-chain.py` (已存在, CTE 模式正确)

**已实现**分层递归查询,就是用户想要的"分层 inner join":
```sql
WITH RECURSIVE chain(...) AS (
    SELECT n.id, ... FROM nodes n WHERE n.id = :entry_id AND n.kind = 'method'
    UNION ALL
    SELECT callee.id, ... FROM chain c
    JOIN edges e ON e.source = c.id AND e.kind = 'calls'
    JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || callee.id || '|') = 0  -- 防环
)
SELECT id, qualified_name, depth, file_path, start_line FROM chain ORDER BY depth
```

**保留不动**,只补 sink 提取。

---

### 3. `scripts/chain/sqlite-multi-hop-search.py` (已存在, **确认有问题**)

**L3 人工批注**: "这个实现有问题,应该是分层innerjoin"

现状: 一次性 LEFT JOIN 拉平 5 跳, 20 个 LEFT JOIN。
问题:
- LEFT JOIN 拉平会把"调用链"展平成多列,但丢失了"某跳没调用 = 该分支终止"的信息
- 无法处理不定深度
- SQL 模板膨胀,每加一跳加 4 个 JOIN

**计划**: 删除模板,统一到 sqlite-extract-chain.py 的 CTE 递归模式,加 sink 提取。

---

### 4. `tools/attack-surface-scanner/attack_surface_scanner.py` (即将在重构中迁入scripts/ast/)

现状: 用 ast-grep 扫 Java 注解,产出 (注解FQN, file, line), 用 md5 当 hashkey。
目标: 改用 codegraph nodes.id 当 hashkey,需要补一步反查:
```sql
-- 拿到 (file, line) 后,反查 nodes.id 作为 hashkey
SELECT id FROM nodes
WHERE file_path = :file
  AND start_line - 1 <= :annotation_line
  AND end_line >= :annotation_line
  AND kind IN ('method', 'class')
LIMIT 1;
```

---

## 总体工作流

### 主流程(端点驱动的调用链构建)

```
输入: 一个入口端点的 nodes.id (即 hashkey)

1. sqlite-extract-chain.py → 得到调用链上每个 method 的 nodes.id 清单
   (用 CTE RECURSIVE, 已实现, 不动)

2. 对每个 method id:
   a. 查 codegraph SQLite 拿 (file_path, start_line, end_line)
   b. 调 java-method-call-extractor-1.0.0.jar 获取该文件内所有 method 的 calls
   c. 用 (file, startLine) 匹配到本 method 的 calls 清单
   d. 从 calls 中过滤 calledFQN 不以 groupId 开头的 → **全部当作 sink**
      (简化策略: 80/20,先不考虑 sink 模式精准匹配,后续可加)

3. 对每个 sink FQN, 调 scanner_utils.inject_sink_comment(method_body_line, sink_fqn) 注入注释
   (scanner_utils.py 已在本次 plan Task 1 实现)

4. 把注入 sink 注释后的 method body 写 Memurai: {groupId}:method:{nodes.id}
   (Memurai SET+EX 86400, 走 pipe_setex_batch 高效)

5. 写 chains/{nodes.id}.json: {"groupId": ..., "chains": [[id1, id2, ...], [...]]}
   (每个子链是一个路径, 存的是 nodes.id 数组, 不存 hash)

输出: chains/{nodes.id}.json + Memurai {groupId}:method:* 缓存
```

### 关键关联算法 (file + startLine → method calls)

```python
def extract_method_calls_for_node(node_id, codegraph_db, groupId, jar_path):
    # 1. 拿 nodes 信息
    row = run_sql(codegraph_db, "SELECT file_path, start_line, end_line FROM nodes WHERE id=?", (node_id,))
    file_path, start_0, end_0 = row
    start_1 = start_0 + 1  # 转 jar 的 1-based

    # 2. 调 jar(对每个 unique file 只调一次,结果缓存在 file_calls_map[file])
    if file_path not in file_calls_map:
        file_calls_map[file_path] = subprocess.run(["java","-jar",jar_path,file_path]).parsed_json

    # 3. 过滤出本 method 的 calls
    method_calls = [r for r in file_calls_map[file_path]
                    if r["startLine"] == start_1]

    # 4. 筛 sink: calledFQN 不以 groupId 开头
    sinks = [r["calledFQN"] for r in method_calls
             if not r["calledFQN"].startswith(groupId + ".")]

    return sinks
```

---

## 关键设计决策(已记录, 待 plan 化)

| 决策 | 选择 | 理由 |
|------|------|------|
| hashkey 标识 | **nodes.id** | 与 codegraph 原生 id 一致,免去 md5 计算和碰撞风险 |
| 调用链查询方式 | **CTE RECURSIVE** (sqlite-extract-chain.py) | 已实现,支持任意深度,带环检测;弃用 LEFT JOIN 模板 |
| method calls 提取 | **java-method-call-extractor-1.0.0.jar** | JavaParser 全 FQN 解析,跨文件精准,非 ast-grep 模式匹配 |
| method ↔ calls 关联 | **(file + startLine) join**, 不依赖 method 名 | 避免 Java 重载干扰; startLine 1:1 映射 method(绝大多数情况) |
| sink 判定 | **calledFQN 不以 groupId 开头 → 全是 sink** | 80/20 简化,先跑通流程,后续可加 sink 模式表精准匹配 |
| sink 注释 | **`// sink: <FQN>`** 注入 method body 内调用行之前 | 设计文档 §8 inject_sink_comment 的规格;与 scanner_utils 一致 |
| Memurai 缓存 key | `{groupId}:method:{nodes.id}` | 与 nodes.id hashkey 体系一致 |
| chains 文件命名 | `chains/{nodes.id}.json` | hashkey 即文件名,无需额外映射 |
| attack-surface-scanner | **迁移到 nodes.id 体系** | 需要在 ast-grep 产出的 (file, line) 上加 codegraph 反查 nodes.id |

---

## 范围边界(本次 draft 不实施, 记录用途)

### 本次 draft (chain-sql-engine) 范围 (未来)
- ✏️ 新建 `scripts/chain/method_calls_extractor.py`(Python 封装 jar 调用 + file/startLine 关联)
- ✏️ 新建 `scripts/chain/chain_builder.py`(整合 sqlite-extract-chain + method_calls_extractor + sink 提取 + Memurai 写入 + chains JSON 输出)
- ✏️ 调整 `scripts/ast/attack_surface_scanner.py` 输出节点 id 而非 md5 hash(需要 codegraph 反查)
- ❌ 删除 `scripts/chain/sqlite-multi-hop-search.py`(LEFT JOIN 模板,已被 CTE 完全替代)
- ❌ 删除 `scripts/ast/scanner_utils.py` 中的 `node_hash_key`(直接用 nodes.id,不需要规范化函数)

### 本 draft 明确不做
- ❌ 不引入新数据库(继续用 SQLite + Memurai)
- ❌ 不实现 sink 模式精准匹配(用 80/20 全 sink 策略)
- ❌ 不跨语言(只 Java)

---

## 与 scripts-refactor 的依赖关系

```
scripts-refactor (当前)
├── 建立 scanner_utils.py (含 node_hash_key, inject_sink_comment, classify_route, validate)
├── 把 tools/attack-surface-scanner/*.py 迁入scripts/ast/(暂不改 hashkey 策略)
└── thrid-tool typo 合并到 tools/ → 修正所有引用
    │
    ▼ (scripts-refactor 完成后)
chain-sql-engine (本 draft)
├── 实现 method_calls_extractor.py (封装 java-method-call-extractor-1.0.0.jar)
├── 实现 chain_builder.py (CTE + call extractor + sink 提取 + Memurai + chains.json)
├── 改 attack_surface_scanner 输出 nodes.id (codegraph 反查)
├── 删除 sqlite-multi-hop-search.py
└── 删除 scanner_utils.node_hash_key (冗余)
```

---

## 待回答的开放问题(启动 chain-sql-engine plan 前需澄清)

1. **startLine 是否 100% 唯一**: 在同一文件内, 不同 method 的 startLine 是否一定不同? 如果有内部匿名类方法, startLine 可能等于外层 method 吗? — 需要实测验证
2. **codegraph 的 start_line 是 0-based 还是 1-based**: sqlite-extract-chain.py 代码显示是 0-based (SELECT start_line - 1 as start), 但 jar 输出是 1-based, 关联时需要 +1 或 -1 对齐
3. **Memurai 缓存过期策略**: 设计文档 §3 说 TTL 24h + 每轮启动 DEL groupId:*; 本 draft 沿用还是缩短?
4. **codegraph SQLite 是否支持并发读**: 多个 worker 同时 query 同一 codegraph.db 会不会锁? 要不要用 WAL 模式?
5. **大 method (body > 1MB) 处理**: inject_sink_comment 是单行处理的,对超长 body 是否需要流式或分段?

---

## 验证策略(待 plan 化后细化)

- **单元**: extract_method_calls(file_path, start_line) 对 fixture Java 文件输出正确 calls + 正确 sink 过滤
- **集成**: chain_builder 对一个小型 Java 项目产出 chains/{id}.json + Memurai key 正确
- **端到端**: 对一个 WebGoat 端点完整跑流程,人工抽检一个调用链的 sink 提取是否正确

---

## 下一步

等 scripts-refactor 完工(`/start-work` 跑完) → 回到本 draft, 用 `/writing-plans` 生成正式 plan。